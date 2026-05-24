import stripe
from app.config import get_settings
from app.db import queries as db
import structlog

log = structlog.get_logger()


def _stripe() -> stripe:
    stripe.api_key = get_settings().stripe_secret_key
    return stripe


async def create_checkout(user_id: str, user_email: str, success_url: str, cancel_url: str) -> str:
    """
    Create a Stripe Checkout session for the Pro plan.
    Returns the hosted checkout URL.
    """
    s = get_settings()
    client = _stripe()

    # Reuse existing Stripe customer if available
    sub = await db.get_subscription(user_id)
    customer_id = sub.get("stripe_customer") if sub else None

    session = client.checkout.Session.create(
        customer=customer_id,
        customer_email=None if customer_id else user_email,
        mode="subscription",
        line_items=[{"price": s.stripe_pro_price_id, "quantity": 1}],
        success_url=success_url + "?session_id={CHECKOUT_SESSION_ID}",
        cancel_url=cancel_url,
        metadata={"user_id": user_id},
    )
    log.info("checkout_created", user=user_id)
    return session.url


async def handle_webhook(payload: bytes, sig_header: str) -> None:
    """
    Process Stripe webhook events.
    Called by the webhook endpoint — verifies signature then updates Supabase.
    """
    s = get_settings()
    try:
        event = _stripe().Webhook.construct_event(payload, sig_header, s.stripe_webhook_secret)
    except Exception as e:
        log.error("webhook_signature_invalid", error=str(e))
        raise ValueError("Invalid webhook signature")

    event_type = event["type"]
    log.info("stripe_event", type=event_type)

    if event_type == "checkout.session.completed":
        session = event["data"]["object"]
        user_id = session["metadata"].get("user_id")
        if user_id:
            await db.upsert_subscription(
                user_id=user_id,
                stripe_customer=session["customer"],
                stripe_sub_id=session["subscription"],
                status="pro",
            )

    elif event_type in ("customer.subscription.deleted", "customer.subscription.paused"):
        sub_obj = event["data"]["object"]
        existing = await db.get_subscription_by_stripe_customer(sub_obj["customer"])
        if existing:
            await db.upsert_subscription(
                user_id=existing["user_id"],
                stripe_customer=sub_obj["customer"],
                stripe_sub_id=sub_obj["id"],
                status="cancelled",
            )


async def get_subscription_status(user_id: str) -> dict:
    sub = await db.get_subscription(user_id)
    if not sub:
        return {"status": "free", "stripe_customer": None, "stripe_sub_id": None}
    return {
        "status":          sub.get("status", "free"),
        "stripe_customer": sub.get("stripe_customer"),
        "stripe_sub_id":   sub.get("stripe_sub_id"),
    }

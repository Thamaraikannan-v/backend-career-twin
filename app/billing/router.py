from fastapi import APIRouter, Depends, Request, HTTPException
from app.billing import service
from app.billing.schemas import CheckoutRequest, CheckoutResponse, SubscriptionStatus
from app.dependencies import get_current_user, AuthUser

router = APIRouter(prefix="/api/billing", tags=["billing"])


@router.post("/checkout", response_model=CheckoutResponse)
async def create_checkout(
    body: CheckoutRequest,
    user: AuthUser = Depends(get_current_user),
):
    """
    Create a Stripe Checkout session.
    Returns a URL the frontend redirects the user to.
    """
    url = await service.create_checkout(
        user_id=user.id,
        user_email=user.email,
        success_url=body.success_url,
        cancel_url=body.cancel_url,
    )
    return CheckoutResponse(checkout_url=url)


@router.post("/webhook")
async def stripe_webhook(request: Request):
    """
    Stripe webhook receiver.
    Must be excluded from JWT auth — Stripe calls this directly.
    Add this URL in your Stripe dashboard → Webhooks.
    """
    payload    = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    try:
        await service.handle_webhook(payload, sig_header)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {"received": True}


@router.get("/subscription", response_model=SubscriptionStatus)
async def get_subscription(user: AuthUser = Depends(get_current_user)):
    """Returns the current user's subscription status (free | pro | cancelled)."""
    return await service.get_subscription_status(user.id)

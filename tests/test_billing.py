"""
Unit tests for billing service logic (mocked Stripe).

Run:  pytest tests/test_billing.py -v
"""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from app.billing import service


@pytest.mark.asyncio
async def test_create_checkout_returns_url():
    mock_session = MagicMock()
    mock_session.url = "https://checkout.stripe.com/test-session"

    with (
        patch("app.billing.service.db.get_subscription", return_value=None),
        patch("app.billing.service._stripe") as mock_stripe,
    ):
        mock_stripe.return_value.checkout.Session.create.return_value = mock_session

        url = await service.create_checkout(
            user_id="user-1",
            user_email="user@example.com",
            success_url="https://myapp.com/success",
            cancel_url="https://myapp.com/cancel",
        )
        assert url == "https://checkout.stripe.com/test-session"


@pytest.mark.asyncio
async def test_handle_webhook_checkout_completed():
    payload    = b'{"type": "checkout.session.completed"}'
    sig_header = "test-sig"

    mock_event = {
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "metadata":     {"user_id": "user-1"},
                "customer":     "cus_123",
                "subscription": "sub_456",
            }
        },
    }

    with (
        patch("app.billing.service._stripe") as mock_stripe,
        patch("app.billing.service.db.upsert_subscription", new_callable=AsyncMock) as mock_upsert,
    ):
        mock_stripe.return_value.Webhook.construct_event.return_value = mock_event

        await service.handle_webhook(payload, sig_header)

        mock_upsert.assert_called_once_with(
            user_id="user-1",
            stripe_customer="cus_123",
            stripe_sub_id="sub_456",
            status="pro",
        )


@pytest.mark.asyncio
async def test_get_subscription_status_free_when_no_record():
    with patch("app.billing.service.db.get_subscription", return_value=None):
        result = await service.get_subscription_status("user-1")
        assert result["status"] == "free"


@pytest.mark.asyncio
async def test_get_subscription_status_pro():
    mock_sub = {
        "status":          "pro",
        "stripe_customer": "cus_123",
        "stripe_sub_id":   "sub_456",
    }
    with patch("app.billing.service.db.get_subscription", return_value=mock_sub):
        result = await service.get_subscription_status("user-1")
        assert result["status"] == "pro"

from pydantic import BaseModel
from typing import Optional


class CheckoutRequest(BaseModel):
    success_url: str
    cancel_url:  str


class CheckoutResponse(BaseModel):
    checkout_url: str


class SubscriptionStatus(BaseModel):
    status:          str            # free | pro | cancelled
    stripe_customer: Optional[str]
    stripe_sub_id:   Optional[str]

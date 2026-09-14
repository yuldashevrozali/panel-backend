from datetime import datetime
from decimal import Decimal
from pydantic import BaseModel, Field


class PaymentRequestCreate(BaseModel):
    amount: Decimal = Field(gt=0)
    method: str = Field(default="admin")
    currency: str = Field(default="USD")


class PaymentRequestRead(BaseModel):
    id: int
    user_id: int
    amount: Decimal
    currency: str
    method: str
    status: str
    rejection_reason: str | None = None
    reviewed_by: int | None = None
    reviewed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

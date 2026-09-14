from datetime import datetime
from decimal import Decimal
from pydantic import BaseModel, EmailStr, Field


class AdminUserRead(BaseModel):
    id: int
    telegram_id: int | None = None
    google_sub: str | None = None
    email: str | None = None
    username: str | None = None
    first_name: str | None = None
    role: str
    balance: Decimal
    created_at: datetime


class AdminUserList(BaseModel):
    items: list[AdminUserRead]
    total: int
    limit: int
    offset: int


class AdminOrderRead(BaseModel):
    id: int
    user_id: int
    external_order_id: str | None = None
    service_id: str
    link: str
    quantity: int
    charge: Decimal
    status: str
    provider_charge: Decimal | None = None
    failure_reason: str | None = None
    created_at: datetime
    updated_at: datetime


class AdminOrderList(BaseModel):
    items: list[AdminOrderRead]
    total: int
    limit: int
    offset: int


class AdminStatsRead(BaseModel):
    total_users: int
    total_orders: int
    pending_orders: int
    completed_orders: int
    failed_orders: int
    total_revenue: Decimal
    total_user_balance: Decimal


class AdminCreate(BaseModel):
    email: EmailStr


class AdminPaymentRead(BaseModel):
    id: int
    user_id: int
    user_email: str | None = None
    user_name: str | None = None
    amount: Decimal
    currency: str
    method: str
    status: str
    rejection_reason: str | None = None
    reviewed_by: int | None = None
    reviewer_email: str | None = None
    reviewed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class AdminPaymentList(BaseModel):
    items: list[AdminPaymentRead]
    total: int
    pending_count: int
    limit: int
    offset: int


class AdminRejectPayload(BaseModel):
    rejection_reason: str | None = Field(default=None, max_length=255)

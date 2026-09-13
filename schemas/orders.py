from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class OrderCreate(BaseModel):
    service_id: str = Field(min_length=1, max_length=100)
    link: HttpUrl
    quantity: int = Field(gt=0)


class OrderRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    external_order_id: str
    service_id: str
    link: str
    quantity: int
    charge: Decimal
    status: str
    created_at: datetime
    updated_at: datetime


class OrderPage(BaseModel):
    items: list[OrderRead]
    page: int
    page_size: int
    total: int

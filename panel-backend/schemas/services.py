from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class ServiceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    service: str
    name: str
    category: str
    rate: Decimal
    min: int
    max: int
    type: str | None = None
    refill: bool
    cancel: bool
    dripfeed: bool

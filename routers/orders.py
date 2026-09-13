from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal, ROUND_UP
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database.connection import get_db
from database.models import Order, User, WalletTransaction
from routers.security import get_current_user
from services.onepanel import OnePanelError, onepanel


router = APIRouter(prefix="/orders", tags=["Orders"])


# ============================================================
# SCHEMAS
# ============================================================


class OrderCreate(BaseModel):
    service_id: str = Field(min_length=1, max_length=100)
    link: str = Field(min_length=1, max_length=5000)
    quantity: int = Field(gt=0)


class OrderRead(BaseModel):
    id: int
    external_order_id: str | None
    service_id: str
    link: str
    quantity: int
    charge: Decimal
    status: str
    provider_charge: Decimal | None
    failure_reason: str | None
    created_at: datetime
    updated_at: datetime


# ============================================================
# HELPERS
# ============================================================


MONEY_QUANT = Decimal("0.01")
RATE_QUANT = Decimal("0.0001")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def calculate_charge(rate: Decimal, quantity: int) -> Decimal:
    """
    1xPanel rate is normally the price per 1000 units.

    Example:
        rate = 0.42
        quantity = 1000
        charge = 0.42

    Because the User.balance column has 2 decimal places,
    customer charge is rounded UP to the nearest cent.
    This prevents very small orders from becoming free.
    """
    raw_charge = (rate * Decimal(quantity)) / Decimal("1000")

    if raw_charge <= Decimal("0"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Xizmat narxi noto‘g‘ri.",
        )

    return raw_charge.quantize(MONEY_QUANT, rounding=ROUND_UP)


def build_request_fingerprint(
    service_id: str,
    link: str,
    quantity: int,
) -> str:
    payload = {
        "service_id": service_id,
        "link": link.strip(),
        "quantity": quantity,
    }

    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")

    return hashlib.sha256(encoded).hexdigest()


def validate_idempotency_key(value: str | None) -> str:
    if not value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Idempotency-Key required.",
        )

    key = value.strip()

    if not key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Idempotency-Key bo‘sh bo‘lishi mumkin emas.",
        )

    if len(key) > 128:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Idempotency-Key juda uzun.",
        )

    return key


def serialize_order(order: Order) -> dict[str, Any]:
    return {
        "id": order.id,
        "external_order_id": order.external_order_id,
        "service_id": order.service_id,
        "link": order.link,
        "quantity": order.quantity,
        "charge": order.charge,
        "status": order.status,
        "provider_charge": order.provider_charge,
        "failure_reason": order.failure_reason,
        "created_at": order.created_at,
        "updated_at": order.updated_at,
    }


def refund_order(
    db: Session,
    order: Order,
    reason: str,
) -> None:
    """
    Refund a failed provider order.

    The refund transaction intentionally has order_id=None because
    WalletTransaction.order_id is unique and the original debit already
    uses the order_id.
    """
    user = db.query(User).filter(User.id == order.user_id).with_for_update().one()

    balance_before = Decimal(user.balance or 0)
    refund_amount = Decimal(order.charge)

    user.balance = balance_before + refund_amount

    refund_transaction = WalletTransaction(
        user_id=user.id,
        order_id=None,
        amount=refund_amount,
        transaction_type="refund",
        balance_before=balance_before,
        balance_after=user.balance,
        metadata_json={
            "order_id": order.id,
            "reason": reason,
        },
    )

    db.add(refund_transaction)

    order.status = "failed"
    order.failure_reason = reason
    order.updated_at = utc_now()

    db.commit()


# ============================================================
# CREATE ORDER
# ============================================================


@router.post("", response_model=OrderRead)
def create_order(
    payload: OrderCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    idempotency_key: str | None = Header(
        default=None,
        alias="Idempotency-Key",
    ),
):
    """
    Create a customer order.

    Flow:

    1. Validate idempotency key
    2. Check existing idempotent order
    3. Find service from 1xPanel
    4. Validate quantity min/max
    5. Calculate customer charge
    6. Lock user row
    7. Check balance
    8. Create pending order
    9. Deduct balance
    10. Commit durable order + wallet transaction
    11. Send order to 1xPanel
    12. Save provider order ID
    """

    key = validate_idempotency_key(idempotency_key)

    fingerprint = build_request_fingerprint(
        service_id=payload.service_id,
        link=payload.link,
        quantity=payload.quantity,
    )

    # --------------------------------------------------------
    # IDEMPOTENCY CHECK
    # --------------------------------------------------------

    existing_order = (
        db.query(Order)
        .filter(
            Order.user_id == current_user.id,
            Order.idempotency_key == key,
        )
        .first()
    )

    if existing_order:
        if existing_order.request_fingerprint != fingerprint:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Bu Idempotency-Key boshqa buyurtma bilan allaqachon ishlatilgan."
                ),
            )

        return serialize_order(existing_order)

    # --------------------------------------------------------
    # SERVICE LOOKUP
    # --------------------------------------------------------

    try:
        service = onepanel.find_service(payload.service_id)

    except OnePanelError as exc:
        raise HTTPException(
            status_code=503 if exc.unavailable else 502,
            detail=str(exc),
        ) from exc

    if service is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tanlangan xizmat topilmadi.",
        )

    # --------------------------------------------------------
    # QUANTITY VALIDATION
    # --------------------------------------------------------

    minimum = int(service["min"])
    maximum = int(service["max"])

    if payload.quantity < minimum:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(f"Miqdor minimal qiymatdan kam. Minimal miqdor: {minimum:,}."),
        )

    if payload.quantity > maximum:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(f"Miqdor maksimal qiymatdan oshdi. Maksimal miqdor: {maximum:,}."),
        )

    # --------------------------------------------------------
    # LINK VALIDATION
    # --------------------------------------------------------

    link = payload.link.strip()

    if not (link.startswith("https://") or link.startswith("http://")):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Maqsadli havola http:// yoki https:// bilan boshlanishi kerak.",
        )

    # --------------------------------------------------------
    # CUSTOMER CHARGE
    # --------------------------------------------------------

    customer_charge = calculate_charge(
        rate=Decimal(service["rate"]),
        quantity=payload.quantity,
    )

    # --------------------------------------------------------
    # LOCK USER
    # --------------------------------------------------------

    user = db.query(User).filter(User.id == current_user.id).with_for_update().one()

    current_balance = Decimal(user.balance or 0)

    # --------------------------------------------------------
    # BALANCE CHECK
    # --------------------------------------------------------

    if current_balance < customer_charge:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=(
                f"Balansingiz yetarli emas. "
                f"Kerak: {customer_charge:.2f}. "
                f"Mavjud: {current_balance:.2f}."
            ),
        )

    # --------------------------------------------------------
    # CREATE LOCAL ORDER
    # --------------------------------------------------------

    order = Order(
        user_id=user.id,
        external_order_id=None,
        service_id=str(payload.service_id),
        link=link,
        quantity=payload.quantity,
        charge=customer_charge,
        status="pending",
        idempotency_key=key,
        request_fingerprint=fingerprint,
        provider_charge=None,
        provider_checked_at=None,
        failure_reason=None,
    )

    db.add(order)
    db.flush()

    # --------------------------------------------------------
    # DEDUCT BALANCE
    # --------------------------------------------------------

    balance_before = current_balance
    balance_after = current_balance - customer_charge

    user.balance = balance_after

    wallet_transaction = WalletTransaction(
        user_id=user.id,
        order_id=order.id,
        amount=-customer_charge,
        transaction_type="order",
        balance_before=balance_before,
        balance_after=balance_after,
        metadata_json={
            "service_id": str(payload.service_id),
            "quantity": payload.quantity,
        },
    )

    db.add(wallet_transaction)

    # --------------------------------------------------------
    # DURABLE LOCAL STATE
    # --------------------------------------------------------

    try:
        db.commit()

    except IntegrityError:
        db.rollback()

        existing_order = (
            db.query(Order)
            .filter(
                Order.user_id == current_user.id,
                Order.idempotency_key == key,
            )
            .first()
        )

        if existing_order:
            if existing_order.request_fingerprint != fingerprint:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        "Bu Idempotency-Key boshqa buyurtma bilan "
                        "allaqachon ishlatilgan."
                    ),
                )

            return serialize_order(existing_order)

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Buyurtma yaratishda konflikt yuz berdi.",
        )

    db.refresh(order)

    # --------------------------------------------------------
    # SEND TO 1XPANEL
    # --------------------------------------------------------

    try:
        provider_result = onepanel.create_order(
            service_id=str(payload.service_id),
            link=link,
            quantity=payload.quantity,
        )

    except OnePanelError as exc:
        db.refresh(order)

        if exc.unavailable:
            # IMPORTANT:
            # The provider may have accepted the order even though
            # the response timed out. Do NOT automatically refund.
            order.status = "provider_pending"
            order.failure_reason = (
                "Provider javobi noaniq. Buyurtma holati tekshirilmoqda."
            )
            order.updated_at = utc_now()
            db.commit()

            return serialize_order(order)

        # A definite provider rejection can be refunded.
        refund_order(
            db=db,
            order=order,
            reason="Provider buyurtmani qabul qilmadi.",
        )

        db.refresh(order)
        return serialize_order(order)

    # --------------------------------------------------------
    # PROVIDER SUCCESS
    # --------------------------------------------------------

    external_order_id = provider_result["external_order_id"]

    provider_charge = provider_result.get("charge")

    if provider_charge is not None:
        try:
            provider_charge = Decimal(str(provider_charge))
        except Exception:
            provider_charge = None

    order.external_order_id = external_order_id
    order.provider_charge = provider_charge
    order.provider_checked_at = utc_now()
    order.status = "processing"
    order.failure_reason = None
    order.updated_at = utc_now()

    db.commit()
    db.refresh(order)

    return serialize_order(order)


# ============================================================
# LIST MY ORDERS
# ============================================================


@router.get("", response_model=list[OrderRead])
def list_orders(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    orders = (
        db.query(Order)
        .filter(Order.user_id == current_user.id)
        .order_by(Order.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    return [serialize_order(order) for order in orders]


# ============================================================
# GET SINGLE ORDER
# ============================================================


@router.get("/{order_id}", response_model=OrderRead)
def get_order(
    order_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    order = (
        db.query(Order)
        .filter(
            Order.id == order_id,
            Order.user_id == current_user.id,
        )
        .first()
    )

    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Buyurtma topilmadi.",
        )

    return serialize_order(order)

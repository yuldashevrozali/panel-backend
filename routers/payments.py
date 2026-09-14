import os
from decimal import Decimal, ROUND_HALF_UP

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from database.connection import get_db
from database.models import PaymentRequest, User
from routers.security import get_current_user
from schemas.payments import PaymentRequestCreate, PaymentRequestRead

router = APIRouter(prefix="/payments", tags=["Payments"])

MIN_DEPOSIT_AMOUNT = Decimal(os.getenv("MIN_DEPOSIT_AMOUNT", "1.00"))
MONEY_QUANT = Decimal("0.01")


@router.post(
    "/requests", response_model=PaymentRequestRead, status_code=status.HTTP_201_CREATED
)
def create_payment_request(
    payload: PaymentRequestCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    method = payload.method.strip().lower()
    if method != "admin":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="To'lov usuli hozircha mavjud emas. Faqat Admin orqali qo'lda to'lov mavjud.",
        )

    currency = payload.currency.strip().upper()
    if currency != "USD":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Valyuta faqat USD bo'lishi kerak.",
        )

    try:
        raw_amount = Decimal(str(payload.amount))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Summa noto'g'ri kiritilgan.",
        ) from exc

    if not raw_amount.is_finite() or raw_amount <= Decimal("0"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Summa musbat bo'lishi kerak.",
        )

    amount = raw_amount.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)

    if amount < MIN_DEPOSIT_AMOUNT:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Minimal to'lov summasi ${MIN_DEPOSIT_AMOUNT:.2f}.",
        )

    req = PaymentRequest(
        user_id=current_user.id,
        amount=amount,
        currency="USD",
        method="admin",
        status="pending",
    )

    db.add(req)
    db.commit()
    db.refresh(req)

    return req


@router.get("/requests", response_model=list[PaymentRequestRead])
def list_my_payment_requests(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    requests = (
        db.query(PaymentRequest)
        .filter(PaymentRequest.user_id == current_user.id)
        .order_by(PaymentRequest.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    return requests

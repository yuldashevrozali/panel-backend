from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import String, func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database.connection import get_db
from database.models import Order, PaymentRequest, User, WalletTransaction
from routers.security import (
    PRIMARY_ADMIN_EMAIL,
    is_primary_super_admin,
    require_admin,
    require_super_admin,
)
from schemas.admin import (
    AdminCreate,
    AdminOrderList,
    AdminOrderRead,
    AdminPaymentList,
    AdminPaymentRead,
    AdminRejectPayload,
    AdminStatsRead,
    AdminUserList,
    AdminUserRead,
)
from services.onepanel import OnePanelError, onepanel

router = APIRouter(prefix="/admin", tags=["Admin"])


@router.get("/stats", response_model=AdminStatsRead)
def get_admin_stats(
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    total_users = db.query(func.count(User.id)).scalar() or 0
    total_orders = db.query(func.count(Order.id)).scalar() or 0

    pending_orders = (
        db.query(func.count(Order.id))
        .filter(Order.status.in_(["pending", "processing", "in progress"]))
        .scalar()
        or 0
    )

    completed_orders = (
        db.query(func.count(Order.id))
        .filter(Order.status.in_(["completed", "complete"]))
        .scalar()
        or 0
    )

    failed_orders = (
        db.query(func.count(Order.id))
        .filter(Order.status.in_(["failed", "canceled", "cancelled"]))
        .scalar()
        or 0
    )

    total_revenue = db.query(func.sum(Order.charge)).filter(
        Order.status != "failed"
    ).scalar() or Decimal("0.00")

    total_user_balance = db.query(func.sum(User.balance)).scalar() or Decimal("0.00")

    return {
        "total_users": total_users,
        "total_orders": total_orders,
        "pending_orders": pending_orders,
        "completed_orders": completed_orders,
        "failed_orders": failed_orders,
        "total_revenue": total_revenue,
        "total_user_balance": total_user_balance,
    }


@router.get("/users", response_model=AdminUserList)
def list_admin_users(
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
    search: str | None = Query(default=None, max_length=200),
    role: str | None = Query(default=None, max_length=20),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    query = db.query(User)

    if role:
        query = query.filter(User.role == role)

    if search:
        term = f"%{search.strip().lower()}%"
        query = query.filter(
            or_(
                func.lower(User.email).like(term),
                func.lower(User.username).like(term),
                func.lower(User.first_name).like(term),
                func.cast(User.id, String).like(term),
                func.cast(User.telegram_id, String).like(term),
            )
        )

    total = query.count()
    users = query.order_by(User.created_at.desc()).offset(offset).limit(limit).all()

    # Dynamic check for primary super admin role
    serialized = []
    for user in users:
        u_role = user.role
        if is_primary_super_admin(user):
            u_role = "super_admin"
        serialized.append(
            AdminUserRead(
                id=user.id,
                telegram_id=user.telegram_id,
                google_sub=user.google_sub,
                email=user.email,
                username=user.username,
                first_name=user.first_name,
                role=u_role,
                balance=user.balance,
                created_at=user.created_at,
            )
        )

    return AdminUserList(items=serialized, total=total, limit=limit, offset=offset)


@router.get("/users/{user_id}", response_model=AdminUserRead)
def get_admin_user(
    user_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    u_role = user.role
    if is_primary_super_admin(user):
        u_role = "super_admin"

    return AdminUserRead(
        id=user.id,
        telegram_id=user.telegram_id,
        google_sub=user.google_sub,
        email=user.email,
        username=user.username,
        first_name=user.first_name,
        role=u_role,
        balance=user.balance,
        created_at=user.created_at,
    )


@router.get("/orders", response_model=AdminOrderList)
def list_admin_orders(
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
    status: str | None = Query(default=None, max_length=50),
    search: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    query = db.query(Order)

    if status:
        query = query.filter(func.lower(Order.status) == status.strip().lower())

    if search:
        term = f"%{search.strip().lower()}%"
        query = query.filter(
            or_(
                func.lower(Order.link).like(term),
                func.lower(Order.service_id).like(term),
                func.lower(Order.external_order_id).like(term),
                func.cast(Order.id, String).like(term),
                func.cast(Order.user_id, String).like(term),
            )
        )

    total = query.count()
    orders = query.order_by(Order.created_at.desc()).offset(offset).limit(limit).all()

    items = [
        AdminOrderRead(
            id=o.id,
            user_id=o.user_id,
            external_order_id=o.external_order_id,
            service_id=o.service_id,
            link=o.link,
            quantity=o.quantity,
            charge=o.charge,
            status=o.status,
            provider_charge=o.provider_charge,
            failure_reason=o.failure_reason,
            created_at=o.created_at,
            updated_at=o.updated_at,
        )
        for o in orders
    ]

    return AdminOrderList(items=items, total=total, limit=limit, offset=offset)


@router.get("/orders/{order_id}", response_model=AdminOrderRead)
def get_admin_order(
    order_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    o = db.query(Order).filter(Order.id == order_id).first()
    if not o:
        raise HTTPException(status_code=404, detail="Order not found")

    return AdminOrderRead(
        id=o.id,
        user_id=o.user_id,
        external_order_id=o.external_order_id,
        service_id=o.service_id,
        link=o.link,
        quantity=o.quantity,
        charge=o.charge,
        status=o.status,
        provider_charge=o.provider_charge,
        failure_reason=o.failure_reason,
        created_at=o.created_at,
        updated_at=o.updated_at,
    )


@router.get("/services")
def list_admin_services(
    admin: User = Depends(require_admin),
):
    try:
        return onepanel.get_services()
    except OnePanelError as exc:
        raise HTTPException(
            status_code=503 if exc.unavailable else 502, detail=str(exc)
        ) from exc


@router.get("/admins", response_model=list[AdminUserRead])
def list_admins(
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    users = (
        db.query(User)
        .filter(
            or_(
                User.role.in_(["admin", "super_admin"]),
                func.lower(User.email) == PRIMARY_ADMIN_EMAIL,
            )
        )
        .order_by(User.id.asc())
        .all()
    )

    serialized = []
    for user in users:
        u_role = user.role
        if is_primary_super_admin(user):
            u_role = "super_admin"
        serialized.append(
            AdminUserRead(
                id=user.id,
                telegram_id=user.telegram_id,
                google_sub=user.google_sub,
                email=user.email,
                username=user.username,
                first_name=user.first_name,
                role=u_role,
                balance=user.balance,
                created_at=user.created_at,
            )
        )

    return serialized


@router.post(
    "/admins", response_model=AdminUserRead, status_code=status.HTTP_201_CREATED
)
def add_admin(
    payload: AdminCreate,
    db: Session = Depends(get_db),
    super_admin: User = Depends(require_super_admin),
):
    normalized_email = payload.email.strip().lower()

    if normalized_email == PRIMARY_ADMIN_EMAIL:
        raise HTTPException(
            status_code=400, detail="This email is the primary Super Admin."
        )

    existing = db.query(User).filter(func.lower(User.email) == normalized_email).first()

    if existing:
        if existing.role == "super_admin":
            raise HTTPException(
                status_code=400, detail="Cannot alter another Super Admin role."
            )
        existing.role = "admin"
        db.commit()
        db.refresh(existing)
        return AdminUserRead(
            id=existing.id,
            telegram_id=existing.telegram_id,
            google_sub=existing.google_sub,
            email=existing.email,
            username=existing.username,
            first_name=existing.first_name,
            role=existing.role,
            balance=existing.balance,
            created_at=existing.created_at,
        )

    new_admin = User(
        email=normalized_email,
        first_name="Admin",
        role="admin",
        balance=Decimal("0.00"),
    )
    db.add(new_admin)
    db.commit()
    db.refresh(new_admin)

    return AdminUserRead(
        id=new_admin.id,
        telegram_id=new_admin.telegram_id,
        google_sub=new_admin.google_sub,
        email=new_admin.email,
        username=new_admin.username,
        first_name=new_admin.first_name,
        role=new_admin.role,
        balance=new_admin.balance,
        created_at=new_admin.created_at,
    )


@router.delete("/admins/{user_id}")
def remove_admin(
    user_id: int,
    db: Session = Depends(get_db),
    super_admin: User = Depends(require_super_admin),
):
    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    if is_primary_super_admin(target):
        raise HTTPException(
            status_code=400, detail="Cannot remove or demote the primary Super Admin."
        )

    if target.id == super_admin.id:
        raise HTTPException(status_code=400, detail="Cannot demote yourself.")

    if target.role == "super_admin":
        raise HTTPException(status_code=400, detail="Cannot demote a Super Admin.")

    target.role = "user"
    db.commit()

    return {"message": "Admin demoted to user successfully."}


def serialize_admin_payment(p: PaymentRequest, db: Session) -> AdminPaymentRead:
    user = db.query(User).filter(User.id == p.user_id).first()
    reviewer = (
        db.query(User).filter(User.id == p.reviewed_by).first()
        if p.reviewed_by
        else None
    )
    return AdminPaymentRead(
        id=p.id,
        user_id=p.user_id,
        user_email=user.email if user else None,
        user_name=user.first_name if user else f"User #{p.user_id}",
        amount=p.amount,
        currency=p.currency,
        method=p.method,
        status=p.status,
        rejection_reason=p.rejection_reason,
        reviewed_by=p.reviewed_by,
        reviewer_email=(
            reviewer.email
            if (reviewer and reviewer.email)
            else (reviewer.first_name if reviewer else None)
        ),
        reviewed_at=p.reviewed_at,
        created_at=p.created_at,
        updated_at=p.updated_at,
    )


@router.get("/payments", response_model=AdminPaymentList)
def list_admin_payments(
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
    status: str | None = Query(default=None, max_length=20),
    search: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    query = db.query(PaymentRequest)

    if status:
        query = query.filter(
            func.lower(PaymentRequest.status) == status.strip().lower()
        )

    if search:
        term = f"%{search.strip().lower()}%"
        query = query.join(User, PaymentRequest.user_id == User.id).filter(
            or_(
                func.lower(User.email).like(term),
                func.lower(User.first_name).like(term),
                func.cast(PaymentRequest.id, String).like(term),
                func.cast(PaymentRequest.user_id, String).like(term),
            )
        )

    total = query.count()
    pending_count = (
        db.query(func.count(PaymentRequest.id))
        .filter(PaymentRequest.status == "pending")
        .scalar()
        or 0
    )

    requests = (
        query.order_by(PaymentRequest.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    items = [serialize_admin_payment(p, db) for p in requests]

    return AdminPaymentList(
        items=items,
        total=total,
        pending_count=pending_count,
        limit=limit,
        offset=offset,
    )


@router.get("/payments/{payment_id}", response_model=AdminPaymentRead)
def get_admin_payment(
    payment_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    p = db.query(PaymentRequest).filter(PaymentRequest.id == payment_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Payment request not found")

    return serialize_admin_payment(p, db)


@router.post("/payments/{payment_id}/approve", response_model=AdminPaymentRead)
def approve_admin_payment(
    payment_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    # Lock PaymentRequest row
    p = (
        db.query(PaymentRequest)
        .filter(PaymentRequest.id == payment_id)
        .with_for_update()
        .first()
    )

    if not p:
        raise HTTPException(status_code=404, detail="Payment request not found")

    if p.status != "pending":
        raise HTTPException(
            status_code=400,
            detail="Payment request has already been processed.",
        )

    # Lock User row
    user = db.query(User).filter(User.id == p.user_id).with_for_update().first()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    now = datetime.now(timezone.utc)
    balance_before = user.balance or Decimal("0.00")
    balance_after = balance_before + Decimal(p.amount)

    user.balance = balance_after

    # Create WalletTransaction with payment_request_id (unique constraint protects double approval!)
    transaction = WalletTransaction(
        user_id=user.id,
        payment_request_id=p.id,
        amount=Decimal(p.amount),
        transaction_type="deposit",
        balance_before=balance_before,
        balance_after=balance_after,
        metadata_json={
            "payment_request_id": p.id,
            "method": p.method,
            "approved_by": admin.id,
        },
        created_at=now,
    )

    db.add(transaction)

    p.status = "approved"
    p.reviewed_by = admin.id
    p.reviewed_at = now
    p.updated_at = now

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=400,
            detail="Payment request has already been processed.",
        ) from exc

    db.refresh(p)
    return serialize_admin_payment(p, db)


@router.post("/payments/{payment_id}/reject", response_model=AdminPaymentRead)
def reject_admin_payment(
    payment_id: int,
    payload: AdminRejectPayload = AdminRejectPayload(),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    p = (
        db.query(PaymentRequest)
        .filter(PaymentRequest.id == payment_id)
        .with_for_update()
        .first()
    )

    if not p:
        raise HTTPException(status_code=404, detail="Payment request not found")

    if p.status != "pending":
        raise HTTPException(
            status_code=400,
            detail="Payment request has already been processed.",
        )

    now = datetime.now(timezone.utc)
    p.status = "rejected"
    p.rejection_reason = (
        payload.rejection_reason.strip()
        if payload.rejection_reason
        else "Payment could not be verified."
    )
    p.reviewed_by = admin.id
    p.reviewed_at = now
    p.updated_at = now

    db.commit()
    db.refresh(p)

    return serialize_admin_payment(p, db)

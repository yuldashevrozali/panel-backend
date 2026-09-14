import os
import time
import unittest.mock as mock

from decimal import Decimal
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ["TELEGRAM_BOT_TOKEN"] = "123456789:TestBotTokenSecretKeyHere"
os.environ["GOOGLE_CLIENT_ID"] = "test-google-client-id.apps.googleusercontent.com"
os.environ["JWT_SECRET"] = "a_very_secret_jwt_key_that_is_at_least_32_bytes_long!"
os.environ["PRIMARY_ADMIN_EMAIL"] = "yuldashevrozalibek1@gmail.com"

from main import app
from database.connection import get_db
from database.models import Base, User, PaymentRequest, WalletTransaction
from routers.security import create_access_token

engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base.metadata.create_all(bind=engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db
client = TestClient(app)


def test_payment_suite():
    db = TestingSessionLocal()

    # Create users
    normal_user = User(
        telegram_id=10101,
        username="user1",
        first_name="User1",
        role="user",
        balance=Decimal("25.00"),
    )
    normal_user2 = User(
        telegram_id=20202,
        username="user2",
        first_name="User2",
        role="user",
        balance=Decimal("0.00"),
    )
    admin_user = User(
        telegram_id=30303,
        username="admin1",
        first_name="Admin1",
        role="admin",
        balance=Decimal("0.00"),
    )
    super_admin_user = User(
        google_sub="88888888",
        email="yuldashevrozalibek1@gmail.com",
        first_name="SuperAdmin",
        role="super_admin",
        balance=Decimal("0.00"),
    )

    db.add_all([normal_user, normal_user2, admin_user, super_admin_user])
    db.commit()

    token_user1 = create_access_token(normal_user.id)
    token_admin = create_access_token(admin_user.id)
    token_super = create_access_token(super_admin_user.id)

    headers_user1 = {"Authorization": f"Bearer {token_user1}"}
    headers_admin = {"Authorization": f"Bearer {token_admin}"}
    headers_super = {"Authorization": f"Bearer {token_super}"}

    print("--- Test 1: Unauthenticated request fails ---")
    res = client.post(
        "/payments/requests",
        json={"amount": 10.0, "method": "admin", "currency": "USD"},
    )
    assert res.status_code == 401

    print("--- Test 2 & 3 & 4 & 5: Crypto, UZS Card, Visa requests rejected ---")
    for invalid_method in ["crypto", "uzs_card", "visa"]:
        r_inv = client.post(
            "/payments/requests",
            json={"amount": 10.0, "method": invalid_method, "currency": "USD"},
            headers=headers_user1,
        )
        assert r_inv.status_code == 400
        assert (
            "to'lov usuli" in r_inv.json()["detail"].lower()
            or "method" in r_inv.json()["detail"].lower()
        )

    print("--- Test 6 & 7: User1 creates manual payment request ($15.00) ---")
    r_create = client.post(
        "/payments/requests",
        json={"amount": 15.0, "method": "admin", "currency": "USD"},
        headers=headers_user1,
    )
    assert r_create.status_code == 201
    p1 = r_create.json()
    assert p1["status"] == "pending"
    assert Decimal(str(p1["amount"])) == Decimal("15.00")
    p1_id = p1["id"]

    # Verify balance did NOT change on creation!
    db.refresh(normal_user)
    assert normal_user.balance == Decimal("25.00"), (
        "Balance must NOT change on payment request creation"
    )

    print(
        "--- Test 8 & 9: Normal user blocked from admin endpoints, Admin can view ---"
    )
    r_no_admin = client.get("/admin/payments", headers=headers_user1)
    assert r_no_admin.status_code == 403

    r_admin_view = client.get("/admin/payments", headers=headers_admin)
    assert r_admin_view.status_code == 200
    p_data = r_admin_view.json()
    assert p_data["pending_count"] >= 1
    assert any(req["id"] == p1_id for req in p_data["items"])

    print("--- Test 10 & 11 & 12: Admin approves payment request ---")
    r_app = client.post(f"/admin/payments/{p1_id}/approve", headers=headers_admin)
    if r_app.status_code != 200:
        print("Approve error:", r_app.status_code, r_app.text)
    assert r_app.status_code == 200, f"Approve failed: {r_app.status_code} {r_app.text}"
    assert r_app.json()["status"] == "approved"

    db.refresh(normal_user)
    assert normal_user.balance == Decimal("40.00"), (
        f"Expected 40.00, got {normal_user.balance}"
    )

    w_tx = (
        db.query(WalletTransaction)
        .filter(WalletTransaction.payment_request_id == p1_id)
        .all()
    )
    assert len(w_tx) == 1, "Exactly ONE WalletTransaction must be created"
    assert w_tx[0].amount == Decimal("15.00")
    assert w_tx[0].transaction_type == "deposit"

    print("--- Test 13: Double approval fails gracefully without double-crediting ---")
    r_app_again = client.post(f"/admin/payments/{p1_id}/approve", headers=headers_admin)
    assert r_app_again.status_code == 400
    assert "already" in r_app_again.json()["detail"].lower()

    db.refresh(normal_user)
    assert normal_user.balance == Decimal("40.00"), "Balance must NOT double-credit"

    print(
        "--- Test 14 & 15: User creates second request ($50.00), Admin rejects it ---"
    )
    r_create2 = client.post(
        "/payments/requests",
        json={"amount": 50.0, "method": "admin", "currency": "USD"},
        headers=headers_user1,
    )
    assert r_create2.status_code == 201
    p2_id = r_create2.json()["id"]

    r_rej = client.post(
        f"/admin/payments/{p2_id}/reject",
        json={"rejection_reason": "Payment could not be verified."},
        headers=headers_admin,
    )
    assert r_rej.status_code == 200
    assert r_rej.json()["status"] == "rejected"
    assert r_rej.json()["rejection_reason"] == "Payment could not be verified."

    db.refresh(normal_user)
    assert normal_user.balance == Decimal("40.00"), "Rejection must NOT alter balance"

    print("--- Test 16: Rejected payment cannot later be approved ---")
    r_app_rej = client.post(f"/admin/payments/{p2_id}/approve", headers=headers_admin)
    assert r_app_rej.status_code == 400

    print("--- Test 17: Approved payment cannot later be rejected ---")
    r_rej_app = client.post(f"/admin/payments/{p1_id}/reject", headers=headers_admin)
    assert r_rej_app.status_code == 400

    print(
        "--- Test 18 & 19: Super admin can access payment management & sees same list ---"
    )
    r_super_view = client.get("/admin/payments", headers=headers_super)
    assert r_super_view.status_code == 200
    assert r_super_view.json()["total"] >= 2

    print("--- Test 20: User lists their payment history ---")
    r_my_reqs = client.get("/payments/requests", headers=headers_user1)
    assert r_my_reqs.status_code == 200
    my_list = r_my_reqs.json()
    assert len(my_list) == 2

    db.close()
    print("ALL 20 PAYMENT SUITE TESTS PASSED PERFECTLY!")


if __name__ == "__main__":
    test_payment_suite()

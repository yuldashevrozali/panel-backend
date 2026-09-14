import os
import time
import hmac
import hashlib
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
from database.models import Base, User, Order, TelegramLoginReplay
from routers.security import create_access_token
from schemas.auth import TelegramAuthData, GoogleAuthData

# In-memory database for testing
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


def test_admin_suite():
    db = TestingSessionLocal()

    # 1. Create test users
    normal_user = User(
        telegram_id=111111,
        username="normal_user",
        first_name="Normal",
        role="user",
        balance=Decimal("50.00"),
    )
    admin_user = User(
        telegram_id=222222,
        username="admin_user",
        first_name="AdminUser",
        role="admin",
        balance=Decimal("100.00"),
    )
    primary_super_admin = User(
        google_sub="999999999",
        email="yuldashevrozalibek1@gmail.com",
        first_name="Rozalibek",
        role="super_admin",
        balance=Decimal("500.00"),
    )
    db.add_all([normal_user, admin_user, primary_super_admin])
    db.commit()
    db.refresh(normal_user)
    db.refresh(admin_user)
    db.refresh(primary_super_admin)

    normal_token = create_access_token(normal_user.id)
    admin_token = create_access_token(admin_user.id)
    super_admin_token = create_access_token(primary_super_admin.id)

    normal_headers = {"Authorization": f"Bearer {normal_token}"}
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    super_admin_headers = {"Authorization": f"Bearer {super_admin_token}"}

    print("--- 1. Testing Normal User Access Restrictions ---")
    r1 = client.get("/admin/stats", headers=normal_headers)
    assert r1.status_code == 403, f"Expected 403 for normal user, got {r1.status_code}"
    r2 = client.get("/admin/users", headers=normal_headers)
    assert r2.status_code == 403, f"Expected 403 for normal user, got {r2.status_code}"
    print("Normal user correctly blocked with 403.")

    print("--- 2. Testing Admin Access to Stats & Lists ---")
    r3 = client.get("/admin/stats", headers=admin_headers)
    assert r3.status_code == 200, f"Expected 200 for admin, got {r3.status_code}"
    data3 = r3.json()
    assert "total_users" in data3
    assert data3["total_users"] == 3

    r4 = client.get("/admin/users", headers=admin_headers)
    assert r4.status_code == 200, (
        f"Expected 200 for admin user list, got {r4.status_code}"
    )
    print("Admin user successfully accessed /admin/stats and /admin/users.")

    print("--- 3. Testing Admin Management Restrictions for Normal Admin ---")
    r5 = client.post(
        "/admin/admins", json={"email": "someone@gmail.com"}, headers=admin_headers
    )
    assert r5.status_code == 403, (
        f"Expected 403 when normal admin tries to add admin, got {r5.status_code}"
    )
    print("Normal admin correctly blocked from adding admins with 403.")

    print("--- 4. Testing Super Admin Management ---")
    r6 = client.post(
        "/admin/admins",
        json={"email": "newadmin@gmail.com"},
        headers=super_admin_headers,
    )
    assert r6.status_code == 201, (
        f"Expected 201 when super admin adds admin, got {r6.status_code}"
    )
    data6 = r6.json()
    assert data6["email"] == "newadmin@gmail.com"
    assert data6["role"] == "admin"
    new_admin_id = data6["id"]
    print("Super admin successfully granted admin role to newadmin@gmail.com.")

    print("--- 5. Testing Primary Super Admin Protection ---")
    r7 = client.delete(
        f"/admin/admins/{primary_super_admin.id}", headers=super_admin_headers
    )
    assert r7.status_code == 400, (
        f"Expected 400 when trying to remove primary super admin, got {r7.status_code}"
    )
    print("Primary super admin successfully protected against removal/demotion.")

    print("--- 6. Testing Super Admin Demoting Admin ---")
    r8 = client.delete(f"/admin/admins/{new_admin_id}", headers=super_admin_headers)
    assert r8.status_code == 200, (
        f"Expected 200 when demoting admin, got {r8.status_code}"
    )
    print("Super admin successfully demoted secondary admin.")

    print("--- 7. Testing Telegram Authentication & Role Preservation ---")
    auth_date = int(time.time())
    tg_data = {
        "auth_date": auth_date,
        "first_name": "TgTest",
        "id": 777888,
        "username": "tg_test",
    }
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(tg_data.items()))
    secret_key = hashlib.sha256("123456789:TestBotTokenSecretKeyHere".encode()).digest()
    hash_val = hmac.new(
        secret_key, data_check_string.encode(), hashlib.sha256
    ).hexdigest()

    tg_payload = {
        "id": 777888,
        "first_name": "TgTest",
        "username": "tg_test",
        "auth_date": auth_date,
        "hash": hash_val,
    }
    try:
        r9 = client.post("/auth/telegram", json=tg_payload)
        print(
            "Telegram Auth status:",
            r9.status_code,
            r9.json() if r9.status_code == 200 else r9.text,
        )
        assert r9.status_code == 200
        assert r9.json()["user"]["role"] == "user"
        print("Telegram authentication works and returns default role 'user'.")
    except Exception as exc:
        print("Caught exception during Telegram test:", exc)
        raise exc
    assert r9.json()["user"]["role"] == "user"
    print("Telegram authentication works and returns default role 'user'.")

    print("--- 8. Testing Primary Super Admin Login via Google ---")
    mock_id_info = {
        "sub": "999999999",
        "email": "yuldashevrozalibek1@gmail.com",
        "given_name": "Rozalibek",
        "iss": "https://accounts.google.com",
    }
    with mock.patch("routers.auth.verify_google_auth", return_value=mock_id_info):
        r10 = client.post("/auth/google", json={"credential": "fake.jwt.token"})
        assert r10.status_code == 200, (
            f"Expected 200 for Google auth, got {r10.status_code}"
        )
        assert r10.json()["user"]["role"] == "super_admin"
        print(
            "Google login for primary email automatically returns role 'super_admin'."
        )

    db.close()
    print("ALL ADMIN SUITE TESTS PASSED PERFECTLY!")


if __name__ == "__main__":
    test_admin_suite()

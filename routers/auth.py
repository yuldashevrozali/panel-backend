import os
import time
import hmac
import hashlib

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from dotenv import load_dotenv

from database.connection import get_db
from datetime import datetime, timedelta, timezone
from sqlalchemy.exc import IntegrityError

from google.auth.transport import requests as google_requests
from google.oauth2 import id_token

from database.models import TelegramLoginReplay, User
from schemas.auth import GoogleAuthData, TelegramAuthData
from routers.security import create_access_token


load_dotenv()

router = APIRouter(prefix="/auth", tags=["Authentication"])


PRIMARY_ADMIN_EMAIL = (
    os.getenv("PRIMARY_ADMIN_EMAIL", "yuldashevrozalibek1@gmail.com").strip().lower()
)
AUTH_MAX_AGE_SECONDS = int(os.getenv("TELEGRAM_AUTH_MAX_AGE_SECONDS", "300"))
AUTH_FUTURE_SKEW_SECONDS = 60


def serialize_user(user: User) -> dict:
    role = user.role or "user"
    if user.email and user.email.strip().lower() == PRIMARY_ADMIN_EMAIL:
        role = "super_admin"
    return {
        "id": user.id,
        "telegram_id": user.telegram_id,
        "google_sub": user.google_sub,
        "email": user.email,
        "username": user.username,
        "first_name": user.first_name,
        "balance": user.balance,
        "role": role,
    }


def verify_telegram_auth(data: TelegramAuthData) -> str:
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not bot_token:
        raise HTTPException(
            status_code=500, detail="Telegram bot token is not configured"
        )

    # Telegram login ma'lumotlarini dictionaryga o'tkazamiz
    # Telegram signs every field it sends, including optional profile fields.
    # Excluding absent optional fields is essential for an exact hash match.
    data_dict = data.model_dump(exclude_none=True)

    received_hash = data_dict.pop("hash", None)
    if not received_hash:
        raise HTTPException(
            status_code=400, detail="Missing Telegram authentication hash"
        )

    # key=value format
    data_check_string = "\n".join(
        f"{key}={value}" for key, value in sorted(data_dict.items())
    )

    # Telegram bot tokenidan secret key
    secret_key = hashlib.sha256(bot_token.encode()).digest()

    # HMAC-SHA256
    calculated_hash = hmac.new(
        secret_key, data_check_string.encode(), hashlib.sha256
    ).hexdigest()

    # Hashlarni solishtirish (case-insensitive check)
    if not hmac.compare_digest(calculated_hash.lower(), received_hash.lower()):
        raise HTTPException(
            status_code=401, detail="Invalid Telegram authentication signature"
        )

    # Eski authentication ma'lumotlarini qabul qilmaslik
    current_time = int(time.time())

    if current_time - data.auth_date > AUTH_MAX_AGE_SECONDS:
        raise HTTPException(
            status_code=401, detail="Telegram authentication data expired"
        )
    if data.auth_date - current_time > AUTH_FUTURE_SKEW_SECONDS:
        raise HTTPException(
            status_code=401, detail="Telegram authentication timestamp is invalid"
        )
    return hashlib.sha256(data_check_string.encode()).hexdigest()


@router.post("/telegram")
def telegram_login(data: TelegramAuthData, db: Session = Depends(get_db)):
    # Telegram ma'lumotlarini tekshirish
    payload_hash = verify_telegram_auth(data)
    replay = TelegramLoginReplay(
        payload_hash=payload_hash,
        telegram_id=data.id,
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=AUTH_MAX_AGE_SECONDS),
    )
    db.add(replay)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=401, detail="Telegram authentication payload was already used"
        ) from exc

    # Userni database'dan qidirish
    user = db.query(User).filter(User.telegram_id == data.id).first()

    # User mavjud bo'lsa
    if user:
        user.username = data.username
        user.first_name = data.first_name

        db.commit()
        db.refresh(user)

        token = create_access_token(user.id)

        return {
            "message": "Login successful",
            "access_token": token,
            "token_type": "bearer",
            "user": serialize_user(user),
        }

    # User mavjud bo'lmasa yangi account
    user = User(
        telegram_id=data.id,
        username=data.username,
        first_name=data.first_name,
        balance=0,
    )

    db.add(user)
    db.commit()
    db.refresh(user)

    token = create_access_token(user.id)

    return {
        "message": "Account created and login successful",
        "access_token": token,
        "token_type": "bearer",
        "user": serialize_user(user),
    }


def verify_google_auth(credential: str) -> dict:
    google_client_id = os.getenv("GOOGLE_CLIENT_ID")
    if not google_client_id:
        raise HTTPException(
            status_code=500, detail="Google client ID is not configured"
        )

    try:
        id_info = id_token.verify_oauth2_token(
            credential, google_requests.Request(), google_client_id
        )
    except Exception as exc:
        raise HTTPException(
            status_code=401, detail="Invalid Google authentication credential"
        ) from exc

    sub = id_info.get("sub")
    if not sub:
        raise HTTPException(status_code=401, detail="Google token missing sub claim")

    iss = id_info.get("iss")
    if iss not in ["accounts.google.com", "https://accounts.google.com"]:
        raise HTTPException(status_code=401, detail="Invalid Google token issuer")

    return id_info


@router.post("/google")
def google_login(data: GoogleAuthData, db: Session = Depends(get_db)):
    id_info = verify_google_auth(data.credential)

    google_sub = str(id_info["sub"])
    email = id_info.get("email")
    first_name = id_info.get("given_name") or id_info.get("name") or "Google User"

    # Userni database'dan google_sub bo'yicha qidirish
    user = db.query(User).filter(User.google_sub == google_sub).first()

    if user:
        if email and user.email != email:
            user.email = email
        if first_name and not user.first_name:
            user.first_name = first_name

        if (
            user.email
            and user.email.strip().lower() == PRIMARY_ADMIN_EMAIL
            and user.role != "super_admin"
        ):
            user.role = "super_admin"

        db.commit()
        db.refresh(user)

        token = create_access_token(user.id)

        return {
            "message": "Login successful",
            "access_token": token,
            "token_type": "bearer",
            "user": serialize_user(user),
        }

    # Yangi Google user yaratish
    is_primary = bool(email and email.strip().lower() == PRIMARY_ADMIN_EMAIL)
    user = User(
        google_sub=google_sub,
        email=email,
        first_name=first_name,
        role="super_admin" if is_primary else "user",
        balance=0,
    )

    db.add(user)
    db.commit()
    db.refresh(user)

    token = create_access_token(user.id)

    return {
        "message": "Account created and login successful",
        "access_token": token,
        "token_type": "bearer",
        "user": serialize_user(user),
    }

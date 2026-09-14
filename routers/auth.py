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

from database.models import TelegramLoginReplay, User
from schemas.auth import TelegramAuthData
from routers.security import create_access_token


load_dotenv()

router = APIRouter(prefix="/auth", tags=["Authentication"])


AUTH_MAX_AGE_SECONDS = int(os.getenv("TELEGRAM_AUTH_MAX_AGE_SECONDS", "300"))
AUTH_FUTURE_SKEW_SECONDS = 60


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
            "user": {
                "id": user.id,
                "telegram_id": user.telegram_id,
                "username": user.username,
                "first_name": user.first_name,
                "balance": user.balance,
            },
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
        "user": {
            "id": user.id,
            "telegram_id": user.telegram_id,
            "username": user.username,
            "first_name": user.first_name,
            "balance": user.balance,
        },
    }

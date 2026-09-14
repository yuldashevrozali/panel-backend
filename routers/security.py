import os

from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from database.connection import get_db
from database.models import User


load_dotenv()

JWT_SECRET = os.getenv("JWT_SECRET")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
JWT_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", "60"))

if not JWT_SECRET or len(JWT_SECRET) < 32:
    raise RuntimeError("JWT_SECRET must be at least 32 characters")
if JWT_ALGORITHM != "HS256":
    raise RuntimeError("JWT_ALGORITHM must be HS256")
if not 5 <= JWT_EXPIRE_MINUTES <= 1440:
    raise RuntimeError("JWT_EXPIRE_MINUTES must be between 5 and 1440")

security = HTTPBearer(auto_error=False)

PRIMARY_ADMIN_EMAIL = (
    os.getenv("PRIMARY_ADMIN_EMAIL", "yuldashevrozalibek1@gmail.com").strip().lower()
)


def is_primary_super_admin(user: User) -> bool:
    if user.email and user.email.strip().lower() == PRIMARY_ADMIN_EMAIL:
        return True
    return False


def create_access_token(user_id: int):
    expire = datetime.now(timezone.utc) + timedelta(minutes=JWT_EXPIRE_MINUTES)

    payload = {"sub": str(user_id), "exp": expire}

    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    db: Session = Depends(get_db),
):
    if credentials is None:
        raise HTTPException(status_code=401, detail="Authentication required")

    token = credentials.credentials

    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])

        user_id = payload.get("sub")

        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid token")

    except (JWTError, ValueError, TypeError):
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    user = db.query(User).filter(User.id == int(user_id)).first()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if is_primary_super_admin(user) and user.role != "super_admin":
        user.role = "super_admin"
        db.commit()
        db.refresh(user)

    return user


def require_admin(
    current_user: User = Depends(get_current_user),
) -> User:
    if current_user.role in ("admin", "super_admin") or is_primary_super_admin(
        current_user
    ):
        return current_user
    raise HTTPException(status_code=403, detail="Admin access required")


def require_super_admin(
    current_user: User = Depends(get_current_user),
) -> User:
    if current_user.role == "super_admin" or is_primary_super_admin(current_user):
        return current_user
    raise HTTPException(status_code=403, detail="Super Admin access required")

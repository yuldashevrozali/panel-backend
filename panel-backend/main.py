import os

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.orm import Session

from database.connection import engine, get_db
from database.models import Base, User
from routers.auth import router as auth_router
from routers.security import get_current_user
from routers.services import router as services_router

try:
    from routers.orders import router as orders_router
except ModuleNotFoundError as exc:
    if exc.name != "routers.orders":
        raise
    orders_router = None


ENABLE_DEBUG_ENDPOINTS = os.getenv(
    "ENABLE_DEBUG_ENDPOINTS", "false"
).strip().lower() in {"1", "true", "yes", "on"}

CORS_ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "CORS_ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"
    ).split(",")
    if origin.strip()
]
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000").strip()
if FRONTEND_URL and FRONTEND_URL not in CORS_ALLOWED_ORIGINS:
    CORS_ALLOWED_ORIGINS.append(FRONTEND_URL)

app = FastAPI(title="SMM Panel Backend")
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
)
app.include_router(auth_router)
app.include_router(services_router)
if orders_router is not None:
    app.include_router(orders_router)

Base.metadata.create_all(bind=engine)


@app.get("/")
def home():
    return {"message": "SMM Panel Backend is running"}


@app.get("/health")
def health():
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return {"status": "ok"}
    except Exception as exc:
        return {"status": "error", "message": "Database unavailable"}


if ENABLE_DEBUG_ENDPOINTS:

    @app.get("/db-test")
    def db_test():
        try:
            with engine.connect():
                return {"database": "connected"}
        except Exception as e:
            return {"database": "error", "message": str(e)}

    @app.post("/users")
    def create_user(
        telegram_id: int,
        username: str | None = None,
        first_name: str | None = None,
        db: Session = Depends(get_db),
    ):
        user = User(
            telegram_id=telegram_id, username=username, first_name=first_name, balance=0
        )

        db.add(user)
        db.commit()
        db.refresh(user)

        return {
            "id": user.id,
            "telegram_id": user.telegram_id,
            "username": user.username,
            "first_name": user.first_name,
            "balance": user.balance,
        }

    @app.get("/users/{telegram_id}")
    def get_user(telegram_id: int, db: Session = Depends(get_db)):
        user = db.query(User).filter(User.telegram_id == telegram_id).first()

        if not user:
            return {"error": "User not found"}

        return {
            "id": user.id,
            "telegram_id": user.telegram_id,
            "username": user.username,
            "first_name": user.first_name,
            "balance": user.balance,
        }

    @app.get("/users/me")
    def get_me(current_user: User = Depends(get_current_user)):
        return {
            "id": current_user.id,
            "telegram_id": current_user.telegram_id,
            "username": current_user.username,
            "first_name": current_user.first_name,
            "balance": current_user.balance,
            "created_at": current_user.created_at,
        }

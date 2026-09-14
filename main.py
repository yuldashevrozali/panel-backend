import os

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.orm import Session

from database.connection import engine, get_db
from database.migrations import run_migrations
from database.models import Base, User
from routers.admin import router as admin_router
from routers.auth import router as auth_router
from routers.payments import router as payments_router
from routers.security import get_current_user
from routers.services import router as services_router

try:
    from routers.orders import router as orders_router
except ModuleNotFoundError as exc:
    if exc.name != "routers.orders":
        raise
    orders_router = None


# ============================================================
# ENVIRONMENT CONFIGURATION
# ============================================================

ENABLE_DEBUG_ENDPOINTS = os.getenv(
    "ENABLE_DEBUG_ENDPOINTS", "false"
).strip().lower() in {"1", "true", "yes", "on"}


# ============================================================
# CORS CONFIGURATION
# ============================================================

# Production Vercel frontend
DEFAULT_CORS_ORIGINS = [
    "https://smm-panel-fr.vercel.app",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]

CORS_ALLOWED_ORIGINS = [
    origin.strip().rstrip("/")
    for origin in os.getenv(
        "CORS_ALLOWED_ORIGINS",
        ",".join(DEFAULT_CORS_ORIGINS),
    ).split(",")
    if origin.strip()
]

FRONTEND_URL = (
    os.getenv(
        "FRONTEND_URL",
        "https://smm-panel-fr.vercel.app",
    )
    .strip()
    .rstrip("/")
)

if FRONTEND_URL and FRONTEND_URL not in CORS_ALLOWED_ORIGINS:
    CORS_ALLOWED_ORIGINS.append(FRONTEND_URL)


# ============================================================
# FASTAPI APPLICATION
# ============================================================

app = FastAPI(
    title="SMM Panel Backend",
)


# ============================================================
# CORS MIDDLEWARE
# ============================================================

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=[
        "GET",
        "POST",
        "PUT",
        "PATCH",
        "DELETE",
        "OPTIONS",
    ],
    allow_headers=[
        "Authorization",
        "Content-Type",
        "Accept",
        "Origin",
        "Idempotency-Key",
    ],
)


# ============================================================
# ROUTERS
# ============================================================

app.include_router(auth_router)
app.include_router(services_router)
app.include_router(admin_router)
app.include_router(payments_router)

if orders_router is not None:
    app.include_router(orders_router)


# ============================================================
# DATABASE
# ============================================================

try:
    run_migrations()
except Exception as exc:
    print(f"Migration warning: {exc}")

Base.metadata.create_all(bind=engine)


# ============================================================
# BASIC ENDPOINTS
# ============================================================


@app.get("/")
def home():
    return {"message": "SMM Panel Backend is running"}


@app.get("/health")
def health():
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))

        return {"status": "ok"}

    except Exception:
        return {
            "status": "error",
            "message": "Database unavailable",
        }


# ============================================================
# DEBUG ENDPOINTS
# ============================================================

if ENABLE_DEBUG_ENDPOINTS:

    @app.get("/db-test")
    def db_test():
        try:
            with engine.connect():
                return {"database": "connected"}

        except Exception as exc:
            return {
                "database": "error",
                "message": str(exc),
            }

    @app.post("/users")
    def create_user(
        telegram_id: int,
        username: str | None = None,
        first_name: str | None = None,
        db: Session = Depends(get_db),
    ):
        user = User(
            telegram_id=telegram_id,
            username=username,
            first_name=first_name,
            balance=0,
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
    def get_user(
        telegram_id: int,
        db: Session = Depends(get_db),
    ):
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
    def get_me(
        current_user: User = Depends(get_current_user),
    ):
        return {
            "id": current_user.id,
            "telegram_id": current_user.telegram_id,
            "google_sub": current_user.google_sub,
            "email": current_user.email,
            "username": current_user.username,
            "first_name": current_user.first_name,
            "role": current_user.role,
            "balance": current_user.balance,
            "created_at": current_user.created_at,
        }

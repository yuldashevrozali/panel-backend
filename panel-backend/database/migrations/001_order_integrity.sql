CREATE TABLE IF NOT EXISTS schema_migrations (
    version VARCHAR(100) PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE orders ALTER COLUMN external_order_id DROP NOT NULL;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS request_fingerprint VARCHAR(64);
ALTER TABLE orders ADD COLUMN IF NOT EXISTS provider_charge NUMERIC(14,4);
ALTER TABLE orders ADD COLUMN IF NOT EXISTS provider_checked_at TIMESTAMPTZ;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS failure_reason VARCHAR(200);
UPDATE orders SET idempotency_key = CONCAT('legacy-', id) WHERE idempotency_key IS NULL;
UPDATE orders SET request_fingerprint = LPAD(MD5(CONCAT('legacy-', id)), 64, '0') WHERE request_fingerprint IS NULL;
ALTER TABLE orders ALTER COLUMN idempotency_key SET NOT NULL;
ALTER TABLE orders ALTER COLUMN request_fingerprint SET NOT NULL;

CREATE TABLE IF NOT EXISTS wallet_transactions (
    id BIGSERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    order_id BIGINT UNIQUE REFERENCES orders(id) ON DELETE RESTRICT,
    amount NUMERIC(14,4) NOT NULL,
    transaction_type VARCHAR(30) NOT NULL,
    balance_before NUMERIC(12,2) NOT NULL,
    balance_after NUMERIC(12,2) NOT NULL,
    metadata_json JSON,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_wallet_transactions_user_created_at ON wallet_transactions(user_id, created_at);

CREATE TABLE IF NOT EXISTS telegram_login_replays (
    payload_hash VARCHAR(64) PRIMARY KEY,
    telegram_id BIGINT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_telegram_login_replays_telegram_id ON telegram_login_replays(telegram_id);
CREATE INDEX IF NOT EXISTS ix_telegram_login_replays_expires_at ON telegram_login_replays(expires_at);

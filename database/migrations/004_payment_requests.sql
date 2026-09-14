CREATE TABLE IF NOT EXISTS payment_requests (
    id BIGSERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    amount NUMERIC(12,2) NOT NULL,
    currency VARCHAR(10) NOT NULL DEFAULT 'USD',
    method VARCHAR(30) NOT NULL DEFAULT 'admin',
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    rejection_reason VARCHAR(255),
    reviewed_by INTEGER REFERENCES users(id) ON DELETE RESTRICT,
    reviewed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_payment_requests_user_created_at ON payment_requests(user_id, created_at);
CREATE INDEX IF NOT EXISTS ix_payment_requests_status ON payment_requests(status);

ALTER TABLE wallet_transactions ADD COLUMN IF NOT EXISTS payment_request_id BIGINT UNIQUE REFERENCES payment_requests(id) ON DELETE RESTRICT;

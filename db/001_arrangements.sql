-- Occlet demo arrangement registry; Lithic remains the financial system of record.
-- Monetary amounts are stored in integer cents. DO NOT store PAN/CVV/PIN here.
CREATE TABLE IF NOT EXISTS members (
    member_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS product_catalog (
    product_code TEXT PRIMARY KEY,
    product_type TEXT NOT NULL,
    configuration JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO members(member_id,display_name)
VALUES ('member_demo','Synthetic Occlet member') ON CONFLICT DO NOTHING;
INSERT INTO product_catalog(product_code,product_type,configuration)
VALUES ('DEBIT_SPENDING','DEPOSIT','{"purpose":"shared funding for parent and virtual cards"}'::jsonb)
ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS funding_arrangements (
    arrangement_id TEXT PRIMARY KEY,
    member_id TEXT NOT NULL REFERENCES members(member_id),
    product_code TEXT NOT NULL DEFAULT 'DEBIT_SPENDING' REFERENCES product_catalog(product_code),
    status TEXT NOT NULL CHECK (status IN ('ACTIVE', 'PAUSED')),
    funding_mode TEXT NOT NULL CHECK (funding_mode IN ('DEMO_MIRROR','LITHIC_LEDGER')),
    lithic_account_token UUID,
    lithic_issuing_financial_account_token UUID,
    parent_policy JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS agent_identities (
    agent_id TEXT PRIMARY KEY,
    member_id TEXT NOT NULL REFERENCES members(member_id),
    display_name TEXT NOT NULL,
    public_key_b64 TEXT NOT NULL,
    key_fingerprint TEXT NOT NULL,
    purpose TEXT,
    purchase_goal TEXT,
    registered_limits JSONB,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS payment_instruments (
    instrument_id TEXT PRIMARY KEY,
    arrangement_id TEXT NOT NULL REFERENCES funding_arrangements(arrangement_id),
    instrument_role TEXT NOT NULL CHECK (instrument_role IN ('PARENT','AGENT_VIRTUAL')),
    agent_id TEXT REFERENCES agent_identities(agent_id),
    instrument_status TEXT NOT NULL,
    lithic_account_token UUID,
    lithic_card_token UUID,
    card_last_four TEXT,
    instrument_alias TEXT,
    CHECK (instrument_role <> 'AGENT_VIRTUAL' OR agent_id IS NOT NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_instrument_lithic_token ON payment_instruments (lithic_card_token)
    WHERE lithic_card_token IS NOT NULL;
CREATE TABLE IF NOT EXISTS delegation_grants (
    grant_id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL REFERENCES agent_identities(agent_id),
    instrument_id TEXT NOT NULL UNIQUE REFERENCES payment_instruments(instrument_id),
    arrangement_id TEXT NOT NULL REFERENCES funding_arrangements(arrangement_id),
    status TEXT NOT NULL,
    authorization_flow TEXT NOT NULL CHECK (authorization_flow IN ('SIGNED_INTENT_REQUIRED','CARD_BOUND_NETWORK')),
    purpose TEXT,
    merchant_allowlist JSONB NOT NULL DEFAULT '[]'::jsonb,
    policy JSONB NOT NULL,
    policy_version INTEGER NOT NULL DEFAULT 1,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS delegation_usage (
    grant_id TEXT PRIMARY KEY REFERENCES delegation_grants(grant_id),
    authorized_utilization_cents BIGINT NOT NULL DEFAULT 0 CHECK (authorized_utilization_cents >= 0),
    -- This is agent POLICY utilization, not a financial balance or authorization hold.
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS payment_intents (
    intent_id TEXT PRIMARY KEY,
    grant_id TEXT NOT NULL REFERENCES delegation_grants(grant_id),
    agent_id TEXT NOT NULL REFERENCES agent_identities(agent_id),
    merchant_id TEXT NOT NULL,
    amount_cents BIGINT NOT NULL CHECK (amount_cents > 0),
    currency CHAR(3) NOT NULL DEFAULT 'USD',
    status TEXT NOT NULL,
    expires_at TIMESTAMPTZ,
    lithic_transaction_token UUID,
    detail JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE TABLE IF NOT EXISTS agent_nonces (
    agent_id TEXT NOT NULL REFERENCES agent_identities(agent_id),
    nonce TEXT NOT NULL,
    PRIMARY KEY (agent_id, nonce)
);
CREATE TABLE IF NOT EXISTS authorization_decisions (
    id BIGSERIAL PRIMARY KEY,
    network_reference TEXT NOT NULL UNIQUE,
    instrument_id TEXT REFERENCES payment_instruments(instrument_id),
    intent_id TEXT REFERENCES payment_intents(intent_id),
    source TEXT NOT NULL,
    decision TEXT NOT NULL,
    amount_cents BIGINT NOT NULL,
    merchant_id TEXT,
    reason TEXT,
    evidence JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS lithic_balance_observations (
    id BIGSERIAL PRIMARY KEY,
    arrangement_id TEXT NOT NULL REFERENCES funding_arrangements(arrangement_id),
    financial_account_token UUID NOT NULL,
    currency CHAR(3) NOT NULL,
    available_amount BIGINT NOT NULL,
    pending_amount BIGINT NOT NULL,
    total_amount BIGINT NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    raw_balance JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_balance_observations_account_time ON lithic_balance_observations(financial_account_token, fetched_at DESC);
-- Restartable demo state. Existing FastAPI code uses an in-process cache; it is
-- snapshotted under a SQL transaction for this demo, NOT a production ledger.
CREATE TABLE IF NOT EXISTS occlet_demo_runtime (
    singleton BOOLEAN PRIMARY KEY DEFAULT true CHECK (singleton),
    snapshot JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

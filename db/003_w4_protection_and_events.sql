-- W4: member-protected intent is a POLICY obligation, not a ledger hold.
-- All money stays in the single demo funding arrangement.
CREATE TABLE IF NOT EXISTS demo_w4_protections (
    arrangement_id TEXT PRIMARY KEY REFERENCES demo_ledger_books(arrangement_id) ON DELETE CASCADE,
    purpose TEXT NOT NULL DEFAULT 'TRIP',
    initial_cents BIGINT NOT NULL CHECK(initial_cents > 0),
    protected_cents BIGINT NOT NULL CHECK(protected_cents >= 0),
    yield_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    version BIGINT NOT NULL DEFAULT 1,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS demo_w4_releases (
    arrangement_id TEXT NOT NULL REFERENCES demo_w4_protections(arrangement_id) ON DELETE CASCADE,
    release_ref TEXT NOT NULL,
    amount_cents BIGINT NOT NULL CHECK(amount_cents > 0),
    old_protected_cents BIGINT NOT NULL,
    new_protected_cents BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (arrangement_id,release_ref)
);
CREATE TABLE IF NOT EXISTS demo_w4_decisions (
    arrangement_id TEXT NOT NULL REFERENCES demo_ledger_books(arrangement_id) ON DELETE CASCADE,
    authorization_ref TEXT NOT NULL,
    amount_cents BIGINT NOT NULL CHECK(amount_cents>0),
    decision TEXT NOT NULL CHECK(decision IN ('APPROVED','DECLINED')),
    reason TEXT NOT NULL,
    protected_cents BIGINT NOT NULL,
    available_cents BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY(arrangement_id,authorization_ref)
);
-- Event IDs are delivery identifiers, transaction tokens are group identifiers.
CREATE TABLE IF NOT EXISTS demo_lithic_event_inbox (
    event_id TEXT PRIMARY KEY,
    arrangement_id TEXT NOT NULL REFERENCES demo_ledger_books(arrangement_id),
    transaction_token TEXT NOT NULL,
    event_status TEXT NOT NULL,
    processing_state TEXT NOT NULL CHECK(processing_state IN ('RECEIVED','PROCESSED','QUARANTINED')),
    detail TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    processed_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_demo_lithic_event_tx ON demo_lithic_event_inbox(arrangement_id,transaction_token);

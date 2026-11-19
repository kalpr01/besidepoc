-- OCCLET LOCAL DEMO LEDGER, not a Lithic Ledger financial account.
-- One book per member funding arrangement. There are NO independent agent balances.
-- Journal lines must balance; ledger code inserts both sides in a single SQL transaction.
ALTER TABLE funding_arrangements DROP CONSTRAINT IF EXISTS funding_arrangements_funding_mode_check;
ALTER TABLE funding_arrangements ADD CONSTRAINT funding_arrangements_funding_mode_check
    CHECK (funding_mode IN ('DEMO_MIRROR','LITHIC_LEDGER','OCCLET_SQL_LEDGER'));

CREATE TABLE IF NOT EXISTS demo_ledger_books (
    arrangement_id TEXT PRIMARY KEY REFERENCES funding_arrangements(arrangement_id) ON DELETE CASCADE,
    currency CHAR(3) NOT NULL DEFAULT 'USD' CHECK(currency='USD'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS demo_ledger_accounts (
    arrangement_id TEXT NOT NULL REFERENCES demo_ledger_books(arrangement_id) ON DELETE CASCADE,
    code TEXT NOT NULL CHECK(code IN ('CASH','AVAILABLE','AUTH_HOLDS')),
    normal_side TEXT NOT NULL CHECK(normal_side IN ('DEBIT','CREDIT')),
    balance_cents BIGINT NOT NULL DEFAULT 0 CHECK(balance_cents >= 0),
    PRIMARY KEY(arrangement_id,code),
    CHECK ((code='CASH' AND normal_side='DEBIT') OR
           (code IN ('AVAILABLE','AUTH_HOLDS') AND normal_side='CREDIT'))
);
CREATE TABLE IF NOT EXISTS demo_ledger_journals (
    id BIGSERIAL PRIMARY KEY,
    arrangement_id TEXT NOT NULL REFERENCES demo_ledger_books(arrangement_id) ON DELETE CASCADE,
    journal_ref TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('OPENING','TOP_UP','AUTHORIZE','RELEASE','CLEAR')),
    related_authorization TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(arrangement_id,journal_ref)
);
CREATE TABLE IF NOT EXISTS demo_ledger_entries (
    id BIGSERIAL PRIMARY KEY,
    journal_id BIGINT NOT NULL REFERENCES demo_ledger_journals(id) ON DELETE CASCADE,
    arrangement_id TEXT NOT NULL,
    account_code TEXT NOT NULL,
    side TEXT NOT NULL CHECK(side IN ('DEBIT','CREDIT')),
    amount_cents BIGINT NOT NULL CHECK(amount_cents > 0),
    FOREIGN KEY(arrangement_id,account_code)
      REFERENCES demo_ledger_accounts(arrangement_id,code)
);
CREATE INDEX IF NOT EXISTS ix_demo_ledger_entries_journal ON demo_ledger_entries(journal_id);
CREATE TABLE IF NOT EXISTS demo_ledger_authorizations (
    arrangement_id TEXT NOT NULL REFERENCES demo_ledger_books(arrangement_id) ON DELETE CASCADE,
    authorization_ref TEXT NOT NULL,
    card_reference TEXT NOT NULL,
    merchant_id TEXT NOT NULL,
    amount_cents BIGINT NOT NULL CHECK(amount_cents>0),
    clearing_amount_cents BIGINT,
    status TEXT NOT NULL CHECK(status IN ('AUTHORIZED','RELEASED','CLEARED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY(arrangement_id,authorization_ref),
    CHECK(clearing_amount_cents IS NULL OR
          (clearing_amount_cents >= 0 AND clearing_amount_cents <= amount_cents))
);
CREATE INDEX IF NOT EXISTS ix_demo_ledger_authorizations_period
  ON demo_ledger_authorizations(arrangement_id, created_at);

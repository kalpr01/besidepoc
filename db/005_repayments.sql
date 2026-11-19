-- Member repayments on the parent card: same balanced journal as a top-up
-- (CASH debit / AVAILABLE credit) but recorded as REPAYMENT, so it reduces the
-- member's "amount due" (settled purchases − repayments + pending holds).
-- Idempotent: re-runs on every startup.
ALTER TABLE demo_ledger_journals DROP CONSTRAINT IF EXISTS demo_ledger_journals_kind_check;
ALTER TABLE demo_ledger_journals ADD CONSTRAINT demo_ledger_journals_kind_check
    CHECK (kind IN ('OPENING','TOP_UP','AUTHORIZE','RELEASE','CLEAR','REPAYMENT'));

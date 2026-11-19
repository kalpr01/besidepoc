-- Test-member switcher for the lab. Each member keeps its own saved runtime snapshot
-- (funding arrangement, Lithic parent link, agents, delegated cards). Only the ACTIVE
-- member is loaded in the app; occlet_demo_runtime remains the active member's live copy.
ALTER TABLE members ADD COLUMN IF NOT EXISTS runtime_snapshot JSONB;
ALTER TABLE members ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now();
UPDATE members SET display_name = 'Ann Gutierrez'
 WHERE member_id = 'member_demo' AND display_name = 'Synthetic Occlet member';

CREATE TABLE IF NOT EXISTS occlet_demo_active_member (
    singleton BOOLEAN PRIMARY KEY DEFAULT true CHECK (singleton),
    member_id TEXT NOT NULL REFERENCES members(member_id),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO occlet_demo_active_member(singleton, member_id)
VALUES (true, 'member_demo') ON CONFLICT DO NOTHING;

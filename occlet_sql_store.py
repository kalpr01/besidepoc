"""Local SQL arrangement registry/persistence for the Occlet sandbox demo.

This module persists arrangement configuration and the demo runtime snapshot.
occlet_demo_ledger owns the local SQL financial journal when selected; Lithic
Ledger is an alternative authority, never active alongside the local book.
This is demo-grade single-process persistence, not an issuer system.
"""
from __future__ import annotations
import copy
import json
import os
from datetime import datetime, timezone
from typing import Any


def enabled() -> bool:
    return bool(os.environ.get("DATABASE_URL", "").strip())


def _connect():
    import psycopg
    return psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=4)


def initialize() -> None:
    if not enabled():
        return
    from pathlib import Path
    with _connect() as conn:
        conn.execute((Path(__file__).parent / 'db' / '001_arrangements.sql').read_text())
        conn.execute((Path(__file__).parent / 'db' / '002_occlet_local_ledger.sql').read_text())
        conn.execute((Path(__file__).parent / 'db' / '003_w4_protection_and_events.sql').read_text())


def load_runtime() -> dict | None:
    if not enabled():
        return None
    with _connect() as conn:
        row = conn.execute('SELECT snapshot FROM occlet_demo_runtime WHERE singleton=true').fetchone()
        return row[0] if row else None


def persist_runtime(*, parent: dict, agents: dict, cards: dict, intents: dict,
                    nonces: set, network_refs: dict, lithic_refs: dict,
                    events: list, auth_traces: list) -> None:
    if not enabled():
        return
    # Never persist temporary bearer payment handles, even in sandbox.
    clean_intents = copy.deepcopy(intents)
    for intent in clean_intents.values():
        intent.pop('payment_token', None)
        if intent.get('status') in ('READY', 'AWAITING_MEMBER_APPROVAL'):
            intent['status'] = 'EXPIRED_ON_RESTART'  # only affects restored snapshot
    clean_refs = copy.deepcopy(network_refs)
    for auth in clean_refs.values():
        auth.pop('payment_token', None)
    snapshot = dict(parent=parent, agents=agents, cards=cards, intents=clean_intents,
                    nonces=[list(x) for x in nonces], network_refs=clean_refs,
                    lithic_refs=lithic_refs, events=events, auth_traces=auth_traces)
    p = parent
    with _connect() as conn:
        # Normalize arrangement data and preserve decision evidence.
        # Relational rows are the SQL representation; snapshot restores demo cache.
        # UPSERT rather than deleting/reinserting: the audit and balance history
        # stay append-only even as delegation and card policy state changes.
        if p.get('enabled'):
            conn.execute("""INSERT INTO funding_arrangements
              (arrangement_id,member_id,product_code,status,funding_mode,lithic_account_token,
               lithic_issuing_financial_account_token,parent_policy)
              VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
              ON CONFLICT (arrangement_id) DO UPDATE SET
              status=EXCLUDED.status, funding_mode=EXCLUDED.funding_mode,
              lithic_account_token=EXCLUDED.lithic_account_token,
              lithic_issuing_financial_account_token=EXCLUDED.lithic_issuing_financial_account_token,
              parent_policy=EXCLUDED.parent_policy, updated_at=now()""",
              (p['id'], 'member_demo', p.get('product_code','DEBIT_SPENDING'),p['status'],
               p.get('ledger_mode','DEMO_MIRROR'),p.get('lithic_account_token'),
               p.get('lithic_financial_account_token'),json.dumps(dict(
                   max_transaction_cents=p['max_transaction_cents'],
                   monthly_policy_limit_cents=p['monthly_policy_limit_cents']))))
        for a in agents.values():
            conn.execute("""INSERT INTO agent_identities
              (agent_id,member_id,display_name,public_key_b64,key_fingerprint,purpose,purchase_goal,registered_limits)
              VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
              ON CONFLICT (agent_id) DO UPDATE SET
              display_name=EXCLUDED.display_name, public_key_b64=EXCLUDED.public_key_b64,
              key_fingerprint=EXCLUDED.key_fingerprint, purpose=EXCLUDED.purpose,
              purchase_goal=EXCLUDED.purchase_goal, registered_limits=EXCLUDED.registered_limits,
              updated_at=now()""",
              (a['agent_id'],'member_demo',a['name'],a['public_key_b64'],a['key_fingerprint'],
               a.get('purpose'),a.get('purchase_goal'),json.dumps(a.get('registered_limits'))))
        if p.get('enabled') and p.get('lithic_parent_card_token'):
            conn.execute("""INSERT INTO payment_instruments
              (instrument_id,arrangement_id,instrument_role,instrument_status,lithic_account_token,
               lithic_card_token,card_last_four,instrument_alias)
              VALUES (%s,%s,'PARENT',%s,%s,%s,%s,%s)
              ON CONFLICT (instrument_id) DO UPDATE SET
              arrangement_id=EXCLUDED.arrangement_id,
              instrument_status=EXCLUDED.instrument_status,
              lithic_account_token=EXCLUDED.lithic_account_token,
              lithic_card_token=EXCLUDED.lithic_card_token,
              card_last_four=EXCLUDED.card_last_four""",
              ('parent_demo_card',p['id'],p['status'],p.get('lithic_account_token'),
               p['lithic_parent_card_token'],p.get('parent_card_last_four'),'PARENT-CARD'))
        for c in cards.values():
            if not p.get('enabled'):
                # Legacy standalone demo cards are not funding arrangements.
                continue
            conn.execute("""INSERT INTO payment_instruments
              (instrument_id,arrangement_id,instrument_role,agent_id,instrument_status,
               lithic_account_token,lithic_card_token,instrument_alias)
              VALUES (%s,%s,'AGENT_VIRTUAL',%s,%s,%s,%s,%s)
              ON CONFLICT (instrument_id) DO UPDATE SET
              instrument_status=EXCLUDED.instrument_status,
              lithic_account_token=EXCLUDED.lithic_account_token,
              lithic_card_token=EXCLUDED.lithic_card_token,
              instrument_alias=EXCLUDED.instrument_alias""",
              (c['id'],p['id'],c['agent_id'],c['status'],c.get('lithic_account_token'),
               c.get('lithic_card_token'),c.get('alias')))
            conn.execute("""INSERT INTO delegation_grants
              (grant_id,agent_id,instrument_id,arrangement_id,status,authorization_flow,
               purpose,merchant_allowlist,policy)
              VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb)
              ON CONFLICT (grant_id) DO UPDATE SET
              status=EXCLUDED.status, authorization_flow=EXCLUDED.authorization_flow,
              purpose=EXCLUDED.purpose,merchant_allowlist=EXCLUDED.merchant_allowlist,
              policy=EXCLUDED.policy,policy_version=delegation_grants.policy_version +
                CASE WHEN delegation_grants.policy IS DISTINCT FROM EXCLUDED.policy THEN 1 ELSE 0 END,
              updated_at=now()""",
              ('grant_'+c['id'], c['agent_id'],c['id'],p['id'],c['status'],
               c['authorization_flow'],c.get('purpose'),json.dumps(c['allowed_merchants']),
               json.dumps(dict(per_transaction_cents=c['per_transaction_cents'],
                               budget_cents=c['budget_cents'],
                               approval_threshold_cents=c['approval_threshold_cents']))))
            conn.execute("""INSERT INTO delegation_usage(grant_id,authorized_utilization_cents)
              VALUES (%s,%s) ON CONFLICT(grant_id) DO UPDATE SET
              authorized_utilization_cents=EXCLUDED.authorized_utilization_cents,updated_at=now()""",
              ('grant_'+c['id'], c['spent_cents']))
        for i in intents.values():
            if not p.get('enabled') or i.get('card_id') not in cards:
                continue
            conn.execute("""INSERT INTO payment_intents
              (intent_id,grant_id,agent_id,merchant_id,amount_cents,currency,status,expires_at,
               lithic_transaction_token,detail)
              VALUES (%s,%s,%s,%s,%s,%s,%s,to_timestamp(%s),%s,%s::jsonb)
              ON CONFLICT (intent_id) DO UPDATE SET
              status=EXCLUDED.status, lithic_transaction_token=EXCLUDED.lithic_transaction_token,
              detail=EXCLUDED.detail""",
              (i['id'],'grant_'+i['card_id'],i.get('agent_id',cards[i['card_id']]['agent_id']),
               i['merchant_id'],i['amount_cents'],i.get('currency','USD'),i['status'],
               i.get('expires_at',0),i.get('lithic_transaction_token'),
               json.dumps({'member_approved':i.get('member_approved')})))
        for agent_id, nonce in nonces:
            if agent_id in agents:
                conn.execute('INSERT INTO agent_nonces(agent_id,nonce) VALUES (%s,%s) ON CONFLICT DO NOTHING',(agent_id,nonce))
        # Decision evidence is append-only: retries do not overwrite the original.
        for trace in auth_traces:
            ref = trace.get('network_reference')
            if not ref or not p.get('enabled'):
                continue
            card_id = trace.get('card_id')
            intent_id = trace.get('payment_intent_id')
            if intent_id not in intents: intent_id = None
            conn.execute("""INSERT INTO authorization_decisions
             (network_reference,instrument_id,intent_id,source,decision,amount_cents,merchant_id,reason,evidence)
             VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
             ON CONFLICT (network_reference) DO NOTHING""",
             (ref,card_id if card_id in cards else None,intent_id,trace['source'],trace['decision'],
              trace['amount_cents'],trace.get('merchant_id'),trace.get('reason'),json.dumps(trace)))
        conn.execute("""INSERT INTO occlet_demo_runtime(singleton,snapshot) VALUES (true,%s::jsonb)
                      ON CONFLICT (singleton) DO UPDATE SET snapshot=EXCLUDED.snapshot, updated_at=now()""",
                     (json.dumps(snapshot),))


def save_balance_observation(arrangement_id: str, balance: dict) -> None:
    if not enabled():
        return
    with _connect() as conn:
        conn.execute("""INSERT INTO lithic_balance_observations
          (arrangement_id,financial_account_token,currency,available_amount,pending_amount,total_amount,raw_balance)
          VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb)""",
          (arrangement_id,balance['token'],balance['currency'],balance['available_amount'],
           balance['pending_amount'],balance['total_amount'],json.dumps(balance)))


def health() -> dict:
    if not enabled():
        return {'configured':False,'connected':False}
    try:
        with _connect() as conn:
            conn.execute('SELECT 1')
        return {'configured':True,'connected':True}
    except Exception:
        return {'configured':True,'connected':False}


def reset_sql_registry() -> None:
    """Explicit local reset: also removes cached Lithic balance observations."""
    if not enabled():
        return
    with _connect() as conn:
        conn.execute('TRUNCATE authorization_decisions, agent_nonces, payment_intents, '
                     'delegation_usage, delegation_grants, payment_instruments, agent_identities, '
                     'lithic_balance_observations, funding_arrangements, occlet_demo_runtime '
                     'RESTART IDENTITY CASCADE')


def read_registry() -> dict:
    """Read-only normalized SQL view for demo inspection. No card secrets returned."""
    if not enabled():
        raise RuntimeError('Set DATABASE_URL and start Postgres first')
    from psycopg.rows import dict_row
    queries = {
        'funding_arrangements': '''SELECT arrangement_id,member_id,product_code,status,funding_mode,
              lithic_account_token,lithic_issuing_financial_account_token,parent_policy,updated_at
              FROM funding_arrangements ORDER BY arrangement_id LIMIT 20''',
        'agent_identities': '''SELECT agent_id,display_name,key_fingerprint,purpose,purchase_goal,
              registered_limits FROM agent_identities ORDER BY agent_id LIMIT 50''',
        'payment_instruments': '''SELECT instrument_id,arrangement_id,instrument_role,agent_id,
              instrument_status,lithic_card_token,card_last_four FROM payment_instruments
              ORDER BY instrument_role,instrument_id LIMIT 50''',
        'delegation_grants': '''SELECT grant_id,agent_id,instrument_id,status,authorization_flow,
              purpose,merchant_allowlist,policy,policy_version FROM delegation_grants LIMIT 50''',
        'payment_intents': '''SELECT intent_id,agent_id,merchant_id,amount_cents,status,
              lithic_transaction_token FROM payment_intents ORDER BY expires_at DESC LIMIT 50''',
        'authorization_decisions': '''SELECT network_reference,source,decision,amount_cents,
              merchant_id,reason,created_at FROM authorization_decisions ORDER BY id DESC LIMIT 30''',
        'lithic_balance_observations': '''SELECT financial_account_token,currency,available_amount,
              pending_amount,total_amount,fetched_at FROM lithic_balance_observations
              ORDER BY id DESC LIMIT 10''',
    }
    with _connect() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            result = {}
            for group, statement in queries.items():
                cur.execute(statement)
                result[group] = cur.fetchall()
            return result

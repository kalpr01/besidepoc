"""Tests for Lithic ledger discovery and strict separation of money vs permissions."""
from __future__ import annotations
import os
import uuid
from fastapi.testclient import TestClient
import app as demo
from app import app
import lithic_client
import occlet_sql_store


def test_discovery_requires_existing_parent_account():
    with TestClient(app) as c:
        c.post('/api/demo/reset')
        r = c.get('/api/lithic/ledger/discover')
        assert r.status_code == 409


def test_discovery_and_ledger_link_verify_parent_account(monkeypatch):
    account = str(uuid.uuid4())
    issuing = str(uuid.uuid4())
    other = str(uuid.uuid4())
    with TestClient(app) as c:
        c.post('/api/demo/reset')
        c.post('/api/member/parent/configure', json={
            'credit_limit_cents':500_000,'max_transaction_cents':100_000,'monthly_policy_limit_cents':500_000})
        demo._parent['lithic_account_token'] = account
        def fake_get(method, path, **kw):
            assert method=='GET'
            assert path==f'/financial_accounts?account_token={account}'
            return {'data':[{'token':issuing, 'type':'ISSUING','account_token':account,'status':'OPEN'},
                            {'token':other, 'type':'ISSUING','account_token':str(uuid.uuid4()),'status':'OPEN'}]}
        monkeypatch.setattr(lithic_client,'lithic_request',fake_get)
        r=c.get('/api/lithic/ledger/discover')
        assert r.status_code==200 and r.json()['ledger_provisioned']
        assert len(r.json()['issuing_financial_accounts'])==1
        assert c.post('/api/lithic/ledger/link',json={'financial_account_token':other}).status_code==409
        good=c.post('/api/lithic/ledger/link',json={'financial_account_token':issuing})
        assert good.status_code==200 and good.json()['mode']=='DEMO_MIRROR'
        assert demo._parent['lithic_financial_account_token']==issuing


def test_activate_fails_closed_without_confirmed_program(monkeypatch):
    with TestClient(app) as c:
        c.post('/api/demo/reset')
        monkeypatch.delenv('LITHIC_AUTH_FROM_BALANCE_CONFIRMED',raising=False)
        assert c.post('/api/lithic/ledger/activate').status_code==409


def test_balances_are_read_from_lithic_not_locally_invented(monkeypatch):
    financial = str(uuid.uuid4())
    with TestClient(app) as c:
        c.post('/api/demo/reset')
        c.post('/api/member/parent/configure',json={
            'credit_limit_cents':500_000,'max_transaction_cents':100_000,'monthly_policy_limit_cents':500_000})
        demo._parent['lithic_financial_account_token']=financial
        data={'data':[{'type':'ISSUING','token':financial,'currency':'USD',
                'available_amount':12000,'pending_amount':3000,'total_amount':15000,'updated':'2026-11-18T12:00:00Z'}]}
        monkeypatch.setattr(lithic_client,'lithic_request',lambda m,p,**kw:data)
        observed=[]
        monkeypatch.setattr(occlet_sql_store,'save_balance_observation', lambda a,b: observed.append((a,b)))
        resp=c.get('/api/lithic/ledger/balance')
        assert resp.status_code==200, resp.text
        assert resp.json()['balance']['available_amount']==12000
        assert observed[0][1]['token']==financial
        assert demo._parent['holds']=={}  # no local monetary entry


def test_ledger_mode_skips_local_financial_hold_but_counts_policy(monkeypatch):
    monkeypatch.setenv('LITHIC_AUTH_FROM_BALANCE_CONFIRMED','1')
    with demo._lock:
        demo._parent.clear();demo._parent.update(demo.default_parent())
        demo._parent.update(enabled=True,ledger_mode='LITHIC_LEDGER',
          lithic_financial_account_token=str(uuid.uuid4()),max_transaction_cents=100000,
          monthly_policy_limit_cents=1000000,credit_limit_cents=1)
        assert demo.parent_policy(50000, 'GROCERY_101') is None
        demo.reserve_parent_once('test:one',50000)
        assert demo._parent['holds']=={}
        assert demo._parent['policy_authorizations']['test:one']==50000
        assert demo.parent_used()==50000
        demo._parent.clear();demo._parent.update(demo.default_parent())


def test_local_network_simulator_disabled_in_ledger_mode():
    with TestClient(app) as c:
        c.post('/api/demo/reset')
        demo._parent['ledger_mode']='LITHIC_LEDGER'
        r=c.post('/api/network/direct/authorize',json={
            'card_reference':'anything','merchant_id':'GROCERY_101','amount_cents':500,
            'network_reference':'1234567890abcdef'})
        assert r.status_code==409
        demo._parent.clear(); demo._parent.update(demo.default_parent())


def test_sql_schema_keeps_financial_state_out_of_occlet_tables():
    from pathlib import Path
    schema=(Path(__file__).parent.parent/'db'/'001_arrangements.sql').read_text()
    for table in ['funding_arrangements','payment_instruments','agent_identities',
                  'delegation_grants','payment_intents','authorization_decisions',
                  'lithic_balance_observations','delegation_usage']:
        assert f'CREATE TABLE IF NOT EXISTS {table}' in schema
    assert 'raw_balance JSONB' in schema
    assert 'CREATE TABLE IF NOT EXISTS ledger_entries' not in schema

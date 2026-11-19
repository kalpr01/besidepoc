"""Unit tests; full SQL transaction test is opt-in and needs PostgreSQL."""
import os
import uuid
import pytest
from fastapi.testclient import TestClient
import app as application
import occlet_demo_ledger as ledger
import occlet_sql_store as store
import lithic_client

client = TestClient(application.app)


def test_opening_funding_is_double_entry():
    assert ledger.journal_effects([('CASH','DEBIT',50000),('AVAILABLE','CREDIT',50000)]) == {
        'CASH': 50000, 'AVAILABLE': 50000, 'AUTH_HOLDS': 0}


def test_hold_moves_liability_not_cash():
    assert ledger.journal_effects([('AVAILABLE','DEBIT',2000),('AUTH_HOLDS','CREDIT',2000)]) == {
        'CASH': 0, 'AVAILABLE': -2000, 'AUTH_HOLDS': 2000}


def test_clearing_less_than_hold_releases_difference():
    assert ledger.journal_effects([('AUTH_HOLDS','DEBIT',2000),('CASH','CREDIT',1800),('AVAILABLE','CREDIT',200)]) == {
        'CASH': -1800, 'AVAILABLE': 200, 'AUTH_HOLDS': -2000}


def test_reversal_restores_available():
    assert ledger.journal_effects([('AUTH_HOLDS','DEBIT',2000),('AVAILABLE','CREDIT',2000)]) == {
        'CASH': 0, 'AVAILABLE': 2000, 'AUTH_HOLDS': -2000}


@pytest.mark.parametrize('lines',[
    [('CASH','DEBIT',20),('AVAILABLE','CREDIT',19)],
    [('CASH','DEBIT',20),('AVAILABLE','CREDIT',0)],
    [('CASH','DEBIT',20),('WRONG','CREDIT',20)],
    [('CASH','DEBIT',20),('AVAILABLE','WITHDRAW',20)],
    [],
])
def test_invalid_journal_rejected(lines):
    with pytest.raises(ledger.LedgerRejected):
        ledger.journal_effects(lines)


def test_no_database_fails_closed(monkeypatch):
    monkeypatch.delenv('DATABASE_URL', raising=False)
    with pytest.raises(ledger.LedgerRejected, match='POSTGRES_NOT_CONFIGURED'):
        ledger.authorize('arr','ref',500,card_reference='card',merchant_id='GROCERY_101',
                         max_transaction_cents=1000,monthly_limit_cents=5000)


def test_cannot_initialize_without_parent(monkeypatch):
    monkeypatch.delenv('DATABASE_URL',raising=False)
    client.post('/api/demo/reset')
    r=client.post('/api/demo-ledger/initialize',json={'opening_balance_cents':50000})
    assert r.status_code == 409


def test_existing_parent_lithic_link_does_not_store_pan(monkeypatch):
    from app import _parent
    client.post('/api/demo/reset')
    client.post('/api/member/parent/configure',json={
        'credit_limit_cents':50000,'max_transaction_cents':25000,'monthly_policy_limit_cents':50000})
    acct=str(uuid.uuid4()); card=str(uuid.uuid4())
    def fake_request(method,path,*,payload=None):
        assert method=='GET' and path==f'/cards/{card}'
        return {'token':card,'account_token':acct,'state':'OPEN','last_four':'2909',
                'pan':'4111111111111111','cvv':'123'}
    monkeypatch.setattr(lithic_client,'lithic_request',fake_request)
    r=client.post('/api/member/parent/lithic/link-existing',json={'account_token':acct,'card_token':card})
    assert r.status_code==200,r.text
    assert r.json()['last_four']=='2909'
    assert 'pan' not in r.text.lower() and '411111' not in str(application._parent)
    assert application._parent['lithic_account_token']==acct


def test_existing_parent_link_rejects_wrong_account(monkeypatch):
    client.post('/api/demo/reset')
    client.post('/api/member/parent/configure',json={
        'credit_limit_cents':50000,'max_transaction_cents':25000,'monthly_policy_limit_cents':50000})
    monkeypatch.setattr(lithic_client,'lithic_request',lambda *a,**kw: {'token':str(uuid.uuid4()),
             'account_token':str(uuid.uuid4()),'state':'OPEN','last_four':'2909'})
    r=client.post('/api/member/parent/lithic/link-existing',json={
        'account_token':str(uuid.uuid4()),'card_token':str(uuid.uuid4())})
    assert r.status_code==409


def test_double_authority_prohibited(monkeypatch):
    client.post('/api/demo/reset')
    client.post('/api/member/parent/configure',json={
        'credit_limit_cents':50000,'max_transaction_cents':25000,'monthly_policy_limit_cents':50000})
    with application._lock:
        application._parent['ledger_mode']='LITHIC_LEDGER'
    r=client.post('/api/demo-ledger/initialize',json={'opening_balance_cents':50000})
    assert r.status_code==409
    client.post('/api/demo/reset')


def test_schema_contains_durable_one_book_and_reference_guards():
    from pathlib import Path
    sql=(Path(__file__).parents[1]/'db'/'002_occlet_local_ledger.sql').read_text()
    assert 'PRIMARY KEY(arrangement_id,authorization_ref)' in sql
    assert 'UNIQUE(arrangement_id,journal_ref)' in sql
    assert "funding_mode IN ('DEMO_MIRROR','LITHIC_LEDGER','OCCLET_SQL_LEDGER')" in sql
    assert 'REFERENCES demo_ledger_books(arrangement_id)' in sql


def test_new_sql_mode_fail_closed_for_unavailable_balance(monkeypatch):
    client.post('/api/demo/reset')
    client.post('/api/member/parent/configure',json={
        'credit_limit_cents':50000,'max_transaction_cents':25000,'monthly_policy_limit_cents':50000})
    with application._lock:
        application._parent['ledger_mode']='OCCLET_SQL_LEDGER'
    monkeypatch.setattr(ledger,'monthly_used',lambda _:0)
    monkeypatch.setattr(ledger,'summary',lambda _: {'available_cents':100,'held_cents':0,'cash_cents':100,'authorizations':[]})
    assert application.parent_policy(150,'GROCERY_101')=='PARENT_AVAILABLE_CREDIT'
    client.post('/api/demo/reset')

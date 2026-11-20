"""Member projection tests: source-of-truth ledger, card attribution, step-ups, requests."""
import time
import app as demo
from fastapi.testclient import TestClient

client=TestClient(demo.app)


def test_member_projection_without_postgres_does_not_forge_balance(monkeypatch):
    with demo._lock:
        demo._parent.clear();demo._parent.update(demo.default_parent())
        demo._cards.clear();demo._agents.clear();demo._intents.clear();demo._auth_traces.clear();demo._events.clear()
    r=client.get('/api/member/experience')
    assert r.status_code==200
    p=r.json()['parent']
    assert p['ledger_mode']=='DEMO_MIRROR'
    assert p['available_cents'] is None and p['cash_cents'] is None
    assert p['note'].startswith('PostgreSQL financial ledger not initialized')


def test_member_projection_has_ledger_card_and_action_inbox(monkeypatch):
    import occlet_demo_ledger,occlet_w4
    with demo._lock:
        demo._parent.clear();demo._parent.update(demo.default_parent())
        demo._parent.update(enabled=True,id='test_parent_v4',ledger_mode='OCCLET_SQL_LEDGER',
                            lithic_parent_card_token='parent_token',parent_card_last_four='2909')
        demo._cards.clear();demo._agents.clear();demo._intents.clear();demo._auth_traces.clear();demo._events.clear()
        demo._agents['a1']={'agent_id':'a1','name':'Grocery helper','key_fingerprint':'xyz','purpose':'GROCERIES',
                            'purchase_goal':'weekly food','registered_limits':{'budget_cents':20000}}
        demo._cards['vc_test']={'id':'vc_test','alias':'DELEGATED-TEST','agent_id':'a1','status':'ACTIVE',
                                 'authorization_flow':'SIGNED_INTENT_REQUIRED','purpose':'GROCERIES',
                                 'per_transaction_cents':7500,'budget_cents':20000,'approval_threshold_cents':2500,
                                 'allowed_merchants':['GROCERY_101'],'spent_cents':3500,'lithic_card_token':None,
                                 'parent_arrangement_id':'test_parent_v4'}
        demo._intents['pi_test']={'id':'pi_test','card_id':'vc_test','agent_id':'a1','merchant_id':'GROCERY_101',
                                  'amount_cents':7500,'status':'AWAITING_MEMBER_APPROVAL',
                                  'payment_token':'secret-do-not-return','expires_at':time.time()+80}
        demo._auth_traces.append({'at':'2026-11-18T19:00:00+00:00','source':'W4_LOCAL_PARENT_CARD',
            'mode':'PARENT_CARD','decision':'DECLINED','card_id':None,'merchant_id':'GROCERY_101',
            'network_reference':'r-denied','amount_cents':35000,'reason':'PROTECTED_BY_MEMBER'})
    monkeypatch.setattr(occlet_demo_ledger,'summary',lambda arr:{'cash_cents':50000,'available_cents':46500,
       'held_cents':3500,'authorizations':[{'reference':'local:r1','card_reference':'vc_test',
       'merchant':'GROCERY_101','amount_cents':3500,'clearing_amount_cents':None,'status':'AUTHORIZED','created_at':'2026-11-18'}]})
    monkeypatch.setattr(occlet_demo_ledger,'monthly_used',lambda arr:3500)
    monkeypatch.setattr(occlet_w4,'overview',lambda arr:{'protected_cents':30000,'yield_enabled':False,
            'version':1,'free_to_spend_cents':16500})
    try:
        r=client.get('/api/member/experience')
        assert r.status_code==200,r.text
        data=r.json()
        assert data['parent']['cash_cents']==50000
        assert data['parent']['available_cents']==46500
        assert data['parent']['held_cents']==3500
        assert data['parent']['spendable_after_protection_cents']==16500
        # Newest first: the 19:00 decline sorts ahead of the date-only ledger row.
        assert [t['reference'] for t in data['transactions']]==['r-denied','local:r1']
        assert data['transactions'][1]['card_label']=='Grocery helper'
        assert any(t['card_label']=='Parent card' and t['status']=='DECLINED' for t in data['transactions'])
        assert data['pending_approvals'][0]['id']=='pi_test'
        assert 'payment_token' not in data['pending_approvals'][0]
        req=client.post('/api/member/holds/request-release',json={'authorization_ref':'local:r1'})
        assert req.status_code==200,req.text
        assert req.json()['status']=='PENDING_REVIEW'
        assert client.post('/api/member/holds/request-release',json={'authorization_ref':'local:r1'}).status_code==200
        snapshot=client.get('/api/member/experience').json()
        assert len(snapshot['hold_release_requests'])==1
        assert snapshot['hold_release_requests'][0]['status']=='PENDING_REVIEW'
        assert client.post('/api/member/holds/request-release',json={'authorization_ref':'wrong-ref'}).status_code==409
    finally:
        with demo._lock:
            demo._parent.clear();demo._parent.update(demo.default_parent())
            demo._cards.clear();demo._agents.clear();demo._intents.clear();demo._auth_traces.clear();demo._events.clear()


def test_admin_lab_has_hold_review_and_member_link():
    html=client.get('/').text
    assert 'Admin operations · Member hold-release requests' in html
    assert 'Confirm demo reversal' in html
    assert 'href="/member/delegated"' in html

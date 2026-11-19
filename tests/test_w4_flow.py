"""W4 parent-card test coverage with in-memory DB adapter + signed webhook logic."""
import base64
import hashlib
import hmac
import json
import secrets
import time
import uuid
import pytest
from fastapi.testclient import TestClient

import app as a
import lithic_bridge as bridge
import occlet_w4 as w4
import occlet_demo_ledger as ledger
import occlet_lithic_events as events
from test_lithic import asa

ui=TestClient(a.app)

def test_w4_pure_rule_and_financial_journal_are_separate():
    assert w4.rule(35000,50000,30000,False)=='PROTECTED_BY_MEMBER'
    assert w4.rule(35000,50000,15000,False) is None
    assert w4.rule(35000,20000,0,False)=='INSUFFICIENT_FUNDS'
    assert w4.rule(35000,50000,30000,True) is None
    assert ledger.journal_effects([('AVAILABLE','DEBIT',35000),('AUTH_HOLDS','CREDIT',35000)])=={
        'CASH':0,'AVAILABLE':-35000,'AUTH_HOLDS':35000}


def fake_w4(monkeypatch):
    """Controlled fake persistence for HTTP wiring/ASA tests (not a Postgres test)."""
    state={'cash':50000,'available':50000,'held':0,'protected':30000,'auths':{},'denied':[]}
    monkeypatch.setattr('occlet_sql_store.enabled',lambda:True)
    def fake_init(arrangement,initial,parent):
        state['cash']=state['available']=initial
        state['held']=0;state['protected']=30000;state['auths']={};state['denied']=[]
        return {}
    monkeypatch.setattr(ledger,'initialize',fake_init)
    monkeypatch.setattr(w4,'seed_trip',lambda *args: fake_overview('ignored'))
    def fake_overview(_):
        return {'arrangement_id':a._parent['id'],'purpose':'TRIP','protected_cents':state['protected'],
                'yield_enabled':False,'version':1,'free_to_spend_cents':max(0,state['available']-state['protected']),
                'balance':{'cash_cents':state['cash'],'available_cents':state['available'],'held_cents':state['held'],
                           'authorizations':[{'reference':k,'status':'AUTHORIZED','amount_cents':v}
                                             for k,v in state['auths'].items()], 'journal_lines':[]},
                'decisions':state['denied'],'releases':[]}
    monkeypatch.setattr(w4,'overview',fake_overview)
    monkeypatch.setattr('occlet_lithic_events.event_history',lambda _:[])
    def fake_release(arr,amount,ref):
        assert amount<=state['protected']
        state['protected']-=amount
        return fake_overview(arr)
    monkeypatch.setattr(w4,'release_protection',fake_release)
    monkeypatch.setattr(ledger,'summary',lambda _:fake_overview('ignored')['balance'])
    monkeypatch.setattr(ledger,'monthly_used',lambda _:sum(state['auths'].values()))
    def fake_auth(arr,ref,amt,*,card_reference,merchant_id,max_transaction_cents,monthly_limit_cents):
        if ref in state['auths']:
            assert state['auths'][ref]==amt
            return {'idempotent':True}
        reason=w4.rule(amt,state['available'],state['protected'],False)
        if reason:
            state['denied'].append({'decision':'DECLINED','reason':reason,'amount_cents':amt,'reference':ref})
            raise ledger.LedgerRejected(reason)
        state['available']-=amt;state['held']+=amt;state['auths'][ref]=amt
        return {'idempotent':False}
    monkeypatch.setattr(ledger,'authorize',fake_auth)
    # Disable automatic DB snapshot since the controlled in-memory test adapter has no PostgreSQL.
    monkeypatch.setattr('occlet_sql_store.persist_runtime',lambda **kwargs:None)
    return state


def test_w4_http_local_denial_release_retry_and_no_double_hold(monkeypatch):
    state=fake_w4(monkeypatch)
    r=ui.post('/api/use-cases/w4/setup')
    assert r.status_code==200,r.text
    assert r.json()['free_to_spend_cents']==20000
    r=ui.post('/api/use-cases/w4/authorize',json={'reference':'attempt-first-1','amount_cents':35000})
    assert r.status_code==200
    assert r.json()['decision']=='DECLINED'
    assert r.json()['reason']=='PROTECTED_BY_MEMBER'
    assert state['cash']==50000 and state['held']==0 and state['available']==50000
    r=ui.post('/api/use-cases/w4/release',json={'release_cents':15000,'release_ref':'member-release-1'})
    assert r.status_code==200 and state['protected']==15000
    assert state['available']==50000 and state['held']==0
    req={'reference':'attempt-after-release','amount_cents':35000}
    a1=ui.post('/api/use-cases/w4/authorize',json=req)
    assert a1.status_code==200 and a1.json()['decision']=='APPROVED'
    assert state['available']==15000 and state['held']==35000 and state['cash']==50000
    a2=ui.post('/api/use-cases/w4/authorize',json=req)
    assert a2.status_code==200 and state['held']==35000
    assert len(state['auths'])==1


def test_w4_lithic_parent_asa_policy_uses_same_backend(monkeypatch):
    state=fake_w4(monkeypatch)
    assert ui.post('/api/use-cases/w4/setup').status_code==200
    parent_card=str(uuid.uuid4())
    with a._lock:
        a._parent['lithic_parent_card_token']=parent_card
    x=asa(parent_card,35000)
    r=a.decide_lithic_asa(x)
    assert r['result']=='UNAUTHORIZED_MERCHANT'
    assert state['held']==0
    assert a._lithic_refs[x['token']]['reason']=='PROTECTED_BY_MEMBER'
    assert ui.post('/api/use-cases/w4/release',json={'release_cents':15000,'release_ref':'release-permission'}).status_code==200
    x2=asa(parent_card,35000)
    assert a.decide_lithic_asa(x2)['result']=='APPROVED'
    assert a.decide_lithic_asa(x2)['result']=='APPROVED'
    assert state['held']==35000


def test_events_normalization_and_fail_closed_for_unsupported_cases():
    tx=str(uuid.uuid4());card=str(uuid.uuid4());account=str(uuid.uuid4())
    def envelope(status,amount=0,currency='USD',polarity='DEBIT'):
        return {'event_type':'card_transaction.updated','payload':{
            'event_type':'card_transaction.updated','token':tx,'account_token':account,
            'card_token':card,'status':status,'result':'APPROVED',
            'amounts':{'settlement':{'amount':amount,'currency':currency}},
            'events':[{'type':'CLEARING','effective_polarity':polarity}]}}
    e=events.normalize_event(envelope('SETTLED',-34500))
    assert e['settlement_cents']==34500
    assert events.action_for(e)=='CLEAR'
    assert events.action_for(events.normalize_event(envelope('PENDING'))) =='CONFIRM'
    assert events.action_for(events.normalize_event(envelope('VOIDED'))) =='RELEASE'
    assert events.action_for(events.normalize_event(envelope('EXPIRED'))) =='RELEASE'
    with pytest.raises(ledger.LedgerRejected):
        events.normalize_event(envelope('SETTLED',-34500,'EUR'))
    with pytest.raises(ledger.LedgerRejected):
        events.normalize_event({'event_type':'not_a_card_transaction'})


def test_signed_event_bridge_rejects_missing_signature_and_forwards_only_signed(monkeypatch):
    import httpx
    secret='whsec_'+base64.b64encode(secrets.token_bytes(32)).decode()
    monkeypatch.setenv('LITHIC_EVENT_SECRET',secret)
    monkeypatch.setenv('OCCLET_BRIDGE_SECRET','private-bridge')
    payload={'event_type':'card_transaction.updated','payload':{
       'token':str(uuid.uuid4()),'card_token':str(uuid.uuid4()),'status':'PENDING','amounts':{}}}
    web=TestClient(bridge.app)
    assert web.post('/lithic/events',json=payload).status_code==401
    raw=json.dumps(payload).encode()
    msg_id='msg_'+uuid.uuid4().hex
    stamp=str(int(time.time()))
    signing=f'{msg_id}.{stamp}.'.encode()+raw
    sig=base64.b64encode(hmac.new(base64.b64decode(secret[6:]),signing,hashlib.sha256).digest()).decode()
    headers={'webhook-id':msg_id,'webhook-timestamp':stamp,'webhook-signature':'v1,'+sig,
             'content-type':'application/json'}
    class FakeHttpClient:
        def __init__(self,**kwargs):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def post(self,url,headers,json):
            assert url=='http://127.0.0.1:8000/api/internal/lithic/event'
            assert headers['X-Occlet-Bridge-Secret']=='private-bridge'
            assert json['webhook_id']==msg_id and json['event']==payload
            return httpx.Response(200,json={'action':'CONFIRM'},request=httpx.Request('POST',url))
    monkeypatch.setattr(httpx,'AsyncClient',FakeHttpClient)
    ok=web.post('/lithic/events',content=raw,headers=headers)
    assert ok.status_code==200 and ok.json()=={'received':True,'action':'CONFIRM'}
    tampered=web.post('/lithic/events',content=raw+b' ',headers=headers)
    assert tampered.status_code==401


def test_private_event_sink_rejects_external_and_bad_secret(monkeypatch):
    monkeypatch.setenv('OCCLET_BRIDGE_SECRET','private-secret')
    request={'webhook_id':'msg_abc12345','event':{'event_type':'card_transaction.updated'}}
    assert ui.post('/api/internal/lithic/event',json=request).status_code==403
    monkeypatch.setenv('OCCLET_ALLOW_TESTCLIENT','1')
    assert ui.post('/api/internal/lithic/event',json=request,headers={'X-Occlet-Bridge-Secret':'wrong'}).status_code==403


def test_event_inbox_deduplicates_clearing_and_never_reauthorizes(monkeypatch):
    # Isolated fake SQL event inbox: exercises exact reducer behavior without Docker.
    seen={};ledger_effects=[]
    monkeypatch.setattr(events,'enabled',lambda:True)
    class Cursor:
        def __init__(self,value=None):self.value=value
        def fetchone(self):return self.value
    class DB:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def execute(self,sql,params):
            if sql.strip().startswith('SELECT'):
                row=seen.get(params[0])
                return Cursor((row['tx'],row['status'],row['processed']) if row else None)
            if sql.strip().startswith('INSERT'):
                eid,arr,tx,status=params
                seen[eid]={'tx':tx,'status':status,'processed':'RECEIVED'}
                return Cursor()
            if sql.strip().startswith('UPDATE'):
                action,eid=params
                seen[eid]['processed']='PROCESSED'
                return Cursor()
            raise AssertionError('Unknown SQL '+sql)
    monkeypatch.setattr(events,'_connect',lambda:DB())
    def mock_clear(arr,ref,amount):
        ledger_effects.append(('CLEAR',arr,ref,amount))
        return {'status':'CLEARED','idempotent':False}
    def mock_release(arr,ref):
        ledger_effects.append(('RELEASE',arr,ref))
        return {'status':'RELEASED','idempotent':False}
    monkeypatch.setattr(events,'clear',mock_clear)
    monkeypatch.setattr(events,'release',mock_release)
    tx=str(uuid.uuid4());card=str(uuid.uuid4());acc=str(uuid.uuid4())
    event={'event_type':'card_transaction.updated','payload':{'token':tx,'card_token':card,'account_token':acc,
      'status':'SETTLED','result':'APPROVED','amounts':{'settlement':{'amount':-34500,'currency':'USD'}},
      'events':[{'type':'CLEARING','effective_polarity':'DEBIT'}]}}
    asa_ref={'card_token':card,'amount':35000,'result':'APPROVED'}
    kwargs={'arrangement_id':'arr_demo','event_id':'msg_123456','envelope':event,
        'asa_reference':asa_ref,'expected_account':acc}
    r1=events.process_signed_event(**kwargs)
    r2=events.process_signed_event(**kwargs)
    assert r1['action']=='CLEAR' and r1['idempotent'] is False
    assert r2['status']=='ALREADY_PROCESSED'
    assert ledger_effects==[('CLEAR','arr_demo','lithic:'+tx,34500)]
    with pytest.raises(ledger.LedgerRejected,match='ACCOUNT_TOKEN_MISMATCH'):
        events.process_signed_event(**{**kwargs,'event_id':'msg_second','expected_account':str(uuid.uuid4())})
    with pytest.raises(ledger.LedgerRejected,match='UNKNOWN_OR_MISMATCHED_ASA_AUTHORIZATION'):
        events.process_signed_event(**{**kwargs,'event_id':'msg_third','asa_reference':{'result':'APPROVED','card_token':str(uuid.uuid4())}})
    too_much=json.loads(json.dumps(event))
    too_much['payload']['amounts']['settlement']['amount']=-35001
    with pytest.raises(ledger.LedgerRejected,match='SETTLEMENT_ABOVE_HOLD'):
        events.process_signed_event(**{**kwargs,'event_id':'msg_fourth','envelope':too_much})
    assert len(ledger_effects)==1


def test_w4_schema_exposes_policy_only_protection_and_signed_event_inbox():
    from pathlib import Path
    sql=(Path(__file__).parents[1]/'db'/'003_w4_protection_and_events.sql').read_text()
    assert 'demo_w4_protections' in sql
    assert 'demo_w4_releases' in sql
    assert 'demo_w4_decisions' in sql
    assert 'demo_lithic_event_inbox' in sql
    assert 'PRIMARY KEY (arrangement_id,release_ref)' in sql
    assert 'event_id TEXT PRIMARY KEY' in sql

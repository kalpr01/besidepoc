import base64
import time
import uuid

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
from fastapi.testclient import TestClient
from app import app

client=TestClient(app)


def setup_demo():
    client.post('/api/demo/reset')
    private=Ed25519PrivateKey.generate()
    pub=private.public_key().public_bytes(encoding=serialization.Encoding.Raw,format=serialization.PublicFormat.Raw)
    agent='agent-test'
    assert client.post('/api/agents',json={'agent_id':agent,'name':'Test agent','public_key_b64':base64.b64encode(pub).decode()}).status_code==200
    card=client.post('/api/member/cards',json={'agent_id':agent,'per_transaction_cents':12000,'budget_cents':30000,'approval_threshold_cents':8000,'allowed_merchants':['GROCERY_101']}).json()
    return private,agent,card


def signed(private,agent,card,amount=4900,merchant='GROCERY_101'):
    d={'agent_id':agent,'card_id':card['id'],'merchant_id':merchant,'amount_cents':amount,'currency':'USD','nonce':str(uuid.uuid4()),'timestamp_ms':int(time.time()*1000)}
    msg='|'.join(map(str,[d['agent_id'],d['card_id'],d['merchant_id'],d['amount_cents'],d['currency'],d['nonce'],d['timestamp_ms']]))
    d['signature_b64']=base64.b64encode(private.sign(msg.encode())).decode()
    return d


def test_success_and_token_reuse_denied():
    private,agent,card=setup_demo()
    body=signed(private,agent,card)
    r=client.post('/api/agent/intents',json=body)
    assert r.status_code==200
    intent=r.json()
    assert intent['status']=='READY'
    # Agent request replay cannot pass even with valid signature.
    assert client.post('/api/agent/intents',json=body).status_code==409
    p={'payment_token':intent['payment_token'],'merchant_id':'GROCERY_101','amount_cents':4900,'network_reference':'rrn-001-unique'}
    wrong={**p,'merchant_id':'ELECTRONICS_202','network_reference':'rrn-002-unique'}
    assert client.post('/api/network/authorize',json=wrong).status_code==403
    approved=client.post('/api/network/authorize',json=p)
    assert approved.status_code==200
    assert approved.json()['matched_intent_id']==intent['intent_id']
    # Same network message is idempotent; new network ref using old handle is rejected.
    assert client.post('/api/network/authorize',json=p).json()==approved.json()
    assert client.post('/api/network/authorize',json={**p,'network_reference':'rrn-003-unique'}).status_code==403
    state=client.get('/api/state').json()
    assert state['cards'][0]['spent_cents']==4900


def test_policy_signature_stepup_and_revoke():
    private,agent,card=setup_demo()
    assert client.post('/api/agent/intents',json=signed(private,agent,card,amount=15000)).status_code==403
    assert client.post('/api/agent/intents',json=signed(private,agent,card,merchant='ELECTRONICS_202')).status_code==403
    tampered=signed(private,agent,card)
    tampered['amount_cents']=4950
    assert client.post('/api/agent/intents',json=tampered).status_code==401
    r=client.post('/api/agent/intents',json=signed(private,agent,card,amount=9500)).json()
    assert r['status']=='AWAITING_MEMBER_APPROVAL'
    assert 'payment_token' not in r
    approved=client.post('/api/member/intents/'+r['intent_id']+'/approval',json={'approved':True}).json()
    assert approved['status']=='READY'
    assert 'payment_token' in approved
    assert client.post('/api/member/cards/'+card['id']+'/revoke').status_code==200
    network={'payment_token':approved['payment_token'],'merchant_id':'GROCERY_101','amount_cents':9500,'network_reference':'rrn-004-unique'}
    assert client.post('/api/network/authorize',json=network).status_code==403


def test_budget_reservation_prevents_overcommit():
    private,agent,card=setup_demo()
    # Three READY $80 transactions reserve $240; fourth exceeds $300.
    for i in range(3):
        assert client.post('/api/agent/intents',json=signed(private,agent,card,amount=8000)).status_code==200
    assert client.post('/api/agent/intents',json=signed(private,agent,card,amount=8000)).status_code==403
    s=client.get('/api/state').json()['cards'][0]
    assert s['reserved_cents']==24000
    assert s['remaining_cents']==6000

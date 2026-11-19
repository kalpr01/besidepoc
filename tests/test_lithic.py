import base64
import hashlib
import hmac
import json
import os
import secrets
import time
import uuid

from fastapi.testclient import TestClient
from app import app, decide_lithic_asa, _lock, _cards
from lithic_bridge import app as bridge, verify_lithic_signature
from test_flow import setup_demo, signed

local = TestClient(app)


def asa(card_token, amount=4900, merchant_id='GROCERY101', tx=None):
    return {
        'event_type': 'card_authorization.approval_request',
        'status': 'AUTHORIZATION',
        'token': tx or str(uuid.uuid4()),
        'card': {'token': card_token, 'type': 'VIRTUAL', 'state': 'OPEN'},
        'merchant': {'acceptor_id': merchant_id, 'mcc': '5411'},
        'amounts': {'cardholder': {'amount': amount, 'currency': 'USD'}},
        'transaction_initiator': 'CARDHOLDER',
    }


def bind(card, token):
    with _lock:
        _cards[card['id']]['lithic_card_token'] = token


def test_asa_requires_signed_intent_and_matches_exactly():
    private, agent, card = setup_demo()
    card_token = str(uuid.uuid4())
    bind(card, card_token)
    assert decide_lithic_asa(asa(card_token, 4900))['result'] == 'UNAUTHORIZED_MERCHANT'
    intent = local.post('/api/agent/intents', json=signed(private, agent, card, 4900)).json()
    assert intent['status'] == 'READY'
    assert decide_lithic_asa(asa(card_token, 5100))['result'] == 'UNAUTHORIZED_MERCHANT'
    assert decide_lithic_asa(asa(card_token, 4900, 'ELECTRONICS202'))['result'] == 'UNAUTHORIZED_MERCHANT'
    transaction = asa(card_token, 4900)
    assert decide_lithic_asa(transaction)['result'] == 'APPROVED'
    assert decide_lithic_asa(transaction)['result'] == 'APPROVED'  # retry, no double-spend
    assert decide_lithic_asa(asa(card_token, 4900))['result'] == 'UNAUTHORIZED_MERCHANT'
    state = local.get('/api/state').json()
    assert state['cards'][0]['spent_cents'] == 4900
    assert state['intents'][0]['status'] == 'AUTHORIZED'


def test_asa_rejects_ambiguous_ready_intents_and_no_member_step_up():
    private, agent, card = setup_demo()
    token = str(uuid.uuid4())
    bind(card, token)
    for _ in range(2):
        assert local.post('/api/agent/intents', json=signed(private, agent, card, 4900)).status_code == 200
    assert decide_lithic_asa(asa(token, 4900))['result'] == 'UNAUTHORIZED_MERCHANT'
    r = local.post('/api/agent/intents', json=signed(private, agent, card, 9500)).json()
    assert r['status'] == 'AWAITING_MEMBER_APPROVAL'
    assert decide_lithic_asa(asa(token, 9500))['result'] == 'UNAUTHORIZED_MERCHANT'


def test_asa_unrecognized_transaction_shapes_fail_closed():
    private, agent, card = setup_demo()
    token = str(uuid.uuid4())
    bind(card, token)
    local.post('/api/agent/intents', json=signed(private, agent, card, 4900))
    x = asa(token)
    x['amounts']['cardholder']['currency'] = 'EUR'
    assert decide_lithic_asa(x)['result'] != 'APPROVED'
    x = asa(token)
    x['status'] = 'CREDIT_AUTHORIZATION'
    assert decide_lithic_asa(x)['result'] != 'APPROVED'
    x = asa(token)
    x['amounts']['cardholder'].pop('currency')
    assert decide_lithic_asa(x)['result'] != 'APPROVED'


def test_internal_endpoint_requires_loopback_secret(monkeypatch):
    monkeypatch.setenv('OCCLET_BRIDGE_SECRET', 'some-test-secret')
    private, agent, card = setup_demo()
    token = str(uuid.uuid4())
    bind(card, token)
    local.post('/api/agent/intents', json=signed(private, agent, card, 4900))
    request = {'webhook_id': str(uuid.uuid4()), 'authorization': asa(token)}
    denied = local.post('/api/internal/lithic/authorize', json=request)
    assert denied.status_code == 403
    monkeypatch.setenv('OCCLET_ALLOW_TESTCLIENT', '1')
    loopback = TestClient(app, base_url='http://127.0.0.1:8000')
    valid = loopback.post('/api/internal/lithic/authorize', json=request,
                          headers={'X-Occlet-Bridge-Secret': 'some-test-secret'})
    assert valid.status_code == 200, valid.text
    assert valid.json()['result'] == 'APPROVED'


def signed_headers(body: bytes, secret: str):
    message_id = str(uuid.uuid4())
    timestamp = str(int(time.time()))
    raw_key = base64.b64decode(secret.removeprefix('whsec_'))
    sig = base64.b64encode(hmac.new(raw_key, f'{message_id}.{timestamp}.'.encode()+body, hashlib.sha256).digest()).decode()
    return {'webhook-id': message_id, 'webhook-timestamp': timestamp, 'webhook-signature': f'v1,{sig}'}


def test_lithic_hmac_verification_and_bad_timestamp():
    secret = 'whsec_' + base64.b64encode(secrets.token_bytes(32)).decode()
    body = b'{"status":"AUTHORIZATION"}'
    h = signed_headers(body, secret)
    assert verify_lithic_signature(body, h, secret) == h['webhook-id']
    from pytest import raises
    with raises(ValueError):
        verify_lithic_signature(body+b' ', h, secret)
    with raises(ValueError):
        verify_lithic_signature(body, {**h, 'webhook-timestamp': '1234'}, secret)


def test_public_bridge_rejects_unsigned_and_has_no_member_routes(monkeypatch):
    secret = 'whsec_' + base64.b64encode(secrets.token_bytes(32)).decode()
    monkeypatch.setenv('LITHIC_ASA_SECRET', secret)
    monkeypatch.setenv('OCCLET_BRIDGE_SECRET', 'some-test-secret')
    web = TestClient(bridge)
    assert web.get('/api/state').status_code == 404
    assert web.post('/lithic/asa', json=asa(str(uuid.uuid4()))).status_code == 401


def test_bridge_signed_request_returns_authenticated_policy_decision(monkeypatch):
    secret = 'whsec_' + base64.b64encode(secrets.token_bytes(32)).decode()
    monkeypatch.setenv('LITHIC_ASA_SECRET', secret)
    monkeypatch.setenv('OCCLET_BRIDGE_SECRET', 'internal-bridge-secret')
    message = asa(str(uuid.uuid4()))
    raw = json.dumps(message, separators=(',', ':')).encode()
    headers = signed_headers(raw, secret)

    import httpx
    class FakeHttpClient:
        def __init__(self, **kwargs):
            assert kwargs['timeout'] <= 2.0
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def post(self, url, headers, json):
            assert url == 'http://127.0.0.1:8000/api/internal/lithic/authorize'
            assert headers['X-Occlet-Bridge-Secret'] == 'internal-bridge-secret'
            assert json['authorization'] == message
            return httpx.Response(200, json={'result':'APPROVED'}, request=httpx.Request('POST',url))

    monkeypatch.setattr(httpx, 'AsyncClient', FakeHttpClient)
    web = TestClient(bridge)
    result = web.post('/lithic/asa', data=raw, headers={'Content-Type':'application/json', **headers})
    assert result.status_code == 200, result.text
    assert result.json() == {'result': 'APPROVED'}

"""Offline tests: real Capital One credentials are never accepted, transmitted or stored."""
import base64
import time
import uuid
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
from fastapi.testclient import TestClient
from app import app

client = TestClient(app)


def setup():
    client.post('/api/demo/reset', json={})
    sk = Ed25519PrivateKey.generate()
    key = sk.public_key().public_bytes(encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)
    assert client.post('/api/agents', json={'agent_id': 'shopping-agent-1', 'name': 'Shopping agent', 'public_key_b64': base64.b64encode(key).decode()}).status_code == 200
    card = client.post('/api/member/cards', json={'agent_id': 'shopping-agent-1', 'per_transaction_cents': 2500, 'budget_cents': 10000, 'approval_threshold_cents': 2399, 'allowed_merchants': ['GROCERY_101']}).json()
    assert client.post('/api/member/cards/' + card['id'] + '/capitalone/link', json={'merchant_id': 'GROCERY_101', 'checkout_url': 'https://www.target.com/', 'last_four': '9876'}).status_code == 200
    return sk, card


def signed(sk, card, amount, merchant='GROCERY_101'):
    data = {'agent_id': 'shopping-agent-1', 'card_id': card['id'], 'merchant_id': merchant, 'amount_cents': amount, 'currency': 'USD', 'nonce': str(uuid.uuid4()), 'timestamp_ms': int(time.time() * 1000)}
    msg = '|'.join(map(str, [data[x] for x in ('agent_id','card_id','merchant_id','amount_cents','currency','nonce','timestamp_ms')]))
    data['signature_b64'] = base64.b64encode(sk.sign(msg.encode())).decode()
    return data


def handoff(response):
    d = response.json()
    return client.post('/api/external/checkout/handoff', json={'intent_id': d['intent_id'], 'payment_token': d.get('payment_token', 'missing')})


def test_2399_release_once_no_card_secrets():
    sk, card = setup()
    issued = client.post('/api/agent/intents', json=signed(sk, card, 2399))
    assert issued.status_code == 200 and issued.json()['status'] == 'READY'
    result = handoff(issued)
    assert result.status_code == 200
    r = result.json()
    assert r['decision'] == 'OCCLET_GATE_APPROVED'
    assert r['issuer_authorized'] is False
    assert r['checkout_url'] == 'https://www.target.com/'
    assert r['card_last_four'] == '9876'
    assert handoff(issued).status_code == 403  # single handoff
    state = client.get('/api/state').json()
    assert 'card_number' not in str(state).lower()
    assert 'cvv' not in str(state).lower()


def test_over_25_blocked_before_checkout():
    sk, card = setup()
    denied = client.post('/api/agent/intents', json=signed(sk, card, 2501))
    assert denied.status_code == 403
    assert not any(i['amount_cents'] == 2501 for i in client.get('/api/state').json()['intents'])


def test_step_up_requires_member_approval():
    sk, card = setup()
    pending = client.post('/api/agent/intents', json=signed(sk, card, 2450))
    assert pending.status_code == 200 and pending.json()['status'] == 'AWAITING_MEMBER_APPROVAL'
    assert handoff(pending).status_code == 403
    granted = client.post('/api/member/intents/' + pending.json()['intent_id'] + '/approval', json={'approved': True})
    assert granted.status_code == 200
    assert handoff(granted).status_code == 200


def test_unregistered_agent_or_invalid_signature_declined():
    sk, card = setup()
    rogue = signed(sk, card, 1000)
    rogue['agent_id'] = 'unknown-agent'
    assert client.post('/api/agent/intents', json=rogue).status_code == 401
    altered = signed(sk, card, 1000)
    altered['amount_cents'] = 2300
    assert client.post('/api/agent/intents', json=altered).status_code == 401


def test_merchant_domain_cannot_be_substituted_and_revoke_blocks():
    sk, card = setup()
    invalid = client.post('/api/member/cards/' + card['id'] + '/capitalone/link', json={'merchant_id': 'GROCERY_101', 'checkout_url': 'https://target.com.evil.example/', 'last_four': '9876'})
    assert invalid.status_code == 400
    authorized = client.post('/api/agent/intents', json=signed(sk, card, 2300))
    assert authorized.status_code == 200
    assert client.post('/api/member/cards/' + card['id'] + '/revoke', json={}).status_code == 200
    assert handoff(authorized).status_code == 403


def test_wrong_token_denied():
    sk, card = setup()
    authorized = client.post('/api/agent/intents', json=signed(sk, card, 2300)).json()
    assert client.post('/api/external/checkout/handoff', json={'intent_id': authorized['intent_id'], 'payment_token': 'other-test-token'}).status_code == 403


def test_backend_policy_stays_strict_even_if_member_issues_loose_demo_card():
    sk, card = setup()
    # Deliberately relax the original card limits after registration, to show
    # Capital One's external policy is not just a UI preset.
    from app import _cards
    _cards[card['id']]['per_transaction_cents'] = 12000
    _cards[card['id']]['approval_threshold_cents'] = 8000
    assert client.post('/api/agent/intents', json=signed(sk, card, 2501)).status_code == 403
    pending = client.post('/api/agent/intents', json=signed(sk, card, 2400))
    assert pending.status_code == 200
    assert pending.json()['status'] == 'AWAITING_MEMBER_APPROVAL'
    assert handoff(pending).status_code == 403

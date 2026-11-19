"""Over-threshold purchases from either flow surface in Beside → Member actions."""
import base64
import time

from fastapi.testclient import TestClient

from app import app, decide_lithic_asa
from test_dual_flows import setup_scoped, bind, asa_for

client = TestClient(app)


def pending():
    return client.get('/api/member/experience').json()['pending_approvals']


def test_flow_b_over_threshold_requests_member_approval_then_retry_approves():
    key, agent, card, merchant = setup_scoped('GROCERIES', 'CARD_BOUND_NETWORK')
    attempt = {'card_reference': card['id'], 'merchant_id': merchant, 'amount_cents': 4000}
    first = client.post('/api/network/direct/authorize', json={**attempt, 'network_reference': 'rrn-approval-a1'}).json()
    assert first['decision'] == 'DECLINED' and first['reason'] == 'MEMBER_APPROVAL_REQUIRED_BEFORE_PURCHASE'
    req_id = first['approval_request_id']
    [req] = pending()
    assert req['id'] == req_id and req['amount_cents'] == 4000 and req['card_id'] == card['id']
    # a second attempt before approval reuses the same request
    again = client.post('/api/network/direct/authorize', json={**attempt, 'network_reference': 'rrn-approval-a2'}).json()
    assert again['approval_request_id'] == req_id and len(pending()) == 1
    assert client.get(f'/api/member/approvals/{req_id}').json()['status'] == 'AWAITING_MEMBER_APPROVAL'
    assert client.post(f'/api/member/intents/{req_id}/approval', json={'approved': True}).status_code == 200
    assert pending() == []
    ok = client.post('/api/network/direct/authorize', json={**attempt, 'network_reference': 'rrn-approval-a3'}).json()
    assert ok['decision'] == 'APPROVED', ok
    assert client.get(f'/api/member/approvals/{req_id}').json()['status'] == 'USED'
    # the approval is one-time: the next attempt asks again
    nxt = client.post('/api/network/direct/authorize', json={**attempt, 'network_reference': 'rrn-approval-a4'}).json()
    assert nxt['reason'] == 'MEMBER_APPROVAL_REQUIRED_BEFORE_PURCHASE' and nxt['approval_request_id'] != req_id


def test_flow_b_lithic_over_threshold_requests_approval_and_retry_approves():
    key, agent, card, merchant = setup_scoped('TICKETS', 'CARD_BOUND_NETWORK')
    token = bind(card)
    assert decide_lithic_asa(asa_for(token, 4000, merchant))['result'] != 'APPROVED'
    [req] = pending()
    client.post(f"/api/member/intents/{req['id']}/approval", json={'approved': True})
    assert decide_lithic_asa(asa_for(token, 4000, merchant))['result'] == 'APPROVED'


def test_flow_b_member_decline_is_final():
    key, agent, card, merchant = setup_scoped('GROCERIES', 'CARD_BOUND_NETWORK')
    r = client.post('/api/network/direct/authorize', json={'card_reference': card['id'], 'merchant_id': merchant,
                                                           'amount_cents': 4000, 'network_reference': 'rrn-approval-d1'}).json()
    client.post(f"/api/member/intents/{r['approval_request_id']}/approval", json={'approved': False})
    assert client.get(f"/api/member/approvals/{r['approval_request_id']}").json()['status'] == 'REJECTED'


def _sign(key, text):
    return base64.b64encode(key.sign(text.encode())).decode()


def test_flow_a_intent_waits_for_member_and_agent_polls_signed_status():
    key, agent, card, merchant = setup_scoped('GROCERIES')
    nonce, ts = 'nonce-' + 'x' * 12, int(time.time() * 1000)
    canonical = f"{agent}|{card['id']}|{merchant}|4000|USD|{nonce}|{ts}"
    intent = client.post('/api/agent/intents', json={'agent_id': agent, 'card_id': card['id'], 'merchant_id': merchant,
        'amount_cents': 4000, 'currency': 'USD', 'nonce': nonce, 'timestamp_ms': ts,
        'signature_b64': _sign(key, canonical)}).json()
    assert intent['status'] == 'AWAITING_MEMBER_APPROVAL' and intent['expires_in_seconds'] == 600
    iid = intent['intent_id']
    assert [p['id'] for p in pending()] == [iid]

    def poll():
        t = int(time.time() * 1000)
        return client.post(f'/api/agent/intents/{iid}/status',
                           json={'agent_id': agent, 'timestamp_ms': t, 'signature_b64': _sign(key, f'status|{iid}|{t}')})
    first = poll().json()
    assert first['status'] == 'AWAITING_MEMBER_APPROVAL' and 'payment_token' not in first
    bad = client.post(f'/api/agent/intents/{iid}/status', json={'agent_id': agent, 'timestamp_ms': int(time.time() * 1000),
                                                                'signature_b64': _sign(key, 'status|other|1')})
    assert bad.status_code == 401
    client.post(f'/api/member/intents/{iid}/approval', json={'approved': True})
    ready = poll().json()
    assert ready['status'] == 'READY' and ready['payment_token']
    r = client.post('/api/network/authorize', json={'payment_token': ready['payment_token'], 'merchant_id': merchant,
                                                     'amount_cents': 4000, 'network_reference': 'rrn-approval-fa'}).json()
    assert r['decision'] == 'APPROVED', r
    # approval status endpoint never leaks the token
    assert 'payment_token' not in client.get(f'/api/member/approvals/{iid}').json()

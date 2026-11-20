"""Over-threshold purchases from either flow surface in Beside → Member actions."""
import base64
import time

from fastapi.testclient import TestClient

from app import app, decide_lithic_asa
from test_dual_flows import setup_scoped, bind, asa_for

client = TestClient(app)


def pending():
    return client.get('/api/member/experience').json()['pending_approvals']


def test_flow_b_over_threshold_requests_member_approval_and_approval_resends_purchase():
    key, agent, card, merchant = setup_scoped('GROCERIES', 'CARD_BOUND_NETWORK')
    attempt = {'card_reference': card['id'], 'merchant_id': merchant, 'amount_cents': 4000}
    first = client.post('/api/network/direct/authorize', json={**attempt, 'network_reference': 'rrn-approval-a1'}).json()
    assert first['decision'] == 'DECLINED' and first['reason'] == 'MEMBER_APPROVAL_REQUIRED_BEFORE_PURCHASE'
    req_id = first['approval_request_id']
    [req] = pending()
    assert req['id'] == req_id and req['amount_cents'] == 4000 and req['card_id'] == card['id']
    again = client.post('/api/network/direct/authorize', json={**attempt, 'network_reference': 'rrn-approval-a2'}).json()
    assert again['approval_request_id'] == req_id and len(pending()) == 1
    held_before = client.get('/api/state').json()['parent']['held_cents']
    r = client.post(f'/api/member/intents/{req_id}/approval', json={'approved': True}).json()
    # approving re-sends the purchase once, on its original (local) route
    assert r['retry'] == {'route': 'LOCAL', 'decision': 'APPROVED', 'reason': 'POLICY_AND_FUNDING_OK'} and r['status'] == 'USED'
    assert 'payment_token' not in r
    assert client.get('/api/state').json()['parent']['held_cents'] == held_before + 4000
    assert pending() == []
    nxt = client.post('/api/network/direct/authorize', json={**attempt, 'network_reference': 'rrn-approval-a4'}).json()
    assert nxt['reason'] == 'MEMBER_APPROVAL_REQUIRED_BEFORE_PURCHASE' and nxt['approval_request_id'] != req_id


def test_flow_b_lithic_approval_resends_to_lithic_and_asa_approves(monkeypatch):
    import lithic_client
    key, agent, card, merchant = setup_scoped('TICKETS', 'CARD_BOUND_NETWORK')
    token = bind(card)
    assert decide_lithic_asa(asa_for(token, 4000, merchant))['result'] != 'APPROVED'
    [req] = pending()
    sent = []
    def fake_sim(card_token, amount, merchant_id):  # Lithic calls the ASA synchronously
        sent.append((card_token, amount, merchant_id))
        return {'token': 'tx-retry', 'result': decide_lithic_asa(asa_for(card_token, amount, merchant_id))['result']}
    monkeypatch.setattr(lithic_client, 'simulate_auth', fake_sim)
    r = client.post(f"/api/member/intents/{req['id']}/approval", json={'approved': True}).json()
    assert sent == [(token, 4000, merchant)]
    assert r['retry'] == {'route': 'LITHIC', 'transaction_token': 'tx-retry'} and r['status'] == 'USED'
    assert client.get('/api/state').json()['parent']['held_cents'] == 4000


def test_flow_b_lithic_resend_failure_leaves_approval_ready(monkeypatch):
    import lithic_client
    key, agent, card, merchant = setup_scoped('TICKETS', 'CARD_BOUND_NETWORK')
    token = bind(card)
    decide_lithic_asa(asa_for(token, 4000, merchant))
    [req] = pending()
    def boom(*a):
        raise lithic_client.LithicError('sandbox down')
    monkeypatch.setattr(lithic_client, 'simulate_auth', boom)
    r = client.post(f"/api/member/intents/{req['id']}/approval", json={'approved': True}).json()
    assert r['retry']['error'] and r['status'] == 'READY'
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


def test_declined_lithic_event_is_acknowledged_without_ledger_effect(monkeypatch):
    import app as demo
    monkeypatch.setenv('OCCLET_BRIDGE_SECRET', 'private-secret')
    monkeypatch.setenv('OCCLET_ALLOW_TESTCLIENT', '1')
    with demo._lock:
        demo._parent.update(ledger_mode='OCCLET_SQL_LEDGER', id='arr_evt')
        demo._lithic_refs['tx-declined'] = {'card_token': 'c1', 'result': 'UNAUTHORIZED_MERCHANT'}
    called = []
    monkeypatch.setattr(demo, 'process_signed_event', lambda **kw: called.append(kw), raising=False)
    import occlet_lithic_events
    monkeypatch.setattr(occlet_lithic_events, 'process_signed_event', lambda **kw: called.append(kw))
    event = {'event_type': 'card_transaction.updated',
             'payload': {'token': 'tx-declined', 'card_token': 'c1', 'status': 'DECLINED'}}
    r = client.post('/api/internal/lithic/event', json={'webhook_id': 'msg_declined1', 'event': event},
                    headers={'X-Occlet-Bridge-Secret': 'private-secret'})
    assert r.status_code == 200 and r.json()['status'] == 'IGNORED' and called == []


def test_experience_lists_intents_for_visa_activity_without_tokens():
    key, agent, card, merchant = setup_scoped('GROCERIES', 'CARD_BOUND_NETWORK')
    r = client.post('/api/network/direct/authorize', json={'card_reference': card['id'], 'merchant_id': merchant,
                                                           'amount_cents': 4000, 'network_reference': 'rrn-visa-act1'}).json()
    client.post(f"/api/member/intents/{r['approval_request_id']}/approval", json={'approved': True})
    view = client.get('/api/member/experience').json()
    [row] = view['intents']
    assert row['kind'] == 'MEMBER_APPROVAL_REQUEST' and row['status'] == 'USED' and row['created_at']
    assert row['transaction_reference'] == 'local:approved-' + row['id']
    refs = {t['reference'] for t in view['transactions']}
    assert row['transaction_reference'] in refs | {'local:' + r for r in refs}
    assert 'payment_token' not in row and 'payment_token' not in str(view['intents'])
    html = client.get('/member/delegated').text
    assert 'renderOccletVisaActivity' in html and 'occard:' in html
    js = client.get('/member/assets/beside-delegated.js').text
    assert 'besideOccletActivity' in js and 'besideOpenDelegated' in js

"""Two distinct Q/Occlet delegated-card journeys using the existing Lithic ASA.

FLOW A: member-approved registered purpose -> signed transaction intent -> ASA.
FLOW B: member-approved registered purpose -> network ASA directly -> card-bound
        agent *attribution*, which is not proof of active agent authentication.
"""
import base64
import uuid

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from app import app, decide_lithic_asa, _lock, _cards
from test_flow import signed
from test_lithic import asa

client = TestClient(app)


def setup_scoped(purpose="GROCERIES", mode="SIGNED_INTENT_REQUIRED", *, cap=10000, budget=20000, auto=2500):
    client.post('/api/demo/reset', json={})
    assert client.post('/api/member/parent/configure', json={
        'credit_limit_cents': 100_000, 'max_transaction_cents': 30_000,
        'monthly_policy_limit_cents': 100_000}).status_code == 200
    key = Ed25519PrivateKey.generate()
    from cryptography.hazmat.primitives import serialization
    raw = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    agent = 'agent-' + uuid.uuid4().hex[:10]
    merchant = 'GROCERY_101' if purpose == 'GROCERIES' else 'TICKETS_303'
    r = client.post('/api/agents', json={
        'agent_id': agent, 'name': f'{purpose} Shopper', 'public_key_b64': base64.b64encode(raw).decode(),
        'purpose': purpose, 'purchase_goal': 'Buy groceries for family' if purpose == 'GROCERIES' else 'Buy concert tickets',
        'registered_per_transaction_cents': cap, 'registered_budget_cents': budget,
        'registered_approval_threshold_cents': auto})
    assert r.status_code == 200, r.text
    r = client.post('/api/member/cards', json={
        'agent_id': agent, 'per_transaction_cents': cap, 'budget_cents': budget,
        'approval_threshold_cents': auto, 'allowed_merchants': [merchant],
        'authorization_flow': mode})
    assert r.status_code == 200, r.text
    card = r.json()
    return key, agent, card, merchant


def bind(card):
    token = str(uuid.uuid4())
    with _lock:
        _cards[card['id']]['lithic_card_token'] = token
    return token


def asa_for(token, cents, merchant):
    acceptor = {'GROCERY_101':'GROCERY101', 'TICKETS_303':'TICKETS303', 'ELECTRONICS_202':'ELECTRONICS202'}[merchant]
    data = asa(token, cents, acceptor)
    data['merchant']['mcc'] = {'GROCERY_101':'5411', 'TICKETS_303':'7922', 'ELECTRONICS_202':'5732'}[merchant]
    return data


def test_member_purpose_registration_and_enforced_card_ceiling():
    key, agent, card, merchant = setup_scoped('TICKETS')
    state = client.get('/api/state').json()
    assert state['agents'][0]['purpose'] == 'TICKETS'
    assert 'concert tickets' in state['agents'][0]['purchase_goal']
    assert state['cards'][0]['purpose'] == 'TICKETS'
    assert state['cards'][0]['authorization_flow'] == 'SIGNED_INTENT_REQUIRED'
    r = client.post('/api/member/cards', json={
        'agent_id': agent, 'per_transaction_cents': 11000, 'budget_cents': 20000,
        'approval_threshold_cents': 2500, 'allowed_merchants': ['TICKETS_303']})
    assert r.status_code == 403
    r = client.post('/api/member/cards', json={
        'agent_id': agent, 'per_transaction_cents': 10000, 'budget_cents': 20000,
        'approval_threshold_cents': 2500, 'allowed_merchants': ['GROCERY_101']})
    assert r.status_code == 403


def test_flow_a_ticket_agent_signs_first_then_lithic_approves():
    key, agent, card, merchant = setup_scoped('TICKETS')
    token = bind(card)
    # A direct network attempt with no signed purchase intent MUST fail closed.
    assert decide_lithic_asa(asa_for(token, 2000, merchant))['result'] != 'APPROVED'
    r = client.post('/api/agent/intents', json=signed(key, agent, card, 2000, merchant=merchant))
    assert r.status_code == 200, r.text
    assert r.json()['status'] == 'READY'
    tx = asa_for(token, 2000, merchant)
    assert decide_lithic_asa(tx)['result'] == 'APPROVED'
    assert decide_lithic_asa(tx)['result'] == 'APPROVED'
    state = client.get('/api/state').json()
    assert state['parent']['held_cents'] == 2000
    assert state['parent']['hold_count'] == 1
    trace = state['authorization_traces'][0]
    assert trace['source'] == 'LITHIC_SANDBOX_ASA'
    assert trace['agent_attribution'] == 'SIGNED_AGENT_INTENT'
    assert trace['payment_intent_id'] == r.json()['intent_id']
    assert state['cards'][0]['spent_cents'] == 2000


def test_flow_a_requires_member_approval_before_network():
    key, agent, card, merchant = setup_scoped('GROCERIES')
    token = bind(card)
    response = client.post('/api/agent/intents', json=signed(key, agent, card, 7500, merchant=merchant)).json()
    assert response['status'] == 'AWAITING_MEMBER_APPROVAL'
    assert decide_lithic_asa(asa_for(token, 7500, merchant))['result'] != 'APPROVED'
    approved = client.post(f"/api/member/intents/{response['intent_id']}/approval", json={'approved':True})
    assert approved.status_code == 200
    assert decide_lithic_asa(asa_for(token, 7500, merchant))['result'] == 'APPROVED'
    assert client.get('/api/state').json()['parent']['held_cents'] == 7500


def test_flow_b_groceries_without_agent_signature_or_intent():
    key, agent, card, merchant = setup_scoped('GROCERIES', 'CARD_BOUND_NETWORK')
    token = bind(card)
    tx = asa_for(token, 2000, merchant)
    assert decide_lithic_asa(tx)['result'] == 'APPROVED'
    assert decide_lithic_asa(tx)['result'] == 'APPROVED'
    state = client.get('/api/state').json()
    assert state['intents'] == []
    assert state['parent']['held_cents'] == 2000
    assert state['parent']['hold_count'] == 1
    trace = state['authorization_traces'][0]
    assert trace['mode'] == 'CARD_BOUND_NETWORK'
    assert trace['agent_id'] == agent
    assert trace['payment_intent_id'] is None
    assert trace['agent_attribution'] == 'INFERRED_FROM_CARD_BINDING'
    assert 'does NOT prove' in trace['warning']


def test_flow_b_threshold_and_purpose_and_wrong_flow_fail_closed():
    key, agent, card, merchant = setup_scoped('TICKETS', 'CARD_BOUND_NETWORK')
    token = bind(card)
    # No member approval in flight: requests above the autonomous limit decline.
    assert decide_lithic_asa(asa_for(token, 7500, merchant))['result'] != 'APPROVED'
    assert decide_lithic_asa(asa_for(token, 1500, 'GROCERY_101'))['result'] != 'APPROVED'
    assert client.post('/api/agent/intents', json=signed(key, agent, card, 1200, merchant=merchant)).status_code == 403
    # A signed-intent-only credential cannot use the local network-only bypass.
    key2, agent2, signed_card, grocery = setup_scoped('GROCERIES', 'SIGNED_INTENT_REQUIRED')
    r = client.post('/api/network/direct/authorize', json={
        'card_reference': signed_card['id'], 'merchant_id': grocery,
        'amount_cents': 1000, 'network_reference': 'rrn-signed-bypass-1'})
    assert r.status_code == 200
    assert r.json()['decision'] == 'DECLINED'
    assert r.json()['reason'] == 'SIGNED_INTENT_REQUIRED'


def test_flow_b_local_network_only_works_without_agent_identifier():
    key, agent, card, merchant = setup_scoped('GROCERIES', 'CARD_BOUND_NETWORK')
    network = {'card_reference':card['id'], 'merchant_id':merchant, 'amount_cents':2300,
               'network_reference':'rrn-network-direct-123'}
    result = client.post('/api/network/direct/authorize', json=network)
    assert result.status_code == 200
    assert result.json()['decision'] == 'APPROVED'
    assert result.json()['attribution'] == 'CARD_BINDING_ONLY'
    assert result.json()['agent_id'] == agent
    assert client.post('/api/network/direct/authorize', json=network).json() == result.json()
    # One financially authoritative hold, even on network retries.
    assert client.get('/api/state').json()['parent']['hold_count'] == 1
    too_high = {**network, 'amount_cents':2600, 'network_reference':'rrn-network-direct-124'}
    denied = client.post('/api/network/direct/authorize', json=too_high).json()
    assert denied['decision'] == 'DECLINED'
    assert denied['reason'] == 'MEMBER_APPROVAL_REQUIRED_BEFORE_PURCHASE'
    unknown = {**network, 'card_reference':'unknown', 'network_reference':'rrn-network-direct-125'}
    assert client.post('/api/network/direct/authorize', json=unknown).json()['decision'] == 'DECLINED'


def test_parent_pause_blocks_network_only_agent():
    key, agent, card, merchant = setup_scoped('TICKETS', 'CARD_BOUND_NETWORK')
    token=bind(card)
    assert client.post('/api/member/parent/status', json={'status':'PAUSED'}).status_code == 200
    assert decide_lithic_asa(asa_for(token, 1200, merchant))['result'] != 'APPROVED'
    assert client.get('/api/state').json()['parent']['held_cents'] == 0

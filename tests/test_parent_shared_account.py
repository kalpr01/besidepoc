"""Verify member-defined parent limits and one shared authorization hold."""
import uuid

from fastapi.testclient import TestClient
import lithic_client
from lithic_native_rules import build_rules
from app import app, _lock, _cards, _parent
from test_flow import setup_demo, signed
from test_lithic import asa, decide_lithic_asa

client = TestClient(app)


def parent_setup():
    private, agent, old_card = setup_demo()
    # Separate local parent arrangement: no independently funded child accounts.
    r = client.post('/api/member/parent/configure', json={
        'credit_limit_cents': 10000, 'max_transaction_cents': 8000,
        'monthly_policy_limit_cents': 10000})
    assert r.status_code == 200
    card = client.post('/api/member/cards', json={
        'agent_id': agent, 'per_transaction_cents': 10000,
        'budget_cents': 10000, 'approval_threshold_cents': 2500,
        'allowed_merchants': ['GROCERY_101']}).json()
    return private, agent, card


def test_shared_parent_limit_and_member_approval():
    private, agent, card = parent_setup()
    assert client.post('/api/agent/intents', json=signed(private, agent, card, 8001)).status_code == 403
    approved = client.post('/api/agent/intents', json=signed(private, agent, card, 2300)).json()
    assert approved['status'] == 'READY'
    higher = client.post('/api/agent/intents', json=signed(private, agent, card, 4000)).json()
    assert higher['status'] == 'AWAITING_MEMBER_APPROVAL'
    state = client.get('/api/state').json()['parent']
    assert state['held_cents'] == 0  # no financial hold during PRE-APPROVAL
    token = client.post('/api/member/intents/'+higher['intent_id']+'/approval',
                        json={'approved': True}).json()['payment_token']
    network = {'payment_token':token,'merchant_id':'GROCERY_101',
               'amount_cents':4000,'network_reference':'rrn-parent-001'}
    assert client.post('/api/network/authorize', json=network).json()['decision'] == 'APPROVED'
    assert client.post('/api/network/authorize', json=network).json()['decision'] == 'APPROVED'
    state = client.get('/api/state').json()['parent']
    assert state['held_cents'] == 4000 and state['hold_count'] == 1
    assert state['available_cents'] == 6000
    assert client.post('/api/member/parent/status',json={'status':'PAUSED'}).status_code == 200
    assert client.post('/api/agent/intents', json=signed(private, agent, card, 1000)).status_code == 403


def test_parent_and_child_lithic_cards_share_account_and_a_single_ledger(monkeypatch):
    private, agent, card = parent_setup()
    acct_token = str(uuid.uuid4())
    calls = []
    def fake_create_test_card(*, account_token=None, memo=''):
        calls.append((account_token, memo))
        return {'token':str(uuid.uuid4()),'account_token':account_token or acct_token,'last_four':'4321'}
    monkeypatch.setattr(lithic_client, 'create_test_card', fake_create_test_card)
    parent_card = client.post('/api/member/parent/lithic/create').json()
    assert parent_card['account_token'] == acct_token
    child_card = client.post('/api/member/cards/'+card['id']+'/lithic/create').json()
    assert child_card['account_token'] == acct_token
    assert calls[0][0] is None and calls[1][0] == acct_token

    parent_auth = asa(parent_card['card_token'], 2500)
    assert decide_lithic_asa(parent_auth)['result'] == 'APPROVED'
    assert decide_lithic_asa(parent_auth)['result'] == 'APPROVED'
    parent_state = client.get('/api/state').json()['parent']
    assert parent_state['held_cents'] == 2500 and parent_state['hold_count'] == 1

    # No signed intent -> no authorization on the agentic credential.
    assert decide_lithic_asa(asa(child_card['card_token'],1500))['result'] != 'APPROVED'
    intent = client.post('/api/agent/intents', json=signed(private, agent, card, 1500)).json()
    assert intent['status'] == 'READY'
    child_auth = asa(child_card['card_token'],1500)
    assert decide_lithic_asa(child_auth)['result'] == 'APPROVED'
    assert decide_lithic_asa(child_auth)['result'] == 'APPROVED'
    parent_state = client.get('/api/state').json()['parent']
    child_state = next(c for c in client.get('/api/state').json()['cards'] if c['id'] == card['id'])
    assert parent_state['held_cents'] == 4000 and parent_state['hold_count'] == 2
    assert parent_state['available_cents'] == 6000
    assert child_state['spent_cents'] == 1500  # policy utilization, not a second ledger debit


def test_parent_bound_agent_card_cannot_bind_different_lithic_account(monkeypatch):
    private, agent, card = parent_setup()
    actual = str(uuid.uuid4())
    calls = 0
    def wrong_account(*, account_token=None, memo=''):
        nonlocal calls
        calls += 1
        return {'token':str(uuid.uuid4()),'account_token':actual if calls == 1 else str(uuid.uuid4()),'last_four':'1234'}
    monkeypatch.setattr(lithic_client,'create_test_card',wrong_account)
    assert client.post('/api/member/parent/lithic/create').status_code == 200
    assert client.post('/api/member/cards/'+card['id']+'/lithic/create').status_code == 502
    with _lock:
        assert _cards[card['id']]['lithic_card_token'] is None


def test_native_lithic_rule_generator_matches_parent_and_card_scopes(monkeypatch):
    private, agent, card = parent_setup()
    token_account = str(uuid.uuid4())
    def issue_card(*, account_token=None, memo=''):
        return {'token':str(uuid.uuid4()), 'account_token':account_token or token_account,'last_four':'9999'}
    monkeypatch.setattr(lithic_client,'create_test_card',issue_card)
    client.post('/api/member/parent/lithic/create')
    client.post('/api/member/cards/'+card['id']+'/lithic/create')
    r = client.get('/api/lithic/rules/preview',params={'card_id':card['id']})
    assert r.status_code == 200, r.text
    rules = r.json()['rules']
    assert len(rules)==4
    assert rules[0]['entities']['include'][0] == {'type':'ACCOUNT','token':token_account}
    assert rules[2]['entities']['include'][0]['type']=='CARD'
    assert rules[1]['parameters']['scope']=='ACCOUNT'
    assert rules[3]['parameters']['scope']=='CARD'
    assert rules[0]['parameters']['conditions'][0]['value']==8000
    assert rules[2]['parameters']['conditions'][0]['value']==10000
    # Generation / preview never writes rules to Lithic.


def test_rules_api_build_does_not_embed_member_identity_or_keys():
    rules=build_rules(str(uuid.uuid4()),str(uuid.uuid4()),parent_max_cents=100000,
                      agent_max_cents=2500,account_monthly_cents=500000,agent_monthly_cents=20000)
    assert len(rules)==4
    assert all(r['event_stream']=='AUTHORIZATION' for r in rules)
    assert all(r['parameters'].get('action')=='DECLINE' for r in [rules[0],rules[2]])
    assert rules[2]['parameters']['conditions'][0]['value']==2500


def test_parent_credit_applies_to_agent_card_without_second_balance(monkeypatch):
    private, agent, card = parent_setup()
    acct = str(uuid.uuid4())
    def issue(*, account_token=None, memo=''):
        return {'token':str(uuid.uuid4()),'account_token':account_token or acct,'last_four':'0001'}
    monkeypatch.setattr(lithic_client,'create_test_card',issue)
    parent = client.post('/api/member/parent/lithic/create').json()
    child = client.post('/api/member/cards/'+card['id']+'/lithic/create').json()
    assert decide_lithic_asa(asa(parent['card_token'], 7000))['result'] == 'APPROVED'
    # The agent's separate limit/budget may permit it, but the shared parent account cannot.
    assert client.post('/api/agent/intents', json=signed(private,agent,card,4000)).status_code == 403
    assert client.get('/api/state').json()['parent']['available_cents'] == 3000
    assert decide_lithic_asa(asa(child['card_token'],4000))['result'] != 'APPROVED'
    assert client.get('/api/state').json()['parent']['hold_count'] == 1


def test_check_lithic_transaction_result_is_sanitized_and_card_scoped(monkeypatch):
    private, agent, card = parent_setup()
    acct = str(uuid.uuid4())
    child = str(uuid.uuid4())
    with _lock:
        _cards[card['id']]['lithic_card_token'] = child
    txid = str(uuid.uuid4())
    def fake_get_transaction(token):
        assert token == txid
        return {'token':txid,'account_token':acct,'card_token':child,'status':'DECLINED',
                'result':'DECLINED','detailed_results':['AUTH_RULE'],
                'rule_results':[{'name':'Occlet demo | Parent max purchase',
                                 'result':'AUTH_RULE', 'explanation':'Amount too high'}]}
    monkeypatch.setattr(lithic_client,'get_transaction',fake_get_transaction)
    data=client.get('/api/lithic/transactions/'+txid)
    assert data.status_code == 200
    assert data.json()['rule_results'][0]['name'].startswith('Occlet demo')
    with _lock:
        _cards[card['id']]['lithic_card_token'] = str(uuid.uuid4())
    assert client.get('/api/lithic/transactions/'+txid).status_code == 404

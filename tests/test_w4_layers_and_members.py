"""W4 Level 1 / Level 2 rule trace, remove = revoke + close Lithic card, lab member endpoints."""
import app as demo
from fastapi.testclient import TestClient

client = TestClient(demo.app)


def _w4_parent(monkeypatch, *, available=50000, protected=30000, yield_enabled=False, auths=None):
    import occlet_w4
    with demo._lock:
        demo._parent.clear(); demo._parent.update(demo.default_parent())
        demo._parent.update(enabled=True, status='ACTIVE', id='arr_layers', ledger_mode='OCCLET_SQL_LEDGER',
                            max_transaction_cents=50000, monthly_policy_limit_cents=50000)
    monkeypatch.setattr(occlet_w4, 'overview', lambda arr: {
        'protected_cents': protected, 'yield_enabled': yield_enabled, 'version': 1,
        'free_to_spend_cents': available - protected,
        'balance': {'available_cents': available, 'held_cents': 0, 'cash_cents': available,
                    'authorizations': auths or []}})
    monkeypatch.setattr(demo, 'parent_used', lambda: 0)


def _results(layers):
    return {r['id']: r['result'] for r in layers['rules']}


def test_w4_first_attempt_declined_by_level1_protected_intent(monkeypatch):
    _w4_parent(monkeypatch)
    with demo._lock:
        layers = demo.w4_rule_layers(35000, 'GROCERY_101')
    assert layers['decided_by'] == 'LEVEL_1' and layers['reason'] == 'PROTECTED_BY_MEMBER'
    r = _results(layers)
    assert [r[k] for k in ('L1.1', 'L1.2', 'L1.3', 'L1.4')] == ['PASS'] * 4
    assert r['L1.5'] == 'FAIL'
    assert [r[k] for k in ('L2.1', 'L2.2', 'L2.3')] == ['NOT_REACHED'] * 3
    assert layers['inputs']['room_cents'] == 20000


def test_w4_retry_after_release_passes_both_levels(monkeypatch):
    _w4_parent(monkeypatch, protected=15000)
    with demo._lock:
        layers = demo.w4_rule_layers(35000, 'GROCERY_101')
    assert layers['decided_by'] is None and layers['level1_passed']
    assert set(_results(layers).values()) == {'PASS'}
    final = demo.finalize_layers(layers, 'APPROVED', None)['final']
    assert final == {'decision': 'APPROVED', 'reason': 'POLICY_AND_FUNDING_OK'}


def test_level2_can_only_tighten_price_band(monkeypatch):
    _w4_parent(monkeypatch, available=200000, protected=0)
    with demo._lock:
        demo._parent.update(max_transaction_cents=200000, monthly_policy_limit_cents=200000)
        layers = demo.w4_rule_layers(60000, 'GROCERY_101')   # above the $500 grocery band
    assert layers['decided_by'] == 'LEVEL_2' and layers['reason'] == 'L2_PRICE_BAND'
    r = _results(layers)
    assert all(r[k] == 'PASS' for k in ('L1.1', 'L1.2', 'L1.3', 'L1.4', 'L1.5', 'L2.1', 'L2.2'))
    assert r['L2.3'] == 'FAIL'


def test_sql_recheck_disagreement_is_flagged(monkeypatch):
    _w4_parent(monkeypatch, protected=15000)
    with demo._lock:
        layers = demo.w4_rule_layers(35000, 'GROCERY_101')
    final = demo.finalize_layers(layers, 'DECLINED', 'PROTECTED_BY_MEMBER')['final']
    assert 'row-locked recheck' in final['note']


def test_no_layers_without_w4_arrangement():
    with demo._lock:
        demo._parent.clear(); demo._parent.update(demo.default_parent())
        assert demo.w4_rule_layers(35000, 'GROCERY_101') is None


def _card_with_lithic(token='tok-del'):
    with demo._lock:
        demo._cards.clear(); demo._intents.clear()
        demo._cards['vc_rm'] = {'id': 'vc_rm', 'alias': 'LITHIC-1234', 'status': 'ACTIVE', 'agent_id': 'a',
                                'lithic_card_token': token}


def test_remove_revokes_and_closes_lithic_card(monkeypatch):
    import lithic_client
    _card_with_lithic()
    calls = []
    monkeypatch.setattr(lithic_client, 'lithic_request',
                        lambda method, path, payload=None: calls.append((method, path, payload)) or {'state': 'CLOSED'})
    r = client.post('/api/member/cards/vc_rm/revoke', json={})
    assert r.status_code == 200 and r.json()['lithic_state'] == 'CLOSED'
    assert calls == [('PATCH', '/cards/tok-del', {'state': 'CLOSED'})]
    with demo._lock:
        card = demo._cards['vc_rm']
    assert card['status'] == 'REVOKED' and card['removed_at'] and card['lithic_state'] == 'CLOSED'


def test_remove_still_revokes_when_lithic_close_fails(monkeypatch):
    import lithic_client
    _card_with_lithic()
    def boom(method, path, payload=None):
        raise lithic_client.LithicError('sandbox unavailable')
    monkeypatch.setattr(lithic_client, 'lithic_request', boom)
    r = client.post('/api/member/cards/vc_rm/revoke', json={})
    assert r.status_code == 200 and r.json()['lithic_state'] == 'CLOSE_FAILED'
    with demo._lock:
        assert demo._cards['vc_rm']['status'] == 'REVOKED'


def test_lab_member_endpoints_need_postgres(monkeypatch):
    monkeypatch.delenv('DATABASE_URL', raising=False)
    assert client.get('/api/lab/members').status_code == 409
    r = client.post('/api/lab/members', json={'display_name': 'Maria', 'lithic_parent': 'NONE'})
    assert r.status_code == 409


def test_member_page_and_lab_wiring():
    js = client.get('/member/assets/beside-delegated.js').text
    assert 'removeCard' in js and "'/revoke'" in js and 'bd-member-eyebrow' in js
    lab = client.get('/member/assets/lab-members.js').text
    for needle in ('/api/lab/members', 'occletRenderLayers', 'NOT_REACHED', '/revoke'):
        assert needle in lab
    html = client.get('/').text
    assert 'id="lm-panel"' in html and 'id="w4-layers"' in html and 'lab-members.js' in html


def test_bring_available_to_target_repays_first_then_adds_funds(monkeypatch):
    import occlet_demo_ledger
    with demo._lock:
        demo._parent.clear(); demo._parent.update(demo.default_parent())
        demo._parent.update(enabled=True, id='arr_fund', ledger_mode='OCCLET_SQL_LEDGER')
    book = {'available_cents': 15500, 'cash_cents': 15500, 'held_cents': 0,
            'settled_cents': 10000, 'repaid_cents': 0, 'authorizations': []}
    posted = []
    def fake_fund(arr, amount, ref, kind):
        posted.append((kind, amount))
        book['available_cents'] += amount; book['cash_cents'] += amount
        if kind == 'REPAYMENT':
            book['repaid_cents'] += amount
    monkeypatch.setattr(occlet_demo_ledger, 'summary', lambda arr: dict(book))
    monkeypatch.setattr(occlet_demo_ledger, 'fund', fake_fund)
    r = client.post('/api/member/funding', json={'kind': 'RESTORE', 'target_available_cents': 50000,
                                                 'reference': 'test-split'})
    assert r.status_code == 200, r.text
    assert posted == [('REPAYMENT', 10000), ('TOP_UP', 24500)]
    assert r.json()['available_cents'] == 50000 and r.json()['amount_due_cents'] == 0

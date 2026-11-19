"""Member-page Lithic linking, Lithic delegated issuance and parent-card amount due."""
import app as demo
from fastapi.testclient import TestClient

client = TestClient(demo.app)


def _fresh_parent(**extra):
    with demo._lock:
        demo._parent.clear(); demo._parent.update(demo.default_parent())
        demo._parent.update(enabled=True, **extra)
        demo._cards.clear(); demo._agents.clear(); demo._intents.clear()
        demo._auth_traces.clear(); demo._events.clear()


def test_amount_due_is_settled_plus_pending_holds(monkeypatch):
    import occlet_demo_ledger, occlet_w4
    _fresh_parent(id='due_parent', ledger_mode='OCCLET_SQL_LEDGER',
                  lithic_parent_card_token='parent_tok', parent_card_last_four='4242')
    monkeypatch.setattr(occlet_demo_ledger, 'summary', lambda arr: {
        'cash_cents': 15500, 'available_cents': 12000, 'held_cents': 3500,
        'settled_cents': 34500, 'authorizations': []})
    monkeypatch.setattr(occlet_demo_ledger, 'monthly_used', lambda arr: 38000)
    monkeypatch.setattr(occlet_w4, 'overview', lambda arr: {
        'protected_cents': 0, 'yield_enabled': False, 'version': 1, 'free_to_spend_cents': 12000})
    p = client.get('/api/member/experience').json()['parent']
    assert p['settled_cents'] == 34500
    assert p['amount_due_cents'] == 34500 + 3500
    assert p['parent_card_last_four'] == '4242'


def test_amount_due_absent_without_postgres_ledger():
    _fresh_parent()
    p = client.get('/api/member/experience').json()['parent']
    assert p['amount_due_cents'] is None and p['settled_cents'] is None


def test_lithic_card_list_drops_pan_closed_and_already_delegated(monkeypatch):
    import lithic_client
    _fresh_parent()
    with demo._lock:
        demo._cards['vc_x'] = {'id': 'vc_x', 'lithic_card_token': 'tok-delegated', 'status': 'ACTIVE'}
    def fake(method, path, payload=None):
        assert method == 'GET' and path.startswith('/cards')
        return {'data': [
            {'token': 'tok-open', 'account_token': 'acct-1', 'last_four': '1111', 'state': 'OPEN',
             'type': 'VIRTUAL', 'memo': 'parent', 'pan': '4111111111111111', 'cvv': '123'},
            {'token': 'tok-closed', 'account_token': 'acct-1', 'last_four': '2222', 'state': 'CLOSED'},
            {'token': 'tok-delegated', 'account_token': 'acct-1', 'last_four': '3333', 'state': 'OPEN'},
        ]}
    monkeypatch.setattr(lithic_client, 'lithic_request', fake)
    r = client.get('/api/member/parent/lithic/cards')
    assert r.status_code == 200
    cards = r.json()['cards']
    assert [c['card_token'] for c in cards] == ['tok-open']
    assert 'pan' not in cards[0] and 'cvv' not in cards[0]
    assert '4111111111111111' not in r.text


def test_lithic_issue_renames_demo_alias_and_keeps_parent_account(monkeypatch):
    import lithic_client
    _fresh_parent(lithic_account_token='acct-parent', lithic_parent_card_token='parent_tok')
    with demo._lock:
        demo._cards['vc_new'] = {'id': 'vc_new', 'alias': 'VIRTUAL-DEMO-ABC123', 'status': 'ACTIVE',
                                 'lithic_card_token': None}
    seen = {}
    def fake_create(*, account_token=None, memo=''):
        seen['account'] = account_token
        return {'token': 'tok-child', 'last_four': '9876', 'account_token': 'acct-parent'}
    monkeypatch.setattr(lithic_client, 'create_test_card', fake_create)
    r = client.post('/api/member/cards/vc_new/lithic/create', json={})
    assert r.status_code == 200 and r.json()['last_four'] == '9876'
    assert seen['account'] == 'acct-parent'
    with demo._lock:
        card = demo._cards['vc_new']
    assert card['alias'] == 'LITHIC-9876' and card['lithic_card_token'] == 'tok-child'


def test_member_page_script_links_and_issues_on_lithic():
    js = client.get('/member/assets/beside-delegated.js').text
    for needle in ('/api/member/parent/lithic/cards', '/api/member/parent/lithic/link-existing',
                   '/api/member/parent/lithic/create', 'issueLithic', 'besideOccletParent',
                   "select.value=before;syncMerchant();"):
        assert needle in js
    assert 'beforesyncMerchant' not in js
    html = (demo.Path(demo.__file__).parent / 'static' / 'beside.html').read_text(encoding='utf-8')
    assert "eyebrow:'Amount due'" in html and "window.addEventListener('occlet:parent'" in html

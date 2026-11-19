"""The uploaded Beside Main design is preserved, with a new delegated-card experience."""
from pathlib import Path
from fastapi.testclient import TestClient
from app import app

ROOT = Path(__file__).resolve().parents[1]


def test_beside_routes_preserve_original_pages_and_persona():
    c = TestClient(app)
    member = c.get('/member')
    assert member.status_code == 200
    assert 'Beside | Member Experience' in member.text
    assert 'Ann Gutierrez' in member.text
    for label in ('Overview', 'My certificates', 'Community pools', 'My impact', 'Manage preference', 'Member settings'):
        assert label in member.text
    assert 'beside-delegated.js' in member.text
    assert 'beside-delegated.css' in member.text
    assert c.get('/beside').status_code == 200
    assert c.get('/member/legacy').status_code == 200
    assert 'Account & arrangements' in c.get('/member/legacy').text


def test_delegated_asset_has_policy_and_w4_buttons_no_sensitive_credentials():
    c = TestClient(app)
    js = c.get('/member/assets/beside-delegated.js')
    css = c.get('/member/assets/beside-delegated.css')
    assert js.status_code == css.status_code == 200
    assert 'javascript' in js.headers['content-type']
    assert 'text/css' in css.headers['content-type']
    for field in ('Create delegated card', 'Ann Gutierrez', 'Per-purchase limit',
                  'Total delegated budget', 'Auto-approve up to',
                  '/api/member/cards', '/api/member/experience',
                  'Lithic sandbox', 'bd-w4-release', 'bd-card-filter', 'bd-actions'):
        assert field in js.text
    assert '411111' not in js.text
    assert 'LITHIC_API_KEY=' not in js.text
    assert c.get('/member/assets/secret.env').status_code == 404


def test_beside_is_same_uploaded_design_not_a_second_standalone_mock():
    html = (ROOT/'static'/'beside.html').read_text()
    assert len(html)>1_000_000  # keeps embedded Beside assets and source markup
    assert 'Certificate •••• 0447' in html or '0447' in html
    assert 'member-nav' in html
    assert 'Community Capital' in html
    assert '<script src="/member/assets/beside-delegated.js"></script>' in html

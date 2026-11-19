from fastapi.testclient import TestClient
from app import app

def test_beside_direct_links_and_visible_navigation():
    c=TestClient(app)
    for route in ('/member/delegated','/member/delegated/setup'):
        r=c.get(route)
        assert r.status_code==200
        assert 'Ann Gutierrez' in r.text
        assert 'id="bd-direct-nav"' in r.text
        assert 'href="/member/delegated"' in r.text
        assert 'beside-delegated.js' in r.text
    version=c.get('/api/demo/version').json()
    assert version['edition']=='beside-member-ledger-actions-v4'
    assert version['delegated_setup']=='/member/delegated/setup'

def test_sidebar_and_auto_expand_hooks():
    c=TestClient(app)
    script=c.get('/member/assets/beside-delegated.js').text
    assert 'bd-direct-nav' in script
    assert "pathname.endsWith('/setup')" in script
    assert "$('bd-create-panel').hidden=false" in script


def test_delegated_route_survives_beside_sign_in():
    c=TestClient(app)
    page=c.get('/member/delegated/setup').text
    js=c.get('/member/assets/beside-delegated.js').text
    assert "window.dispatchEvent(new CustomEvent('beside:authenticated'))" in page
    assert "window.addEventListener(\'beside:authenticated\',()=>{" in js
    assert "history.pushState({besidePage:'delegated'}" in js
    assert "showPage(location.pathname.endsWith(\'/setup\')" in js


def test_delegation_is_in_current_beside_navigation_not_only_hidden_old_menu():
    c=TestClient(app)
    html=c.get('/member').text
    assert "['delegated','Delegated Card']" in html
    assert "id==='delegated'?document.getElementById('bd-direct-nav')" in html
    assert "const delegated=document.querySelector('#delegated.view.active')" in html
    assert "if(location.pathname.startsWith('/member/delegated'))" in html

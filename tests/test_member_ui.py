"""Beside is now the default member UI; the previous portal stays available as legacy."""
from fastapi.testclient import TestClient
from app import app


def test_member_portal_and_assets():
    client = TestClient(app)
    main = client.get('/member')
    assert main.status_code == 200
    assert 'Beside | Member Experience' in main.text
    assert 'Ann Gutierrez' in main.text
    assert 'beside-delegated.js' in main.text
    legacy=client.get('/member/legacy')
    assert legacy.status_code == 200
    assert 'Account & arrangements' in legacy.text
    assert 'My agents' in legacy.text
    for filename, typ in [('member.css','text/css'),('member.js','javascript'),
                          ('beside-delegated.css','text/css'),('beside-delegated.js','javascript')]:
        asset=client.get('/member/assets/'+filename)
        assert asset.status_code==200
        assert typ in asset.headers['content-type']
    assert client.get('/member/assets/secrets.txt').status_code==404
    assert client.get('/').status_code==200
    assert 'Member experience' in client.get('/').text


def test_member_portal_does_not_embed_sensitive_card_data():
    client=TestClient(app)
    html=client.get('/member').text
    assert '411111' not in html
    assert 'LITHIC_API_KEY=' not in html
    assert '/api/agents' not in html  # business logic stays in external JS and server

"""Error transparency and safe discovery diagnostics: no Lithic secrets exposed."""
import uuid
from fastapi.testclient import TestClient
import app as demo
from app import app
import lithic_client


def test_lithic_permission_error_not_mislabeled_entitlement(monkeypatch):
    with TestClient(app) as c:
        c.post('/api/demo/reset')
        demo._parent['lithic_account_token'] = str(uuid.uuid4())
        def denied(method,path,**kw):
            raise lithic_client.LithicError('Lithic HTTP 403: API key or program lacks access to this endpoint (ACCESS_DENIED)',status_code=403,category='ACCESS_DENIED')
        monkeypatch.setattr(lithic_client,'lithic_request',denied)
        r=c.get('/api/lithic/ledger/discover')
        assert r.status_code == 403
        assert '403' in r.json()['detail']
        assert 'Ledger entitlement' not in r.json()['detail']
        diag=c.get('/api/lithic/ledger/diagnose')
        assert diag.status_code == 200
        assert diag.json()['parent_financial_accounts']['status'] == 'ACCESS_DENIED'
        assert diag.json()['program_financial_accounts']['http_status'] == 403


def test_discover_200_without_issuing_is_not_an_api_error(monkeypatch):
    with TestClient(app) as c:
        c.post('/api/demo/reset')
        acc=str(uuid.uuid4())
        demo._parent['lithic_account_token']=acc
        monkeypatch.setattr(lithic_client,'lithic_request', lambda method,path,**kw: {'data':[], 'has_more':False})
        r=c.get('/api/lithic/ledger/discover')
        assert r.status_code == 200
        assert not r.json()['ledger_provisioned']
        diag=c.get('/api/lithic/ledger/diagnose')
        assert diag.status_code == 200
        assert diag.json()['parent_financial_accounts']['issuing_count'] == 0
        assert 'card-only' in diag.json()['interpretation']


def test_missing_parent_cannot_invoke_diagnostic(monkeypatch):
    with TestClient(app) as c:
        c.post('/api/demo/reset')
        assert c.get('/api/lithic/ledger/diagnose').status_code == 409

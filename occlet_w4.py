"""W4 protected intent: permission-only earmark, never a second financial ledger."""
from __future__ import annotations
from occlet_sql_store import _connect, enabled
from occlet_demo_ledger import LedgerRejected, _lock_book


def require_db():
    if not enabled():
        raise LedgerRejected('POSTGRES_NOT_CONFIGURED')


def seed_trip(arrangement_id: str, protected_cents: int = 30000):
    require_db()
    with _connect() as conn:
        _lock_book(conn, arrangement_id)
        conn.execute('''INSERT INTO demo_w4_protections
         (arrangement_id,purpose,initial_cents,protected_cents,yield_enabled)
         VALUES (%s,'TRIP',%s,%s,FALSE)''', (arrangement_id,protected_cents,protected_cents))
    return overview(arrangement_id)


def protection(conn, arrangement_id):
    row=conn.execute('''SELECT protected_cents,yield_enabled,version
       FROM demo_w4_protections WHERE arrangement_id=%s''',(arrangement_id,)).fetchone()
    return {'protected_cents':int(row[0]),'yield_enabled':bool(row[1]),'version':int(row[2])} if row else None


def rule(amount_cents: int, available_cents: int, protected_cents: int, yield_enabled: bool):
    """Pure hard rule; actual SQL authorize applies it again under row lock."""
    if amount_cents > available_cents:
        return 'INSUFFICIENT_FUNDS'
    if not yield_enabled and amount_cents > available_cents-protected_cents:
        return 'PROTECTED_BY_MEMBER'
    return None


def overview(arrangement_id: str):
    require_db()
    from occlet_demo_ledger import summary
    with _connect() as conn:
        p=protection(conn, arrangement_id)
        if p is None:
            raise LedgerRejected('W4_NOT_INITIALIZED')
        balance=summary(arrangement_id,conn=conn)
        rows=conn.execute('''SELECT authorization_ref,amount_cents,decision,reason,
         protected_cents,available_cents,created_at FROM demo_w4_decisions
         WHERE arrangement_id=%s ORDER BY created_at DESC LIMIT 15''',(arrangement_id,)).fetchall()
        releases=conn.execute('''SELECT release_ref,amount_cents,old_protected_cents,
         new_protected_cents,created_at FROM demo_w4_releases
         WHERE arrangement_id=%s ORDER BY created_at DESC LIMIT 15''',(arrangement_id,)).fetchall()
        return {'arrangement_id':arrangement_id,'purpose':'TRIP', 'protected_cents':p['protected_cents'],
          'yield_enabled':p['yield_enabled'],'version':p['version'],
          'free_to_spend_cents':max(0,balance['available_cents']-p['protected_cents']) if not p['yield_enabled'] else balance['available_cents'],
          'balance':balance,
          'decisions':[dict(zip(('reference','amount_cents','decision','reason','protected_cents','available_cents','created_at'),r)) for r in rows],
          'releases':[dict(zip(('release_ref','amount_cents','old_protected_cents','new_protected_cents','created_at'),r)) for r in releases]}


def release_protection(arrangement_id: str, amount_cents: int, release_ref: str):
    """Member edits trip permission. NO financial journal or balance transfer."""
    require_db()
    if not isinstance(amount_cents,int) or isinstance(amount_cents,bool) or amount_cents<=0 or amount_cents>100_000_000:
        raise LedgerRejected('INVALID_RELEASE_AMOUNT')
    if not release_ref or len(release_ref)>120:
        raise LedgerRejected('INVALID_RELEASE_REFERENCE')
    with _connect() as conn:
        _lock_book(conn,arrangement_id)
        existing=conn.execute('''SELECT amount_cents FROM demo_w4_releases
          WHERE arrangement_id=%s AND release_ref=%s''',(arrangement_id,release_ref)).fetchone()
        if existing:
            if existing[0]!=amount_cents:
                raise LedgerRejected('REFERENCE_CONFLICT')
            return {'idempotent':True, **overview(arrangement_id)}
        p=protection(conn,arrangement_id)
        if not p:
            raise LedgerRejected('W4_NOT_INITIALIZED')
        if p['yield_enabled'] or amount_cents>p['protected_cents']:
            raise LedgerRejected('INVALID_PROTECTION_RELEASE')
        new=p['protected_cents']-amount_cents
        conn.execute('''UPDATE demo_w4_protections SET protected_cents=%s,
            version=version+1,updated_at=now() WHERE arrangement_id=%s''',(new,arrangement_id))
        conn.execute('''INSERT INTO demo_w4_releases
          (arrangement_id,release_ref,amount_cents,old_protected_cents,new_protected_cents)
          VALUES (%s,%s,%s,%s,%s)''',(arrangement_id,release_ref,amount_cents,p['protected_cents'],new))
    return {'idempotent':False, **overview(arrangement_id)}

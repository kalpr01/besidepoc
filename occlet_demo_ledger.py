"""PostgreSQL-backed double-entry ledger for Occlet's LOCAL SANDBOX DEMO only.

One funding arrangement, one book, one authoritative local available balance.
Agent virtual cards have delegated policy budgets, NEVER a financial account here.
Authorizations and balance checks are serialized using SELECT ... FOR UPDATE.
Clearing in this simplified demo includes settlement; no real funds move.

This is not a bank ledger and is not synchronized with Lithic's financial state.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from occlet_sql_store import _connect, enabled


@dataclass
class LedgerRejected(Exception):
    code: str
    def __str__(self):
        return self.code


def _require_db():
    if not enabled():
        raise LedgerRejected('POSTGRES_NOT_CONFIGURED')


def journal_effects(lines: list[tuple[str, str, int]]) -> dict[str, int]:
    """Validate balanced journal and calculate normal-side balance effects.

    ACCOUNT -> normal side: CASH debit, AVAILABLE / AUTH_HOLDS credit.
    """
    normals = {'CASH': 'DEBIT', 'AVAILABLE': 'CREDIT', 'AUTH_HOLDS': 'CREDIT'}
    debit = sum(amt for _, side, amt in lines if side == 'DEBIT')
    credit = sum(amt for _, side, amt in lines if side == 'CREDIT')
    if not lines or debit != credit or debit <= 0:
        raise LedgerRejected('UNBALANCED_JOURNAL')
    effects = {code: 0 for code in normals}
    for code, side, amt in lines:
        if code not in normals or side not in ('DEBIT','CREDIT') or not isinstance(amt,int) or isinstance(amt,bool) or amt <= 0:
            raise LedgerRejected('INVALID_JOURNAL_ENTRY')
        effects[code] += amt if side == normals[code] else -amt
    return effects


def _write_journal(conn, arrangement_id: str, reference: str, kind: str,
                   lines: list[tuple[str,str,int]], authorization_ref: str | None=None):
    effects = journal_effects(lines)
    new_id = conn.execute('''INSERT INTO demo_ledger_journals
        (arrangement_id,journal_ref,kind,related_authorization)
        VALUES (%s,%s,%s,%s) RETURNING id''',
        (arrangement_id,reference,kind,authorization_ref)).fetchone()[0]
    for account,side,amt in lines:
        conn.execute('''INSERT INTO demo_ledger_entries
         (journal_id,arrangement_id,account_code,side,amount_cents)
         VALUES (%s,%s,%s,%s,%s)''', (new_id,arrangement_id,account,side,amt))
    for account,delta in effects.items():
        if delta:
            result = conn.execute('''UPDATE demo_ledger_accounts SET balance_cents=balance_cents+%s
              WHERE arrangement_id=%s AND code=%s AND balance_cents+%s >= 0''',
              (delta,arrangement_id,account,delta))
            if result.rowcount != 1:
                raise LedgerRejected('INVALID_ACCOUNT_BALANCE')
    return new_id


def initialize(arrangement_id: str, initial_cents: int, parent: dict) -> dict:
    """Create one shared financial book and opening funding journal (idempotent)."""
    _require_db()
    if not (isinstance(initial_cents,int) and not isinstance(initial_cents,bool) and 0 < initial_cents <= 100_000_000):
        raise LedgerRejected('INVALID_OPENING_AMOUNT')
    with _connect() as conn:
        # Source-of-truth arrangement must exist before its financial book.
        conn.execute('''INSERT INTO funding_arrangements
            (arrangement_id,member_id,product_code,status,funding_mode, lithic_account_token, parent_policy)
            VALUES (%s,'member_demo','DEBIT_SPENDING',%s,'OCCLET_SQL_LEDGER',%s,%s::jsonb)
            ON CONFLICT(arrangement_id) DO NOTHING''',
            (arrangement_id,parent['status'],parent.get('lithic_account_token'), '{}'))
        conn.execute('''SELECT arrangement_id FROM funding_arrangements WHERE arrangement_id=%s FOR UPDATE''', (arrangement_id,))
        existing = conn.execute('SELECT arrangement_id FROM demo_ledger_books WHERE arrangement_id=%s FOR UPDATE',(arrangement_id,)).fetchone()
        if existing:
            return summary(arrangement_id, conn=conn)
        conn.execute('INSERT INTO demo_ledger_books(arrangement_id) VALUES (%s)',(arrangement_id,))
        for code,normal in (('CASH','DEBIT'),('AVAILABLE','CREDIT'),('AUTH_HOLDS','CREDIT')):
            conn.execute('''INSERT INTO demo_ledger_accounts(arrangement_id,code,normal_side)
              VALUES (%s,%s,%s)''',(arrangement_id,code,normal))
        _write_journal(conn,arrangement_id,'opening','OPENING',
                       [('CASH','DEBIT',initial_cents),('AVAILABLE','CREDIT',initial_cents)])
        conn.execute('''UPDATE funding_arrangements SET funding_mode='OCCLET_SQL_LEDGER',updated_at=now()
          WHERE arrangement_id=%s''',(arrangement_id,))
        return summary(arrangement_id, conn=conn)


def _lock_book(conn, arrangement_id):
    row=conn.execute('SELECT arrangement_id FROM demo_ledger_books WHERE arrangement_id=%s FOR UPDATE',(arrangement_id,)).fetchone()
    if not row:
        raise LedgerRejected('DEMO_LEDGER_NOT_INITIALIZED')


def summary(arrangement_id: str, *, conn=None) -> dict:
    _require_db()
    if conn is None:
        with _connect() as c:
            return summary(arrangement_id, conn=c)
    book=conn.execute('SELECT arrangement_id,currency FROM demo_ledger_books WHERE arrangement_id=%s',(arrangement_id,)).fetchone()
    if not book:
        raise LedgerRejected('DEMO_LEDGER_NOT_INITIALIZED')
    rows=conn.execute('''SELECT code,balance_cents FROM demo_ledger_accounts
                          WHERE arrangement_id=%s''',(arrangement_id,)).fetchall()
    balances=dict(rows)
    recent=conn.execute('''SELECT authorization_ref,card_reference,merchant_id,amount_cents,
        clearing_amount_cents,status,created_at FROM demo_ledger_authorizations WHERE arrangement_id=%s
        ORDER BY created_at DESC,authorization_ref DESC LIMIT 30''',(arrangement_id,)).fetchall()
    journals=conn.execute('''SELECT j.journal_ref,j.kind,j.created_at,
            e.account_code,e.side,e.amount_cents FROM demo_ledger_journals j
         JOIN demo_ledger_entries e ON e.journal_id=j.id
         WHERE j.arrangement_id=%s ORDER BY j.id DESC, e.id ASC LIMIT 60''',(arrangement_id,)).fetchall()
    return {'source':'OCCLET_POSTGRES_DEMO_LEDGER','authoritative_for':'LOCAL_OCCLET_DEMO_ONLY',
      'arrangement_id':arrangement_id,'currency':book[1],
      'available_cents':balances.get('AVAILABLE',0),
      'held_cents':balances.get('AUTH_HOLDS',0),
      'cash_cents':balances.get('CASH',0),
      'authorizations':[dict(zip(('reference','card_reference','merchant','amount_cents','clearing_amount_cents','status','created_at'),r)) for r in recent],
      'journal_lines':[dict(zip(('journal_ref','kind','created_at','account','side','amount_cents'),r)) for r in journals]}


def monthly_used(arrangement_id: str) -> int:
    _require_db()
    with _connect() as conn:
        return _monthly_used(conn, arrangement_id)


def _monthly_used(conn,arrangement_id):
    return conn.execute('''SELECT COALESCE(SUM(CASE WHEN status='CLEARED' THEN clearing_amount_cents ELSE amount_cents END),0)
       FROM demo_ledger_authorizations WHERE arrangement_id=%s
       AND status IN ('AUTHORIZED','CLEARED')
       AND created_at >= date_trunc('month', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC' ''',
       (arrangement_id,)).fetchone()[0]


def authorize(arrangement_id: str, reference: str, amount_cents: int,
              *, card_reference: str, merchant_id: str,
              max_transaction_cents: int, monthly_limit_cents: int) -> dict:
    """Serialized/atomic funds availability + policy limit check and exactly one hold."""
    _require_db()
    if not reference or amount_cents <= 0 or amount_cents>100_000_000 or not card_reference or not merchant_id:
        raise LedgerRejected('INVALID_AUTH_REQUEST')
    with _connect() as conn:
        _lock_book(conn,arrangement_id)
        previous=conn.execute('''SELECT card_reference,merchant_id,amount_cents,status
            FROM demo_ledger_authorizations WHERE arrangement_id=%s AND authorization_ref=%s''',
            (arrangement_id,reference)).fetchone()
        if previous:
            if (previous[0],previous[1],previous[2]) != (card_reference,merchant_id,amount_cents):
                raise LedgerRejected('REFERENCE_CONFLICT')
            if previous[3] != 'AUTHORIZED':
                raise LedgerRejected('AUTHORIZATION_ALREADY_FINALIZED')
            return {'status':previous[3],'idempotent':True,'reference':reference,
                    'balance':summary(arrangement_id, conn=conn)}
        # A declined network attempt is terminal; member permission changes
        # require a NEW transaction reference for the retry.
        prior_w4=conn.execute('''SELECT decision FROM demo_w4_decisions
           WHERE arrangement_id=%s AND authorization_ref=%s''',
           (arrangement_id,reference)).fetchone()
        if prior_w4:
            raise LedgerRejected('PREVIOUS_W4_DECISION_REQUIRES_NEW_REFERENCE')
        if amount_cents>max_transaction_cents:
            raise LedgerRejected('PARENT_PER_TRANSACTION_LIMIT')
        if _monthly_used(conn,arrangement_id)+amount_cents>monthly_limit_cents:
            raise LedgerRejected('PARENT_SHARED_SPENDING_LIMIT')
        available=conn.execute('''SELECT balance_cents FROM demo_ledger_accounts
             WHERE arrangement_id=%s AND code='AVAILABLE' ''',(arrangement_id,)).fetchone()[0]
        if amount_cents>available:
            raise LedgerRejected('INSUFFICIENT_FUNDS')
        # W4 policy earmarking is NOT a money hold. All purchases unrelated to the
        # protected trip must leave its protected amount available. This hard rule
        # runs under the SAME funding-book lock as the financial authorization.
        from occlet_w4 import protection, rule
        p = protection(conn,arrangement_id)
        if p:
            reason=rule(amount_cents,available,p['protected_cents'],p['yield_enabled'])
            if reason:
                conn.execute('''INSERT INTO demo_w4_decisions
                    (arrangement_id,authorization_ref,amount_cents,decision,reason,protected_cents,available_cents)
                    VALUES (%s,%s,%s,'DECLINED',%s,%s,%s)
                    ON CONFLICT(arrangement_id,authorization_ref) DO NOTHING''' ,
                    (arrangement_id,reference,amount_cents,reason,p['protected_cents'],available))
                # Transaction must COMMIT the denial record before we raise.
                decline=reason
            else:
                decline=None
        else:
            decline=None
        if decline is None:
            conn.execute('''INSERT INTO demo_ledger_authorizations
                (arrangement_id,authorization_ref,card_reference,merchant_id,amount_cents,status)
                VALUES (%s,%s,%s,%s,%s,'AUTHORIZED')''',
                (arrangement_id,reference,card_reference,merchant_id,amount_cents))
            _write_journal(conn,arrangement_id,'auth:'+reference,'AUTHORIZE',
                           [('AVAILABLE','DEBIT',amount_cents),('AUTH_HOLDS','CREDIT',amount_cents)],reference)
            if p:
                conn.execute('''INSERT INTO demo_w4_decisions
                    (arrangement_id,authorization_ref,amount_cents,decision,reason,protected_cents,available_cents)
                    VALUES (%s,%s,%s,'APPROVED','POLICY_AND_FUNDING_OK',%s,%s)
                    ON CONFLICT(arrangement_id,authorization_ref) DO NOTHING''',
                    (arrangement_id,reference,amount_cents,p['protected_cents'],available))
            result={'status':'AUTHORIZED','idempotent':False,'reference':reference,
                    'balance':summary(arrangement_id, conn=conn)}
        else:
            result=None
    if decline:
        raise LedgerRejected(decline)
    return result



def release(arrangement_id: str, reference: str) -> dict:
    _require_db()
    with _connect() as conn:
        _lock_book(conn,arrangement_id)
        row=conn.execute('''SELECT amount_cents,status,card_reference FROM demo_ledger_authorizations
            WHERE arrangement_id=%s AND authorization_ref=%s FOR UPDATE''',(arrangement_id,reference)).fetchone()
        if not row:
            raise LedgerRejected('UNKNOWN_AUTHORIZATION')
        amount,status,card_reference=row
        auth={'amount_cents':amount,'card_reference':card_reference}
        if status=='RELEASED':
            return {'status':'RELEASED','idempotent':True,'authorization':auth,'balance':summary(arrangement_id, conn=conn)}
        if status!='AUTHORIZED':
            raise LedgerRejected('AUTHORIZATION_ALREADY_CLEARED')
        _write_journal(conn,arrangement_id,'release:'+reference,'RELEASE',
                     [('AUTH_HOLDS','DEBIT',amount),('AVAILABLE','CREDIT',amount)],reference)
        conn.execute('''UPDATE demo_ledger_authorizations SET status='RELEASED',updated_at=now()
            WHERE arrangement_id=%s AND authorization_ref=%s''',(arrangement_id,reference))
        return {'status':'RELEASED','idempotent':False,'authorization':auth,'balance':summary(arrangement_id, conn=conn)}


def clear(arrangement_id: str, reference: str, settled_cents: int) -> dict:
    """Demo combines clearing & settlement (NOT a real Lithic transaction).

    If the merchant settled less than the hold, the remainder returns to AVAILABLE.
    For a larger settlement, use a new/incremental authorization instead.
    """
    _require_db()
    with _connect() as conn:
        _lock_book(conn,arrangement_id)
        row=conn.execute('''SELECT amount_cents,clearing_amount_cents,status,card_reference
           FROM demo_ledger_authorizations WHERE arrangement_id=%s AND authorization_ref=%s FOR UPDATE''',
           (arrangement_id,reference)).fetchone()
        if not row:
            raise LedgerRejected('UNKNOWN_AUTHORIZATION')
        amount, prior_clearing,status,card_reference=row
        auth={'amount_cents':amount,'card_reference':card_reference}
        if status=='CLEARED':
            if settled_cents!=prior_clearing:
                raise LedgerRejected('REFERENCE_CONFLICT')
            return {'status':'CLEARED','idempotent':True,'authorization':auth,'balance':summary(arrangement_id, conn=conn)}
        if status!='AUTHORIZED':
            raise LedgerRejected('AUTHORIZATION_NOT_ACTIVE')
        if not isinstance(settled_cents,int) or isinstance(settled_cents,bool) or settled_cents<=0 or settled_cents>amount:
            raise LedgerRejected('INVALID_CLEARING_AMOUNT')
        lines=[('AUTH_HOLDS','DEBIT',amount),('CASH','CREDIT',settled_cents)]
        if amount>settled_cents:
            lines.append(('AVAILABLE','CREDIT',amount-settled_cents))
        _write_journal(conn,arrangement_id,'clear:'+reference,'CLEAR',lines,reference)
        conn.execute('''UPDATE demo_ledger_authorizations
           SET status='CLEARED',clearing_amount_cents=%s,updated_at=now()
           WHERE arrangement_id=%s AND authorization_ref=%s''',(settled_cents,arrangement_id,reference))
        return {'status':'CLEARED','idempotent':False,'authorization':auth,'balance':summary(arrangement_id, conn=conn)}


def topup(arrangement_id: str, amount_cents: int, reference: str) -> dict:
    """Synthetic demo funding. Never represents moving actual money."""
    _require_db()
    if not isinstance(amount_cents,int) or isinstance(amount_cents,bool) or not (0<amount_cents<=100_000_000):
        raise LedgerRejected('INVALID_TOPUP')
    with _connect() as conn:
        _lock_book(conn,arrangement_id)
        existing=conn.execute('''SELECT id FROM demo_ledger_journals
           WHERE arrangement_id=%s AND journal_ref=%s''',(arrangement_id,'topup:'+reference)).fetchone()
        if existing:
            raise LedgerRejected('REFERENCE_ALREADY_USED')
        _write_journal(conn,arrangement_id,'topup:'+reference,'TOP_UP',
                       [('CASH','DEBIT',amount_cents),('AVAILABLE','CREDIT',amount_cents)])
        return {'status':'FUNDED_WITH_SYNTHETIC_CENTS','balance':summary(arrangement_id, conn=conn)}

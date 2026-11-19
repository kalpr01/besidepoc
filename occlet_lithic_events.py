"""Signed Lithic Events API callback reducer for the *local demo* ledger.

Never a source of authorization: ASA already holds funds. Events confirm, release,
or clear that SAME hold. Unsupported situations are quarantined (fail closed).
Complex multi-completion, over-capture, returns and force-post require a fuller ledger.
"""
from __future__ import annotations
from occlet_sql_store import enabled, _connect
from occlet_demo_ledger import LedgerRejected, release, clear


def normalize_event(envelope: dict) -> dict:
    """Lithic Events API envelope has event_type + payload; payload also has event_type."""
    if not isinstance(envelope,dict) or envelope.get('event_type')!='card_transaction.updated':
        raise LedgerRejected('UNSUPPORTED_EVENT_TYPE')
    txn=envelope.get('payload') if isinstance(envelope.get('payload'),dict) else envelope
    tx=txn.get('token')
    card=txn.get('card_token')
    account=txn.get('account_token')
    status=txn.get('status')
    if not all(isinstance(x,str) and x for x in (tx,card,status)):
        raise LedgerRejected('INVALID_LITHIC_TRANSACTION_EVENT')
    amounts=txn.get('amounts') or {}
    settlement=amounts.get('settlement') if isinstance(amounts,dict) else {}
    if not isinstance(settlement,dict):
        settlement={}
    raw_amount=settlement.get('amount')
    currency=settlement.get('currency')
    if status=='SETTLED':
        if not isinstance(raw_amount,int) or isinstance(raw_amount,bool) or raw_amount==0 or currency!='USD':
            raise LedgerRejected('INVALID_SETTLEMENT_CURRENCY_OR_AMOUNT')
        # Lithic's USD debit settlement amounts are negative; positive is a
        # credit/return and must NOT silently be posted as a debit purchase.
        if raw_amount>0:
            raise LedgerRejected('CREDIT_OR_UNSUPPORTED_SETTLEMENT_DIRECTION')
    if status not in {'PENDING','SETTLED','DECLINED','VOIDED','EXPIRED'}:
        raise LedgerRejected('UNSUPPORTED_TRANSACTION_STATUS')
    return {'transaction_token':tx,'card_token':card,'account_token':account,
            'status':status,'result':txn.get('result'),
            'settlement_cents':abs(raw_amount) if status=='SETTLED' else 0,
            'polarity':(txn.get('events') or [{}])[-1].get('effective_polarity') if isinstance(txn.get('events'),list) and txn.get('events') else None}


def action_for(event: dict) -> str:
    if event['status']=='PENDING':
        return 'RELEASE' if event.get('result') not in (None,'APPROVED') else 'CONFIRM'
    if event['status']=='SETTLED':
        return 'CLEAR'
    return 'RELEASE'


def process_signed_event(*, arrangement_id: str, event_id: str, envelope: dict,
                         asa_reference: dict, expected_account: str | None = None) -> dict:
    """Expects an already-verified signature + a prior Occlet ASA approval.

    Inbox persisted before returning ACK. Journal operations idempotent independently
    using the authorization reference; repeated event IDs cannot double-post.
    """
    if not enabled():
        raise LedgerRejected('POSTGRES_NOT_CONFIGURED')
    event=normalize_event(envelope)
    tx=event['transaction_token']
    if not event_id or len(event_id)>180:
        raise LedgerRejected('INVALID_WEBHOOK_ID')
    ref='lithic:'+tx
    if not asa_reference or asa_reference.get('result')!='APPROVED' or asa_reference.get('card_token')!=event['card_token']:
        raise LedgerRejected('UNKNOWN_OR_MISMATCHED_ASA_AUTHORIZATION')
    if expected_account and event['account_token']!=expected_account:
        raise LedgerRejected('ACCOUNT_TOKEN_MISMATCH')
    action=action_for(event)
    # Guard against non-debit / unsupported partial or incremental clearing.
    if event['status']=='SETTLED':
        if event['polarity']=='CREDIT':
            raise LedgerRejected('CREDIT_EVENT_REQUIRES_SEPARATE_REFUND_POSTING')
        if event['settlement_cents']>asa_reference.get('amount',0):
            raise LedgerRejected('SETTLEMENT_ABOVE_HOLD_REQUIRES_INCREMENTAL_AUTH')
    with _connect() as conn:
        existing=conn.execute('''SELECT transaction_token, event_status,processing_state
          FROM demo_lithic_event_inbox WHERE event_id=%s''',(event_id,)).fetchone()
        if existing:
            if (existing[0],existing[1])!=(tx,event['status']):
                raise LedgerRejected('WEBHOOK_ID_REUSE_CONFLICT')
            if existing[2]=='PROCESSED':
                return {'status':'ALREADY_PROCESSED','action':action,'idempotent':True,'transaction_token':tx}
        else:
            conn.execute('''INSERT INTO demo_lithic_event_inbox
             (event_id,arrangement_id,transaction_token,event_status,processing_state)
             VALUES (%s,%s,%s,%s,'RECEIVED')''',(event_id,arrangement_id,tx,event['status']))
    # SQL ledger owns authorization holds; callback does not create a second hold.
    if action=='RELEASE':
        result=release(arrangement_id,ref)
    elif action=='CLEAR':
        result=clear(arrangement_id,ref,event['settlement_cents'])
    else:
        result={'status':'CONFIRMED','idempotent':False}
    with _connect() as conn:
        conn.execute('''UPDATE demo_lithic_event_inbox SET processing_state='PROCESSED',
         processed_at=now(),detail=%s WHERE event_id=%s''',(action,event_id))
    return {'status':'PROCESSED','action':action,'idempotent':result.get('idempotent',False),
            'transaction_token':tx,'ledger_status':result.get('status')}


def event_history(arrangement_id: str) -> list[dict]:
    if not enabled():
        return []
    with _connect() as conn:
        rows=conn.execute('''SELECT event_id,transaction_token,event_status,processing_state,detail,created_at
          FROM demo_lithic_event_inbox WHERE arrangement_id=%s ORDER BY created_at DESC LIMIT 20''',(arrangement_id,)).fetchall()
        return [dict(zip(('event_id','transaction_token','event_status','processing_state','detail','created_at'),r)) for r in rows]

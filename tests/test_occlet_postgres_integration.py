"""Opt-in real Postgres integration: set DATABASE_URL + OCCLET_RUN_DB_TESTS=1.

Runs ONLY with an explicit opt-in. Creates and deletes a unique arrangement.
"""
import os
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

pytestmark=pytest.mark.skipif(os.getenv('OCCLET_RUN_DB_TESTS')!='1' or not os.getenv('DATABASE_URL'),
                           reason='Requires explicit local Postgres test opt-in')


def test_real_postgres_atomic_holds_clearing_and_reversal():
    import occlet_sql_store
    from occlet_demo_ledger import initialize, authorize, summary, clear, release, LedgerRejected
    occlet_sql_store.initialize()
    arrangement='test_'+uuid.uuid4().hex
    parent={'status':'ACTIVE','lithic_account_token':None}
    try:
        starting=initialize(arrangement,10000,parent)
        assert starting['available_cents']==10000
        assert starting['cash_cents']==10000
        # Row-lock serializes two simultaneous $70 authorizations against $100.
        def attempt(i):
            try:
                return authorize(arrangement,'concurrent-'+str(i),7000,
                    card_reference='card-'+str(i),merchant_id='GROCERY_101',
                    max_transaction_cents=10000,monthly_limit_cents=20000)
            except LedgerRejected as exc:
                return exc.code
        with ThreadPoolExecutor(max_workers=2) as executor:
            attempts=list(executor.map(attempt,[1,2]))
        assert sum(isinstance(x,dict) for x in attempts)==1
        assert 'INSUFFICIENT_FUNDS' in attempts
        b=summary(arrangement)
        assert b['available_cents']==3000 and b['held_cents']==7000 and b['cash_cents']==10000
        auth=next(a for a in b['authorizations'] if a['status']=='AUTHORIZED')
        reference=auth['reference']
        assert authorize(arrangement,reference,7000,card_reference=auth['card_reference'],
                         merchant_id='GROCERY_101',max_transaction_cents=10000,
                         monthly_limit_cents=20000)['idempotent'] is True
        cleared=clear(arrangement,reference,6500)
        assert cleared['balance']['available_cents']==3500
        assert cleared['balance']['held_cents']==0
        assert cleared['balance']['cash_cents']==3500
        assert clear(arrangement,reference,6500)['idempotent'] is True
        with pytest.raises(LedgerRejected):
            release(arrangement,reference)
        # Another new auth and reversal should release entire hold.
        authorize(arrangement,'reverse-me',2000,card_reference='card-a',merchant_id='GROCERY_101',
                  max_transaction_cents=10000,monthly_limit_cents=20000)
        assert release(arrangement,'reverse-me')['balance']['available_cents']==3500
        # Verify every posted journal is balanced in SQL.
        with occlet_sql_store._connect() as conn:
            rows=conn.execute('''SELECT j.id, SUM(CASE WHEN e.side='DEBIT' THEN e.amount_cents ELSE -e.amount_cents END)
               FROM demo_ledger_journals j JOIN demo_ledger_entries e ON e.journal_id=j.id
               WHERE j.arrangement_id=%s GROUP BY j.id''',(arrangement,)).fetchall()
            assert rows and all(total==0 for _,total in rows)
    finally:
        with occlet_sql_store._connect() as conn:
            conn.execute('DELETE FROM funding_arrangements WHERE arrangement_id=%s',(arrangement,))

"""Real PostgreSQL W4 scenario. Run with DATABASE_URL and OCCLET_RUN_DB_TESTS=1."""
import os,uuid,pytest
pytestmark=pytest.mark.skipif(os.getenv('OCCLET_RUN_DB_TESTS')!='1' or not os.getenv('DATABASE_URL'),
                            reason='Requires locally running PostgreSQL and explicit opt-in')

def test_w4_postgres_denial_release_retry_clear_and_journals():
    from occlet_sql_store import initialize as migrate,_connect
    from occlet_demo_ledger import initialize,authorize,clear,summary,LedgerRejected
    from occlet_w4 import seed_trip,overview,release_protection
    migrate()
    arr='test_w4_'+uuid.uuid4().hex
    try:
        initialize(arr,50000,{'status':'ACTIVE','lithic_account_token':None})
        p=seed_trip(arr,30000)
        assert p['balance']['cash_cents']==50000 and p['free_to_spend_cents']==20000
        def purchase(ref):
            return authorize(arr,ref,35000,card_reference='parent_demo_card',merchant_id='GROCERY_101',
                             max_transaction_cents=50000,monthly_limit_cents=50000)
        with pytest.raises(LedgerRejected,match='PROTECTED_BY_MEMBER'):
            purchase('w4-before')
        assert summary(arr)['held_cents']==0
        assert len(overview(arr)['decisions'])==1
        before=len(summary(arr)['journal_lines'])
        release_protection(arr,15000,'release-1')
        assert len(summary(arr)['journal_lines'])==before, 'Member permission edit must create no financial journal'
        assert overview(arr)['protected_cents']==15000
        with pytest.raises(LedgerRejected,match='PREVIOUS_W4_DECISION_REQUIRES_NEW_REFERENCE'):
            purchase('w4-before')
        r=purchase('w4-after')
        assert r['balance']['held_cents']==35000 and r['balance']['cash_cents']==50000
        assert r['balance']['available_cents']==15000
        assert purchase('w4-after')['idempotent'] is True
        c=clear(arr,'w4-after',34500)
        assert c['balance']['cash_cents']==15500
        assert c['balance']['available_cents']==15500
        assert c['balance']['held_cents']==0
        assert overview(arr)['protected_cents']==15000
        with _connect() as conn:
            rows=conn.execute('''SELECT j.id, SUM(CASE WHEN e.side='DEBIT' THEN e.amount_cents ELSE -e.amount_cents END)
              FROM demo_ledger_journals j JOIN demo_ledger_entries e ON e.journal_id=j.id
              WHERE j.arrangement_id=%s GROUP BY j.id''',(arr,)).fetchall()
            assert rows and all(total==0 for _,total in rows)
    finally:
        with _connect() as conn:
            conn.execute('DELETE FROM funding_arrangements WHERE arrangement_id=%s',(arr,))

from pathlib import Path
from tests.test_financial_planning_migration import _run_alembic, _connect, _schema, _business_rows


def test_finance_upgrade_preserves_data_and_empty_downgrade(tmp_path:Path):
    data=tmp_path/'finance-migration';_run_alembic(data,'upgrade','20260928_0031')
    with _connect(data) as db: before_schema,before_rows=_schema(db),_business_rows(db)
    _run_alembic(data,'upgrade','20260928_0032')
    with _connect(data) as db:
        new=_schema(db)
        assert len({name for kind,name in new.keys()-before_schema.keys() if kind=='table'})==7
        for key,value in before_schema.items(): assert new[key]==value
        for table,rows in before_rows.items(): assert db.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall()==rows
        assert db.execute('PRAGMA foreign_key_check').fetchall()==[]
    _run_alembic(data,'downgrade','20260928_0031')
    with _connect(data) as db: assert _schema(db)==before_schema and _business_rows(db)==before_rows


def test_finance_populated_downgrade_refused(tmp_path:Path):
    data=tmp_path/'retained-finance';_run_alembic(data,'upgrade','20260928_0032')
    with _connect(data) as db:
        bid=db.execute("SELECT id FROM buildings WHERE code='DEMO_A'").fetchone()[0]
        db.execute("""INSERT INTO finance_cash (building_id,entry_key,request_hash,direction,occurred_on,amount,counterparty,reference,note,origin,revision,voided,created_by,created_at) VALUES (?,'test-key','hash','in','2026-01-01',100,'demo','','','manual',1,0,'test','2026-09-28')""",(bid,));db.commit()
    result=_run_alembic(data,'downgrade','20260928_0031',expect_success=False)
    assert '不可自动删除' in result.stderr+result.stdout
    with _connect(data) as db: assert db.execute('SELECT amount FROM finance_cash').fetchone()[0]==100

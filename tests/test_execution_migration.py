from pathlib import Path
from tests.test_financial_planning_migration import _run_alembic, _connect, _schema, _business_rows

def test_execution_upgrade_preserves_old_schema_rows_and_empty_roundtrip(tmp_path:Path):
    data=tmp_path/'execution'
    _run_alembic(data,'upgrade','20260928_0030')
    with _connect(data) as db: before_schema,before_rows=_schema(db),_business_rows(db)
    _run_alembic(data,'upgrade','20260928_0031')
    with _connect(data) as db:
        new=_schema(db)
        assert {name for kind,name in new.keys()-before_schema.keys() if kind=='table'}=={'execution_actions'}
        for key,value in before_schema.items(): assert new[key]==value
        for table,rows in before_rows.items(): assert db.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall()==rows
        assert db.execute('PRAGMA foreign_key_check').fetchall()==[]
    _run_alembic(data,'downgrade','20260928_0030')
    with _connect(data) as db: assert _schema(db)==before_schema and _business_rows(db)==before_rows

def test_execution_records_block_destructive_downgrade(tmp_path:Path):
    data=tmp_path/'retained-execution';_run_alembic(data,'upgrade','20260928_0031')
    with _connect(data) as db:
        bid=db.execute("SELECT id FROM buildings WHERE code='DEMO_A'").fetchone()[0]
        db.execute("INSERT INTO contracts (id,contract_no,building_id,start_date,end_date) VALUES (1,'TEST',?,'2026-01-01','2026-12-31')",(bid,))
        db.execute("""INSERT INTO execution_actions (building_id,source_key,kind,contract_id,title,source_json,owner,due_on,status,decision,next_action,result_note,verification_json,benefit_json,revision,created_by,created_at,updated_at) VALUES (?,'expiry:1','expiry',1,'Test','{}','test','2026-12-01','open','undecided','Review','','{}','{}',1,'test','2026-09-28','2026-09-28')""",(bid,));db.commit();before=_business_rows(db)
    result=_run_alembic(data,'downgrade','20260928_0030',expect_success=False)
    assert '不可自动删除' in result.stderr+result.stdout
    with _connect(data) as db: assert _business_rows(db)==before

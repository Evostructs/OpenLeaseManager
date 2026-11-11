from pathlib import Path

from tests.test_financial_planning_migration import _run_alembic, _connect, _schema, _business_rows


def test_payment_receipt_upgrade_preserves_old_rows_and_empty_roundtrip(tmp_path: Path):
    data = tmp_path / 'payment-migration'
    _run_alembic(data, 'upgrade', '20260910_0029')
    with _connect(data) as db:
        db.execute("INSERT INTO system_config (key, value) VALUES ('r1a-sentinel', 'unchanged')")
        db.commit()
        old_schema, old_rows = _schema(db), _business_rows(db)
    _run_alembic(data, 'upgrade', '20260928_0030')
    with _connect(data) as db:
        new_schema = _schema(db)
        assert {name for kind, name in new_schema.keys() - old_schema.keys() if kind == 'table'} == {'receivable_payment_operations'}
        for key, definition in old_schema.items():
            assert new_schema[key] == definition
        for table, rows in old_rows.items():
            assert db.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall() == rows
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
    _run_alembic(data, 'downgrade', '20260910_0029')
    with _connect(data) as db:
        assert _schema(db) == old_schema
        assert _business_rows(db) == old_rows


def test_populated_receipt_cannot_be_downgraded_even_after_payment_deletion(tmp_path: Path):
    data = tmp_path / 'retained-receipt'
    _run_alembic(data, 'upgrade', '20260928_0030')
    with _connect(data) as db:
        db.execute("""INSERT INTO contracts (id, contract_no, building_id, start_date, end_date)
                      VALUES (1, 'R1A', (SELECT id FROM buildings WHERE code='DEMO_A'), '2026-01-01', '2026-12-31')""")
        db.execute("""INSERT INTO receivable_payments (id, contract_id, period_month, amount)
                      VALUES (1, 1, '2026-09', 100)""")
        db.execute("""INSERT INTO receivable_payment_operations
                      (operation_id, contract_id, payment_id, request_hash, created_at)
                      VALUES ('00000000-0000-4000-8000-000000000001', 1, 1, 'test', '2026-09-28')""")
        db.execute('DELETE FROM receivable_payments WHERE id=1')
        db.commit()
        assert db.execute('SELECT payment_id FROM receivable_payment_operations').fetchone()[0] is None
        before = _business_rows(db)
    failed = _run_alembic(data, 'downgrade', '20260910_0029', expect_success=False)
    assert '收款操作回执不可自动删除' in failed.stderr + failed.stdout
    with _connect(data) as db:
        assert _business_rows(db) == before
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'

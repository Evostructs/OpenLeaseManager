import importlib.util
from pathlib import Path
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine,text

def test_formal_names_are_idempotent_and_preserve_custom_names():
    path=Path(__file__).parents[1]/'alembic/versions/20261001_0033_formal_display_names.py'
    spec=importlib.util.spec_from_file_location('formal_names_migration',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    engine=create_engine('sqlite://')
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE portfolios (code TEXT, name TEXT)'));conn.execute(text('CREATE TABLE buildings (code TEXT, name TEXT)'))
        conn.execute(text('INSERT INTO portfolios VALUES (:code,:name)'),dict(code='DEMO_OFFICE_PORTFOLIO',name='示例资产组合（写字楼，暂定）'))
        conn.execute(text('INSERT INTO buildings VALUES (:code,:name)'),[dict(code='DEMO_A',name='示例双塔中心（暂定）'),dict(code='CUSTOM',name='用户自定义楼宇')])
        with Operations.context(MigrationContext.configure(conn)):
            module.upgrade();module.upgrade()
        assert conn.execute(text('SELECT name FROM portfolios')).scalar()=='示例资产组合（写字楼）'
        assert conn.execute(text('SELECT name FROM buildings WHERE code="DEMO_A"')).scalar()=='示例双塔中心'
        assert conn.execute(text('SELECT name FROM buildings WHERE code="CUSTOM"')).scalar()=='用户自定义楼宇'

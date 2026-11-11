from __future__ import annotations

import os
from datetime import date
from pathlib import Path
import shutil
import tempfile

import pytest


TEST_DATA_DIR = tempfile.mkdtemp(prefix="lease-manager-test-")
os.environ["DATA_DIR"] = TEST_DATA_DIR
os.environ["JWT_SECRET"] = "test-secret-not-for-production-32bytes"
os.environ["COOKIE_SECURE"] = "false"
os.environ["TOKEN_EXPIRE_HOURS"] = "2"
os.environ["MIN_PASSWORD_LENGTH"] = "8"
os.environ["DATA_ENCRYPTION_ENABLED"] = "true"
os.environ["DATA_ENCRYPTION_KEY"] = "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="


@pytest.fixture(autouse=True)
def isolated_login_throttle():
    # 登录限流计数器是进程级内存状态，跨测试自动重置，
    # 避免其它用例的故意失败登录累积触发锁定
    from app.core.login_throttle import reset_login_throttle

    reset_login_throttle()
    yield
    reset_login_throttle()


@pytest.fixture(autouse=True)
def isolated_database():
    from importlib.util import module_from_spec, spec_from_file_location

    from sqlalchemy import text

    from app.core.config import get_settings
    from app.core.migrations import run_migrations
    from app.database import SessionLocal, engine
    from app.models import (
        AreaMeasurement,
        Attachment,
        AuthorizationControl,
        Building,
        BuildingMetricFact,
        BuildingOperatingGateEvent,
        BuildingOperatingSnapshot,
        Contract,
        ContractDeposit,
        ContractRoom,
        Customer,
        CustomerBuildingProfile,
        CustomerFollowUp,
        DataSource,
        ExecutionAction,
        FinancialExpense,
        FinancialPlanVersion,
        FinancialPlanningState,
        ImportBatch,
        ImportError,
        MetricDefinition,
        OperationLog,
        Portfolio,
        PortfolioBuilding,
        ReceivablePayment,
        ReceivablePaymentOperation,
        ReceivableRecord,
        ReceivableStatement,
        RentTerm,
        Room,
        SnapshotExchangeReceipt,
        SnapshotExchangeSourceBinding,
        SourceRecord,
        SystemConfig,
        User,
        UserAuthorizationRevision,
        UserCapability,
        UserPortfolioOverviewGrant,
        UserScopeGrant,
        Zone,
    )

    run_migrations()
    settings = get_settings()
    official_membership_dates = {
        "DEMO_A": date(2026, 8, 4),
        "DEMO_B": date(2026, 8, 5),
        "DEMO_C": date(2026, 8, 5),
        "DEMO_D": date(2026, 8, 5),
        "DEMO_E": date(2026, 8, 5),
    }
    seed_db = SessionLocal()
    try:
        seed_portfolio = (
            seed_db.query(Portfolio)
            .filter(Portfolio.code == "DEMO_OFFICE_PORTFOLIO")
            .one()
        )
        seed_portfolio_state = {
            column: getattr(seed_portfolio, column)
            for column in (
                "id",
                "code",
                "name",
                "is_provisional",
                "is_active",
                "created_at",
                "updated_at",
            )
        }
        seed_building_state = {
            item.code: {
                column: getattr(item, column)
                for column in (
                    "id",
                    "code",
                    "name",
                    "timezone",
                    "currency",
                    "ingestion_mode",
                    "is_provisional",
                    "is_active",
                    "created_at",
                    "updated_at",
                )
            }
            for item in seed_db.query(Building)
            .filter(Building.code.in_(tuple(official_membership_dates)))
            .all()
        }
        if set(seed_building_state) != set(official_membership_dates):
            raise RuntimeError("五栋正式楼宇静态种子不完整，测试初始化已停止")
        demo_a_seed_id = int(seed_building_state["DEMO_A"]["id"])
        seed_zone_state = {
            int(item.id): {
                column: getattr(item, column)
                for column in (
                    "id",
                    "building_id",
                    "code",
                    "name",
                    "zone_type",
                    "sort_order",
                    "valid_from",
                    "valid_to",
                    "is_active",
                    "created_at",
                    "updated_at",
                )
            }
            for item in seed_db.query(Zone).filter(Zone.building_id == demo_a_seed_id).all()
        }
        if len(seed_zone_state) != 3:
            raise RuntimeError("DEMO_A 静态分区种子不完整，测试初始化已停止")
        seed_gate_ids = tuple(
            int(item.id)
            for item in seed_db.query(BuildingOperatingGateEvent)
            .filter(BuildingOperatingGateEvent.origin == "migration")
            .order_by(BuildingOperatingGateEvent.id.asc())
            .all()
        )
        if len(seed_gate_ids) != 5:
            raise RuntimeError("五栋正式楼宇开闸种子不完整，测试初始化已停止")
    finally:
        seed_db.close()
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "20260805_0024_ops_v1_publication_workflow.py"
    )
    migration_spec = spec_from_file_location("test_ops_v1_publication_guards", migration_path)
    if migration_spec is None or migration_spec.loader is None:
        raise RuntimeError("无法加载 OPS_V1 发布保护迁移")
    publication_guards = module_from_spec(migration_spec)
    migration_spec.loader.exec_module(publication_guards)
    gate_migration_path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "20260805_0026_building_operating_gates.py"
    )
    gate_migration_spec = spec_from_file_location("test_building_operating_gate_guards", gate_migration_path)
    if gate_migration_spec is None or gate_migration_spec.loader is None:
        raise RuntimeError("无法加载楼宇经营开闸保护迁移")
    gate_guards = module_from_spec(gate_migration_spec)
    gate_migration_spec.loader.exec_module(gate_guards)
    exchange_migration_path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "20260806_0028_snapshot_exchange.py"
    )
    exchange_migration_spec = spec_from_file_location(
        "test_snapshot_exchange_guards",
        exchange_migration_path,
    )
    if exchange_migration_spec is None or exchange_migration_spec.loader is None:
        raise RuntimeError("无法加载快照交换回执保护迁移")
    exchange_guards = module_from_spec(exchange_migration_spec)
    exchange_migration_spec.loader.exec_module(exchange_guards)

    finance_spec = spec_from_file_location("test_finance_guards", Path(__file__).resolve().parents[1] / "alembic/versions/20260928_0032_finance_operations.py")
    finance_guards = module_from_spec(finance_spec)
    finance_spec.loader.exec_module(finance_guards)
    from app.finance_models import FinanceCash, FinanceAllocation, FinanceBankLine, FinanceBankMatch, FinanceExpenseDetail, FinancePeriod, FinancePeriodEvent

    def cleanup() -> None:
        # Hold the same physical connection until FK restoration. Session.commit
        # can otherwise return the disabled connection to the pool and restore a
        # different one when concurrent tests have created more than one entry.
        cleanup_connection = engine.connect()
        db = SessionLocal(bind=cleanup_connection)
        try:
            # Exchange receipts form immutable self-referencing chains and also
            # point to candidate rows through RESTRICT links.  Disable FK checks
            # only on this test-cleanup connection while removing the whole graph.
            db.execute(text("PRAGMA foreign_keys = OFF"))
            if db.execute(text("PRAGMA foreign_keys")).scalar_one() != 0:
                raise RuntimeError("测试清理无法暂时关闭 SQLite 外键检查")
            # Production keeps authorization revisions immutable.  Test cases
            # share one temporary database, so isolation explicitly removes and
            # recreates only those two guards around fixture cleanup.
            db.execute(text("DROP TRIGGER IF EXISTS trg_user_authorization_revisions_no_delete"))
            db.execute(text("DROP TRIGGER IF EXISTS trg_user_authorization_revisions_no_update"))
            db.execute(text(f"DROP TRIGGER IF EXISTS {gate_guards.DELETE_TRIGGER}"))
            db.execute(text(f"DROP TRIGGER IF EXISTS {gate_guards.UPDATE_TRIGGER}"))
            for trigger_name in finance_guards.guard_sql():
                db.execute(text(f"DROP TRIGGER IF EXISTS {trigger_name}"))
            for trigger_name in publication_guards.TRIGGERS:
                db.execute(text(f"DROP TRIGGER IF EXISTS {trigger_name}"))
            for trigger_name in exchange_guards.RECEIPT_TRIGGERS:
                db.execute(text(f"DROP TRIGGER IF EXISTS {trigger_name}"))
            # Published correction chains are self-referential. Test isolation
            # severs only those links before bulk cleanup; production never does.
            db.query(BuildingOperatingSnapshot).update(
                {BuildingOperatingSnapshot.supersedes_id: None},
                synchronize_session=False,
            )
            db.query(ImportBatch).update(
                {ImportBatch.supersedes_id: None},
                synchronize_session=False,
            )
            db.query(BuildingOperatingGateEvent).filter(
                BuildingOperatingGateEvent.id.notin_(seed_gate_ids)
            ).delete(synchronize_session=False)
            # 板块、五栋正式楼宇、成员关系和 DEMO_A 分区是迁移后的静态种子，跨用例保留。
            # 其余新表按外键依赖顺序清理，避免测试数据污染后续用例。
            for model in (
                FinancePeriodEvent, FinancePeriod, FinanceBankMatch, FinanceAllocation, FinanceExpenseDetail, FinanceCash, FinanceBankLine,
                ExecutionAction,
                FinancialPlanningState,
                FinancialPlanVersion,
                FinancialExpense,
                UserAuthorizationRevision,
                SnapshotExchangeReceipt,
                BuildingMetricFact,
                AreaMeasurement,
                ImportError,
                SourceRecord,
                ImportBatch,
                BuildingOperatingSnapshot,
                MetricDefinition,
                CustomerBuildingProfile,
                UserCapability,
                UserPortfolioOverviewGrant,
                UserScopeGrant,
                SnapshotExchangeSourceBinding,
                DataSource,
                Attachment,
                ReceivablePaymentOperation,
                ReceivablePayment,
                ReceivableRecord,
                ReceivableStatement,
                ContractDeposit,
                RentTerm,
                ContractRoom,
                CustomerFollowUp,
                OperationLog,
                Contract,
                Customer,
                Room,
                User,
                SystemConfig,
            ):
                db.query(model).delete()
            official_ids = tuple(
                int(state["id"]) for state in seed_building_state.values()
            )
            seed_zone_ids = tuple(seed_zone_state)
            db.query(PortfolioBuilding).delete(synchronize_session=False)
            db.query(Zone).filter(Zone.id.notin_(seed_zone_ids)).delete(
                synchronize_session=False
            )
            db.query(Building).filter(Building.id.notin_(official_ids)).delete(
                synchronize_session=False
            )
            db.query(Portfolio).filter(
                Portfolio.id != int(seed_portfolio_state["id"])
            ).delete(synchronize_session=False)

            portfolio = db.query(Portfolio).filter(
                Portfolio.id == int(seed_portfolio_state["id"])
            ).one_or_none()
            official_buildings = {
                code: db.query(Building).filter(
                    Building.id == int(state["id"])
                ).one_or_none()
                for code, state in seed_building_state.items()
            }
            seed_zones = {
                zone_id: db.query(Zone).filter(Zone.id == zone_id).one_or_none()
                for zone_id in seed_zone_state
            }
            if (
                portfolio is None
                or any(item is None for item in official_buildings.values())
                or any(item is None for item in seed_zones.values())
            ):
                raise RuntimeError("五栋正式楼宇静态种子不完整，测试隔离已停止")

            for column, value in seed_portfolio_state.items():
                setattr(portfolio, column, value)
            for code, state in seed_building_state.items():
                building = official_buildings[code]
                for column, value in state.items():
                    setattr(building, column, value)
            for zone_id, state in seed_zone_state.items():
                zone = seed_zones[zone_id]
                for column, value in state.items():
                    setattr(zone, column, value)
            db.flush()

            # 只重建五条迁移定义的成员关系，避免测试创建的同楼多板块关系泄漏。
            for code, valid_from in official_membership_dates.items():
                building = official_buildings[code]
                db.add(
                    PortfolioBuilding(
                        portfolio_id=portfolio.id,
                        building_id=building.id,
                        valid_from=valid_from,
                        valid_to=None,
                        created_at=building.created_at,
                    )
                )
            db.flush()
            db.query(AuthorizationControl).filter(AuthorizationControl.id == 1).update(
                {AuthorizationControl.global_revision: 0},
                synchronize_session=False,
            )
            db.execute(text(
                """
                CREATE TRIGGER trg_user_authorization_revisions_no_update
                BEFORE UPDATE ON user_authorization_revisions
                BEGIN
                    SELECT RAISE(ABORT, 'authorization revision history is immutable');
                END
                """
            ))
            db.execute(text(
                """
                CREATE TRIGGER trg_user_authorization_revisions_no_delete
                BEFORE DELETE ON user_authorization_revisions
                BEGIN
                    SELECT RAISE(ABORT, 'authorization revision history is immutable');
                END
                """
            ))
            for sql in finance_guards.guard_sql().values():
                db.execute(text(sql))
            publication_guards._create_guards(db.connection())
            gate_guards._create_immutable_guards(db.connection())
            exchange_guards._create_receipt_immutability_guards(db.connection())
            db.commit()
        finally:
            db.rollback()
            try:
                db.execute(text("PRAGMA foreign_keys = ON"))
                if db.execute(text("PRAGMA foreign_keys")).scalar_one() != 1:
                    raise RuntimeError("测试清理未能恢复 SQLite 外键检查")
            finally:
                db.close()
                cleanup_connection.close()
        for directory in (settings.upload_dir, settings.attachment_dir, settings.backup_dir):
            if directory.exists():
                shutil.rmtree(directory)
            Path(directory).mkdir(parents=True, exist_ok=True)

    cleanup()
    yield
    cleanup()

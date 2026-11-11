from __future__ import annotations

from datetime import timedelta

import pytest

from app.core.time import today_in_timezone
from app.database import SessionLocal
from app.models import Building, Contract, SystemConfig
from app.services.legacy_demo_a_scope import ObjectScope
from app.tasks import building_feishu_config_key


@pytest.fixture
def task_building_ids():
    created: list[int] = []
    yield created
    if not created:
        return
    db = SessionLocal()
    try:
        db.query(Contract).filter(Contract.building_id.in_(created)).delete(synchronize_session=False)
        db.query(Building).filter(Building.id.in_(created)).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def _add_building(
    task_building_ids: list[int],
    *,
    code: str,
    name: str,
    ingestion_mode: str,
    is_active: bool = True,
) -> int:
    db = SessionLocal()
    try:
        building = Building(
            code=code,
            name=name,
            ingestion_mode=ingestion_mode,
            is_active=is_active,
        )
        db.add(building)
        db.commit()
        building_id = int(building.id)
        task_building_ids.append(building_id)
        return building_id
    finally:
        db.close()


def _add_expiring_contract(*, building_id: int, contract_no: str, tenant_name: str) -> None:
    today = today_in_timezone()
    db = SessionLocal()
    try:
        db.add(
            Contract(
                building_id=building_id,
                contract_no=contract_no,
                tenant_name=tenant_name,
                tower="南塔",
                room_no=contract_no,
                area=100,
                unit_price=4,
                monthly_rent=12000,
                start_date=today - timedelta(days=300),
                end_date=today + timedelta(days=30),
                internal_contact_person="测试责任人",
                is_deleted=False,
            )
        )
        db.commit()
    finally:
        db.close()


def test_expiry_reminders_are_isolated_by_explicit_detail_building(monkeypatch, task_building_ids) -> None:
    from app.tasks import check_expiring_contracts

    db = SessionLocal()
    try:
        demo_a = db.query(Building).filter(Building.code == "DEMO_A").one()
        demo_a_id = int(demo_a.id)
        db.add(SystemConfig(key="feishu_webhook", value="https://example.test/demo_a"))
        db.commit()
    finally:
        db.close()

    other_id = _add_building(
        task_building_ids,
        code="TASK_DETAIL",
        name="后台任务明细楼",
        ingestion_mode="detail",
    )
    snapshot_id = _add_building(
        task_building_ids,
        code="TASK_SNAPSHOT",
        name="后台任务快照楼",
        ingestion_mode="snapshot",
    )
    db = SessionLocal()
    try:
        db.add(
            SystemConfig(
                key=building_feishu_config_key(other_id),
                value="https://example.test/other",
            )
        )
        db.commit()
    finally:
        db.close()

    _add_expiring_contract(building_id=demo_a_id, contract_no="TASK-DEMO_A", tenant_name="DEMO_A 客户")
    _add_expiring_contract(building_id=other_id, contract_no="TASK-OTHER", tenant_name="其他楼客户")
    _add_expiring_contract(building_id=snapshot_id, contract_no="TASK-SNAPSHOT", tenant_name="快照楼客户")

    sent: list[dict[str, str]] = []

    def fake_send(url: str, title: str, content: str) -> bool:
        sent.append({"url": url, "title": title, "content": content})
        return True

    monkeypatch.setattr("app.tasks.send_feishu_message", fake_send)
    check_expiring_contracts()

    assert len(sent) == 2
    assert {item["url"] for item in sent} == {
        "https://example.test/demo_a",
        "https://example.test/other",
    }
    demo_a_message = next(item for item in sent if "DEMO_A" in item["title"])
    other_message = next(item for item in sent if "后台任务明细楼" in item["title"])
    assert "DEMO_A" in demo_a_message["title"]
    assert "TASK-DEMO_A" in demo_a_message["content"]
    assert "TASK-OTHER" not in demo_a_message["content"]
    assert "后台任务明细楼" in other_message["title"]
    assert "TASK-OTHER" in other_message["content"]
    assert "TASK-DEMO_A" not in other_message["content"]
    assert demo_a_message["url"] == "https://example.test/demo_a"
    assert other_message["url"] == "https://example.test/other"
    assert all("TASK-SNAPSHOT" not in item["content"] for item in sent)


def test_monthly_freeze_processes_only_active_detail_buildings_with_explicit_scope(
    monkeypatch,
    task_building_ids,
) -> None:
    from app.tasks import monthly_statement_freeze

    db = SessionLocal()
    try:
        demo_a_id = int(db.query(Building).filter(Building.code == "DEMO_A").one().id)
        initial_detail_ids = {
            int(row.id)
            for row in db.query(Building)
            .filter(
                Building.is_active.is_(True),
                Building.ingestion_mode == "detail",
            )
            .all()
        }
    finally:
        db.close()

    detail_id = _add_building(
        task_building_ids,
        code="FREEZE_DETAIL",
        name="出账明细楼",
        ingestion_mode="detail",
    )
    _add_building(
        task_building_ids,
        code="FREEZE_SNAPSHOT",
        name="出账快照楼",
        ingestion_mode="snapshot",
    )
    _add_building(
        task_building_ids,
        code="FREEZE_EXTERNAL",
        name="出账外部楼",
        ingestion_mode="external",
    )
    _add_building(
        task_building_ids,
        code="FREEZE_DISABLED",
        name="停用明细楼",
        ingestion_mode="detail",
        is_active=False,
    )

    calls: list[dict[str, object]] = []

    def fake_generate(db, month, operator, regenerate=False, *, building_ids=None, object_scope=None):
        calls.append(
            {
                "month": month,
                "operator": operator,
                "regenerate": regenerate,
                "building_ids": tuple(building_ids or ()),
                "object_scope": object_scope,
            }
        )
        return {"created": 0, "updated": 0, "skipped": 0}

    monkeypatch.setattr("app.services.contracts.generate_receivable_statements", fake_generate)
    monthly_statement_freeze()

    assert demo_a_id in initial_detail_ids
    assert {call["building_ids"] for call in calls} == {
        (building_id,) for building_id in initial_detail_ids | {detail_id}
    }
    assert all(call["operator"] == "system" for call in calls)
    assert all(
        call["object_scope"]
        == ObjectScope(scope_type="building", building_id=call["building_ids"][0])
        for call in calls
    )

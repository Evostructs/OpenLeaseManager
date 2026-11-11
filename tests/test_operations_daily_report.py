from __future__ import annotations

from datetime import timedelta

from fastapi.testclient import TestClient

from app.core.time import today_in_timezone
from app.database import SessionLocal
from app.main import app
from app.models import Building, OperationLog


def _client() -> TestClient:
    return TestClient(app)


def _ensure_admin(client: TestClient) -> None:
    if client.get("/api/auth/status").json()["need_init"]:
        response = client.post(
            "/api/auth/register",
            json={"username": "admin", "password": "Admin123", "display_name": "Admin"},
        )
        assert response.status_code == 200


def _login(client: TestClient) -> None:
    response = client.post("/api/auth/login", json={"username": "admin", "password": "Admin123"})
    assert response.status_code == 200


def _room_payload(room_no: str) -> dict:
    return {"tower": "南塔", "floor": "9", "room_no": room_no, "room_type": "办公", "area": 100}


def _contract_payload(contract_no: str, room_no: str, customer_id: int, start_date, end_date) -> dict:
    return {
        "contract_no": contract_no,
        "customer_id": customer_id,
        "tower": "南塔",
        "room_no": room_no,
        "tenant_name": f"{contract_no} 客户",
        "area": 100,
        "unit_price": 4,
        "monthly_rent": 12000,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
    }


def test_operations_daily_report_requires_login() -> None:
    with _client() as client:
        response = client.get("/api/reports/operations-daily")
        assert response.status_code == 401


def test_operations_daily_report_exports_html_sections_and_short_audit_log() -> None:
    today = today_in_timezone()

    with _client() as client:
        _ensure_admin(client)
        _login(client)

        assert client.post("/api/rooms", json=_room_payload("REPORT-901")).status_code == 200
        customer = client.post("/api/customers", json={"name": "日报测试客户"}).json()
        contract_response = client.post(
            "/api/contracts",
            json=_contract_payload("DEMO_A-REPORT-001", "REPORT-901", customer["id"], today, today + timedelta(days=45)),
        )
        assert contract_response.status_code == 200

        response = client.get("/api/reports/operations-daily")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        html = response.text

        for text in [
            "经营日报",
            "报告日期",
            "当前月份",
            "生成时间",
            "版本",
            "打印 / 转 PDF",
            "今日重点",
            "今日经营动作摘要",
            "本月应收",
            "本月已收",
            "本月未收",
            "含未到期未收，不等同本月逾期",
            "到期日按合同付款日（每月固定日）计算，未约定付款日的合同以月末为到期日",
            "本月逾期",
            "历史逾期 / 回款处理队列",
            "90 天到期合同",
            "待跟进客户",
            "未收押金",
            "缺合同正文/附件缺失",
            "高风险项摘要",
            "近 6 个月回款趋势",
        ]:
            assert text in html
        for text in [
            "@media print",
            "@page",
            "size: A4 portrait",
            "page-break-inside: avoid",
            "display: table-header-group",
            "counter(page)",
        ]:
            assert text in html
        assert "裙楼" not in html
        for sensitive in ["Authorization", "password", "cookie", "token", "完整 prompt", "完整 AI output"]:
            assert sensitive.lower() not in html.lower()

        assert client.get(
            "/api/reports/operations-daily",
            params={"building": "NOT_AUTHORIZED"},
        ).status_code == 404
        assert client.get(
            "/api/reports/operations-daily",
            params={"building_id": 1},
        ).status_code == 422

        db = SessionLocal()
        try:
            log = db.query(OperationLog).filter(OperationLog.action == "daily_report_export").one()
            assert log.operator == "admin"
            assert log.target_type == "report"
            assert log.scope_type == "building"
            assert log.building_id == db.query(Building.id).filter(Building.code == "DEMO_A").scalar()
            assert today.strftime("%Y-%m") in (log.changes or "")
            assert "<html" not in (log.changes or "").lower()
            assert "经营日报" not in (log.changes or "")
        finally:
            db.close()

from __future__ import annotations

import asyncio
import hashlib
import io
import importlib
from types import SimpleNamespace

import openpyxl
import pytest
from fastapi import HTTPException, UploadFile
from fastapi.testclient import TestClient

from app.core.excel_safety import sanitize_cell_text, sanitize_workbook
from app.core.login_throttle import reset_login_throttle
from app.core.security import hash_password, password_needs_rehash, verify_password
from app.database import SessionLocal
from app.main import app
from app.models import User


@pytest.fixture(autouse=True)
def clean_login_throttle():
    reset_login_throttle()
    yield
    reset_login_throttle()


def _client() -> TestClient:
    return TestClient(app)


def _ensure_admin(client: TestClient) -> None:
    if client.get("/api/auth/status").json()["need_init"]:
        response = client.post(
            "/api/auth/register",
            json={"username": "admin", "password": "Admin123", "display_name": "Admin"},
        )
        assert response.status_code == 200


def test_login_locks_after_repeated_failures():
    with _client() as client:
        _ensure_admin(client)
        for _ in range(5):
            response = client.post("/api/auth/login", json={"username": "admin", "password": "Wrong123"})
            assert response.status_code == 401
        # 第 6 次即使密码正确也被锁定
        locked = client.post("/api/auth/login", json={"username": "admin", "password": "Admin123"})
        assert locked.status_code == 429
        assert "重试" in locked.json()["detail"]

        # 解锁后（测试里直接重置）正常登录，且失败计数已清零
        reset_login_throttle()
        ok = client.post("/api/auth/login", json={"username": "admin", "password": "Admin123"})
        assert ok.status_code == 200


def test_login_success_resets_failure_counter():
    with _client() as client:
        _ensure_admin(client)
        for _ in range(4):
            assert client.post("/api/auth/login", json={"username": "admin", "password": "Wrong123"}).status_code == 401
        assert client.post("/api/auth/login", json={"username": "admin", "password": "Admin123"}).status_code == 200
        # 成功登录清零计数：再失败 4 次仍不触发锁定
        for _ in range(4):
            assert client.post("/api/auth/login", json={"username": "admin", "password": "Wrong123"}).status_code == 401
        assert client.post("/api/auth/login", json={"username": "admin", "password": "Admin123"}).status_code == 200


def test_sanitize_cell_text_neutralizes_formula_prefixes():
    assert sanitize_cell_text("=HYPERLINK(\"http://evil\")").startswith("'=")
    assert sanitize_cell_text("+1+1").startswith("'+")
    assert sanitize_cell_text("-2+3").startswith("'-")
    assert sanitize_cell_text("@SUM(A1)").startswith("'@")
    assert sanitize_cell_text("正常客户名") == "正常客户名"
    assert sanitize_cell_text("") == ""


def test_sanitize_workbook_covers_all_sheets():
    workbook = openpyxl.Workbook()
    ws = workbook.active
    ws.append(["客户", "=cmd|'/c calc'!A1", 123])
    extra = workbook.create_sheet("第二页")
    extra.append(["@AT", "ok"])
    sanitize_workbook(workbook)
    assert ws.cell(row=1, column=2).value == "'=cmd|'/c calc'!A1"
    assert ws.cell(row=1, column=3).value == 123
    assert extra.cell(row=1, column=1).value == "'@AT"


def test_contract_export_neutralizes_formula_in_tenant_name():
    with _client() as client:
        _ensure_admin(client)
        assert client.post("/api/auth/login", json={"username": "admin", "password": "Admin123"}).status_code == 200
        created = client.post(
            "/api/contracts",
            json={
                "contract_no": "DEMO_A-XLSX-INJ",
                "tower": "南塔",
                "room_no": "INJ01",
                "tenant_name": "=HYPERLINK(\"http://evil.example\",\"点我\")",
                "area": 100,
                "unit_price": 4,
                "start_date": "2026-01-01",
                "end_date": "2026-12-31",
            },
        )
        assert created.status_code == 200, created.text

        exported = client.get("/api/contracts/export/excel")
        assert exported.status_code == 200
        workbook = openpyxl.load_workbook(io.BytesIO(exported.content), data_only=False)
        hits = []
        for sheet in workbook.worksheets:
            for row in sheet.iter_rows():
                for cell in row:
                    if isinstance(cell.value, str) and "HYPERLINK" in cell.value:
                        hits.append(cell.value)
        assert hits, "导出中应包含该租户名"
        assert all(value.startswith("'=") for value in hits)


def test_security_headers_present_on_api_and_index():
    with _client() as client:
        api_response = client.get("/api/auth/status")
        assert api_response.headers["X-Content-Type-Options"] == "nosniff"
        assert api_response.headers["X-Frame-Options"] == "DENY"
        assert "frame-ancestors 'none'" in api_response.headers["Content-Security-Policy"]
        assert "script-src 'self'" in api_response.headers["Content-Security-Policy"]

        index_response = client.get("/")
        assert index_response.headers["X-Content-Type-Options"] == "nosniff"
        assert "Content-Security-Policy" in index_response.headers


def test_daily_report_uses_relaxed_inline_script_csp():
    with _client() as client:
        _ensure_admin(client)
        assert client.post("/api/auth/login", json={"username": "admin", "password": "Admin123"}).status_code == 200
        response = client.get("/api/reports/operations-daily")
        assert response.status_code == 200
        csp = response.headers["Content-Security-Policy"]
        assert "script-src 'self' 'unsafe-inline'" in csp
        assert "frame-ancestors 'none'" in csp


def test_public_health_endpoint_reports_database_state(monkeypatch):
    with _client() as client:
        healthy = client.get("/healthz")
        assert healthy.status_code == 200
        assert healthy.json()["status"] == "ok"

        main_module = importlib.import_module("app.main")

        class UnavailableEngine:
            def connect(self):
                raise RuntimeError("database unavailable")

        monkeypatch.setattr(main_module, "engine", UnavailableEngine())
        unavailable = client.get("/healthz")
        assert unavailable.status_code == 503
        assert unavailable.json() == {"status": "unavailable"}


def test_new_password_hashes_use_argon2id():
    password_hash = hash_password("StrongerPassword123")
    assert password_hash.startswith("$argon2id$")
    assert verify_password("StrongerPassword123", password_hash)
    assert not verify_password("WrongPassword123", password_hash)
    assert not password_needs_rehash(password_hash)


def test_legacy_password_hash_is_upgraded_after_successful_login():
    password = "Legacy123"
    salt = "legacy-test-salt"
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120_000).hex()

    db = SessionLocal()
    try:
        db.add(
            User(
                username="legacy_user",
                display_name="Legacy User",
                password_hash=f"{salt}${digest}",
                role="admin",
                is_active=True,
                session_version=0,
            )
        )
        db.commit()
    finally:
        db.close()

    with _client() as client:
        response = client.post(
            "/api/auth/login",
            json={"username": "legacy_user", "password": password},
        )
        assert response.status_code == 200

    db = SessionLocal()
    try:
        upgraded = db.query(User).filter(User.username == "legacy_user").one()
        assert upgraded.password_hash.startswith("$argon2id$")
        assert verify_password(password, upgraded.password_hash)
    finally:
        db.close()


def test_declared_oversized_request_is_rejected_before_parsing():
    with _client() as client:
        response = client.post(
            "/api/auth/login",
            content=b"{}",
            headers={"content-type": "application/json", "content-length": str(1024**4)},
        )
        assert response.status_code == 413
        assert response.json()["detail"] == "请求内容过大"


def test_import_upload_stream_is_capped_even_without_declared_size(monkeypatch):
    uploads_module = importlib.import_module("app.core.uploads")
    monkeypatch.setattr(
        uploads_module,
        "get_settings",
        lambda: SimpleNamespace(max_import_file_size_bytes=4),
    )
    upload = UploadFile(filename="oversized.xlsx", file=io.BytesIO(b"12345"), size=None)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(uploads_module.read_import_upload(upload))

    assert exc_info.value.status_code == 413

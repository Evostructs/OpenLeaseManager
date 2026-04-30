from __future__ import annotations

import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time

import httpx
import pytest


pytestmark = pytest.mark.e2e

if os.environ.get("LEASE_E2E") != "1":
    pytest.skip("Set LEASE_E2E=1 to run browser E2E tests.", allow_module_level=True)

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect, sync_playwright  # noqa: E402


ROOT = Path(__file__).resolve().parents[2]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_server(base_url: str) -> None:
    deadline = time.time() + 30
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            response = httpx.get(f"{base_url}/api/version", timeout=1)
            if response.status_code == 200:
                return
        except Exception as exc:  # pragma: no cover - only used during startup wait
            last_error = exc
        time.sleep(0.25)
    raise RuntimeError(f"E2E server did not start: {last_error}")


@pytest.fixture(scope="session")
def e2e_server():
    frontend_index = ROOT / "app" / "frontend_dist" / "index.html"
    if not frontend_index.exists():
        pytest.skip("Run frontend build before E2E: cd frontend && npm run build")

    data_dir = Path(tempfile.mkdtemp(prefix="lease-manager-e2e-"))
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    env = os.environ.copy()
    env.update(
        {
            "DATA_DIR": str(data_dir),
            "JWT_SECRET": "e2e-secret-not-for-production",
            "COOKIE_SECURE": "false",
            "TOKEN_EXPIRE_HOURS": "2",
            "APP_VERSION": "e2e-test",
            "PYTHONPATH": str(ROOT),
        }
    )

    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        _wait_for_server(base_url)
        _seed_e2e_data(base_url)
        yield base_url
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
        shutil.rmtree(data_dir, ignore_errors=True)


def _seed_e2e_data(base_url: str) -> None:
    with httpx.Client(base_url=base_url, timeout=10) as client:
        status = client.get("/api/auth/status").json()
        if status.get("need_init"):
            response = client.post(
                "/api/auth/register",
                json={"username": "admin", "password": "Admin123", "display_name": "管理员"},
            )
            response.raise_for_status()

        response = client.post("/api/auth/login", json={"username": "admin", "password": "Admin123"})
        response.raise_for_status()

        for room in [
            {"tower": "A座", "floor": "3", "room_no": "E0301", "room_type": "办公", "area": 210.5},
            {"tower": "商业区", "floor": "2", "room_no": "P0201", "room_type": "商业", "area": 520.0},
        ]:
            response = client.post("/api/rooms", json=room)
            response.raise_for_status()

        response = client.post(
            "/api/contracts",
            json={
                "contract_no": "LEASE-E2E-001",
                "tower": "A座",
                "room_no": "E0301",
                "tenant_name": "端到端测试客户",
                "area": 210.5,
                "unit_price": 4.2,
                "monthly_rent": 265230.0,
                "property_fee_rate": 28,
                "start_date": "2026-01-01",
                "end_date": "2026-12-31",
            },
        )
        response.raise_for_status()


@pytest.fixture(scope="session")
def browser_page(e2e_server):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 960})
        try:
            yield page, e2e_server
        finally:
            browser.close()


def _login(page, base_url: str) -> None:
    page.goto(base_url)
    page.get_by_test_id("login-username").fill("admin")
    page.get_by_test_id("login-password").fill("Admin123")
    page.get_by_test_id("login-submit").click()
    expect(page.get_by_test_id("nav-dashboard")).to_be_visible()


def test_frontend_core_workflows(browser_page) -> None:
    page, base_url = browser_page
    _login(page, base_url)

    page.get_by_test_id("nav-contracts").click()
    expect(page.get_by_test_id("contracts-table")).to_contain_text("LEASE-E2E-001")
    page.get_by_test_id("contracts-search-input").fill("LEASE-E2E-001")
    page.get_by_test_id("contracts-search-button").click()
    expect(page.get_by_test_id("contracts-table")).to_contain_text("端到端测试客户")
    page.get_by_test_id("contracts-sort-monthly-rent").click()
    expect(page.get_by_test_id("contracts-table")).to_contain_text("265,230.00")

    page.get_by_test_id("nav-rooms").click()
    expect(page.get_by_test_id("rooms-table")).to_contain_text("E0301")
    page.get_by_test_id("rooms-tower-filter").select_option("商业区")
    expect(page.get_by_test_id("rooms-table")).to_contain_text("P0201")
    page.get_by_test_id("rooms-use-filter").select_option("retail")
    expect(page.get_by_test_id("rooms-table")).to_contain_text("P0201")
    page.get_by_test_id("rooms-sort-area").click()
    expect(page.get_by_test_id("rooms-table")).to_be_visible()

    page.get_by_test_id("nav-receivables").click()
    page.get_by_test_id("receivables-query").click()
    expect(page.get_by_test_id("receivables-table")).to_contain_text("LEASE-E2E-001")
    expect(page.get_by_test_id("receivables-table")).to_contain_text("5,894.00")

    page.get_by_test_id("nav-tools").click()
    expect(page.get_by_test_id("integrity-process-missing_contract_pdfs")).to_be_visible()
    page.get_by_test_id("integrity-process-missing_contract_pdfs").click()
    expect(page.get_by_test_id("pdf-upload-button")).to_be_visible()

    page.get_by_test_id("backup-create-button").click()
    expect(page.get_by_test_id("backup-list")).to_contain_text("backup_")
    page.on("dialog", lambda dialog: dialog.dismiss())
    expect(page.get_by_test_id("backup-list").get_by_role("button", name="恢复").first).to_be_visible()

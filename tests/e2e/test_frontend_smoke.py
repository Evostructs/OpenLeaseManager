from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlparse

import httpx
import pytest


pytestmark = pytest.mark.e2e

if os.environ.get("LEASE_E2E") != "1":
    pytest.skip("Set LEASE_E2E=1 to run browser E2E tests.", allow_module_level=True)

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, expect, sync_playwright  # noqa: E402


ROOT = Path(__file__).resolve().parents[2]
E2E_ADMIN_PASSWORD = "E2eAdminPassword123"
E2E_OVERVIEW_LEADER_USERNAME = "portfolio_leader_e2e"
E2E_OVERVIEW_LEADER_PASSWORD = "PortfolioLeaderPassword123"
E2E_SINGLE_OPERATOR_USERNAME = "single_operator_e2e"
E2E_SINGLE_OPERATOR_PASSWORD = "SingleOperatorPassword123"
E2E_MULTI_OPERATOR_USERNAME = "multi_operator_e2e"
E2E_MULTI_OPERATOR_PASSWORD = "MultiOperatorPassword123"
E2E_HYBRID_USERNAME = "hybrid_workspace_e2e"
E2E_HYBRID_PASSWORD = "HybridWorkspacePassword123"
E2E_PORTFOLIO_CODE = "DEMO_OFFICE_PORTFOLIO"
E2E_EXCHANGE_SEED_CODE = r"""
from app.database import SessionLocal
from app.models import Building, DataSource, SnapshotExchangeSourceBinding

db = SessionLocal()
try:
    binding = (
        db.query(SnapshotExchangeSourceBinding)
        .filter(SnapshotExchangeSourceBinding.source_system_code == "DEMO-B-OPS")
        .one_or_none()
    )
    if binding is None:
        building = db.query(Building).filter(Building.code == "DEMO_B").one()
        source = DataSource(
            building_id=building.id,
            source_type="external_api",
            name="示例楼宇来源系统（浏览器模拟）",
            owner="E2E",
            cadence="monthly",
            freshness_rule="统计日后一个工作日内发布",
            is_active=True,
        )
        db.add(source)
        db.flush()
        db.add(
            SnapshotExchangeSourceBinding(
                source_id=source.id,
                source_system_code="DEMO-B-OPS",
                is_active=True,
            )
        )
        db.commit()

    simulated_binding = (
        db.query(SnapshotExchangeSourceBinding)
        .filter(SnapshotExchangeSourceBinding.source_system_code == "DEMO-B-SIM-OPS")
        .one_or_none()
    )
    if simulated_binding is None:
        building = db.query(Building).filter(Building.code == "DEMO_B").one()
        simulated_source = DataSource(
            building_id=building.id,
            source_type="external_api",
            name="示例主裙楼中心模拟经营台账（浏览器验收）",
            owner="E2E",
            cadence="monthly",
            freshness_rule="模拟月结后一个工作日内发布",
            is_active=True,
        )
        db.add(simulated_source)
        db.flush()
        db.add(
            SnapshotExchangeSourceBinding(
                source_id=simulated_source.id,
                source_system_code="DEMO-B-SIM-OPS",
                is_active=True,
            )
        )
        db.commit()
finally:
    db.close()
"""
E2E_XSHGJ_DETAIL_SEED_CODE = r"""
from datetime import timedelta

from app.core.time import today_in_timezone
from app.database import SessionLocal
from app.models import Building, Contract, ContractRoom, Room, Zone
from app.services.contract_rent import calculate_monthly_rent


db = SessionLocal()
try:
    today = today_in_timezone()
    building = db.query(Building).filter(Building.code == "DEMO_C").one()
    building.ingestion_mode = "detail"

    zone = (
        db.query(Zone)
        .filter(Zone.building_id == building.id, Zone.code == "WHOLE_BUILDING")
        .one_or_none()
    )
    if zone is None:
        zone = Zone(
            building_id=building.id,
            code="WHOLE_BUILDING",
            name="整楼",
            zone_type="whole_building",
            sort_order=0,
            valid_from=today,
            is_active=True,
        )
        db.add(zone)
        db.flush()

    room_specs = (
        ("XSH-E2E-A01", 120.0, "Phase 4 Chrome E2E 在租单元"),
        ("XSH-E2E-B01", 80.0, "Phase 4 Chrome E2E 空置单元"),
    )
    rooms = {}
    for room_no, area, notes in room_specs:
        room = (
            db.query(Room)
            .filter(Room.building_id == building.id, Room.room_no == room_no)
            .one_or_none()
        )
        if room is None:
            room = Room(
                building_id=building.id,
                zone_id=zone.id,
                tower="整楼",
                floor="",
                room_no=room_no,
                room_type="办公",
                area=area,
                is_leasable=True,
                is_deleted=False,
                notes=notes,
            )
            db.add(room)
            db.flush()
        rooms[room_no] = room

    contract = (
        db.query(Contract)
        .filter(Contract.contract_no == "DEMO_C-E2E-001")
        .one_or_none()
    )
    if contract is None:
        occupied_room = rooms["XSH-E2E-A01"]
        monthly_rent = calculate_monthly_rent(occupied_room.area, 4.5)
        contract = Contract(
            contract_no="DEMO_C-E2E-001",
            building_id=building.id,
            tower="整楼",
            room_no=occupied_room.room_no,
            tenant_name="Phase 4 整楼模拟租户",
            area=occupied_room.area,
            unit_price=4.5,
            lease_type="新签",
            monthly_rent=monthly_rent,
            start_date=today - timedelta(days=30),
            end_date=today + timedelta(days=365),
            notes="仅用于临时浏览器验收数据库",
        )
        db.add(contract)
        db.flush()
        db.add(
            ContractRoom(
                contract_id=contract.id,
                building_id=building.id,
                room_id=occupied_room.id,
                tower="整楼",
                room_no=occupied_room.room_no,
                area=occupied_room.area,
                unit_price=4.5,
                monthly_rent=monthly_rent,
                start_date=contract.start_date,
                end_date=contract.end_date,
            )
        )
    db.commit()
finally:
    db.close()
"""
E2E_XSHGJ_DETAIL_RESTORE_CODE = r"""
from app.database import SessionLocal
from app.models import Building


db = SessionLocal()
try:
    building = db.query(Building).filter(Building.code == "DEMO_C").one()
    building.ingestion_mode = "snapshot"
    db.commit()
finally:
    db.close()
"""
_ACTIVE_E2E_DATA_DIR: Path | None = None


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
    global _ACTIVE_E2E_DATA_DIR

    frontend_index = ROOT / "app" / "frontend_dist" / "index.html"
    if not frontend_index.exists():
        pytest.skip("Run frontend build before E2E: cd frontend && npm run build")

    data_dir = Path(tempfile.mkdtemp(prefix="lease-manager-e2e-"))
    _ACTIVE_E2E_DATA_DIR = data_dir
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    env = os.environ.copy()
    inherited_pythonpath = env.get("PYTHONPATH", "").strip()
    env.update(
        {
            "DATA_DIR": str(data_dir),
            "JWT_SECRET": "e2e-secret-not-for-production-32bytes",
            "COOKIE_SECURE": "false",
            "TOKEN_EXPIRE_HOURS": "2",
            "APP_VERSION": "e2e-test",
            "PYTHONPATH": os.pathsep.join(
                item for item in (str(ROOT), inherited_pythonpath) if item
            ),
        }
    )

    server_log = (data_dir / "e2e-server.log").open("w+", encoding="utf-8")
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=ROOT,
        env=env,
        stdout=server_log,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        try:
            _wait_for_server(base_url)
        except RuntimeError as exc:
            server_log.flush()
            server_log.seek(0)
            log_tail = server_log.read()[-4000:]
            raise RuntimeError(f"{exc}\nE2E server log tail:\n{log_tail}") from exc
        _seed_e2e_data(base_url, data_dir)
        yield base_url
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
        server_log.close()
        shutil.rmtree(data_dir, ignore_errors=True)
        _ACTIVE_E2E_DATA_DIR = None


def _seed_e2e_data(base_url: str, data_dir: Path) -> None:
    with httpx.Client(base_url=base_url, timeout=10) as client:
        status = client.get("/api/auth/status").json()
        if status.get("need_init"):
            response = client.post(
                "/api/auth/register",
                json={"username": "admin", "password": E2E_ADMIN_PASSWORD, "display_name": "管理员"},
            )
            response.raise_for_status()

        response = client.post("/api/auth/login", json={"username": "admin", "password": E2E_ADMIN_PASSWORD})
        response.raise_for_status()

        for room in [
            {"tower": "南塔", "floor": "3", "room_no": "E0301", "room_type": "办公", "area": 210.5},
            {"tower": "商业配套", "floor": "2", "room_no": "P0201", "room_type": "餐饮", "area": 520.0},
        ]:
            response = client.post("/api/rooms", json=room)
            response.raise_for_status()

        customer_response = client.post(
            "/api/customers",
            json={"name": "端到端测试客户", "contact_name": "E2E Owner"},
        )
        customer_response.raise_for_status()
        customer_id = customer_response.json()["id"]

        response = client.post(
            "/api/contracts",
            json={
                "contract_no": "DEMO_A-E2E-001",
                "customer_id": customer_id,
                "tower": "南塔",
                "room_no": "E0301",
                "tenant_name": "端到端测试客户",
                "area": 210.5,
                "unit_price": 4.2,
                "monthly_rent": 1,
                "property_fee_rate": 28,
                "start_date": "2026-01-01",
                "end_date": "2026-12-31",
            },
        )
        response.raise_for_status()

        for username, display_name in (
            ("e2e-demo-reviewer", "模拟数据复核人"),
            ("e2e-demo-publisher", "模拟数据发布人"),
        ):
            response = client.post(
                "/api/auth/create-user",
                json={
                    "username": username,
                    "password": "DemoActorPassword123",
                    "display_name": display_name,
                    "role": "editor",
                },
            )
            response.raise_for_status()

        response = client.post(
            "/api/auth/create-user",
            json={
                "username": E2E_OVERVIEW_LEADER_USERNAME,
                "password": E2E_OVERVIEW_LEADER_PASSWORD,
                "display_name": "板块概况模拟账号",
                "role": "viewer",
                "initial_scope": "none",
            },
        )
        response.raise_for_status()

        for username, password, display_name in (
            (
                E2E_SINGLE_OPERATOR_USERNAME,
                E2E_SINGLE_OPERATOR_PASSWORD,
                "示例双塔中心运营人员",
            ),
            (
                E2E_MULTI_OPERATOR_USERNAME,
                E2E_MULTI_OPERATOR_PASSWORD,
                "多楼运营人员",
            ),
            (
                E2E_HYBRID_USERNAME,
                E2E_HYBRID_PASSWORD,
                "组合概况兼楼宇运营人员",
            ),
        ):
            response = client.post(
                "/api/auth/create-user",
                json={
                    "username": username,
                    "password": password,
                    "display_name": display_name,
                    "role": "editor",
                    "initial_scope": "none",
                },
            )
            response.raise_for_status()

        me_response = client.get("/api/auth/me")
        me_response.raise_for_status()
        admin_id = int(me_response.json()["id"])
        users_response = client.get("/api/auth/users")
        users_response.raise_for_status()
        user_ids = {item["username"]: int(item["id"]) for item in users_response.json()}

        leader_id = user_ids[E2E_OVERVIEW_LEADER_USERNAME]
        leader_authorization_response = client.get(
            f"/api/auth/authorizations/users/{leader_id}"
        )
        leader_authorization_response.raise_for_status()
        leader_authorization = leader_authorization_response.json()
        assert leader_authorization["grants"] == []
        response = client.put(
            f"/api/auth/authorizations/users/{leader_id}",
            json={
                "expected_authorization_version": leader_authorization["authorization_version"],
                "grants": [
                    {
                        "scope_type": "portfolio_overview",
                        "scope_code": E2E_PORTFOLIO_CODE,
                        "access_level": "view",
                        "include_future_buildings": False,
                    }
                ],
                "capabilities": [],
            },
        )
        response.raise_for_status()

        for username, grants in (
            (
                E2E_SINGLE_OPERATOR_USERNAME,
                [
                    {
                        "scope_type": "building",
                        "scope_code": "DEMO_A",
                        "access_level": "edit",
                        "include_future_buildings": False,
                    }
                ],
            ),
            (
                E2E_HYBRID_USERNAME,
                [
                    {
                        "scope_type": "building",
                        "scope_code": "DEMO_A",
                        "access_level": "edit",
                        "include_future_buildings": False,
                    },
                    {
                        "scope_type": "portfolio_overview",
                        "scope_code": E2E_PORTFOLIO_CODE,
                        "access_level": "view",
                        "include_future_buildings": False,
                    },
                ],
            ),
            (
                E2E_MULTI_OPERATOR_USERNAME,
                [
                    {
                        "scope_type": "building",
                        "scope_code": "DEMO_A",
                        "access_level": "edit",
                        "include_future_buildings": False,
                    },
                    {
                        "scope_type": "building",
                        "scope_code": "DEMO_B",
                        "access_level": "view",
                        "include_future_buildings": False,
                    },
                ],
            ),
        ):
            user_id = user_ids[username]
            authorization_response = client.get(
                f"/api/auth/authorizations/users/{user_id}"
            )
            authorization_response.raise_for_status()
            authorization = authorization_response.json()
            assert authorization["grants"] == []
            response = client.put(
                f"/api/auth/authorizations/users/{user_id}",
                json={
                    "expected_authorization_version": authorization[
                        "authorization_version"
                    ],
                    "grants": grants,
                    "capabilities": [],
                },
            )
            response.raise_for_status()

        actor_capabilities = {
            "e2e-demo-reviewer": {"snapshot_review"},
            "e2e-demo-publisher": {"snapshot_publish", "platform_admin"},
        }
        for username, required_capabilities in actor_capabilities.items():
            actor_id = user_ids[username]
            actor_authorization_response = client.get(
                f"/api/auth/authorizations/users/{actor_id}"
            )
            actor_authorization_response.raise_for_status()
            actor_authorization = actor_authorization_response.json()
            response = client.put(
                f"/api/auth/authorizations/users/{actor_id}",
                json={
                    "expected_authorization_version": actor_authorization["authorization_version"],
                    "grants": [
                        {
                            "scope_type": "portfolio",
                            "scope_code": "DEMO_OFFICE_PORTFOLIO",
                            "access_level": "edit",
                            "include_future_buildings": True,
                        }
                    ],
                    "capabilities": sorted(
                        set(actor_authorization["capabilities"]) | required_capabilities
                    ),
                },
            )
            response.raise_for_status()

        authorization_response = client.get(f"/api/auth/authorizations/users/{admin_id}")
        authorization_response.raise_for_status()
        authorization = authorization_response.json()
        response = client.put(
            f"/api/auth/authorizations/users/{admin_id}",
            json={
                "expected_authorization_version": authorization["authorization_version"],
                "grants": [
                    {
                        "scope_type": "portfolio",
                        "scope_code": "DEMO_OFFICE_PORTFOLIO",
                        "access_level": "edit",
                        "include_future_buildings": True,
                    }
                ],
                "capabilities": sorted(
                    set(authorization["capabilities"]) | {"data_import_submit"}
                ),
            },
        )
        response.raise_for_status()

    demo_env = os.environ.copy()
    demo_pythonpath = demo_env.get("PYTHONPATH", "").strip()
    demo_env.update(
        {
            "DATA_DIR": str(data_dir),
            "PYTHONPATH": os.pathsep.join(
                item for item in (str(ROOT), demo_pythonpath) if item
            ),
            "PYTHONUTF8": "1",
        }
    )
    demo_result = subprocess.run(
        [
            sys.executable,
            "-m",
            "app.scripts.seed_portfolio_operating_demo",
            "--submitter-id",
            str(admin_id),
            "--reviewer-id",
            str(user_ids["e2e-demo-reviewer"]),
            "--publisher-id",
            str(user_ids["e2e-demo-publisher"]),
        ],
        cwd=ROOT,
        env=demo_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=90,
        check=False,
    )
    assert demo_result.returncode == 0, demo_result.stdout + demo_result.stderr
    demo_payload = json.loads(demo_result.stdout)
    assert demo_payload["data_classification"] == "simulated"
    assert {item["gate_state"] for item in demo_payload["buildings"]} == {"open"}
    exchange_seed = subprocess.run(
        [sys.executable, "-c", E2E_EXCHANGE_SEED_CODE],
        cwd=ROOT,
        env=demo_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    assert exchange_seed.returncode == 0, exchange_seed.stdout + exchange_seed.stderr


def _run_e2e_database_code(data_dir: Path, source: str) -> None:
    env = os.environ.copy()
    inherited_pythonpath = env.get("PYTHONPATH", "").strip()
    env.update(
        {
            "DATA_DIR": str(data_dir),
            "PYTHONPATH": os.pathsep.join(
                item for item in (str(ROOT), inherited_pythonpath) if item
            ),
            "PYTHONUTF8": "1",
        }
    )
    result = subprocess.run(
        [sys.executable, "-c", source],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.fixture()
def xshgj_detail_pilot(e2e_server):
    assert _ACTIVE_E2E_DATA_DIR is not None
    _run_e2e_database_code(_ACTIVE_E2E_DATA_DIR, E2E_XSHGJ_DETAIL_SEED_CODE)
    try:
        yield
    finally:
        _run_e2e_database_code(_ACTIVE_E2E_DATA_DIR, E2E_XSHGJ_DETAIL_RESTORE_CODE)


@pytest.fixture(scope="session")
def e2e_browser():
    with sync_playwright() as playwright:
        channel = os.environ.get("LEASE_E2E_BROWSER_CHANNEL", "").strip()
        browser = playwright.chromium.launch(channel=channel or None)
        try:
            yield browser
        finally:
            browser.close()


@pytest.fixture(scope="session")
def browser_page(e2e_server, e2e_browser):
    context = e2e_browser.new_context(viewport={"width": 1440, "height": 960}, bypass_csp=True)
    page = context.new_page()
    try:
        yield page, e2e_server
    finally:
        context.close()


@pytest.fixture()
def fresh_browser_page(e2e_server, e2e_browser):
    context = e2e_browser.new_context(viewport={"width": 1440, "height": 960}, bypass_csp=True)
    page = context.new_page()
    try:
        yield page, e2e_server
    finally:
        context.close()


def _submit_login_form(
    page,
    username: str = "admin",
    password: str = E2E_ADMIN_PASSWORD,
) -> None:
    page.get_by_test_id("login-username").fill(username)
    page.get_by_test_id("login-password").fill(password)
    page.get_by_test_id("login-submit").click()


def _login(page, base_url: str, username: str = "admin", password: str = E2E_ADMIN_PASSWORD) -> None:
    page.goto(base_url)
    _submit_login_form(page, username, password)
    expect(page.get_by_test_id("nav-dashboard")).to_be_visible()


def _operating_gate_items(client: httpx.Client) -> dict[str, dict]:
    response = client.get("/api/buildings/operating-gates")
    response.raise_for_status()
    return {str(item["building_code"]): item for item in response.json()["items"]}


def _put_operating_gate(
    client: httpx.Client,
    building_code: str,
    *,
    presentation_enabled: bool,
    contribution_enabled: bool,
    reason: str,
) -> None:
    current = _operating_gate_items(client)[building_code]
    if (
        bool(current["presentation_enabled"]) == presentation_enabled
        and bool(current["contribution_enabled"]) == contribution_enabled
    ):
        return
    response = client.put(
        f"/api/buildings/{building_code}/operating-gate",
        json={
            "presentation_enabled": presentation_enabled,
            "contribution_enabled": contribution_enabled,
            "reason": reason,
            "expected_revision": int(current["revision"]),
        },
    )
    response.raise_for_status()


def _open_contract_row_more(contract_row) -> None:
    contract_row.get_by_test_id("contract-row-more-actions").get_by_role("button", name="更多").click()
    expect(contract_row.get_by_test_id("contract-row-more-menu")).to_be_visible()


def _open_contract_batch_actions(page) -> None:
    page.get_by_test_id("contract-batch-trigger").click()
    expect(page.get_by_test_id("contract-batch-panel")).to_be_visible()


def _assert_no_horizontal_overflow(page) -> None:
    overflow = page.evaluate(
        """() => {
            const doc = document.documentElement;
            return doc.scrollWidth - doc.clientWidth;
        }"""
    )
    offenders = []
    if overflow > 2:
        offenders = page.evaluate(
            """() => {
              const items = Array.from(document.querySelectorAll('body *'))
                .map((element) => {
                    const box = element.getBoundingClientRect();
                    return {
                        tag: element.tagName,
                        className: typeof element.className === 'string' ? element.className : '',
                        testId: element.getAttribute('data-testid') || '',
                        left: Math.round(box.left),
                        right: Math.round(box.right),
                        width: Math.round(box.width),
                    };
                })
              const viewport = document.documentElement.clientWidth;
              return {
                scrollX: Math.round(window.scrollX),
                right: items.filter((item) => item.right > viewport + 2).slice(0, 12),
                left: items.filter((item) => item.left < -2).slice(0, 4),
              };
            }"""
        )
    assert overflow <= 2, f"horizontal overflow={overflow}; offenders={offenders}"


def _wait_for_scroll_position(page, expected: int | float, tolerance: int = 5) -> None:
    page.wait_for_function(
        "([top, delta]) => Math.abs(window.scrollY - top) <= delta",
        arg=[expected, tolerance],
        timeout=3000,
    )


def _assert_help_entry_density(page, root_test_id: str, max_info_hints: int = 0) -> None:
    visible_info_hints = page.get_by_test_id(root_test_id).locator(".info-hint-button:visible").count()
    assert visible_info_hints <= max_info_hints


def _assert_topbar_help_location(page) -> None:
    button = page.get_by_test_id("topbar-help-toggle")
    expect(button).to_be_visible()
    box = button.bounding_box()
    viewport = page.viewport_size or {"width": 0, "height": 0}
    assert box is not None
    assert box["x"] > viewport["width"] * 0.55
    assert box["y"] < 80
    assert box["width"] <= 36
    assert box["height"] <= 36


def _assert_scroll_usable(page) -> None:
    state = page.evaluate(
        """() => {
            const doc = document.documentElement;
            const body = document.body;
            return {
                htmlLocked: doc.classList.contains("modal-scroll-locked"),
                htmlOverflow: getComputedStyle(doc).overflowY,
                bodyOverflow: getComputedStyle(body).overflowY,
                scrollHeight: doc.scrollHeight,
                clientHeight: doc.clientHeight,
                before: window.scrollY,
            };
        }"""
    )
    assert not state["htmlLocked"]
    assert state["htmlOverflow"] != "hidden"
    assert state["bodyOverflow"] != "hidden"
    if state["scrollHeight"] - state["clientHeight"] > 24:
        page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
        if page.evaluate("window.scrollY") <= 0:
            return
        page.evaluate("(scrollY) => window.scrollTo(0, scrollY)", state["before"])


def _assert_chart_canvas_nonblank(locator) -> None:
    canvas = locator.locator("canvas").first
    expect(canvas).to_be_visible()
    deadline = time.time() + 5
    while time.time() < deadline:
        if canvas.evaluate(
            """(canvas) => {
                const rect = canvas.getBoundingClientRect();
                if (rect.width <= 10 || rect.height <= 10 || canvas.width <= 10 || canvas.height <= 10) {
                    return false;
                }
                const context = canvas.getContext("2d");
                if (!context) {
                    return false;
                }
                const data = context.getImageData(0, 0, canvas.width, canvas.height).data;
                const step = Math.max(4, Math.floor(data.length / 16000) * 4);
                for (let i = 0; i < data.length; i += step) {
                    const alpha = data[i + 3];
                    const isWhite = data[i] > 248 && data[i + 1] > 248 && data[i + 2] > 248;
                    if (alpha > 0 && !isWhite) {
                        return true;
                    }
                }
                return false;
            }"""
        ):
            return
        time.sleep(0.1)
    assert False, "expected chart canvas to render nonblank pixels"


def _assert_element_within_viewport(page, testid: str) -> None:
    locator = page.get_by_test_id(testid)
    expect(locator).to_be_visible()
    locator.scroll_into_view_if_needed()
    box = locator.bounding_box()
    assert box is not None
    assert box["width"] > 24
    assert box["height"] > 16
    viewport = page.viewport_size or {"width": 0, "height": 0}
    assert box["x"] >= -1
    assert box["x"] + box["width"] <= viewport["width"] + 1


def _assert_no_internal_clipping(root, selector: str) -> None:
    offenders = root.locator(selector).evaluate_all(
        """(elements) => elements.map((element) => ({
            className: element.className,
            horizontal: element.scrollWidth - element.clientWidth,
            vertical: element.scrollHeight - element.clientHeight,
        })).filter((item) => item.horizontal > 2 || item.vertical > 2)"""
    )
    assert offenders == [], f"internal clipping detected: {offenders}"


def _assert_leadership_overview_mobile_layout(page) -> None:
    layout = page.evaluate(
        """() => {
          const title = document.querySelector('.portfolio-brand strong');
          const status = document.querySelector('.portfolio-header-status');
          const grid = document.querySelector('.cockpit-kpis.is-portfolio');
          const cards = Array.from(document.querySelectorAll('.cockpit-kpis.is-portfolio .cockpit-kpi'));
          const viewport = document.documentElement.clientWidth;
          const rect = (element) => {
            const box = element?.getBoundingClientRect();
            return box ? { left: box.left, right: box.right, width: box.width, height: box.height } : null;
          };
          const clippedText = Array.from(document.querySelectorAll(
            '.cockpit-kpis.is-portfolio .cockpit-kpi-label, .cockpit-kpis.is-portfolio .animated-number'
          )).filter((element) => element.scrollWidth - element.clientWidth > 2).map((element) => ({
            text: element.textContent,
            width: element.clientWidth,
            scrollWidth: element.scrollWidth,
          }));
          const statusOutsideViewport = Array.from(status?.children || [])
            .map((element) => ({ text: element.textContent, ...rect(element) }))
            .filter((item) => item.left < -1 || item.right > viewport + 1);
          const columns = grid
            ? getComputedStyle(grid).gridTemplateColumns.split(/\\s+/).filter(Boolean).length
            : 0;
          return {
            viewport,
            title: rect(title),
            status: rect(status),
            columns,
            cardWidths: cards.map((element) => rect(element)?.width || 0),
            clippedText,
            statusOutsideViewport,
          };
        }"""
    )
    assert layout["title"] is not None
    assert layout["title"]["width"] >= 200, layout
    assert layout["title"]["height"] <= 64, layout
    assert layout["status"] is not None
    assert layout["status"]["width"] <= layout["viewport"] - 24, layout
    assert layout["statusOutsideViewport"] == [], layout
    assert 1 <= layout["columns"] <= 2, layout
    assert layout["cardWidths"] and min(layout["cardWidths"]) >= 140, layout
    assert layout["clippedText"] == [], layout


COPY_HYGIENE_DENYLIST = [
    "authorization",
    "cookie",
    "token",
    "api key",
    "api_key",
    "provider",
    "base_url",
    "prompt",
    "completion",
    "response",
    "payload",
    "debug",
    "development",
    "internal",
    "security boundary",
    "api",
    "json",
    "接口返回",
    "安全边界",
    "不会自动",
    "不自动",
    "不写入",
    "只读辅助",
    "只读",
]


def _assert_visible_copy_hygiene(page, root_test_id: str) -> None:
    text = page.get_by_test_id(root_test_id).inner_text()
    normalized = text.lower()
    found = [term for term in COPY_HYGIENE_DENYLIST if term.lower() in normalized]
    assert not found, f"{root_test_id} exposes internal copy: {found}"


def _assert_elements_do_not_overlap(page, first_test_id: str, second_test_id: str) -> None:
    overlap = page.evaluate(
        """([firstTestId, secondTestId]) => {
            const first = document.querySelector(`[data-testid="${firstTestId}"]`);
            const second = document.querySelector(`[data-testid="${secondTestId}"]`);
            if (!first || !second) {
                return null;
            }
            const a = first.getBoundingClientRect();
            const b = second.getBoundingClientRect();
            const width = Math.max(0, Math.min(a.right, b.right) - Math.max(a.left, b.left));
            const height = Math.max(0, Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top));
            return width * height;
        }""",
        [first_test_id, second_test_id],
    )
    assert overlap is not None
    assert overlap <= 1


def _assert_locator_within_viewport(page, locator) -> None:
    expect(locator).to_be_visible()
    locator.scroll_into_view_if_needed()
    box = locator.bounding_box()
    assert box is not None
    assert box["width"] > 24
    assert box["height"] > 16
    viewport = page.viewport_size or {"width": 0, "height": 0}
    assert box["x"] >= -1
    assert box["x"] + box["width"] <= viewport["width"] + 1


def _element_box(page, testid: str) -> dict[str, float]:
    locator = page.get_by_test_id(testid)
    expect(locator).to_be_visible()
    box = locator.bounding_box()
    assert box is not None
    return box


def _assert_page_shell_stays_anchored(before: dict[str, float], after: dict[str, float]) -> None:
    assert abs(after["y"] - before["y"]) <= 2
    assert after["height"] >= before["height"] * 0.85


def _assert_page_shell_at_viewport_top(box: dict[str, float]) -> None:
    assert 0 <= box["y"] <= 80


def _route_sample(page, route_key: str) -> dict:
    page.wait_for_function(
        "routeKey => window.__leaseManagerPerformance?.snapshot().routes.some((sample) => sample.routeKey === routeKey)",
        arg=route_key,
    )
    sample = page.evaluate(
        "routeKey => window.__leaseManagerPerformance.snapshot().routes.filter((item) => item.routeKey === routeKey).at(-1)",
        arg=route_key,
    )
    assert sample is not None
    return sample


def _assert_route_sample_budget(sample: dict, max_cls: float = 0.1, max_long_task_total: int = 300) -> None:
    assert sample["cls"] <= max_cls
    assert sample["longTaskTotal"] <= max_long_task_total
    assert sample["maxLongTask"] <= max_long_task_total
    assert "slowEventCount" in sample
    assert "maxEvent" in sample
    assert isinstance(sample["resources"], list)
    assert all("?token=" not in item.get("name", "").lower() for item in sample["resources"])


def _assert_no_large_route_resource(sample: dict, max_decoded_size: int = 500000) -> None:
    large_resources = [item for item in sample["resources"] if item.get("decodedBodySize", 0) > max_decoded_size]
    assert large_resources == []


def _assert_box_stable(page, testid: str, wait_ms: int = 250, max_top_delta: int = 8, min_height_ratio: float = 0.9) -> None:
    before = _element_box(page, testid)
    page.wait_for_timeout(wait_ms)
    after = _element_box(page, testid)
    assert abs(after["y"] - before["y"]) <= max_top_delta
    assert after["height"] >= before["height"] * min_height_ratio


def _assert_dashboard_visual_layout(page) -> None:
    expect(page.get_by_test_id("dashboard-page")).to_be_visible()
    _assert_no_horizontal_overflow(page)
    _assert_visible_copy_hygiene(page, "dashboard-page")
    _open_parent_details(page, "dashboard-health")
    for dashboard_test_id in [
        "dashboard-page",
        "dashboard-actions",
        "today-health-overview",
        "dashboard-judgement",
        "today-signal-lanes",
        "today-top-actions",
        "dashboard-health",
        "dashboard-signal-band",
    ]:
        _assert_element_within_viewport(page, dashboard_test_id)
    _assert_scroll_usable(page)


def _assert_rooms_visual_layout(page) -> None:
    expect(page.get_by_test_id("rooms-page")).to_be_visible()
    _assert_no_horizontal_overflow(page)
    _assert_visible_copy_hygiene(page, "rooms-page")
    for rooms_test_id in [
        "rooms-operator-map",
        "rooms-workspace-summary",
        "room-status-overview",
        "rooms-status-strip",
        "room-status-segment-vacant",
        "rooms-heatmap",
        "room-floor-map",
        "rooms-heatmap-grid",
    ]:
        _assert_element_within_viewport(page, rooms_test_id)

    heatmap_cells = page.locator('[data-testid^="rooms-heatmap-room-"]')
    expect(heatmap_cells.first).to_be_visible()
    assert heatmap_cells.count() > 0

    _open_parent_details(page, "rooms-table")
    table_wrapper = page.locator(".table-wrapper").filter(has=page.get_by_test_id("rooms-table")).first
    _assert_locator_within_viewport(page, table_wrapper)
    expect(page.get_by_test_id("rooms-table")).to_be_visible()
    table_box = page.get_by_test_id("rooms-table").bounding_box()
    assert table_box is not None
    assert table_box["width"] > 24
    assert table_box["height"] > 16
    wrapper_scroll_state = table_wrapper.evaluate(
        """(element) => ({
            clientWidth: element.clientWidth,
            scrollWidth: element.scrollWidth,
            overflowX: getComputedStyle(element).overflowX,
        })"""
    )
    assert wrapper_scroll_state["scrollWidth"] >= wrapper_scroll_state["clientWidth"]
    assert wrapper_scroll_state["overflowX"] in ["auto", "scroll", "overlay"]
    _assert_scroll_usable(page)


def _assert_table_region_within_viewport(page, table_test_id: str) -> None:
    table_wrapper = page.locator(".table-wrapper").filter(has=page.get_by_test_id(table_test_id)).first
    if table_wrapper.count():
        _assert_locator_within_viewport(page, table_wrapper)
    else:
        _assert_element_within_viewport(page, table_test_id)
    expect(page.get_by_test_id(table_test_id)).to_be_visible()
    table_box = page.get_by_test_id(table_test_id).bounding_box()
    assert table_box is not None
    assert table_box["width"] > 24
    assert table_box["height"] > 16


def _open_parent_details(page, test_id: str) -> None:
    locator = page.get_by_test_id(test_id)
    try:
        locator.first.wait_for(state="attached", timeout=5000)
    except PlaywrightTimeoutError:
        return
    locator.first.evaluate(
        """(element) => {
            const details = element.closest('details');
            if (details) details.open = true;
        }"""
    )


def _assert_customers_visual_layout(page) -> None:
    expect(page.get_by_test_id("customers-page")).to_be_visible()
    _assert_no_horizontal_overflow(page)
    _assert_visible_copy_hygiene(page, "customers-page")
    _open_parent_details(page, "customer-value-risk-view")
    for customers_test_id in [
        "customer-workspace-summary",
        "customer-signal-band",
        "customer-value-risk-view",
        "customer-concentration-chart",
        "customer-risk-matrix",
        "customers-table",
    ]:
        _assert_element_within_viewport(page, customers_test_id)

    _assert_chart_canvas_nonblank(page.get_by_test_id("customer-concentration-chart"))
    risk_cells = page.get_by_test_id("customer-risk-matrix").locator(".customer-risk-cell")
    expect(risk_cells.first).to_be_visible()
    assert risk_cells.count() > 0


def _assert_contracts_visual_layout(page) -> None:
    expect(page.get_by_test_id("contracts-page")).to_be_visible()
    _assert_no_horizontal_overflow(page)
    _assert_visible_copy_hygiene(page, "contracts-page")
    _open_parent_details(page, "contracts-document-panel")
    for contracts_test_id in [
        "contracts-workspace-summary",
        "contracts-lifecycle-visual",
        "contracts-lifecycle-summary",
        "contracts-lifecycle-segment-active",
        "contracts-status-chips",
        "contracts-next-action-strip",
        "contracts-document-panel",
        "contracts-pdf-panel",
        "contracts-integrity-panel",
    ]:
        _assert_element_within_viewport(page, contracts_test_id)
    _assert_table_region_within_viewport(page, "contracts-table")


def _assert_risk_center_visual_layout(page) -> None:
    expect(page.get_by_test_id("risk-center-page")).to_be_visible()
    _assert_no_horizontal_overflow(page)
    _assert_help_entry_density(page, "risk-center-page")
    _assert_visible_copy_hygiene(page, "risk-center-page")
    _open_parent_details(page, "risk-filter-bar")
    _open_parent_details(page, "attachment-completeness-card")
    for risk_test_id in [
        "risk-matrix-overview",
        "urgent-risk-queue",
        "risk-filter-bar",
        "risk-type-strip",
        "risk-matrix",
        "risk-flag-list",
        "attachment-completeness-card",
        "risk-search-input",
    ]:
        _assert_element_within_viewport(page, risk_test_id)


def _assert_ai_assist_visual_layout(page, requested_paths: list[str]) -> None:
    expect(page.get_by_test_id("ai-assist-page")).to_be_visible()
    _assert_no_horizontal_overflow(page)
    _assert_help_entry_density(page, "ai-assist-page", max_info_hints=3)
    _assert_visible_copy_hygiene(page, "ai-assist-page")
    assert not any(path.startswith("/api/ai") for path in requested_paths)
    assert "/api/risks" not in set(requested_paths)
    expect(page.get_by_test_id("ai-mode-customer")).to_be_visible()
    expect(page.get_by_test_id("ai-mode-risk")).to_be_visible()
    for ai_test_id in [
        "ai-mode-customer",
        "ai-mode-risk",
        "ai-customer-search-input",
        "ai-customer-search-button",
        "ai-customer-result-panel",
        "ai-risk-load-button",
        "ai-risk-result-panel",
    ]:
        _assert_element_within_viewport(page, ai_test_id)
    assert "/api/risks" not in set(requested_paths)
    assert not any(path.startswith("/api/ai") for path in requested_paths)


def _assert_receivables_visual_layout(page) -> None:
    expect(page.get_by_test_id("receivables-page")).to_be_visible()
    _assert_no_horizontal_overflow(page)
    _assert_help_entry_density(page, "receivables-page", max_info_hints=2)
    _assert_visible_copy_hygiene(page, "receivables-page")
    _open_parent_details(page, "receivable-exception-lanes")
    for receivables_test_id in [
        "receivables-page",
        "receivable-status-chips",
        "receivable-collection-overview",
        "receivable-collection-segment",
        "receivable-secondary-metrics",
        "receivable-flow-overview",
        "receivable-triage-board",
        "receivable-action-path",
        "receivable-exception-lanes",
        "receivable-priority-card",
        "receivable-detail-ledger",
    ]:
        _assert_element_within_viewport(page, receivables_test_id)
    expect(page.get_by_test_id("receivable-secondary-metrics")).to_contain_text("已收金额")
    expect(page.get_by_test_id("receivable-secondary-metrics")).to_contain_text("本月未收")
    expect(page.get_by_test_id("receivable-secondary-metrics")).to_contain_text("本月逾期")
    _assert_table_region_within_viewport(page, "receivables-table")
    expect(page.get_by_test_id("receivable-detail-ledger")).to_contain_text("主台账")
    expect(page.get_by_test_id("receivable-detail-ledger")).to_contain_text("开票台账")
    expect(page.get_by_test_id("receivable-detail-ledger")).to_contain_text("催收台账")


def _reset_receivables_baseline(page) -> None:
    receivables_page = page.get_by_test_id("receivables-page")
    receivables_page.locator(".receivable-search-input").fill("")
    receivables_page.locator(".receivable-invoice-filter").select_option("")
    receivables_page.locator(".date-picker-trigger").click()
    receivables_page.locator(".date-picker-link.strong").click()
    with page.expect_response(
        lambda response: urlparse(response.url).path == "/api/contracts/receivables/monthly"
        and response.status == 200
    ):
        page.get_by_test_id("receivables-query").click()
    expect(page.get_by_test_id("receivable-triage-board")).to_be_visible()
    page.get_by_test_id("receivable-flow-step-due").click()
    expect(page.get_by_test_id("receivable-flow-step-due")).to_have_attribute("aria-pressed", "true")
    expect(page.get_by_test_id("receivables-table")).to_contain_text("DEMO_A-E2E-001")


def _assert_operations_visual_layout(page) -> None:
    expect(page.get_by_test_id("operations-page")).to_be_visible()
    _assert_no_horizontal_overflow(page)
    _assert_help_entry_density(page, "operations-page")
    _assert_visible_copy_hygiene(page, "operations-page")
    _open_parent_details(page, "operations-health")
    for operations_test_id in [
        "operations-page",
        "operation-command-board",
        "today-priority-map",
        "today-action-queue",
        "operations-queue",
        "operation-filter-strip",
        "operations-health",
        "operations-trend",
    ]:
        _assert_element_within_viewport(page, operations_test_id)
    _assert_scroll_usable(page)


def _assert_audit_visual_layout(page) -> None:
    expect(page.get_by_test_id("audit-page")).to_be_visible()
    _assert_no_horizontal_overflow(page)
    _assert_visible_copy_hygiene(page, "audit-page")
    _open_parent_details(page, "audit-filter-panel")
    for audit_test_id in [
        "audit-page",
        "audit-activity-strip",
        "audit-result-card",
        "audit-latest-action-card",
        "audit-latest-operator-card",
        "audit-latest-target-card",
        "audit-export-button",
        "audit-action-strip",
        "audit-filter-panel",
    ]:
        _assert_element_within_viewport(page, audit_test_id)
    _assert_table_region_within_viewport(page, "audit-log-table")
    for audit_root in [
        "audit-activity-strip",
        "audit-result-card",
        "audit-latest-action-card",
        "audit-latest-operator-card",
        "audit-filter-panel",
    ]:
        _assert_visible_copy_hygiene(page, audit_root)


def _assert_tools_visual_layout(page) -> None:
    expect(page.get_by_test_id("tools-page")).to_be_visible()
    _assert_no_horizontal_overflow(page)
    _assert_help_entry_density(page, "tools-page", max_info_hints=2)
    _assert_visible_copy_hygiene(page, "tools-page")
    tools_text = page.get_by_test_id("tools-page").inner_text()
    assert "合同 PDF" not in tools_text
    assert "资料完整性" not in tools_text
    for tools_test_id in [
        "tools-page",
        "tools-task-grid",
        "tools-help-trigger",
        "tool-section-materials",
        "tool-section-access",
        "tool-card-backup",
        "tool-card-feishu",
        "tool-card-users",
        "backup-list",
    ]:
        _assert_element_within_viewport(page, tools_test_id)
    empty_state_heights = page.get_by_test_id("tools-page").evaluate(
        """(root) => Array.from(root.querySelectorAll(".inline-status-row, .compact-empty"))
            .filter((element) => element.offsetParent !== null)
            .map((element) => element.getBoundingClientRect().height)"""
    )
    assert all(height <= 56 for height in empty_state_heights)
    section_heights = page.get_by_test_id("tools-task-grid").evaluate(
        """(root) => Array.from(root.querySelectorAll(".tool-task-group:not(.tool-task-group-operating-gates)"))
            .filter((element) => element.offsetParent !== null)
            .map((element) => element.getBoundingClientRect().height)"""
    )
    if len(section_heights) >= 2:
        shortest = max(1, min(section_heights))
        assert max(section_heights) / shortest <= 2.2
    gate_section_height = page.get_by_test_id("tool-section-operating-gates").evaluate(
        "(element) => element.getBoundingClientRect().height"
    )
    assert gate_section_height <= 900


def _assert_login_visual_layout(page) -> None:
    expect(page.get_by_test_id("login-entry")).to_be_visible()
    _assert_no_horizontal_overflow(page)
    _assert_visible_copy_hygiene(page, "login-entry")
    brand_mark = page.locator(".login-hero img")
    expect(brand_mark).to_be_visible()
    expect(brand_mark).to_have_attribute("src", "/lease-manager-mark.svg")
    assert brand_mark.evaluate("element => element.complete && element.naturalWidth > 0") is True
    brand_copy = page.get_by_test_id("login-brand-panel").inner_text()
    assert "多楼宇租赁运营平台" in brand_copy
    assert "\u9886\u5bfc" not in brand_copy
    for login_test_id in [
        "login-entry",
        "login-brand-panel",
        "login-signal-band",
        "login-signal-contracts",
        "login-signal-receivables",
        "login-signal-risk",
        "login-card",
        "login-mode",
        "login-form",
    ]:
        _assert_element_within_viewport(page, login_test_id)
    _assert_elements_do_not_overlap(page, "login-brand-panel", "login-card")


def test_frontend_core_workflows(browser_page) -> None:
    page, base_url = browser_page
    requested_paths: list[str] = []
    page.on("request", lambda request: requested_paths.append(urlparse(request.url).path))

    page.goto(base_url)
    _assert_login_visual_layout(page)
    page.set_viewport_size({"width": 390, "height": 844})
    _assert_login_visual_layout(page)
    page.set_viewport_size({"width": 1440, "height": 960})

    _login(page, base_url)

    dashboard_paths = set(requested_paths)
    assert "/api/rooms" not in dashboard_paths
    assert "/api/risks" not in dashboard_paths
    assert "/api/attachments/completeness-tasks" not in dashboard_paths
    assert not any(path.startswith("/api/ai") for path in dashboard_paths)
    assert "/api/dashboard/operations/trend" not in dashboard_paths
    assert "/api/reports/operations-daily" not in dashboard_paths
    assert "/api/customers/import/preview" not in dashboard_paths
    assert "/api/customers/follow-ups/import/preview" not in dashboard_paths
    _assert_dashboard_visual_layout(page)
    _assert_topbar_help_location(page)
    for health_test_id in ["today-health-green", "today-health-yellow", "today-health-red"]:
        expect(page.get_by_test_id(health_test_id)).to_be_visible()
        expect(page.get_by_test_id(health_test_id)).to_have_attribute("aria-pressed", "false")
    page.get_by_test_id("today-health-red").click()
    expect(page.get_by_test_id("today-health-red")).to_have_attribute("aria-pressed", "true")
    expect(page.get_by_test_id("today-signal-lanes")).to_be_visible()
    page.get_by_test_id("today-health-red").click()
    expect(page.get_by_test_id("today-health-red")).to_have_attribute("aria-pressed", "false")
    expect(page.get_by_test_id("today-signal-lane-payment")).to_be_visible()
    expect(page.get_by_test_id("today-signal-lane-followup")).to_be_visible()
    expect(page.get_by_test_id("today-signal-lane-materials")).to_be_visible()
    expect(page.get_by_test_id("today-signal-lane-expiry")).to_be_visible()
    expect(page.get_by_test_id("today-open-operations-queue")).to_be_visible()
    expect(page.locator(".dashboard-lane-row")).to_have_count(0)
    expect(page.get_by_test_id("dashboard-export-daily-report")).to_be_visible()
    expect(page.get_by_text("快捷入口")).to_have_count(0)
    expect(page.get_by_text("常用入口")).to_have_count(0)
    expect(page.get_by_test_id("nav-analytics")).to_be_visible()
    expect(page.get_by_test_id("nav-contracts")).to_be_visible()
    expect(page.get_by_test_id("nav-receivables")).to_be_visible()
    expect(page.get_by_test_id("nav-ai-assist")).to_be_visible()

    requested_paths.clear()
    page.get_by_test_id("topbar-help-toggle").click()
    expect(page.get_by_test_id("control-help-panel")).to_be_visible()
    expect(page.get_by_test_id("control-help-current-page")).to_contain_text("今日看板")
    page.wait_for_timeout(100)
    assert not [path for path in requested_paths if path.startswith("/api/")]
    assert "active" in (page.get_by_test_id("nav-dashboard").get_attribute("class") or "")
    page.set_viewport_size({"width": 390, "height": 844})
    _assert_no_horizontal_overflow(page)
    page.keyboard.press("Escape")
    expect(page.get_by_test_id("control-help-panel")).to_have_count(0)
    page.set_viewport_size({"width": 1440, "height": 960})
    page.get_by_test_id("topbar-help-toggle").click()
    expect(page.get_by_test_id("control-help-panel")).to_be_visible()
    page.get_by_test_id("control-help-close").click()
    expect(page.get_by_test_id("control-help-panel")).to_have_count(0)
    assert "active" in (page.get_by_test_id("nav-dashboard").get_attribute("class") or "")

    page.set_viewport_size({"width": 1920, "height": 1080})
    page.get_by_test_id("topbar-presentation-toggle").click()
    presentation = page.get_by_test_id("unified-cockpit")
    expect(presentation).to_be_visible()
    expect(presentation.get_by_test_id("cockpit-building-select")).to_have_value("DEMO_A")
    expect(presentation).to_contain_text("示例双塔中心")
    _assert_chart_canvas_nonblank(presentation.get_by_role("img", name="在租与待租面积", exact=True))
    _assert_chart_canvas_nonblank(presentation.get_by_role("img", name="各月应收和回款", exact=True))
    _assert_chart_canvas_nonblank(presentation.get_by_role("img", name="楼宇经营指标比较，点击柱形选择楼宇", exact=True))
    for topic in ("overview", "rent", "operations", "space", "decision"):
        expect(presentation.get_by_test_id(f"cockpit-topic-{topic}")).to_be_visible()
    presentation_overflow = presentation.evaluate(
        """(element) => ({
            horizontal: element.scrollWidth - element.clientWidth,
            vertical: element.scrollHeight - element.clientHeight,
        })"""
    )
    assert presentation_overflow["horizontal"] <= 2
    assert presentation_overflow["vertical"] <= 2
    _assert_no_internal_clipping(presentation, ".uc-chart-panel, .uc-building-panel")
    _assert_no_horizontal_overflow(page)
    page.keyboard.press("Escape")
    expect(page.get_by_test_id("unified-cockpit")).to_have_count(0)
    page.set_viewport_size({"width": 1440, "height": 960})
    assert "active" in (page.get_by_test_id("nav-dashboard").get_attribute("class") or "")

    expect(page.get_by_test_id("dashboard-action-row-0")).to_be_visible()
    expect(page.get_by_test_id("dashboard-action-nav-0")).to_be_visible()
    page.get_by_test_id("dashboard-action-row-0").scroll_into_view_if_needed()
    dashboard_scroll_before = page.evaluate("window.scrollY")
    page.get_by_test_id("dashboard-action-row-0").click()
    expect(page.get_by_test_id("contract-decision-card")).to_be_visible()
    assert "active" in (page.get_by_test_id("nav-dashboard").get_attribute("class") or "")
    page.locator(".modal-header .icon-btn").click()
    expect(page.get_by_test_id("contract-decision-card")).to_have_count(0)
    assert "active" in (page.get_by_test_id("nav-dashboard").get_attribute("class") or "")
    _wait_for_scroll_position(page, dashboard_scroll_before)
    page.get_by_test_id("dashboard-action-nav-0").click()
    expect(page.get_by_test_id("receivables-query")).to_be_visible()
    assert "active" in (page.get_by_test_id("nav-receivables").get_attribute("class") or "")
    page.get_by_test_id("nav-dashboard").click()
    expect(page.get_by_test_id("dashboard-page")).to_be_visible()
    _assert_scroll_usable(page)

    page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
    assert page.evaluate("window.scrollY") > 0
    page.get_by_test_id("today-signal-lane-payment").get_by_role("button").last.click()
    expect(page.get_by_test_id("receivables-query")).to_be_visible()
    assert "active" in (page.get_by_test_id("nav-receivables").get_attribute("class") or "")
    page.get_by_test_id("nav-dashboard").click()
    _assert_dashboard_visual_layout(page)

    page.set_viewport_size({"width": 390, "height": 844})
    _assert_dashboard_visual_layout(page)
    page.set_viewport_size({"width": 1440, "height": 960})

    requested_paths.clear()
    page.evaluate("window.scrollTo(0, 0)")
    shell_before_switch = _element_box(page, "app-page-shell")
    with (
        page.expect_request(lambda request: urlparse(request.url).path == "/api/dashboard/operations/v2"),
        page.expect_request(lambda request: urlparse(request.url).path == "/api/dashboard/operations/trend"),
    ):
        page.get_by_test_id("nav-operations").click()
    expect(page.get_by_test_id("operations-page")).to_be_visible()
    _assert_page_shell_stays_anchored(shell_before_switch, _element_box(page, "app-page-shell"))
    expect(page.get_by_test_id("operations-queue")).to_be_visible()
    expect(page.get_by_test_id("today-priority-map")).to_be_visible()
    expect(page.get_by_test_id("today-action-queue")).to_be_visible()
    expect(page.get_by_test_id("operation-filter-strip")).to_be_visible()
    _open_parent_details(page, "operations-health")
    expect(page.get_by_test_id("operations-health")).to_be_visible()
    expect(page.get_by_test_id("operations-trend")).to_be_visible()
    assert "/api/dashboard/operations/v2" in set(requested_paths)
    assert "/api/dashboard/operations/trend" in set(requested_paths)
    _assert_operations_visual_layout(page)
    telemetry = page.evaluate("window.__leaseManagerPerformance.snapshot()")
    assert "layout-shift" in telemetry["supported"]
    operations_sample = _route_sample(page, "operations")
    assert operations_sample["cls"] <= 0.1
    assert "longTaskTotal" in operations_sample

    page.set_viewport_size({"width": 390, "height": 844})
    expect(page.get_by_test_id("operations-page")).to_be_visible()
    expect(page.get_by_test_id("operation-filter-overdue_contracts")).to_be_visible()
    expect(page.get_by_test_id("operation-filter-due_follow_up_customers")).to_be_visible()
    expect(page.get_by_test_id("operation-filter-expiring_contracts")).to_be_visible()
    page.get_by_test_id("today-priority-lane-payment").click()
    expect(page.get_by_test_id("operation-filter-overdue_contracts")).to_have_attribute("aria-selected", "true")
    page.get_by_test_id("today-action-row-0").click()
    page.wait_for_function(
        """() => Boolean(document.querySelector(
            '[data-testid="contract-decision-card"], [data-testid="customer-detail-modal"], [data-testid="operation-target-context-modal"]'
        ))"""
    )
    assert "active" in (page.get_by_test_id("nav-operations").get_attribute("class") or "")
    page.get_by_test_id("base-modal-close").first.click()
    page.get_by_test_id("operation-filter-unpaid_deposits").scroll_into_view_if_needed()
    expect(page.get_by_test_id("operation-filter-unpaid_deposits")).to_be_visible()
    page.get_by_test_id("operation-filter-overdue_contracts").click()
    expect(page.get_by_test_id("operation-priority-action-0")).to_be_visible()
    page.get_by_test_id("operation-priority-row-0").scroll_into_view_if_needed()
    operation_scroll_before = page.evaluate("window.scrollY")
    page.get_by_test_id("operation-priority-row-0").click()
    expect(page.get_by_test_id("contract-decision-card")).to_be_visible()
    assert "active" in (page.get_by_test_id("nav-operations").get_attribute("class") or "")
    expect(page.get_by_test_id("operation-filter-overdue_contracts")).to_have_attribute("aria-selected", "true")
    page.locator(".modal-header .icon-btn").click()
    expect(page.get_by_test_id("contract-decision-card")).to_have_count(0)
    assert "active" in (page.get_by_test_id("nav-operations").get_attribute("class") or "")
    expect(page.get_by_test_id("operation-filter-overdue_contracts")).to_have_attribute("aria-selected", "true")
    _wait_for_scroll_position(page, operation_scroll_before)
    page.get_by_test_id("operation-priority-action-0").click()
    expect(page.get_by_test_id("contract-receivables-context")).to_be_visible()
    assert "active" in (page.get_by_test_id("nav-operations").get_attribute("class") or "")
    page.locator(".modal-header .icon-btn").click()
    expect(page.get_by_test_id("contract-receivables-context")).to_have_count(0)
    page.get_by_test_id("operation-secondary-action-0").click()
    expect(page.get_by_test_id("receivables-query")).to_be_visible()
    assert not page.evaluate("document.documentElement.classList.contains('modal-scroll-locked')")
    assert page.evaluate("getComputedStyle(document.body).overflowY !== 'hidden'")
    assert page.evaluate("document.documentElement.scrollHeight > document.documentElement.clientHeight")
    page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
    assert page.evaluate("window.scrollY > 0")
    page.evaluate("window.scrollTo(0, 0)")
    _assert_scroll_usable(page)
    _assert_no_horizontal_overflow(page)
    page.set_viewport_size({"width": 1440, "height": 960})
    page.get_by_test_id("nav-operations").click()
    expect(page.get_by_test_id("operations-page")).to_be_visible()
    expect(page.get_by_test_id("operation-filter-strip")).to_be_visible()
    _open_parent_details(page, "operations-health")
    expect(page.get_by_test_id("operations-health")).to_be_visible()
    expect(page.get_by_test_id("operations-trend")).to_be_visible()
    _assert_operations_visual_layout(page)

    requested_paths.clear()
    with (
        page.expect_request(lambda request: urlparse(request.url).path == "/api/rooms"),
        page.expect_response(
            lambda response: urlparse(response.url).path == "/api/rent-analytics" and response.status == 200
        ),
    ):
        page.get_by_test_id("nav-analytics").click()
    expect(page.get_by_test_id("analytics-page")).to_be_visible()
    expect(page.get_by_test_id("analytics-insights")).to_be_visible()
    expect(page.get_by_test_id("analytics-command-board")).to_be_visible()
    expect(page.get_by_test_id("analytics-rent-timeline")).to_be_visible()
    expect(page.get_by_test_id("rent-timeline-chart")).to_be_visible()
    expect(page.get_by_test_id("rent-timeline-export-png")).to_be_visible()
    expect(page.get_by_test_id("rent-timeline-export-excel")).to_be_visible()
    expect(page.get_by_test_id("analytics-signal-vacancy")).to_be_visible()
    expect(page.get_by_test_id("analytics-signal-expiry")).to_be_visible()
    expect(page.get_by_test_id("analytics-status-chart")).to_be_visible()
    expect(page.get_by_test_id("analytics-customer-rank")).to_be_visible()
    _open_parent_details(page, "analytics-tower-summary")
    expect(page.get_by_test_id("analytics-tower-summary")).to_be_visible()
    expect(page.get_by_test_id("analytics-tower-mix-chart")).to_be_visible()
    expect(page.get_by_test_id("analytics-tower-rent-chart")).to_have_count(0)
    expect(page.get_by_test_id("analytics-forecast-chart")).to_have_count(0)
    expect(page.get_by_test_id("analytics-action-receivables")).to_be_visible()
    expect(page.get_by_test_id("analytics-action-rooms")).to_be_visible()
    expect(page.get_by_test_id("analytics-status-drilldown-在租")).to_be_visible()
    expect(page.get_by_test_id("analytics-customer-drilldown-0")).to_be_visible()
    expect(page.get_by_test_id("analytics-tower-rooms-商业配套")).to_be_visible()
    assert "/api/rooms" in set(requested_paths)
    assert "/api/rent-analytics" in set(requested_paths)
    _assert_no_horizontal_overflow(page)
    _assert_visible_copy_hygiene(page, "analytics-page")
    for analytics_test_id in [
        "analytics-insights",
        "analytics-rent-timeline",
        "analytics-status-chart",
        "analytics-customer-rank",
        "analytics-tower-summary",
    ]:
        _open_parent_details(page, analytics_test_id)
        _assert_element_within_viewport(page, analytics_test_id)
    for chart_test_id in [
        "rent-timeline-chart",
        "analytics-status-chart",
        "analytics-tower-mix-chart",
    ]:
        _open_parent_details(page, chart_test_id)
        _assert_chart_canvas_nonblank(page.get_by_test_id(chart_test_id))

    for segment_key in ["south_office", "north_office", "commercial"]:
        button = page.get_by_test_id(f"rent-segment-{segment_key}")
        button.click()
        expect(button).to_have_attribute("aria-pressed", "true")
    expect(page.get_by_test_id("rent-segment-overall")).to_have_attribute("aria-pressed", "true")

    rent_payload = page.request.get(f"{base_url}/api/rent-analytics").json()
    page.get_by_test_id("rent-range-past12").click()
    expect(page.get_by_test_id("rent-range-past12")).to_have_attribute("aria-pressed", "true")
    expect(page.get_by_test_id("rent-range-start")).to_have_value(rent_payload["bounds"]["default_start"])
    expect(page.get_by_test_id("rent-range-end")).to_have_value(rent_payload["bounds"]["default_end"])

    with page.expect_download(timeout=30000) as download_info:
        page.get_by_test_id("rent-timeline-export-png").click()
    png_download = download_info.value
    assert png_download.suggested_filename.endswith(".png")
    png_path = Path(png_download.path())
    assert png_path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"

    event_select = page.locator(".rent-event-picker select")
    expect(event_select).to_be_visible()
    assert event_select.locator("option").count() > 1
    event_select.select_option(index=1)
    expect(page.get_by_test_id("rent-timeline-drivers")).to_be_visible()
    page.get_by_test_id("rent-timeline-drivers").get_by_role("button", name="打开合同").first.click()
    expect(page.get_by_test_id("contract-decision-card")).to_be_visible()
    page.locator(".modal-header .icon-btn").click()
    expect(page.get_by_test_id("contract-decision-card")).to_have_count(0)

    page.get_by_test_id("analytics-status-drilldown-在租").click()
    expect(page.get_by_test_id("contracts-table")).to_be_visible()
    expect(page.get_by_test_id("contracts-status-filter")).to_have_value("在租")
    expect(page.get_by_text("来自经营分析").last).to_be_visible()

    page.get_by_test_id("nav-analytics").click()
    expect(page.get_by_test_id("analytics-page")).to_be_visible()
    page.get_by_test_id("analytics-customer-drilldown-0").click()
    expect(page.get_by_test_id("customer-workspace-summary")).to_be_visible()
    expect(page.get_by_text("来自经营分析").last).to_be_visible()
    expect(page.locator(".modal-backdrop")).to_be_visible()
    page.locator(".modal-header .icon-btn").first.click()
    expect(page.locator(".modal-backdrop")).to_have_count(0)
    _assert_scroll_usable(page)

    page.get_by_test_id("nav-analytics").click()
    expect(page.get_by_test_id("analytics-page")).to_be_visible()
    _open_parent_details(page, "analytics-tower-rooms-商业配套")
    page.get_by_test_id("analytics-tower-rooms-商业配套").click()
    _open_parent_details(page, "rooms-table")
    expect(page.get_by_test_id("rooms-table")).to_be_visible()
    expect(page.get_by_test_id("rooms-tower-filter")).to_have_value("商业配套")
    expect(page.get_by_test_id("rooms-use-filter")).to_have_value("retail")

    page.get_by_test_id("nav-analytics").click()
    expect(page.get_by_test_id("analytics-page")).to_be_visible()
    page.get_by_test_id("analytics-action-receivables").click()
    expect(page.get_by_test_id("receivables-query")).to_be_visible()
    expect(page.get_by_text("来自经营分析").last).to_be_visible()

    page.get_by_test_id("nav-analytics").click()
    expect(page.get_by_test_id("analytics-page")).to_be_visible()

    page.set_viewport_size({"width": 390, "height": 844})
    page.get_by_label("切换导航").click()
    expect(page.get_by_test_id("nav-analytics")).to_be_visible()
    page.get_by_test_id("nav-analytics").click()
    expect(page.get_by_test_id("analytics-page")).to_be_visible()
    _assert_no_horizontal_overflow(page)
    for analytics_test_id in [
        "analytics-insights",
        "analytics-rent-timeline",
        "analytics-status-chart",
        "analytics-customer-rank",
        "analytics-tower-summary",
    ]:
        _open_parent_details(page, analytics_test_id)
        _assert_element_within_viewport(page, analytics_test_id)
    page.set_viewport_size({"width": 1440, "height": 960})

    if page.get_by_test_id("nav-contracts").count() == 0:
        page.get_by_test_id("nav-group-business").click()
    page.evaluate("window.__leaseManagerPerformance?.reset()")
    requested_paths.clear()
    page.get_by_test_id("nav-contracts").click()
    expect(page.get_by_test_id("contracts-workspace-summary")).to_be_visible()
    expect(page.get_by_test_id("contracts-lifecycle-visual")).to_be_visible()
    expect(page.get_by_test_id("contracts-lifecycle-summary")).to_be_visible()
    expect(page.get_by_test_id("contracts-status-chips")).to_be_visible()
    _open_parent_details(page, "contracts-document-panel")
    expect(page.get_by_test_id("contracts-document-panel")).to_be_visible()
    expect(page.get_by_test_id("contracts-pdf-panel")).to_be_visible()
    expect(page.get_by_test_id("contracts-integrity-panel")).to_be_visible()
    expect(page.get_by_test_id("contracts-pdf-upload-button")).to_be_visible()
    if page.get_by_test_id("contracts-integrity-process-missing_contract_pdfs").count():
        page.get_by_test_id("contracts-integrity-process-missing_contract_pdfs").click()
        expect(page.get_by_test_id("contracts-pdf-upload-button")).to_be_visible()
    expect(page.get_by_test_id("contracts-table")).to_contain_text("DEMO_A-E2E-001")
    _assert_contracts_visual_layout(page)
    page.get_by_test_id("contracts-lifecycle-segment-active").click()
    expect(page.get_by_test_id("contracts-status-filter")).to_have_value("在租")
    expect(page.get_by_test_id("contracts-lifecycle-segment-active")).to_have_attribute("aria-pressed", "true")
    expect(page.get_by_test_id("contracts-table")).to_contain_text("DEMO_A-E2E-001")
    contracts_sample = _route_sample(page, "contracts")
    assert contracts_sample["cls"] <= 0.1
    assert contracts_sample["longTaskTotal"] <= 250
    contract_row = page.get_by_test_id("contracts-table").locator("tbody tr").filter(has_text="DEMO_A-E2E-001").first
    _open_contract_row_more(contract_row)
    _assert_locator_within_viewport(page, contract_row.get_by_test_id("contract-row-more-menu"))
    page.keyboard.press("Escape")
    expect(contract_row.get_by_test_id("contract-row-more-menu")).to_have_count(0)
    assert not any(path.endswith("/decision-card") for path in requested_paths)
    page.get_by_test_id("contracts-search-input").fill("DEMO_A-E2E-001")
    page.get_by_test_id("contracts-search-button").click()
    expect(page.get_by_test_id("contracts-table")).to_contain_text("端到端测试客户")
    page.evaluate("window.scrollTo(0, 260)")
    page.wait_for_timeout(50)
    contracts_scroll_before_switch = page.evaluate("window.scrollY")
    page.get_by_test_id("nav-rooms").click()
    expect(page.get_by_test_id("rooms-page")).to_be_visible()
    page.get_by_test_id("nav-contracts").click()
    expect(page.get_by_test_id("contracts-search-input")).to_have_value("DEMO_A-E2E-001")
    expect(page.get_by_test_id("contracts-table")).to_contain_text("DEMO_A-E2E-001")
    page.wait_for_timeout(100)
    _wait_for_scroll_position(page, contracts_scroll_before_switch, tolerance=20)
    page.get_by_test_id("contracts-sort-monthly-rent").click()
    expect(page.get_by_test_id("contracts-table")).to_contain_text("26,891.38")
    assert not any(path.endswith("/decision-card") for path in requested_paths)
    terminate_payloads: list[dict[str, object]] = []

    def fulfill_contract_terminate(route) -> None:
        terminate_payloads.append(json.loads(route.request.post_data or "{}"))
        route.fulfill(status=200, content_type="application/json", body=json.dumps({"ok": True}))

    page.route("**/api/contracts/*/terminate**", fulfill_contract_terminate)
    contract_row = page.get_by_test_id("contracts-table").locator("tbody tr").filter(has_text="DEMO_A-E2E-001").first
    _open_contract_row_more(contract_row)
    contract_row.get_by_test_id("contract-terminate-action").click()
    expect(page.get_by_test_id("contract-terminate-modal")).to_be_visible()
    terminate_date = page.get_by_test_id("contract-terminate-date")
    terminate_date.get_by_role("button").first.click()
    terminate_date.locator(".date-picker-link").filter(has_text="清除").click()
    with page.expect_response(
        lambda response: response.request.method == "POST"
        and urlparse(response.url).path.endswith("/terminate")
        and response.status == 200
    ):
        page.get_by_test_id("contract-terminate-confirm").click()
    expect(page.get_by_test_id("contract-terminate-modal")).to_have_count(0)
    assert terminate_payloads == [{"end_date": None}]
    page.unroute("**/api/contracts/*/terminate**")

    native_dialogs: list[str] = []

    def record_native_dialog(dialog) -> None:
        native_dialogs.append(dialog.message)
        dialog.dismiss()

    page.on("dialog", record_native_dialog)

    successor_draft_pattern = re.compile(r".*/api/contracts/\d+/successor-draft.*")
    available_rooms_pattern = re.compile(r".*/api/rooms(?:\?.*)?$")
    change_preview_payloads: list[dict[str, object]] = []
    change_confirm_payloads: list[dict[str, object]] = []
    change_draft = {
        "id": None,
        "contract_no": "DEMO_A-E2E-CHANGE-001",
        "customer_id": None,
        "tower": "南塔",
        "room_no": "E0301",
        "room_items": [
            {
                "tower": "南塔",
                "room_no": "E0301",
                "area": 210.5,
                "unit_price": 4.2,
                "monthly_rent": 26891.38,
                "start_date": "2026-07-01",
                "end_date": "2026-12-31",
                "notes": "",
            }
        ],
        "tenant_name": "端到端测试客户",
        "area": 210.5,
        "unit_price": 4.2,
        "lease_type": "变更签",
        "free_period": 0,
        "monthly_rent": 26891.38,
        "broker_fee": 0,
        "start_date": "2026-07-01",
        "end_date": "2026-12-31",
        "disposal": "",
        "contact_person": "",
        "contact_phone": "",
        "contact_email": "",
        "internal_contact_person": "",
        "internal_contact_feishu_id": "",
        "deposit": 0,
        "deposit_status": "",
        "property_fee_rate": 28,
        "prev_contract_no": "DEMO_A-E2E-001",
        "notes": "",
        "last_updated": "",
    }
    change_preview_response = {
        "requires_confirmation": True,
        "previous_contract": {"contract_no": "DEMO_A-E2E-001", "tenant_name": "端到端测试客户", "room_no": "E0301"},
        "new_contract": {
            "contract_no": "DEMO_A-E2E-CHANGE-001",
            "tenant_name": "端到端测试客户",
            "lease_type": "变更签",
            "start_date": "2026-07-01",
            "end_date": "2026-12-31",
            "room_no": "E0301",
        },
        "change_effective_date": "2026-07-01",
        "previous_end_date_before": "2026-12-31",
        "previous_end_date_after": "2026-06-30",
        "receivable_impacts": [
            {"month": "2026-07", "has_record": True, "payment_count": 1, "received_amount": 1200, "adjustment_amount": 0, "settlement_amount": 1200}
        ],
        "receivable_policy": "不会自动生成冲抵或调整记录。",
    }

    def fulfill_successor_draft(route) -> None:
        route.fulfill(status=200, content_type="application/json", body=json.dumps(change_draft, ensure_ascii=False))

    def fulfill_available_rooms(route) -> None:
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                [{"id": 1, "tower": "南塔", "floor": "3", "room_no": "E0301", "room_type": "办公", "area": 210.5, "occupied": False}],
                ensure_ascii=False,
            ),
        )

    def fulfill_change_preview(route) -> None:
        change_preview_payloads.append(json.loads(route.request.post_data or "{}"))
        route.fulfill(status=200, content_type="application/json", body=json.dumps(change_preview_response, ensure_ascii=False))

    def fulfill_change_confirm(route) -> None:
        change_confirm_payloads.append(json.loads(route.request.post_data or "{}"))
        route.fulfill(status=200, content_type="application/json", body=json.dumps({**change_draft, "id": 99}, ensure_ascii=False))

    page.route(successor_draft_pattern, fulfill_successor_draft)
    page.route(available_rooms_pattern, fulfill_available_rooms)
    page.route("**/api/contracts/change-preview**", fulfill_change_preview)
    page.route("**/api/contracts/change-confirm**", fulfill_change_confirm)

    contract_row = page.get_by_test_id("contracts-table").locator("tbody tr").filter(has_text="DEMO_A-E2E-001").first
    _open_contract_row_more(contract_row)
    with page.expect_request(lambda request: urlparse(request.url).path.endswith("/successor-draft")):
        contract_row.get_by_role("button", name="生成变更签").click()
    form_save = page.locator(".modal-panel .modal-footer .btn-primary").last
    expect(form_save).to_be_enabled()
    with page.expect_request(lambda request: request.method == "POST" and urlparse(request.url).path == "/api/contracts/change-preview"):
        form_save.click()
    expect(page.get_by_test_id("contract-change-preview-modal")).to_be_visible()
    page.get_by_test_id("contract-change-preview-cancel").click()
    expect(page.get_by_test_id("contract-change-preview-modal")).to_have_count(0)
    expect(page.locator(".modal-panel")).to_have_count(1)
    assert change_confirm_payloads == []
    assert native_dialogs == []

    form_save = page.locator(".modal-panel .modal-footer .btn-primary").last
    expect(form_save).to_be_enabled()
    with page.expect_request(lambda request: request.method == "POST" and urlparse(request.url).path == "/api/contracts/change-preview"):
        form_save.click()
    expect(page.get_by_test_id("contract-change-preview-modal")).to_be_visible()
    with page.expect_request(lambda request: request.method == "POST" and urlparse(request.url).path == "/api/contracts/change-confirm"):
        page.get_by_test_id("contract-change-preview-confirm").click()
    expect(page.get_by_test_id("contract-change-preview-modal")).to_have_count(0)
    expect(page.locator(".modal-panel")).to_have_count(0)
    assert len(change_preview_payloads) == 2
    assert change_confirm_payloads == [{"contract": change_preview_payloads[-1]}]
    assert native_dialogs == []
    page.unroute(successor_draft_pattern, fulfill_successor_draft)
    page.unroute(available_rooms_pattern, fulfill_available_rooms)
    page.unroute("**/api/contracts/change-preview**", fulfill_change_preview)
    page.unroute("**/api/contracts/change-confirm**", fulfill_change_confirm)

    contract_delete_pattern = re.compile(r".*/api/contracts/\d+(?:\?.*)?$")
    delete_cancel_requests: list[str] = []

    def fail_contract_delete(route) -> None:
        delete_cancel_requests.append(route.request.url)
        route.fulfill(status=409, content_type="application/json", body=json.dumps({"detail": "unexpected delete"}))

    page.route(contract_delete_pattern, fail_contract_delete)
    contract_row = page.get_by_test_id("contracts-table").locator("tbody tr").filter(has_text="DEMO_A-E2E-001").first
    _open_contract_row_more(contract_row)
    contract_row.get_by_test_id("contract-delete-action").click()
    expect(page.get_by_test_id("contract-delete-confirm-modal")).to_be_visible()
    page.get_by_test_id("contract-delete-cancel").click()
    expect(page.get_by_test_id("contract-delete-confirm-modal")).to_have_count(0)
    assert delete_cancel_requests == []
    assert native_dialogs == []
    page.unroute(contract_delete_pattern, fail_contract_delete)

    delete_requests: list[str] = []

    def fulfill_contract_delete(route) -> None:
        delete_requests.append(urlparse(route.request.url).path)
        route.fulfill(status=200, content_type="application/json", body=json.dumps({"ok": True}))

    page.route(contract_delete_pattern, fulfill_contract_delete)
    contract_row = page.get_by_test_id("contracts-table").locator("tbody tr").filter(has_text="DEMO_A-E2E-001").first
    _open_contract_row_more(contract_row)
    contract_row.get_by_test_id("contract-delete-action").click()
    expect(page.get_by_test_id("contract-delete-confirm-modal")).to_be_visible()
    with page.expect_response(lambda response: response.request.method == "GET" and urlparse(response.url).path == "/api/contracts"):
        with page.expect_request(lambda request: request.method == "DELETE" and re.fullmatch(r"/api/contracts/\d+", urlparse(request.url).path)):
            page.get_by_test_id("contract-delete-confirm").click()
    expect(page.get_by_test_id("contract-delete-confirm-modal")).to_have_count(0)
    assert len(delete_requests) == 1
    assert native_dialogs == []
    page.unroute(contract_delete_pattern, fulfill_contract_delete)

    batch_cancel_requests: list[str] = []

    def fail_contract_batch_delete(route) -> None:
        batch_cancel_requests.append(route.request.url)
        route.fulfill(status=409, content_type="application/json", body=json.dumps({"detail": "unexpected batch delete"}))

    page.route("**/api/contracts/batch-delete**", fail_contract_batch_delete)
    contract_row = page.get_by_test_id("contracts-table").locator("tbody tr").filter(has_text="DEMO_A-E2E-001").first
    selected_contract_id = int(contract_row.locator('input[type="checkbox"]').get_attribute("value") or "0")
    contract_row.locator('input[type="checkbox"]').check()
    _open_contract_batch_actions(page)
    page.get_by_test_id("contract-batch-delete-action").click()
    expect(page.get_by_test_id("contract-batch-delete-confirm-modal")).to_be_visible()
    page.get_by_test_id("contract-batch-delete-cancel").click()
    expect(page.get_by_test_id("contract-batch-delete-confirm-modal")).to_have_count(0)
    assert batch_cancel_requests == []
    assert native_dialogs == []
    page.unroute("**/api/contracts/batch-delete**", fail_contract_batch_delete)

    batch_payloads: list[list[int]] = []

    def fulfill_contract_batch_delete(route) -> None:
        batch_payloads.append(json.loads(route.request.post_data or "[]"))
        route.fulfill(status=200, content_type="application/json", body=json.dumps({"ok": True}))

    page.route("**/api/contracts/batch-delete**", fulfill_contract_batch_delete)
    contract_row.locator('input[type="checkbox"]').check()
    _open_contract_batch_actions(page)
    page.get_by_test_id("contract-batch-delete-action").click()
    expect(page.get_by_test_id("contract-batch-delete-confirm-modal")).to_be_visible()
    with page.expect_response(lambda response: response.request.method == "GET" and urlparse(response.url).path == "/api/contracts"):
        with page.expect_request(lambda request: request.method == "POST" and urlparse(request.url).path == "/api/contracts/batch-delete"):
            page.get_by_test_id("contract-batch-delete-confirm").click()
    expect(page.get_by_test_id("contract-batch-delete-confirm-modal")).to_have_count(0)
    assert batch_payloads == [[selected_contract_id]]
    assert native_dialogs == []
    page.unroute("**/api/contracts/batch-delete**", fulfill_contract_batch_delete)

    rent_term_response = page.request.post(
        f"{base_url}/api/contracts/{selected_contract_id}/rent-terms",
        data=json.dumps(
            {
                "rule_type": "fixed_monthly",
                "start_date": "2026-03-01",
                "end_date": "2026-03-31",
                "monthly_amount": 1234,
                "notes": "E2E rent term delete",
            },
            ensure_ascii=False,
        ),
        headers={"Content-Type": "application/json"},
    )
    assert rent_term_response.ok
    rent_term_id = rent_term_response.json()["id"]
    rent_term_delete_path = f"/api/contracts/{selected_contract_id}/rent-terms/{rent_term_id}"
    rent_term_cancel_requests: list[str] = []

    def fail_rent_term_delete(route) -> None:
        rent_term_cancel_requests.append(route.request.url)
        route.fulfill(status=409, content_type="application/json", body=json.dumps({"detail": "unexpected rent term delete"}))

    page.route(f"**{rent_term_delete_path}**", fail_rent_term_delete)
    contract_row = page.get_by_test_id("contracts-table").locator("tbody tr").filter(has_text="DEMO_A-E2E-001").first
    contract_row.locator('button[title="查看详情"]').click()
    rent_term_delete_button = page.get_by_test_id(f"rent-term-delete-action-{rent_term_id}")
    rent_term_delete_button.scroll_into_view_if_needed()
    rent_term_delete_button.click()
    expect(page.get_by_test_id("rent-term-delete-confirm-modal")).to_be_visible()
    page.get_by_test_id("rent-term-delete-cancel").click()
    expect(page.get_by_test_id("rent-term-delete-confirm-modal")).to_have_count(0)
    page.wait_for_timeout(100)
    assert rent_term_cancel_requests == []
    assert native_dialogs == []
    page.unroute(f"**{rent_term_delete_path}**", fail_rent_term_delete)

    rent_term_delete_button = page.get_by_test_id(f"rent-term-delete-action-{rent_term_id}")
    rent_term_delete_button.scroll_into_view_if_needed()
    rent_term_delete_button.click()
    expect(page.get_by_test_id("rent-term-delete-confirm-modal")).to_be_visible()
    with page.expect_response(lambda response: response.request.method == "DELETE" and urlparse(response.url).path == rent_term_delete_path):
        with page.expect_request(lambda request: request.method == "DELETE" and urlparse(request.url).path == rent_term_delete_path):
            page.get_by_test_id("rent-term-delete-confirm").click()
    expect(page.get_by_test_id("rent-term-delete-confirm-modal")).to_have_count(0)
    assert native_dialogs == []
    page.locator(".modal-header .icon-btn").first.click()

    page.set_viewport_size({"width": 390, "height": 844})
    expect(page.get_by_test_id("contracts-workspace-summary")).to_be_visible()
    expect(page.get_by_test_id("contracts-lifecycle-visual")).to_be_visible()
    expect(page.get_by_test_id("contracts-lifecycle-summary")).to_be_visible()
    expect(page.get_by_test_id("contracts-status-chips")).to_be_visible()
    expect(page.get_by_test_id("contracts-next-action-strip")).to_be_visible()
    expect(page.get_by_test_id("contracts-table")).to_be_visible()
    _assert_no_horizontal_overflow(page)
    page.set_viewport_size({"width": 1440, "height": 960})
    requested_paths.clear()
    page.locator('button[title="查看详情"]').first.click()
    expect(page.get_by_test_id("contract-decision-card")).to_be_visible()
    assert any(path.endswith("/decision-card") for path in requested_paths)
    page.locator(".modal-header .icon-btn").click()

    page.evaluate("window.scrollTo(0, 220)")
    contract_row = page.get_by_test_id("contracts-table").locator("tbody tr").filter(has_text="DEMO_A-E2E-001").first
    contract_row.scroll_into_view_if_needed()
    contract_row.locator('button[title="查看客户"]').click()
    expect(page.get_by_test_id("customer-detail-modal")).to_be_visible()
    assert "active" in (page.get_by_test_id("nav-contracts").get_attribute("class") or "")
    page.locator(".modal-header .icon-btn").first.click()
    expect(page.get_by_test_id("customer-detail-modal")).to_have_count(0)
    assert "active" in (page.get_by_test_id("nav-contracts").get_attribute("class") or "")
    expect(contract_row).to_be_visible()
    _assert_no_horizontal_overflow(page)

    receivables_context_button = page.get_by_test_id("contracts-table").locator("tbody tr").filter(has_text="DEMO_A-E2E-001").first.locator('button[title="查看应收"]')
    receivables_context_button.scroll_into_view_if_needed()
    contract_scroll_before = page.evaluate("window.scrollY")
    receivables_context_button.click()
    expect(page.get_by_test_id("contract-receivables-context")).to_be_visible()
    assert "active" in (page.get_by_test_id("nav-contracts").get_attribute("class") or "")
    page.locator(".modal-header .icon-btn").first.click()
    expect(page.get_by_test_id("contract-receivables-context")).to_have_count(0)
    assert "active" in (page.get_by_test_id("nav-contracts").get_attribute("class") or "")
    _wait_for_scroll_position(page, contract_scroll_before)

    page.set_viewport_size({"width": 390, "height": 844})
    customer_context_button = page.get_by_test_id("contracts-table").locator("tbody tr").filter(has_text="DEMO_A-E2E-001").first.locator(".actions-cell .icon-btn").nth(1)
    customer_context_button.scroll_into_view_if_needed()
    customer_context_button.click()
    expect(page.get_by_test_id("customer-detail-modal")).to_be_visible()
    _assert_no_horizontal_overflow(page)
    page.locator(".modal-header .icon-btn").first.click()
    expect(page.get_by_test_id("customer-detail-modal")).to_have_count(0)
    page.set_viewport_size({"width": 1440, "height": 960})

    page.get_by_test_id("nav-rooms").click()
    _open_parent_details(page, "rooms-table")
    page.get_by_test_id("rooms-tower-filter").select_option("")
    page.get_by_test_id("rooms-use-filter").select_option("")
    page.get_by_test_id("rooms-zone-filter").select_option("")
    page.get_by_test_id("rooms-search-input").fill("")
    expect(page.get_by_test_id("rooms-table")).to_contain_text("E0301")
    _assert_rooms_visual_layout(page)
    _open_parent_details(page, "rooms-priority-list")
    expect(page.get_by_test_id("room-priority-action-card").first).to_be_visible()
    priority_card_count = page.get_by_test_id("room-priority-action-card").count()
    priority_toggle = page.locator('[data-testid^="room-priority-lane-toggle-"]').first
    if priority_toggle.count() > 0:
        priority_toggle.click()
        page.wait_for_function(
            """count => document.querySelectorAll('[data-testid="room-priority-action-card"]').length > count""",
            priority_card_count,
        )
    priority_card = page.get_by_test_id("room-priority-action-card").first
    priority_card.scroll_into_view_if_needed()
    priority_scroll_before = page.evaluate("window.scrollY")
    priority_card.click()
    expect(page.get_by_test_id("room-operation-action-lane")).to_be_visible()
    page.locator(".modal-header .icon-btn").first.click()
    expect(page.get_by_test_id("room-operation-action-lane")).to_have_count(0)
    _wait_for_scroll_position(page, priority_scroll_before)
    rooms_sample = _route_sample(page, "rooms")
    assert rooms_sample["cls"] <= 0.1
    assert rooms_sample["longTaskTotal"] <= 250
    page.set_viewport_size({"width": 390, "height": 844})
    _assert_rooms_visual_layout(page)
    page.set_viewport_size({"width": 1440, "height": 960})
    page.get_by_test_id("room-status-segment-vacant").click()
    _open_parent_details(page, "rooms-table")
    expect(page.get_by_test_id("rooms-table")).to_contain_text("P0201")
    page.get_by_test_id("room-status-segment-vacant").click()
    page.get_by_test_id("rooms-segment-summary").get_by_role("button", name=re.compile("商业配套")).click()
    _open_parent_details(page, "rooms-table")
    expect(page.get_by_test_id("rooms-table")).to_contain_text("P0201")
    page.get_by_test_id("rooms-segment-summary").get_by_role("button", name=re.compile("商业配套")).click()
    page.get_by_test_id("room-vacancy-top3").get_by_test_id("rooms-vacancy-pressure-row").first.click()
    _open_parent_details(page, "rooms-table")
    expect(page.get_by_test_id("rooms-table")).to_contain_text("P0201")
    page.get_by_test_id("rooms-occupancy-filter").select_option("")
    page.get_by_test_id("rooms-tower-filter").select_option("")
    page.get_by_test_id("rooms-zone-filter").select_option("")
    page.get_by_test_id("rooms-tower-filter").select_option("商业配套")
    _open_parent_details(page, "rooms-table")
    expect(page.get_by_test_id("rooms-table")).to_contain_text("P0201")
    page.get_by_test_id("rooms-zone-filter").select_option("retail")
    _open_parent_details(page, "rooms-table")
    expect(page.get_by_test_id("rooms-table")).to_contain_text("P0201")
    page.get_by_test_id("rooms-sort-area").click()
    expect(page.get_by_test_id("rooms-table")).to_be_visible()
    page.get_by_test_id("rooms-tower-filter").select_option("")
    page.get_by_test_id("rooms-zone-filter").select_option("")
    page.get_by_test_id("rooms-search-input").fill("E0301")
    room_row = page.get_by_test_id("rooms-table").locator("tbody tr").filter(has_text="E0301").first
    expect(room_row.get_by_test_id("room-row-more-actions")).to_be_visible()
    room_row.get_by_test_id("room-row-more-actions").get_by_role("button", name="更多").click()
    room_more_menu = room_row.get_by_test_id("room-row-more-menu")
    _assert_locator_within_viewport(page, room_more_menu)
    expect(room_row.get_by_role("button", name="编辑房间")).to_be_visible()
    expect(room_row.get_by_role("button", name="删除房间")).to_be_visible()
    page.keyboard.press("Escape")
    expect(room_row.get_by_test_id("room-row-more-menu")).to_have_count(0)
    assert "active" in (page.get_by_test_id("nav-rooms").get_attribute("class") or "")
    room_detail_action = room_row.get_by_test_id("room-row-detail-action")
    room_detail_action.scroll_into_view_if_needed()
    room_scroll_before = page.evaluate("window.scrollY")
    room_detail_action.click()
    expect(page.get_by_test_id("room-operation-action-lane")).to_be_visible()
    page.locator(".modal-header .icon-btn").first.click()
    expect(page.get_by_test_id("room-operation-action-lane")).to_have_count(0)
    _wait_for_scroll_position(page, room_scroll_before)

    requested_paths.clear()
    page.evaluate("window.__leaseManagerPerformance?.reset()")
    page.get_by_test_id("nav-customers").click()
    expect(page.get_by_test_id("customer-workspace-summary")).to_be_visible()
    expect(page.get_by_test_id("customer-signal-band")).to_be_visible()
    _open_parent_details(page, "customer-value-risk-view")
    expect(page.get_by_test_id("customer-concentration-chart")).to_be_visible()
    expect(page.get_by_test_id("customer-risk-matrix")).to_be_visible()
    expect(page.get_by_test_id("customer-value-risk-view")).to_be_visible()
    expect(page.get_by_test_id("customers-table")).to_contain_text("端到端测试客户")
    expect(page.get_by_test_id("customer-status-chips")).to_be_visible()
    expect(page.get_by_test_id("customer-compact-row").first).to_be_visible()
    expect(page.get_by_test_id("customer-risk-bar").first).to_be_visible()
    expect(page.get_by_test_id("customer-row-action-strip").first).to_be_visible()
    expect(page.get_by_test_id("customer-row-primary-action").first).to_be_visible()
    expect(page.get_by_test_id("customer-batch-trigger")).to_be_visible()
    expect(page.get_by_test_id("customer-preview-button")).to_have_count(0)
    expect(page.get_by_test_id("follow-up-preview-button")).to_have_count(0)
    page.get_by_test_id("customer-batch-trigger").click()
    expect(page.get_by_test_id("customer-batch-panel")).to_be_visible()
    expect(page.get_by_test_id("customer-preview-button")).to_be_visible()
    expect(page.get_by_test_id("follow-up-preview-button")).to_be_visible()
    page.get_by_test_id("customer-batch-trigger").click()
    expect(page.get_by_test_id("customer-batch-panel")).to_have_count(0)
    assert not any(path.startswith("/api/ai") for path in requested_paths)
    _assert_customers_visual_layout(page)
    customers_sample = _route_sample(page, "customers")
    assert customers_sample["cls"] <= 0.1
    assert customers_sample["longTaskTotal"] <= 250
    page.set_viewport_size({"width": 390, "height": 844})
    expect(page.get_by_test_id("customers-page")).to_be_visible()
    _assert_no_horizontal_overflow(page)
    page.set_viewport_size({"width": 1440, "height": 960})
    page.route(
        "**/api/customers/import/preview**",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "total_rows": 2,
                    "valid_rows": 1,
                    "error_rows": 1,
                    "warning_rows": 1,
                    "errors": [{"row_number": 3, "field": "name", "reason": "客户名称不能为空"}],
                    "warnings": [{"row_number": 2, "field": "name", "reason": "客户已存在，正式导入将更新"}],
                    "preview_rows": [{"row_number": 2, "action": "update", "name": "端到端测试客户"}],
                },
                ensure_ascii=False,
            ),
        ),
    )
    requested_paths.clear()
    page.get_by_test_id("customer-preview-input").set_input_files(
        {"name": "bad-customers.xlsx", "mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "buffer": b"mock"}
    )
    expect(page.get_by_test_id("customer-import-preview-result")).to_contain_text("总 2 行")
    expect(page.get_by_test_id("customer-import-preview-result")).to_contain_text("第 3 行")
    expect(page.get_by_test_id("customer-import-preview-result")).to_contain_text("字段 name")
    assert "/api/customers/import/preview" in set(requested_paths)
    page.unroute("**/api/customers/import/preview**")
    page.set_viewport_size({"width": 390, "height": 844})
    expect(page.get_by_test_id("customer-workspace-summary")).to_be_visible()
    _open_parent_details(page, "customer-value-risk-view")
    expect(page.get_by_test_id("customer-value-risk-view")).to_be_visible()
    expect(page.get_by_test_id("customer-concentration-chart")).to_be_visible()
    expect(page.get_by_test_id("customer-risk-matrix")).to_be_visible()
    expect(page.get_by_test_id("customer-import-preview-result")).to_be_visible()
    expect(page.get_by_test_id("customers-table")).to_contain_text("端到端测试客户")
    expect(page.get_by_test_id("customer-compact-row").first).to_be_visible()
    expect(page.get_by_test_id("customer-risk-bar").first).to_be_visible()
    expect(page.get_by_test_id("customer-row-action-strip").first).to_be_visible()
    expect(page.get_by_test_id("customer-row-primary-action").first).to_be_visible()
    page.get_by_test_id("customer-batch-trigger").click()
    expect(page.get_by_test_id("customer-batch-panel")).to_be_visible()
    _assert_no_horizontal_overflow(page)
    page.get_by_test_id("customer-batch-trigger").click()
    expect(page.get_by_test_id("customer-batch-panel")).to_have_count(0)
    page.set_viewport_size({"width": 1440, "height": 960})
    requested_paths.clear()
    page.get_by_test_id("customer-view-button").first.click()
    expect(page.get_by_test_id("customer-ai-assist")).to_be_visible()
    expect(page.get_by_test_id("customer-ai-summary-button")).to_be_visible()
    expect(page.get_by_test_id("customer-ai-suggestion-button")).to_be_visible()
    assert not any(path.startswith("/api/ai") for path in requested_paths)
    with page.expect_request(lambda request: urlparse(request.url).path == "/api/ai/customer-summary"):
        page.get_by_test_id("customer-ai-summary-button").click()
    expect(page.get_by_test_id("customer-ai-error")).to_contain_text("生成失败，请稍后重试。")

    page.route(
        "**/api/ai/customer-summary**",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "summary": "```json\n"
                    + json.dumps(
                        {
                            "summary": "客户经营稳定，近期需要确认续租意向。",
                            "highlights": ["合同正常履行"],
                            "suggested_actions": ["确认联系人", "复核下次沟通时间"],
                        },
                        ensure_ascii=False,
                    )
                    + "\n```",
                    "highlights": [],
                    "suggested_actions": [],
                },
                ensure_ascii=False,
            ),
        ),
    )
    with page.expect_request(lambda request: urlparse(request.url).path == "/api/ai/customer-summary"):
        page.get_by_test_id("customer-ai-summary-button").click()
    expect(page.get_by_test_id("customer-ai-summary-result")).to_contain_text("客户经营稳定")
    expect(page.get_by_test_id("customer-ai-summary-result")).to_contain_text("合同正常履行")
    expect(page.get_by_test_id("customer-ai-summary-result")).to_contain_text("确认联系人")
    expect(page.get_by_test_id("customer-ai-summary-result")).not_to_contain_text('"summary"')
    expect(page.get_by_test_id("customer-ai-summary-result")).not_to_contain_text("{")
    page.get_by_test_id("customer-ai-summary-copy").click()
    expect(page.get_by_text("结果已复制").last).to_be_visible()
    page.unroute("**/api/ai/customer-summary**")

    posted_follow_ups: list[str] = []
    page.on(
        "request",
        lambda request: posted_follow_ups.append(urlparse(request.url).path)
        if request.method == "POST"
        and urlparse(request.url).path.startswith("/api/customers/")
        and urlparse(request.url).path.endswith("/follow-ups")
        else None,
    )
    page.route(
        "**/api/ai/follow-up-suggestion**",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "suggestion": json.dumps(
                        {
                            "summary": "请电话确认续租意向。",
                            "highlights": ["近期到期"],
                            "next_steps": ["确认联系人", "约定下次沟通"],
                        },
                        ensure_ascii=False,
                    ),
                    "highlights": [],
                    "suggested_actions": [],
                },
                ensure_ascii=False,
            ),
        ),
    )
    with page.expect_request(lambda request: urlparse(request.url).path == "/api/ai/follow-up-suggestion"):
        page.get_by_test_id("customer-ai-suggestion-button").click()
    expect(page.get_by_test_id("customer-ai-suggestion-result")).to_contain_text("请电话确认续租意向")
    expect(page.get_by_test_id("customer-ai-suggestion-result")).not_to_contain_text('"summary"')
    expect(page.get_by_test_id("customer-ai-suggestion-result")).not_to_contain_text("{")
    page.get_by_test_id("customer-ai-suggestion-copy").click()
    expect(page.get_by_text("结果已复制").last).to_be_visible()
    page.get_by_test_id("customer-ai-suggestion-to-follow-up").click()
    expect(page.get_by_role("heading", name="新增跟进")).to_be_visible()
    expect(page.get_by_test_id("follow-up-content-input")).to_have_value("请电话确认续租意向。")
    expect(page.get_by_test_id("follow-up-next-action-input")).to_have_value("确认联系人\n约定下次沟通")
    page.wait_for_timeout(100)
    assert posted_follow_ups == []
    page.get_by_test_id("follow-up-content-input").fill("确认续租意向。")
    with page.expect_request(
        lambda request: request.method == "POST"
        and urlparse(request.url).path.startswith("/api/customers/")
        and urlparse(request.url).path.endswith("/follow-ups")
    ):
        page.get_by_role("button", name="保存").last.click()
    expect(page.get_by_text("跟进记录已新增")).to_be_visible()
    assert posted_follow_ups
    page.unroute("**/api/ai/follow-up-suggestion**")

    page.route(
        "**/api/ai/customer-summary**",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"summary": "", "highlights": [], "suggested_actions": []}, ensure_ascii=False),
        ),
    )
    with page.expect_request(lambda request: urlparse(request.url).path == "/api/ai/customer-summary"):
        page.get_by_test_id("customer-ai-summary-button").click()
    expect(page.get_by_test_id("customer-ai-summary-result")).to_contain_text("暂未生成有效摘要")
    expect(page.get_by_test_id("customer-ai-summary-copy")).to_have_count(0)
    page.unroute("**/api/ai/customer-summary**")
    page.get_by_test_id("base-modal-close").first.click()

    requested_paths.clear()
    page.get_by_test_id("nav-group-finance-risk").click()
    expect(page.get_by_test_id("nav-group-finance-risk").locator("xpath=..")).not_to_contain_text("辅助分析")
    page.get_by_test_id("nav-ai-assist").click()
    _assert_ai_assist_visual_layout(page, requested_paths)
    page.set_viewport_size({"width": 390, "height": 844})
    expect(page.get_by_test_id("ai-assist-page")).to_be_visible()
    _assert_visible_copy_hygiene(page, "ai-assist-page")
    _assert_no_horizontal_overflow(page)
    page.set_viewport_size({"width": 1440, "height": 960})
    page.get_by_test_id("ai-customer-search-input").fill("端到端测试客户")
    page.get_by_test_id("ai-customer-search-button").click()
    expect(page.get_by_text("端到端测试客户").first).to_be_visible()
    page.get_by_text("端到端测试客户").first.click()
    expect(page.get_by_test_id("ai-workbench-summary-button")).to_be_visible()
    with page.expect_request(lambda request: urlparse(request.url).path == "/api/ai/customer-summary"):
        page.get_by_test_id("ai-workbench-summary-button").click()
    expect(page.get_by_test_id("ai-workbench-error")).to_contain_text("生成失败，请稍后重试。")
    page.route(
        "**/api/ai/customer-summary**",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "summary": json.dumps(
                        {
                            "summary": "客户租赁状态稳定，建议本周确认续租意向。",
                            "highlights": ["合同正常履行"],
                            "suggested_actions": ["确认联系人", "复核下次沟通时间"],
                        },
                        ensure_ascii=False,
                    ),
                    "highlights": [],
                    "suggested_actions": [],
                },
                ensure_ascii=False,
            ),
        ),
    )
    with page.expect_request(lambda request: urlparse(request.url).path == "/api/ai/customer-summary"):
        page.get_by_test_id("ai-workbench-summary-button").click()
    expect(page.get_by_test_id("ai-customer-summary-result")).to_contain_text("客户租赁状态稳定")
    expect(page.get_by_test_id("ai-result-evidence")).to_contain_text("合同正常履行")
    expect(page.get_by_test_id("ai-result-next-actions")).to_contain_text("确认联系人")
    expect(page.get_by_test_id("ai-customer-summary-result")).not_to_contain_text('"summary"')
    expect(page.get_by_test_id("ai-customer-summary-result")).not_to_contain_text("{")
    page.unroute("**/api/ai/customer-summary**")
    with page.expect_request(lambda request: urlparse(request.url).path == "/api/ai/follow-up-suggestion"):
        page.get_by_test_id("ai-workbench-suggestion-button").click()
    expect(page.get_by_test_id("ai-workbench-suggestion-error")).to_contain_text("生成失败，请稍后重试。")
    page.route(
        "**/api/ai/follow-up-suggestion**",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "suggestion": json.dumps(
                        {
                            "summary": "请电话确认续租意向。",
                            "follow_up_points": ["确认联系人", "约定下次沟通"],
                        },
                        ensure_ascii=False,
                    ),
                    "highlights": [],
                    "suggested_actions": [],
                },
                ensure_ascii=False,
            ),
        ),
    )
    with page.expect_request(lambda request: urlparse(request.url).path == "/api/ai/follow-up-suggestion"):
        page.get_by_test_id("ai-workbench-suggestion-button").click()
    expect(page.get_by_test_id("ai-customer-suggestion-result")).to_contain_text("请电话确认续租意向")
    expect(page.get_by_test_id("ai-result-summary")).to_contain_text("结论")
    expect(page.get_by_test_id("ai-result-evidence")).to_contain_text("依据")
    expect(page.get_by_test_id("ai-result-next-actions")).to_contain_text("下一步")
    expect(page.get_by_test_id("ai-result-next-actions")).to_contain_text("确认联系人")
    expect(page.get_by_test_id("ai-customer-suggestion-result")).not_to_contain_text("人工确认")
    expect(page.get_by_test_id("ai-customer-suggestion-result")).not_to_contain_text('"summary"')
    expect(page.get_by_test_id("ai-customer-suggestion-result")).not_to_contain_text("{")
    page.get_by_test_id("ai-workbench-suggestion-copy").click()
    expect(page.get_by_text("结果已复制").last).to_be_visible()
    page.unroute("**/api/ai/follow-up-suggestion**")
    expect(page.get_by_test_id("ai-risk-workbench-error")).to_have_count(0)
    page.set_viewport_size({"width": 390, "height": 844})
    expect(page.get_by_test_id("ai-assist-page")).to_be_visible()
    _assert_no_horizontal_overflow(page)
    page.set_viewport_size({"width": 1440, "height": 960})

    expect(page.get_by_test_id("ai-mode-customer")).to_be_visible()
    expect(page.get_by_test_id("ai-mode-risk")).to_be_visible()
    page.get_by_test_id("ai-risk-load-button").click()
    expect(page.get_by_test_id("risk-ai-explain-button").first).to_be_visible()
    with page.expect_request(lambda request: urlparse(request.url).path == "/api/ai/risk-explanation"):
        page.get_by_test_id("risk-ai-explain-button").first.click()
    expect(page.get_by_test_id("ai-risk-workbench-error")).to_contain_text("生成失败，请稍后重试。")
    page.route(
        "**/api/ai/risk-explanation**",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "explanation": json.dumps(
                        {
                            "explanation": "该风险需要补齐合同和附件完整性记录。",
                            "highlights": ["附件待补"],
                            "suggested_actions": ["联系经办人补齐资料"],
                        },
                        ensure_ascii=False,
                    ),
                    "highlights": [],
                    "suggested_actions": [],
                },
                ensure_ascii=False,
            ),
        ),
    )
    with page.expect_request(lambda request: urlparse(request.url).path == "/api/ai/risk-explanation"):
        page.get_by_test_id("risk-ai-explain-button").first.click()
    expect(page.get_by_test_id("ai-risk-workbench-result")).to_contain_text("补齐合同")
    expect(page.get_by_test_id("ai-risk-workbench-result")).to_contain_text("结论")
    expect(page.get_by_test_id("ai-risk-workbench-result")).to_contain_text("依据")
    expect(page.get_by_test_id("ai-risk-workbench-result")).to_contain_text("下一步")
    expect(page.get_by_test_id("ai-risk-workbench-result")).to_contain_text("附件待补")
    expect(page.get_by_test_id("ai-risk-workbench-result")).to_contain_text("联系经办人补齐资料")
    expect(page.get_by_test_id("ai-risk-workbench-result")).not_to_contain_text("人工确认")
    expect(page.get_by_test_id("ai-risk-workbench-result")).not_to_contain_text('"explanation"')
    expect(page.get_by_test_id("ai-risk-workbench-result")).not_to_contain_text("{")
    page.get_by_test_id("ai-workbench-risk-copy").click()
    expect(page.get_by_text("结果已复制").last).to_be_visible()
    page.unroute("**/api/ai/risk-explanation**")
    expect(page.get_by_test_id("ai-assist-page")).to_be_visible()
    page.get_by_role("button", name="查看合同附件").first.click()
    expect(page.get_by_test_id("contract-decision-card")).to_be_visible()
    assert "active" in (page.get_by_test_id("nav-ai-assist").get_attribute("class") or "")
    page.locator(".modal-header .icon-btn").click()

    if page.get_by_test_id("nav-receivables").count() == 0:
        page.get_by_test_id("nav-group-finance-risk").click()
    page.evaluate("window.__leaseManagerPerformance?.reset()")
    page.get_by_test_id("nav-receivables").click()
    expect(page.get_by_test_id("receivable-flow-overview")).to_be_visible()
    for flow_test_id in [
        "receivable-flow-step-due",
        "receivable-flow-step-received",
        "receivable-flow-step-unpaid",
        "receivable-flow-step-overdue",
        "receivable-flow-step-todo",
    ]:
        expect(page.get_by_test_id(flow_test_id)).to_be_visible()
    expect(page.get_by_test_id("receivable-detail-ledger")).to_be_visible()
    expect(page.get_by_test_id("receivables-table")).to_have_count(0)
    _reset_receivables_baseline(page)
    expect(page.get_by_test_id("receivable-status-chips")).to_be_visible()
    expect(page.get_by_test_id("receivable-flow-overview")).to_be_visible()
    expect(page.get_by_test_id("receivable-triage-board")).to_be_visible()
    expect(page.get_by_test_id("receivable-action-path")).to_be_visible()
    for action_step_test_id in [
        "receivable-action-step-overdue",
        "receivable-action-step-follow-up",
        "receivable-action-step-dunning",
        "receivable-action-step-invoice",
        "receivable-action-step-not-due",
    ]:
        expect(page.get_by_test_id(action_step_test_id)).to_be_visible()
    _assert_receivables_visual_layout(page)
    receivables_sample = _route_sample(page, "receivables")
    assert receivables_sample["cls"] <= 0.1
    assert receivables_sample["longTaskTotal"] <= 250
    expect(page.get_by_text("未收不等于逾期", exact=True)).to_have_count(0)
    expect(page.get_by_test_id("receivables-table")).to_contain_text("DEMO_A-E2E-001")
    expect(page.get_by_test_id("receivables-table")).to_contain_text("5,894.00")
    requested_paths.clear()
    page.get_by_test_id("receivable-flow-step-overdue").click()
    expect(page.get_by_test_id("receivable-flow-step-overdue")).to_have_attribute("aria-pressed", "true")
    expect(page.get_by_test_id("receivable-ledger-tab-dunning")).to_have_class(re.compile("active"))
    expect(page.get_by_test_id("receivable-detail-ledger")).to_contain_text("催收台账")
    page.get_by_test_id("receivable-flow-step-due").click()
    expect(page.get_by_test_id("receivable-ledger-tab-main")).to_have_class(re.compile("active"))
    expect(page.get_by_test_id("receivables-table")).to_contain_text("DEMO_A-E2E-001")
    requested_paths.clear()
    page.get_by_test_id("receivable-action-step-invoice").click()
    expect(page.get_by_test_id("receivable-action-step-invoice")).to_have_attribute("aria-pressed", "true")
    expect(page.get_by_test_id("receivables-table")).to_contain_text("DEMO_A-E2E-001")
    assert not [path for path in requested_paths if path.startswith("/api/")]
    page.get_by_test_id("receivable-triage-segment-all").click()
    expect(page.get_by_test_id("receivable-triage-segment-all")).to_have_attribute("aria-pressed", "true")
    expect(page.get_by_test_id("receivables-table")).to_contain_text("DEMO_A-E2E-001")
    page.get_by_test_id("receivable-action-step-not-due").click()
    expect(page.get_by_test_id("receivable-action-step-not-due")).to_have_attribute("aria-pressed", "true")
    expect(page.get_by_test_id("receivables-table")).to_contain_text("DEMO_A-E2E-001")
    page.evaluate("window.scrollTo(0, 320)")
    page.wait_for_timeout(50)
    receivables_scroll_before_switch = page.evaluate("window.scrollY")
    page.get_by_test_id("nav-dashboard").click()
    expect(page.get_by_test_id("dashboard-page")).to_be_visible()
    page.get_by_test_id("nav-receivables").click()
    expect(page.get_by_test_id("receivable-action-step-not-due")).to_have_attribute("aria-pressed", "true")
    expect(page.get_by_test_id("receivables-table")).to_contain_text("DEMO_A-E2E-001")
    page.wait_for_timeout(100)
    _wait_for_scroll_position(page, receivables_scroll_before_switch, tolerance=20)
    page.get_by_test_id("receivable-status-chips").get_by_role("button").filter(has_text="未到期").click()
    expect(page.get_by_test_id("receivables-table")).to_contain_text("DEMO_A-E2E-001")
    for row_action in [
        "receivable-register-",
        "receivable-payment-notice-",
        "receivable-dunning-notice-",
        "receivable-contract-",
        "receivable-customer-",
    ]:
        expect(page.locator(f'[data-testid^="{row_action}"]').first).to_be_visible()
    register_button = page.locator('[data-testid^="receivable-register-"]').first
    register_button.click()
    expect(page.get_by_text("登记租金收款与核销")).to_be_visible()
    page.locator(".modal-header .icon-btn").click()
    expect(page.get_by_text("登记租金收款与核销")).to_have_count(0)
    contract_id = int((register_button.get_attribute("data-testid") or "").rsplit("-", 1)[1])
    payment_response = page.request.post(
        f"{base_url}/api/contracts/receivables/monthly/{contract_id}/payments",
        data=json.dumps(
            {
                "month": time.strftime("%Y-%m"),
                "amount": 1000,
                "payment_date": time.strftime("%Y-%m-%d"),
                "allocation_type": "收款",
                "payment_method": "转账",
                "reference_no": "E2E-PAY-001",
            },
            ensure_ascii=False,
        ),
        headers={"Content-Type": "application/json"},
    )
    assert payment_response.status == 200
    with page.expect_response(
        lambda response: urlparse(response.url).path == "/api/contracts/receivables/monthly"
        and response.status == 200
    ):
        page.get_by_test_id("receivables-query").click()
    expect(page.locator(".refreshing-indicator")).to_have_count(0)
    page.locator(f'[data-testid="receivable-register-{contract_id}"]').click()
    expect(page.get_by_text("登记租金收款与核销")).to_be_visible()
    payment_delete_button = page.locator('[data-testid^="receivable-payment-delete-"]').first
    expect(payment_delete_button).to_be_visible()
    payment_id = int((payment_delete_button.get_attribute("data-testid") or "").rsplit("-", 1)[1])
    delete_path = f"/api/contracts/receivables/payments/{payment_id}"
    dialog_messages: list[str] = []
    delete_requests: list[str] = []
    page.on("dialog", lambda dialog: (dialog_messages.append(dialog.message), dialog.dismiss()))
    page.on(
        "request",
        lambda request: delete_requests.append(urlparse(request.url).path)
        if request.method == "DELETE" and urlparse(request.url).path == delete_path
        else None,
    )
    payment_delete_button.click()
    assert dialog_messages == []
    expect(page.get_by_test_id("receivable-payment-delete-confirm-modal")).to_be_visible()
    page.get_by_test_id("receivable-payment-delete-cancel").click()
    expect(page.get_by_test_id("receivable-payment-delete-confirm-modal")).to_have_count(0)
    expect(page.get_by_text("登记租金收款与核销")).to_be_visible()
    page.wait_for_timeout(100)
    assert delete_requests == []
    page.locator('[data-testid^="receivable-payment-delete-"]').first.click()
    expect(page.get_by_test_id("receivable-payment-delete-confirm-modal")).to_be_visible()
    with page.expect_request(lambda request: request.method == "DELETE" and urlparse(request.url).path == delete_path):
        page.get_by_test_id("receivable-payment-delete-confirm").click()
    expect(page.get_by_test_id("receivable-payment-delete-confirm-modal")).to_have_count(0)
    expect(page.get_by_text("登记租金收款与核销")).to_be_visible()
    assert delete_requests == [delete_path]
    page.locator(".modal-header .icon-btn").click()
    expect(page.get_by_text("登记租金收款与核销")).to_have_count(0)
    page.locator('[data-testid^="receivable-payment-notice-"]').first.click()
    expect(page.get_by_text("生成缴款通知书")).to_be_visible()
    page.locator(".modal-header .icon-btn").click()
    expect(page.get_by_text("生成缴款通知书")).to_have_count(0)
    page.locator('[data-testid^="receivable-dunning-notice-"]').first.click()
    expect(page.get_by_text("生成催收通知")).to_be_visible()
    page.locator(".modal-header .icon-btn").click()
    expect(page.get_by_text("生成催收通知")).to_have_count(0)
    page.locator('[data-testid^="receivable-customer-"]:not([disabled])').first.click()
    expect(page.get_by_test_id("customer-detail-modal")).to_be_visible()
    assert "active" in (page.get_by_test_id("nav-receivables").get_attribute("class") or "")
    page.locator(".modal-header .icon-btn").first.click()
    expect(page.get_by_test_id("customer-detail-modal")).to_have_count(0)
    _assert_scroll_usable(page)
    page.set_viewport_size({"width": 390, "height": 844})
    expect(page.get_by_test_id("receivable-status-chips")).to_be_visible()
    expect(page.get_by_test_id("receivable-collection-overview")).to_be_visible()
    _assert_element_within_viewport(page, "receivable-collection-overview")
    expect(page.get_by_test_id("receivable-flow-overview")).to_be_visible()
    _assert_element_within_viewport(page, "receivable-flow-overview")
    expect(page.get_by_test_id("receivable-triage-board")).to_be_visible()
    expect(page.get_by_test_id("receivable-action-path")).to_be_visible()
    _assert_element_within_viewport(page, "receivable-action-path")
    expect(page.get_by_test_id("receivable-detail-ledger")).to_be_visible()
    _assert_no_horizontal_overflow(page)
    page.set_viewport_size({"width": 1440, "height": 960})
    page.locator('[data-testid^="receivable-contract-"]').first.click()
    expect(page.get_by_test_id("contract-decision-card")).to_be_visible()
    assert "active" in (page.get_by_test_id("nav-receivables").get_attribute("class") or "")
    page.locator(".modal-header .icon-btn").click()

    requested_paths.clear()
    risk_flags_pattern = re.compile(r".*/api/risks(?:\?.*)?$")

    def slow_risk_flags(route) -> None:
        time.sleep(0.2)
        route.continue_()

    page.route(risk_flags_pattern, slow_risk_flags)
    with (
        page.expect_request(lambda request: urlparse(request.url).path == "/api/risks"),
        page.expect_request(lambda request: urlparse(request.url).path == "/api/attachments/completeness-tasks"),
    ):
        page.get_by_test_id("nav-risks").click()
    _open_parent_details(page, "risk-filter-bar")
    if page.get_by_test_id("table-skeleton").count():
        expect(page.get_by_test_id("table-skeleton")).to_be_visible()
    else:
        expect(page.get_by_test_id("risk-filter-bar")).to_be_visible()
    page.unroute(risk_flags_pattern, slow_risk_flags)
    _assert_risk_center_visual_layout(page)
    risks_sample = _route_sample(page, "risks")
    assert risks_sample["cls"] <= 0.1
    assert risks_sample["longTaskTotal"] <= 250
    expect(page.get_by_test_id("risk-ai-explain-button").first).to_be_visible()
    assert "/api/risks" in set(requested_paths)
    assert "/api/attachments/completeness-tasks" in set(requested_paths)
    assert "/api/risks/export" not in set(requested_paths)
    assert not any(path.startswith("/api/ai") for path in requested_paths)

    risk_segment = page.locator("[data-risk-segment='true']:not([disabled])").first
    expect(risk_segment).to_be_visible()
    risk_type = risk_segment.get_attribute("data-risk-type")
    risk_severity = risk_segment.get_attribute("data-risk-severity")
    with page.expect_request(lambda request: urlparse(request.url).path == "/api/risks"):
        risk_segment.click()
    _open_parent_details(page, "risk-flag-list")
    expect(page.get_by_test_id("risk-type-select")).to_have_value(risk_type)
    expect(page.get_by_test_id("risk-severity-select")).to_have_value(risk_severity)
    expect(page.get_by_test_id("risk-center-page")).to_be_visible()
    expect(page.get_by_test_id("risk-flag-list")).to_be_visible()
    expect(page.get_by_test_id("risk-ai-explain-button").first).to_be_visible()
    page.evaluate("window.scrollTo(0, 280)")
    page.wait_for_timeout(50)
    risk_scroll_before_switch = page.evaluate("window.scrollY")
    page.get_by_test_id("nav-dashboard").click()
    expect(page.get_by_test_id("dashboard-page")).to_be_visible()
    page.get_by_test_id("nav-risks").click()
    expect(page.get_by_test_id("risk-type-select")).to_have_value(risk_type)
    expect(page.get_by_test_id("risk-severity-select")).to_have_value(risk_severity)
    page.wait_for_timeout(100)
    _wait_for_scroll_position(page, risk_scroll_before_switch, tolerance=20)

    page.set_viewport_size({"width": 390, "height": 844})
    expect(page.get_by_test_id("risk-center-page")).to_be_visible()
    _open_parent_details(page, "risk-filter-bar")
    expect(page.get_by_test_id("risk-filter-bar")).to_be_visible()
    _assert_visible_copy_hygiene(page, "risk-center-page")
    _assert_no_horizontal_overflow(page)
    page.set_viewport_size({"width": 1440, "height": 960})

    requested_paths.clear()
    with page.expect_request(lambda request: urlparse(request.url).path == "/api/ai/risk-explanation"):
        page.get_by_test_id("risk-ai-explain-button").first.click()
    expect(page.get_by_test_id("risk-ai-error")).to_contain_text("生成失败，请稍后重试。")
    assert "/api/ai/risk-explanation" in set(requested_paths)
    assert "/api/risks/export" not in set(requested_paths)
    page.get_by_test_id("base-modal-close").first.click()

    if page.get_by_test_id("nav-audit").count() == 0:
        page.get_by_test_id("nav-group-system").click()
    with page.expect_request(lambda request: urlparse(request.url).path == "/api/audit/logs"):
        page.get_by_test_id("nav-audit").click()
    expect(page.get_by_test_id("audit-activity-strip")).to_be_visible()
    expect(page.get_by_test_id("audit-result-card")).to_be_visible()
    expect(page.get_by_test_id("audit-latest-action-card")).to_be_visible()
    expect(page.get_by_test_id("audit-latest-operator-card")).to_be_visible()
    expect(page.get_by_test_id("audit-latest-target-card")).to_be_visible()
    expect(page.get_by_test_id("audit-export-button")).to_be_visible()
    expect(page.get_by_test_id("audit-action-strip")).to_be_visible()
    _open_parent_details(page, "audit-filter-panel")
    expect(page.get_by_test_id("audit-filter-panel")).to_be_visible()
    expect(page.get_by_test_id("audit-log-table")).to_contain_text("DEMO_A-E2E-001")
    _assert_audit_visual_layout(page)
    page.get_by_test_id("audit-action-filter").select_option("create")
    page.get_by_test_id("audit-target-filter").select_option("contract")
    page.get_by_test_id("audit-filter-submit").click()
    expect(page.get_by_test_id("audit-log-table")).to_contain_text("DEMO_A-E2E-001")
    page.get_by_test_id("audit-refresh").click()
    expect(page.get_by_test_id("audit-log-table")).to_contain_text("DEMO_A-E2E-001")
    page.get_by_test_id("audit-filter-clear").click()
    expect(page.get_by_test_id("audit-log-table")).to_contain_text("DEMO_A-E2E-001")
    assert E2E_ADMIN_PASSWORD not in page.get_by_test_id("audit-log-table").inner_text()
    page.set_viewport_size({"width": 390, "height": 844})
    expect(page.get_by_test_id("audit-filter-panel")).to_be_visible()
    _assert_audit_visual_layout(page)
    _assert_no_horizontal_overflow(page)
    page.set_viewport_size({"width": 1440, "height": 960})

    page.get_by_test_id("nav-tools").click()
    expect(page.get_by_test_id("tool-hub-intro")).to_have_count(0)
    expect(page.get_by_test_id("tools-task-grid")).to_be_visible()
    expect(page.get_by_test_id("tools-help-trigger")).to_be_visible()
    expect(page.get_by_test_id("tool-section-materials")).to_be_visible()
    expect(page.get_by_test_id("tool-section-access")).to_be_visible()
    expect(page.get_by_test_id("tool-section-security")).to_have_count(0)
    expect(page.get_by_test_id("tool-card-pdf")).to_have_count(0)
    expect(page.get_by_test_id("tool-card-integrity")).to_have_count(0)
    expect(page.get_by_test_id("tool-card-backup")).to_be_visible()
    expect(page.get_by_test_id("tool-card-feishu")).to_be_visible()
    expect(page.get_by_test_id("tool-card-users")).to_be_visible()
    gate_card = page.get_by_test_id("tool-card-operating-gates")
    expect(gate_card).to_be_visible()
    expect(gate_card).to_contain_text("所有经营数字当前均为模拟数据")
    expect(page.get_by_test_id("operating-gates-list").locator(".admin-subsection")).to_have_count(5)
    gate_row = page.get_by_test_id("operating-gate-DEMO_B")
    expect(gate_row).to_contain_text("展示并计入合计")
    gate_row.locator("summary").click()
    gate_checkboxes = gate_row.locator('input[type="checkbox"]')
    expect(gate_checkboxes).to_have_count(2)
    expect(gate_checkboxes.nth(0)).to_be_checked()
    expect(gate_checkboxes.nth(1)).to_be_checked()
    gate_checkboxes.nth(1).uncheck()
    gate_row.get_by_label("本次调整原因").fill("端到端验证展示与合计可独立调整")
    page.get_by_test_id("operating-gate-save-DEMO_B").click()
    expect(gate_row).to_contain_text("观察态，不计入合计")
    expect(page.get_by_test_id("tool-card-operating-gates")).to_contain_text("4/5 栋计入合计")
    page.get_by_test_id("operating-gate-DEMO_B").locator("summary").click()
    gate_checkboxes = page.get_by_test_id("operating-gate-DEMO_B").locator('input[type="checkbox"]')
    gate_checkboxes.nth(1).check()
    page.get_by_test_id("operating-gate-DEMO_B").get_by_label("本次调整原因").fill("端到端验证完成，恢复模拟数据全量展示与合计")
    page.get_by_test_id("operating-gate-save-DEMO_B").click()
    expect(page.get_by_test_id("operating-gate-DEMO_B")).to_contain_text("展示并计入合计")
    expect(page.get_by_test_id("tool-card-operating-gates")).to_contain_text("5/5 栋计入合计")
    _assert_tools_visual_layout(page)
    gate_evidence_dir_value = os.environ.get("LEASE_E2E_EVIDENCE_DIR", "").strip()
    if gate_evidence_dir_value:
        gate_evidence_dir = Path(gate_evidence_dir_value)
        gate_evidence_dir.mkdir(parents=True, exist_ok=True)
        page.screenshot(
            path=str(gate_evidence_dir / "阶段3D-逐楼展示与合计管理-1440x960.png"),
            animations="disabled",
            full_page=True,
        )
    page.set_viewport_size({"width": 390, "height": 844})
    expect(page.get_by_test_id("tool-hub-intro")).to_have_count(0)
    _assert_tools_visual_layout(page)
    _assert_no_horizontal_overflow(page)
    page.set_viewport_size({"width": 1440, "height": 960})

    page.get_by_test_id("backup-create-button").click()
    expect(page.get_by_test_id("backup-list")).to_contain_text("backup_")
    _assert_element_within_viewport(page, "backup-list")
    dialogs: list[str] = []

    def record_dialog(dialog) -> None:
        dialogs.append(dialog.message)
        dialog.dismiss()

    page.on("dialog", record_dialog)
    restore_button = page.get_by_test_id("backup-list").get_by_role("button", name="恢复").first
    expect(restore_button).to_be_visible()
    restore_button.click()
    assert dialogs == []
    expect(page.get_by_test_id("backup-restore-confirm-modal")).to_be_visible()
    _assert_element_within_viewport(page, "backup-restore-confirm-modal")
    expect(page.get_by_test_id("backup-restore-confirm-modal")).to_contain_text("安全备份")
    page.get_by_test_id("backup-restore-cancel").click()
    expect(page.get_by_test_id("backup-restore-confirm-modal")).to_have_count(0)
    _assert_scroll_usable(page)

    restore_button.click()
    with page.expect_response(
        lambda response: (
            response.request.method == "POST"
            and urlparse(response.url).path.startswith("/api/backups/")
            and urlparse(response.url).path.endswith("/restore")
        )
    ):
        page.get_by_test_id("backup-restore-confirm").click()
    expect(page.get_by_test_id("backup-restore-confirm-modal")).to_have_count(0)
    expect(page.get_by_test_id("backup-list")).to_contain_text("safety_backup_")
    _assert_scroll_usable(page)
    assert dialogs == []

    response = page.request.post(
        f"{base_url}/api/auth/create-user",
        data={"username": "viewer", "password": "Viewer123", "display_name": "查看用户", "role": "viewer"},
    )
    assert response.ok
    response = page.request.post(
        f"{base_url}/api/auth/create-user",
        data={"username": "editor", "password": "Editor123", "display_name": "编辑用户", "role": "editor"},
    )
    assert response.ok
    page.get_by_role("button", name="退出").click()
    _login(page, base_url, "viewer", "Viewer123")
    expect(page.get_by_test_id("nav-receivables")).to_have_count(0)
    expect(page.get_by_test_id("nav-group-system")).to_have_count(0)
    response = page.request.get(f"{base_url}/api/audit/logs")
    assert response.status in {401, 403}

    page.get_by_test_id("nav-analytics").click()
    expect(page.get_by_test_id("analytics-rent-timeline")).to_be_visible()
    expect(page.get_by_test_id("rent-timeline-export-png")).to_be_visible()
    expect(page.get_by_test_id("rent-timeline-export-excel")).to_have_count(0)
    response = page.request.get(
        f"{base_url}/api/rent-analytics/export",
        params={"start_date": "2026-01-01", "end_date": "2026-01-31", "segments": "overall"},
    )
    assert response.status == 403

    page.get_by_test_id("nav-operations").click()
    expect(page.get_by_test_id("operations-page")).to_be_visible()
    page.get_by_test_id("operation-filter-overdue_contracts").click()
    expect(page.get_by_test_id("operation-priority-action-0")).to_be_visible()
    page.get_by_test_id("operation-priority-action-0").click()
    expect(page.get_by_test_id("contract-decision-card")).to_be_visible()
    assert "active" in (page.get_by_test_id("nav-operations").get_attribute("class") or "")
    expect(page.get_by_test_id("operation-secondary-action-0")).to_have_count(0)
    page.locator(".modal-header .icon-btn").click()
    page.get_by_test_id("nav-ai-assist").click()
    expect(page.get_by_test_id("ai-mode-risk")).to_be_visible()
    page.get_by_test_id("ai-risk-load-button").click()
    expect(page.get_by_role("button", name="查看应收").first).to_be_visible()
    page.get_by_role("button", name="查看应收").first.click()
    expect(page.get_by_test_id("contract-decision-card")).to_be_visible()
    assert "active" in (page.get_by_test_id("nav-ai-assist").get_attribute("class") or "")
    page.locator(".modal-header .icon-btn").click()

    page.get_by_role("button", name="退出").click()
    _login(page, base_url, "editor", "Editor123")
    expect(page.get_by_test_id("nav-group-system")).to_have_count(0)
    response = page.request.get(f"{base_url}/api/audit/logs")
    assert response.status in {401, 403}
    page.get_by_test_id("nav-analytics").click()
    expect(page.get_by_test_id("rent-timeline-export-png")).to_be_visible()
    expect(page.get_by_test_id("rent-timeline-export-excel")).to_be_visible()


def test_portfolio_presentation_layout_and_drilldowns(fresh_browser_page) -> None:
    page, base_url = fresh_browser_page
    portfolio_code = "DEMO_OFFICE_PORTFOLIO"
    building_catalog = [
        {"code": "DEMO_B", "name": "示例主裙楼中心"},
        {"code": "DEMO_D", "name": "示例办公中心"},
        {"code": "DEMO_E", "name": "示例商务中心"},
        {"code": "DEMO_C", "name": "示例单栋中心"},
        {"code": "DEMO_A", "name": "示例双塔中心"},
    ]
    _login(page, base_url)

    me_response = page.request.get(f"{base_url}/api/auth/me")
    assert me_response.status == 200
    me_scope = me_response.json()["scope"]
    assert [
        {"code": item["code"], "name": item["name"]}
        for item in me_scope["items"]
    ] == building_catalog
    assert me_scope["default"]["code"] == "DEMO_A"
    assert me_scope["portfolio"]["items"][0]["building_codes"] == [
        item["code"] for item in building_catalog
    ]
    profiles_by_code = {
        item["code"]: item["operations_profile"]
        for item in me_scope["items"]
    }
    assert profiles_by_code["DEMO_A"]["data_adapter"] == "live_detail"
    assert "contracts" in profiles_by_code["DEMO_A"]["available_tabs"]
    for building_code in ("DEMO_B", "DEMO_C", "DEMO_D", "DEMO_E"):
        assert profiles_by_code[building_code]["data_adapter"] == "published_snapshot"
        assert profiles_by_code[building_code]["available_tabs"] == [
            "dashboard",
            "evidence",
            "exchange",
        ]

    scope_selector = page.get_by_test_id("property-scope-selector")
    expect(scope_selector).to_contain_text("板块 · 示例资产组合")
    with page.expect_response(
        lambda response: urlparse(response.url).path == "/api/operations/snapshot"
        and f"portfolio={portfolio_code}" in urlparse(response.url).query
        and response.status == 200
    ) as snapshot_response_info:
        scope_selector.select_option(f"portfolio:{portfolio_code}")
    snapshot = snapshot_response_info.value.json()
    assert snapshot["data_classification"] == "simulated"
    assert snapshot["scope"]["building_codes"] == [item["code"] for item in building_catalog]
    contributions = {item["building"]["code"]: item for item in snapshot["contributions"]}
    assert set(contributions) == {item["code"] for item in building_catalog}
    assert contributions["DEMO_A"]["publication_status"] == "live_adapter"
    for building_code in ("DEMO_B", "DEMO_C", "DEMO_D", "DEMO_E"):
        assert contributions[building_code]["publication_status"] == "published"
        assert contributions[building_code]["metrics"]
    assert {item["operating_gate"]["state"] for item in contributions.values()} == {"open"}
    assert all(item["operating_gate"]["presentation_enabled"] for item in contributions.values())
    assert all(item["operating_gate"]["contribution_enabled"] for item in contributions.values())
    assert snapshot["coverage"] == {
        "portfolio_buildings": 5,
        "authorized_buildings": 5,
        "presentation_enabled_buildings": 5,
        "contribution_enabled_buildings": 5,
        "published_same_day_buildings": 4,
        "live_adapter_buildings": 1,
        "included_same_day_buildings": 5,
        "same_day_snapshot_buildings": 5,
    }
    occupancy_coverage = snapshot["metrics"]["occupancy.area_rate"]["coverage"]
    assert occupancy_coverage["eligible_buildings"] == 5
    assert occupancy_coverage["included_buildings"] == 5
    assert occupancy_coverage["freshness_status"] == "unknown"
    assert occupancy_coverage["publication_status"] == "published_with_live_adapter"

    portfolio_url = page.url
    page.set_viewport_size({"width": 1440, "height": 960})
    page.get_by_test_id("topbar-presentation-toggle").click()
    presentation = page.get_by_test_id("unified-cockpit")
    expect(presentation).to_be_visible()
    cockpit_scope = presentation.get_by_test_id("cockpit-building-select")
    expect(cockpit_scope).to_have_value("")
    for metric_code in (
        "area.leased_sqm",
        "occupancy.area_rate",
        "rent.effective_daily",
        "billing.receivable_month",
        "collection.rate_month",
        "arrears.balance",
        "expiry.area_pressure_365d",
        "risk.open_items",
    ):
        expect(presentation.get_by_test_id(f"cockpit-metric-{metric_code}")).to_be_visible()
    for topic in ("overview", "rent", "operations", "space", "decision"):
        expect(presentation.get_by_test_id(f"cockpit-topic-{topic}")).to_be_visible()
    _assert_no_horizontal_overflow(page)
    page.keyboard.press("Escape")
    expect(presentation).to_have_count(0)
    expect(page).to_have_url(portfolio_url)

    page.set_viewport_size({"width": 1920, "height": 1080})
    page.get_by_test_id("topbar-presentation-toggle").click()
    expect(presentation).to_be_visible()
    expect(cockpit_scope).to_have_value("")
    expect(presentation).to_contain_text("示例资产组合")
    for building in building_catalog:
        card = presentation.get_by_test_id(f"cockpit-building-{building['code']}")
        expect(card).to_be_visible()
        expect(card).to_be_enabled()
        expect(card).to_contain_text(building["name"])
        expect(card.locator(".uc-building-metrics b")).to_have_count(3)
    _assert_chart_canvas_nonblank(
        presentation.get_by_role("img", name="楼宇经营指标比较，点击柱形选择楼宇", exact=True)
    )
    _assert_chart_canvas_nonblank(presentation.get_by_role("img", name="在租与待租面积", exact=True))
    presentation_overflow = presentation.evaluate(
        """(element) => ({
            horizontal: element.scrollWidth - element.clientWidth,
            vertical: element.scrollHeight - element.clientHeight,
        })"""
    )
    assert presentation_overflow["horizontal"] <= 2
    assert presentation_overflow["vertical"] <= 2
    _assert_no_internal_clipping(presentation, ".uc-building, .uc-chart-panel")
    _assert_no_horizontal_overflow(page)
    evidence_dir_value = os.environ.get("LEASE_E2E_EVIDENCE_DIR", "").strip()
    evidence_dir = Path(evidence_dir_value) if evidence_dir_value else None
    if evidence_dir is not None:
        evidence_dir.mkdir(parents=True, exist_ok=True)
        presentation.screenshot(
            path=str(evidence_dir / "驾驶舱-五栋楼经营总览-1920x1080.png"),
            animations="disabled",
        )

    with page.expect_response(
        lambda response: urlparse(response.url).path == "/api/operations/snapshot"
        and "building=DEMO_A" in urlparse(response.url).query
        and response.status == 200
    ):
        presentation.get_by_test_id("cockpit-building-DEMO_A").click()
    expect(cockpit_scope).to_have_value("DEMO_A")
    expect(presentation.locator(".uc-context")).to_contain_text("示例双塔中心")
    expect(page).to_have_url(portfolio_url)
    _assert_chart_canvas_nonblank(presentation.get_by_role("img", name="在租与待租面积", exact=True))
    presentation.get_by_test_id("cockpit-topic-rent").click()
    expect(presentation.get_by_test_id("analytics-rent-timeline")).to_be_visible()
    expect(presentation.get_by_test_id("cockpit-topic-rent")).to_have_attribute("aria-pressed", "true")
    expect(page).to_have_url(portfolio_url)
    if evidence_dir is not None:
        presentation.screenshot(
            path=str(evidence_dir / "驾驶舱-DEMO_A租金联动-1920x1080.png"),
            animations="disabled",
        )

    cockpit_scope.select_option("DEMO_B")
    expect(cockpit_scope).to_have_value("DEMO_B")
    expect(presentation.get_by_test_id("analytics-rent-timeline")).to_have_count(0)
    expect(presentation).to_contain_text("当前楼宇按已发布的整楼指标展示")
    presentation.get_by_test_id("cockpit-topic-operations").click()
    presentation.locator(".uc-kpis button").filter(has_text="面积出租率").click()
    expect(page.get_by_test_id("cockpit-detail")).to_be_visible()
    expect(page.get_by_test_id("cockpit-detail")).to_contain_text("示例主裙楼中心")
    expect(page.get_by_test_id("cockpit-detail")).to_contain_text("95 %")
    expect(presentation).to_be_visible()
    expect(page).to_have_url(portfolio_url)
    page.get_by_test_id("base-modal-close").click()
    expect(page.get_by_test_id("cockpit-detail")).to_have_count(0)
    presentation.get_by_test_id("cockpit-topic-space").click()
    expect(presentation).to_contain_text("当前楼宇按整楼经营指标展示")
    expect(presentation.locator(".uc-room")).to_have_count(0)
    presentation.get_by_test_id("cockpit-back-overview").click()
    expect(cockpit_scope).to_have_value("")
    expect(presentation.get_by_test_id("cockpit-topic-overview")).to_have_attribute("aria-pressed", "true")
    presentation.get_by_test_id("cockpit-exit").click()
    expect(presentation).to_have_count(0)
    expect(page).to_have_url(portfolio_url)
    expect(scope_selector).to_have_value(f"portfolio:{portfolio_code}")

    # Evidence remains a normal business-page action after leaving the cockpit.
    # Opening a project or metric above must not navigate out of the full screen.
    with page.expect_response(
        lambda response: urlparse(response.url).path == "/api/operations/snapshot"
        and "building=DEMO_B" in urlparse(response.url).query
        and response.status == 200
    ):
        scope_selector.select_option("building:DEMO_B")
    with page.expect_response(
        lambda response: urlparse(response.url).path == "/api/operations/snapshot-evidence"
        and "building=DEMO_B" in urlparse(response.url).query
        and response.status == 200
    ):
        page.get_by_test_id("nav-evidence").click()
    expect(page).to_have_url(re.compile(r"/evidence\?.*building=DEMO_B"))
    expect(page.get_by_test_id("evidence-page")).to_be_visible()
    published_evidence = page.get_by_test_id("evidence-building-DEMO_B")
    expect(page.get_by_test_id("evidence-simulated-badge")).to_be_visible()
    expect(published_evidence).to_contain_text("单楼指标可用")
    expect(published_evidence).to_contain_text("展示并计入合计")
    expect(published_evidence).to_contain_text("DEMO-SIMULATED")
    assert "缺少同日数据" not in published_evidence.inner_text()
    page.set_viewport_size({"width": 390, "height": 844})
    _assert_no_horizontal_overflow(page)

    page.set_viewport_size({"width": 1920, "height": 1080})
    with page.expect_response(
        lambda response: urlparse(response.url).path == "/api/operations/snapshot"
        and f"portfolio={portfolio_code}" in urlparse(response.url).query
        and response.status == 200
    ):
        scope_selector.select_option(f"portfolio:{portfolio_code}")
    expect(page).to_have_url(re.compile(r"/evidence\?.*portfolio=" + portfolio_code))
    expect(page.get_by_test_id("evidence-summary")).to_be_visible()
    expect(page.get_by_test_id("evidence-page")).to_contain_text("原始分子")
    if evidence_dir is not None:
        page.screenshot(
            path=str(evidence_dir / "阶段3D-板块模拟数据依据-1920x1080.png"),
            animations="disabled",
        )
        page.set_viewport_size({"width": 390, "height": 844})
        _assert_no_horizontal_overflow(page)
        page.screenshot(
            path=str(evidence_dir / "阶段3D-板块模拟数据依据-390x844.png"),
            animations="disabled",
        )
        page.set_viewport_size({"width": 1920, "height": 1080})

    page.get_by_test_id("topbar-presentation-toggle").click()
    expect(cockpit_scope).to_have_value("")
    cockpit_scope.select_option("DEMO_B")
    expect(cockpit_scope).to_have_value("DEMO_B")
    presentation.get_by_test_id("cockpit-topic-rent").click()
    expect(presentation).to_contain_text("当前楼宇按已发布的整楼指标展示")
    expect(presentation.get_by_test_id("analytics-rent-timeline")).to_have_count(0)
    page.keyboard.press("Escape")
    expect(presentation).to_have_count(0)
    expect(page).to_have_url(re.compile(r"/evidence\?.*portfolio=" + portfolio_code))

    detail_requests: list[str] = []

    def record_snapshot_building_request(request) -> None:
        parsed = urlparse(request.url)
        detail_prefixes = (
            "/api/contracts",
            "/api/customers",
            "/api/rooms",
            "/api/attachments",
        )
        if parsed.path.startswith(detail_prefixes) and "building=DEMO_B" in parsed.query:
            detail_requests.append(parsed.path)

    page.on("request", record_snapshot_building_request)
    with page.expect_response(
        lambda response: urlparse(response.url).path == "/api/operations/snapshot"
        and "building=DEMO_B" in urlparse(response.url).query
        and response.status == 200
    ):
        page.goto(f"{base_url}/contracts?building=DEMO_B")
    expect(page.get_by_test_id("snapshot-dashboard-summary")).to_be_visible()
    expect(page).to_have_url(re.compile(r"/\?building=DEMO_B(?:&|$)"))
    expect(page.get_by_test_id("sidebar-brand-monogram")).to_contain_text("示例主裙楼中心")
    expect(page.locator(".sidebar-brand-logo img")).to_have_count(0)
    expect(page.get_by_test_id("nav-contracts")).to_have_count(0)
    expect(page.get_by_test_id("nav-customers")).to_have_count(0)
    expect(page.get_by_test_id("nav-rooms")).to_have_count(0)
    expect(page.get_by_test_id("nav-receivables")).to_have_count(0)
    expect(page.get_by_test_id("snapshot-dashboard-summary")).to_contain_text(
        "不把未知数据当作 0"
    )
    gjjt_profile = page.get_by_test_id("building-operations-profile")
    expect(gjjt_profile).to_have_attribute("data-structure", "main_building_with_podium")
    expect(gjjt_profile).to_contain_text("主楼与裙楼")
    expect(gjjt_profile).to_contain_text("主楼 / 裙楼")
    expect(page.get_by_test_id("building-profile-group-WHOLE_BUILDING")).to_contain_text(
        "口径已启用"
    )
    market_group = page.get_by_test_id("building-profile-group-MARKET_LEASING")
    expect(market_group).to_contain_text("市场化出租")
    expect(market_group).to_contain_text("--")
    expect(market_group).to_contain_text("分区指标尚不可用")
    expect(page.get_by_test_id("building-profile-safety")).to_contain_text(
        "暂无分区数据"
    )
    expect(page.get_by_test_id("snapshot-dashboard-simulated-data")).to_be_visible()
    expect(page.get_by_test_id("snapshot-dashboard-metrics")).to_contain_text("95%")
    expect(page.get_by_test_id("snapshot-dashboard-metrics")).to_contain_text("3 份")
    assert detail_requests == []
    if evidence_dir is not None:
        page.wait_for_timeout(800)
        page.screenshot(
            path=str(evidence_dir / "Phase2-快照楼安全首页-1920x1080.png"),
            animations="disabled",
            full_page=True,
        )
        page.screenshot(
            path=str(evidence_dir / "Phase3-集团大厦双口径首页-1920x1080.png"),
            animations="disabled",
            full_page=True,
        )
        page.set_viewport_size({"width": 390, "height": 844})
        _assert_no_horizontal_overflow(page)
        page.screenshot(
            path=str(evidence_dir / "Phase2-快照楼安全首页-390x844.png"),
            animations="disabled",
            full_page=True,
        )
        page.screenshot(
            path=str(evidence_dir / "Phase3-集团大厦双口径首页-390x844.png"),
            animations="disabled",
            full_page=True,
        )
        page.set_viewport_size({"width": 1920, "height": 1080})

    with page.expect_response(
        lambda response: urlparse(response.url).path == "/api/operations/snapshot"
        and "building=DEMO_C" in urlparse(response.url).query
        and response.status == 200
    ) as xshgj_snapshot_response_info:
        scope_selector.select_option("building:DEMO_C")
    xshgj_snapshot = xshgj_snapshot_response_info.value.json()
    assert xshgj_snapshot["scope"]["scope_type"] == "building"
    assert xshgj_snapshot["scope"]["scope_code"] == "DEMO_C"
    assert xshgj_snapshot["scope"]["building_codes"] == ["DEMO_C"]
    assert xshgj_snapshot["metrics"]["occupancy.area_rate"]["value"] == 95
    assert xshgj_snapshot["metrics"]["area.rentable_sqm"]["value"] == 48_000
    assert xshgj_snapshot["metrics"]["area.leased_sqm"]["value"] == 45_600
    xshgj_profile = page.get_by_test_id("building-operations-profile")
    expect(xshgj_profile).to_have_attribute("data-structure", "single_building")
    expect(xshgj_profile).to_contain_text("标准单栋楼")
    expect(xshgj_profile).to_contain_text("不虚构楼层或房号")
    expect(xshgj_profile.locator(".building-profile-groups article")).to_have_count(1)
    expect(xshgj_profile).not_to_contain_text("主楼")
    expect(xshgj_profile).not_to_contain_text("裙楼")
    xshgj_metrics = page.get_by_test_id("snapshot-dashboard-metrics")
    expect(xshgj_metrics).to_contain_text("95%")
    expect(xshgj_metrics).to_contain_text("48,000.00")
    expect(xshgj_metrics).to_contain_text("45,600.00")
    _assert_no_horizontal_overflow(page)
    if evidence_dir is not None:
        page.screenshot(
            path=str(evidence_dir / "Phase3-新上海标准单栋首页-1920x1080.png"),
            animations="disabled",
            full_page=True,
        )

    for stable_building_code in ("DEMO_D", "DEMO_E"):
        with page.expect_response(
            lambda response, code=stable_building_code: urlparse(response.url).path
            == "/api/operations/snapshot"
            and f"building={code}" in urlparse(response.url).query
            and response.status == 200
        ):
            scope_selector.select_option(f"building:{stable_building_code}")
        stable_profile = page.get_by_test_id("building-operations-profile")
        expect(stable_profile).to_have_attribute("data-structure", "whole_building")
        expect(page.get_by_test_id("snapshot-dashboard-summary")).to_contain_text(
            "整楼快照运营"
        )
        expect(stable_profile).to_contain_text("到期续租")
        expect(stable_profile).to_contain_text("收缴")
        expect(stable_profile).to_contain_text("资料完整性")
        expect(stable_profile).not_to_contain_text("永久满租")
        expect(stable_profile.locator(".building-profile-groups article")).to_have_count(1)
        _assert_no_horizontal_overflow(page)

    with page.expect_response(
        lambda response: urlparse(response.url).path == "/api/operations/snapshot"
        and "building=DEMO_B" in urlparse(response.url).query
        and response.status == 200
    ):
        scope_selector.select_option("building:DEMO_B")

    with page.expect_response(
        lambda response: urlparse(response.url).path == "/api/operations/snapshot"
        and "building=DEMO_A" in urlparse(response.url).query
        and response.status == 200
    ):
        scope_selector.select_option("building:DEMO_A")
    detail_page_cases = (
        ("nav-contracts", "contracts-workspace-summary"),
        ("nav-customers", "customers-page"),
        ("nav-rooms", "rooms-page"),
        ("nav-receivables", "receivables-page"),
    )
    for nav_test_id, page_test_id in detail_page_cases:
        page.get_by_test_id(nav_test_id).click()
        expect(page.get_by_test_id(page_test_id)).to_be_visible()
        detail_requests.clear()
        with page.expect_response(
            lambda response: urlparse(response.url).path == "/api/operations/snapshot"
            and "building=DEMO_B" in urlparse(response.url).query
            and response.status == 200
        ):
            scope_selector.select_option("building:DEMO_B")
        expect(page.get_by_test_id("snapshot-dashboard-summary")).to_be_visible()
        page.wait_for_timeout(250)
        assert detail_requests == [], (
            f"{nav_test_id} leaked snapshot-building detail requests: {detail_requests}"
        )
        with page.expect_response(
            lambda response: urlparse(response.url).path == "/api/operations/snapshot"
            and "building=DEMO_A" in urlparse(response.url).query
            and response.status == 200
        ):
            scope_selector.select_option("building:DEMO_A")

    with page.expect_response(
        lambda response: urlparse(response.url).path == "/api/operations/snapshot"
        and "building=DEMO_B" in urlparse(response.url).query
        and response.status == 200
    ):
        scope_selector.select_option("building:DEMO_B")

    gates_response = page.request.get(f"{base_url}/api/buildings/operating-gates")
    assert gates_response.ok
    target_gate = next(
        item for item in gates_response.json()["items"] if item["building_code"] == "DEMO_B"
    )
    hidden_response = page.request.put(
        f"{base_url}/api/buildings/DEMO_B/operating-gate",
        data={
            "presentation_enabled": False,
            "contribution_enabled": True,
            "reason": "端到端验证单楼演示入口服从服务端展示开关",
            "expected_revision": target_gate["revision"],
        },
    )
    assert hidden_response.ok
    hidden_revision = hidden_response.json()["decision"]["revision"]
    with page.expect_response(
        lambda response: urlparse(response.url).path == "/api/auth/me" and response.status == 200
    ):
        page.get_by_test_id("topbar-presentation-toggle").click()
    expect(presentation).to_have_count(0)
    expect(page.get_by_text("该楼宇当前未开放演示大屏，业务页面仍可正常查看")).to_be_visible()

    restored_response = page.request.put(
        f"{base_url}/api/buildings/DEMO_B/operating-gate",
        data={
            "presentation_enabled": True,
            "contribution_enabled": True,
            "reason": "端到端验证完成，恢复模拟楼宇演示大屏",
            "expected_revision": hidden_revision,
        },
    )
    assert restored_response.ok
    page.get_by_test_id("topbar-presentation-toggle").click()
    expect(presentation).to_be_visible()
    expect(presentation.get_by_test_id("cockpit-building-select")).to_have_value("DEMO_B")
    expect(presentation.locator(".uc-context")).to_contain_text("示例主裙楼中心")
    expect(presentation.get_by_test_id("cockpit-metric-occupancy.area_rate")).to_contain_text("95")
    _assert_chart_canvas_nonblank(presentation.get_by_role("img", name="在租与待租面积", exact=True))
    presentation.get_by_test_id("cockpit-topic-rent").click()
    expect(presentation).to_contain_text("当前楼宇按已发布的整楼指标展示")
    expect(presentation.get_by_test_id("analytics-rent-timeline")).to_have_count(0)
    presentation.get_by_test_id("cockpit-topic-space").click()
    expect(presentation).to_contain_text("当前楼宇按整楼经营指标展示")
    expect(presentation.locator(".uc-room")).to_have_count(0)
    presentation.get_by_test_id("cockpit-topic-operations").click()
    expect(presentation.get_by_test_id("analytics-page")).to_have_count(0)
    expect(presentation).to_contain_text("经营指标")
    presentation.get_by_test_id("cockpit-topic-overview").click()
    assert detail_requests == []
    _assert_no_internal_clipping(presentation, ".uc-chart-panel, .uc-building-panel")
    _assert_no_horizontal_overflow(page)
    if evidence_dir is not None:
        page.wait_for_timeout(800)
        presentation.screenshot(
            path=str(evidence_dir / "Phase2-快照楼整楼大屏-1920x1080.png"),
            animations="disabled",
        )
        presentation.screenshot(
            path=str(evidence_dir / "Phase3-集团大厦双口径大屏-1920x1080.png"),
            animations="disabled",
        )
    page.keyboard.press("Escape")
    expect(presentation).to_have_count(0)

    with page.expect_response(
        lambda response: urlparse(response.url).path == "/api/operations/snapshot"
        and "building=DEMO_A" in urlparse(response.url).query
        and response.status == 200
    ):
        scope_selector.select_option("building:DEMO_A")
    expect(page.get_by_test_id("nav-contracts")).to_be_visible()
    expect(page.get_by_test_id("nav-customers")).to_be_visible()
    expect(page.get_by_test_id("nav-rooms")).to_be_visible()
    expect(page.get_by_test_id("nav-receivables")).to_be_visible()
    expect(page.locator(".sidebar-brand-logo img")).to_be_visible()
    expect(page.get_by_test_id("sidebar-brand-monogram")).to_have_count(0)


def test_single_building_operator_workspace_boundary(fresh_browser_page) -> None:
    page, base_url = fresh_browser_page
    evidence_dir_value = os.environ.get("LEASE_E2E_EVIDENCE_DIR", "").strip()
    evidence_dir = Path(evidence_dir_value) if evidence_dir_value else None
    api_requests: list[str] = []

    def record_api_request(request) -> None:
        parsed = urlparse(request.url)
        if parsed.path.startswith("/api/"):
            api_requests.append(f"{request.method} {parsed.path}?{parsed.query}")

    page.on("request", record_api_request)
    page.goto(base_url)
    page.get_by_test_id("login-username").fill(E2E_SINGLE_OPERATOR_USERNAME)
    page.get_by_test_id("login-password").fill(E2E_SINGLE_OPERATOR_PASSWORD)
    page.get_by_test_id("login-submit").click()

    expect(page.get_by_test_id("nav-dashboard")).to_be_visible()
    expect(page.get_by_test_id("nav-overview")).to_have_count(0)
    expect(page.get_by_test_id("workspace-switch-leadership")).to_have_count(0)
    expect(page.get_by_test_id("workspace-switch-operations")).to_have_count(0)
    expect(page.get_by_test_id("current-workspace")).to_contain_text("楼宇运营端")
    expect(page.get_by_test_id("current-scope")).to_contain_text("示例双塔中心")
    expect(page.get_by_test_id("property-scope-selector")).to_have_count(0)
    expect(page.get_by_test_id("topbar-presentation-toggle")).to_have_accessible_name(
        "进入示例双塔中心大屏"
    )
    expect(page).to_have_url(re.compile(r"/\?building=DEMO_A(?:&|$)"))
    assert not any(
        urlparse(request.split(" ", 1)[1]).path
        == "/api/operations/portfolio-overview"
        for request in api_requests
    )
    if evidence_dir is not None:
        evidence_dir.mkdir(parents=True, exist_ok=True)
        page.screenshot(
            path=str(evidence_dir / "Phase1A-single-building-operator-1440x960.png"),
            animations="disabled",
            full_page=True,
        )

    page.get_by_test_id("nav-contracts").click()
    expect(page.get_by_test_id("contracts-workspace-summary")).to_be_visible()
    expect(page).to_have_url(re.compile(r"/contracts\?building=DEMO_A(?:&|$)"))
    page.set_viewport_size({"width": 390, "height": 844})
    _assert_no_horizontal_overflow(page)
    if evidence_dir is not None:
        page.screenshot(
            path=str(evidence_dir / "Phase1A-single-building-operator-390x844.png"),
            animations="disabled",
            full_page=True,
        )


def test_multi_building_operator_switches_scope_without_detail_leaks(
    fresh_browser_page,
) -> None:
    page, base_url = fresh_browser_page
    evidence_dir_value = os.environ.get("LEASE_E2E_EVIDENCE_DIR", "").strip()
    evidence_dir = Path(evidence_dir_value) if evidence_dir_value else None
    snapshot_detail_requests: list[str] = []

    def record_snapshot_detail_request(request) -> None:
        parsed = urlparse(request.url)
        if "building=DEMO_B" not in parsed.query:
            return
        if parsed.path.startswith(
            (
                "/api/contracts",
                "/api/customers",
                "/api/rooms",
                "/api/attachments",
                "/api/risks",
                "/api/rent-analytics",
            )
        ):
            snapshot_detail_requests.append(f"{request.method} {parsed.path}?{parsed.query}")

    page.on("request", record_snapshot_detail_request)
    page.goto(base_url)
    _submit_login_form(page, E2E_MULTI_OPERATOR_USERNAME, E2E_MULTI_OPERATOR_PASSWORD)

    expect(page.get_by_test_id("current-workspace")).to_contain_text("楼宇运营端")
    expect(page.get_by_test_id("workspace-switch-leadership")).to_have_count(0)
    expect(page.get_by_test_id("workspace-switch-operations")).to_have_count(0)
    expect(page.get_by_test_id("nav-tools")).to_have_count(0)
    scope_selector = page.get_by_test_id("property-scope-selector")
    expect(scope_selector).to_be_visible()
    assert set(scope_selector.locator("option").evaluate_all("nodes => nodes.map(node => node.value)")) == {
        "building:DEMO_A",
        "building:DEMO_B",
    }
    expect(page.get_by_test_id("nav-contracts")).to_be_visible()

    with page.expect_response(
        lambda response: urlparse(response.url).path == "/api/operations/snapshot"
        and "building=DEMO_B" in urlparse(response.url).query
        and response.status == 200
    ):
        scope_selector.select_option("building:DEMO_B")
    expect(page.get_by_test_id("snapshot-dashboard-summary")).to_be_visible()
    expect(page.get_by_test_id("current-scope")).to_contain_text("示例主裙楼中心")
    expect(page).to_have_url(re.compile(r"[?&]building=DEMO_B(?:&|$)"))
    for hidden_nav in (
        "nav-contracts",
        "nav-customers",
        "nav-rooms",
        "nav-receivables",
        "nav-risks",
        "nav-ai-assist",
        "nav-analytics",
    ):
        expect(page.get_by_test_id(hidden_nav)).to_have_count(0)

    page.set_viewport_size({"width": 1920, "height": 1080})
    presentation_button = page.get_by_test_id("topbar-presentation-toggle")
    expect(presentation_button).to_have_accessible_name("进入示例主裙楼中心大屏")
    presentation_button.click()
    fullscreen = page.get_by_test_id("unified-cockpit")
    expect(fullscreen).to_be_visible()
    expect(fullscreen.get_by_test_id("cockpit-building-select")).to_have_value("DEMO_B")
    expect(fullscreen.locator(".uc-context")).to_contain_text("示例主裙楼中心")
    fullscreen.get_by_test_id("cockpit-topic-rent").click()
    expect(fullscreen).to_contain_text("当前楼宇按已发布的整楼指标展示")
    expect(fullscreen.get_by_test_id("analytics-rent-timeline")).to_have_count(0)
    fullscreen.get_by_test_id("cockpit-topic-space").click()
    expect(fullscreen.locator(".uc-room")).to_have_count(0)
    fullscreen.get_by_test_id("cockpit-topic-overview").click()
    expect(page).to_have_url(re.compile(r"[?&]building=DEMO_B(?:&|$)"))
    fullscreen_overflow = fullscreen.evaluate(
        """element => ({
            horizontal: element.scrollWidth - element.clientWidth,
            vertical: element.scrollHeight - element.clientHeight,
        })"""
    )
    assert fullscreen_overflow["horizontal"] <= 2
    assert fullscreen_overflow["vertical"] <= 2
    _assert_no_horizontal_overflow(page)
    if evidence_dir is not None:
        evidence_dir.mkdir(parents=True, exist_ok=True)
        page.screenshot(
            path=str(evidence_dir / "Phase5-多楼运营快照楼大屏-1920x1080.png"),
            animations="disabled",
        )
    page.keyboard.press("Escape")
    expect(fullscreen).to_have_count(0)
    expect(page.get_by_test_id("snapshot-dashboard-summary")).to_be_visible()
    expect(scope_selector).to_have_value("building:DEMO_B")
    expect(page).to_have_url(re.compile(r"[?&]building=DEMO_B(?:&|$)"))

    page.set_viewport_size({"width": 390, "height": 844})
    _assert_no_horizontal_overflow(page)
    expect(page.locator(".toast-stack .toast")).to_have_count(0, timeout=7000)
    if evidence_dir is not None:
        page.screenshot(
            path=str(evidence_dir / "Phase5-多楼运营快照楼退出大屏-390x844.png"),
            animations="disabled",
            full_page=True,
        )
    page.set_viewport_size({"width": 1440, "height": 960})

    with page.expect_response(
        lambda response: urlparse(response.url).path == "/api/operations/snapshot"
        and "building=DEMO_A" in urlparse(response.url).query
        and response.status == 200
    ):
        scope_selector.select_option("building:DEMO_A")
    expect(page.get_by_test_id("nav-contracts")).to_be_visible()
    expect(page.get_by_test_id("nav-customers")).to_be_visible()
    expect(page.get_by_test_id("nav-rooms")).to_be_visible()
    expect(page.get_by_test_id("nav-receivables")).to_be_visible()
    expect(page.get_by_test_id("current-scope")).to_contain_text("示例双塔中心")
    assert snapshot_detail_requests == []

    unauthorized = page.request.get(
        f"{base_url}/api/contracts?building=DEMO_C&page=1&page_size=25"
    )
    assert unauthorized.status in {403, 404}, unauthorized.text()


def test_hybrid_account_explicit_workspace_switch_and_page_memory(
    fresh_browser_page,
) -> None:
    page, base_url = fresh_browser_page
    evidence_dir_value = os.environ.get("LEASE_E2E_EVIDENCE_DIR", "").strip()
    evidence_dir = Path(evidence_dir_value) if evidence_dir_value else None
    api_requests: list[str] = []

    def record_api_request(request) -> None:
        parsed = urlparse(request.url)
        if parsed.path.startswith("/api/"):
            api_requests.append(f"{request.method} {parsed.path}?{parsed.query}")

    page.on("request", record_api_request)
    page.goto(base_url)
    page.get_by_test_id("login-username").fill(E2E_HYBRID_USERNAME)
    page.get_by_test_id("login-password").fill(E2E_HYBRID_PASSWORD)
    with page.expect_response(
        lambda response: (
            urlparse(response.url).path == "/api/operations/portfolio-overview"
            and response.status == 200
        )
    ) as overview_response_info:
        page.get_by_test_id("login-submit").click()

    overview_payload = overview_response_info.value.json()
    assert overview_payload["coverage"]["authorized_buildings"] == 5
    assert overview_payload["metrics"]["portfolio.building_count"]["value"] == 5
    expect(page.get_by_test_id("leadership-overview-page")).to_be_visible()
    leadership_switch = page.get_by_test_id("workspace-switch-leadership")
    operations_switch = page.get_by_test_id("workspace-switch-operations")
    expect(leadership_switch).to_have_attribute("aria-pressed", "true")
    expect(operations_switch).to_have_attribute("aria-pressed", "false")
    expect(page.get_by_test_id("nav-overview")).to_be_visible()
    expect(page.get_by_test_id("nav-contracts")).to_have_count(0)
    expect(page.get_by_test_id("current-scope")).to_contain_text("示例资产组合")
    expect(page).to_have_url(re.compile(r"/overview\?overview_portfolio="))
    expect(
        page.get_by_test_id("presentation-kpi-portfolio.building_count").locator(
            ".animated-number"
        )
    ).to_have_text("5 栋")
    expect(page.get_by_test_id("topbar-presentation-toggle")).to_have_accessible_name(
        "进入五栋楼大屏"
    )

    leadership_request_end = len(api_requests)
    forbidden_business_prefixes = (
        "/api/contracts",
        "/api/customers",
        "/api/rooms",
        "/api/dashboard",
        "/api/attachments",
        "/api/risks",
        "/api/operations/snapshot",
    )
    assert not any(
        urlparse(request.split(" ", 1)[1]).path.startswith(forbidden_business_prefixes)
        for request in api_requests[:leadership_request_end]
    )
    if evidence_dir is not None:
        evidence_dir.mkdir(parents=True, exist_ok=True)
        # The KPI values use a 700 ms requestAnimationFrame transition. Playwright's
        # screenshot animation option does not stop that JavaScript animation, so
        # wait for the whole strip to reach its final values before capturing evidence.
        page.wait_for_timeout(800)
        page.screenshot(
            path=str(evidence_dir / "Phase1A-hybrid-leadership-1440x960.png"),
            animations="disabled",
            full_page=True,
        )

    theme_root = page.locator("html")
    original_theme = theme_root.get_attribute("data-theme")
    page.get_by_test_id("topbar-presentation-toggle").click()
    hybrid_presentation = page.get_by_test_id("unified-cockpit")
    expect(hybrid_presentation).to_be_visible()
    page.keyboard.press("Escape")
    expect(hybrid_presentation).to_have_count(0)
    page.get_by_test_id("topbar-theme-toggle").click()
    toggled_theme = "light" if original_theme == "dark" else "dark"
    expect(theme_root).to_have_attribute("data-theme", toggled_theme)

    operations_switch.click()
    expect(theme_root).to_have_attribute("data-theme", toggled_theme)
    page.get_by_test_id("topbar-theme-toggle").click()
    expect(theme_root).to_have_attribute("data-theme", original_theme)
    expect(page.get_by_test_id("nav-dashboard")).to_be_visible()
    expect(page.get_by_test_id("nav-overview")).to_have_count(0)
    expect(operations_switch).to_have_attribute("aria-pressed", "true")
    expect(page.get_by_test_id("current-scope")).to_contain_text("示例双塔中心")
    expect(page.get_by_test_id("property-scope-selector")).to_have_count(0)
    expect(page.get_by_test_id("topbar-presentation-toggle")).to_have_accessible_name(
        "进入示例双塔中心大屏"
    )
    expect(page).to_have_url(re.compile(r"/\?building=DEMO_A(?:&|$)"))

    operations_request_end = len(api_requests)
    operation_scope_requests = [
        request
        for request in api_requests[leadership_request_end:operations_request_end]
        if urlparse(request.split(" ", 1)[1]).path.startswith(
            ("/api/dashboard", "/api/operations/snapshot")
        )
    ]
    assert operation_scope_requests
    assert all("building=DEMO_A" in request for request in operation_scope_requests)

    page.get_by_test_id("nav-contracts").click()
    expect(page.get_by_test_id("contracts-workspace-summary")).to_be_visible()
    expect(page).to_have_url(re.compile(r"/contracts\?building=DEMO_A(?:&|$)"))

    leadership_switch.click()
    expect(page.get_by_test_id("leadership-overview-page")).to_be_visible()
    expect(page.get_by_test_id("nav-contracts")).to_have_count(0)
    expect(leadership_switch).to_have_attribute("aria-pressed", "true")
    expect(page).to_have_url(re.compile(r"/overview\?overview_portfolio="))
    expect(page).not_to_have_url(re.compile(r"[?&](?:building|portfolio)="))

    request_count_before_drilldown = len(api_requests)
    page.get_by_test_id("portfolio-building-DEMO_A").locator(
        ".portfolio-building-name"
    ).click()
    expect(page).to_have_url(re.compile(r"/overview\?.*overview_building=DEMO_A(?:&|$)"))
    expect(leadership_switch).to_have_attribute("aria-pressed", "true")
    page.wait_for_timeout(200)
    assert len(api_requests) == request_count_before_drilldown

    operations_switch.click()
    expect(page.get_by_test_id("contracts-workspace-summary")).to_be_visible()
    expect(page).to_have_url(re.compile(r"/contracts\?building=DEMO_A(?:&|$)"))
    expect(operations_switch).to_have_attribute("aria-pressed", "true")
    page.set_viewport_size({"width": 390, "height": 844})
    _assert_no_horizontal_overflow(page)
    if evidence_dir is not None:
        page.screenshot(
            path=str(evidence_dir / "Phase1A-hybrid-operations-390x844.png"),
            animations="disabled",
            full_page=True,
        )


def test_browser_preferences_are_restored_per_account_across_logins(
    fresh_browser_page,
) -> None:
    page, base_url = fresh_browser_page
    evidence_dir_value = os.environ.get("LEASE_E2E_EVIDENCE_DIR", "").strip()
    evidence_dir = Path(evidence_dir_value) if evidence_dir_value else None
    page.goto(base_url)
    _submit_login_form(page, E2E_HYBRID_USERNAME, E2E_HYBRID_PASSWORD)
    page.get_by_test_id("workspace-switch-operations").click()
    page.get_by_test_id("nav-contracts").click()
    expect(page.get_by_test_id("contracts-workspace-summary")).to_be_visible()

    page.get_by_role("button", name="退出").click()
    expect(page.get_by_test_id("login-username")).to_be_visible()
    _submit_login_form(page, E2E_SINGLE_OPERATOR_USERNAME, E2E_SINGLE_OPERATOR_PASSWORD)
    expect(page.get_by_test_id("nav-dashboard")).to_be_visible()
    expect(page.get_by_test_id("current-workspace")).to_contain_text("楼宇运营端")
    expect(page).to_have_url(re.compile(r"/\?building=DEMO_A(?:&|$)"))

    page.get_by_role("button", name="退出").click()
    expect(page.get_by_test_id("login-username")).to_be_visible()
    _submit_login_form(page, E2E_HYBRID_USERNAME, E2E_HYBRID_PASSWORD)

    expect(page.get_by_test_id("workspace-switch-operations")).to_have_attribute(
        "aria-pressed", "true"
    )
    expect(page.get_by_test_id("contracts-workspace-summary")).to_be_visible()
    expect(page).to_have_url(re.compile(r"/contracts\?building=DEMO_A(?:&|$)"))
    if evidence_dir is not None:
        evidence_dir.mkdir(parents=True, exist_ok=True)
        expect(page.locator(".toast-stack .toast")).to_have_count(0, timeout=7000)
        page.screenshot(
            path=str(evidence_dir / "Phase1B-restored-operations-contracts-1440x960.png"),
            animations="disabled",
            full_page=True,
        )

    page.get_by_role("button", name="退出").click()
    expect(page.get_by_test_id("login-username")).to_be_visible()
    expect(page).to_have_url(re.compile(rf"^{re.escape(base_url)}/$"))
    page.goto(f"{base_url}/overview?overview_portfolio={E2E_PORTFOLIO_CODE}")
    _submit_login_form(page, E2E_HYBRID_USERNAME, E2E_HYBRID_PASSWORD)
    expect(page.get_by_test_id("workspace-switch-leadership")).to_have_attribute(
        "aria-pressed", "true"
    )
    expect(page.get_by_test_id("leadership-overview-page")).to_be_visible()
    expect(page).to_have_url(re.compile(r"/overview\?overview_portfolio="))
    if evidence_dir is not None:
        expect(page.locator(".toast-stack .toast")).to_have_count(0, timeout=7000)
        page.screenshot(
            path=str(evidence_dir / "Phase1B-deep-link-leadership-1440x960.png"),
            animations="disabled",
            full_page=True,
        )
    page.get_by_test_id("workspace-switch-operations").click()
    expect(page.get_by_test_id("contracts-workspace-summary")).to_be_visible()


def test_expired_startup_session_clears_stale_route_before_account_switch(
    fresh_browser_page,
) -> None:
    page, base_url = fresh_browser_page
    page.goto(base_url)
    page.evaluate("window.localStorage.setItem('lease_has_session', '1')")
    page.goto(f"{base_url}/contracts?building=DEMO_A#stale-session")

    expect(page.get_by_test_id("login-username")).to_be_visible()
    expect(page).to_have_url(re.compile(rf"^{re.escape(base_url)}/$"))

    _submit_login_form(page, E2E_SINGLE_OPERATOR_USERNAME, E2E_SINGLE_OPERATOR_PASSWORD)
    expect(page.get_by_test_id("nav-dashboard")).to_be_visible()
    expect(page.get_by_test_id("contracts-workspace-summary")).to_have_count(0)
    expect(page).to_have_url(re.compile(r"/\?building=DEMO_A(?:&|$)"))


def test_authorization_change_invalidates_the_saved_workspace_preference(
    fresh_browser_page,
) -> None:
    page, base_url = fresh_browser_page
    admin_client = httpx.Client(base_url=base_url, timeout=10)
    original_grants: list[dict] = []
    original_capabilities: list[str] = []
    hybrid_id: int | None = None

    try:
        admin_login = admin_client.post(
            "/api/auth/login",
            json={"username": "admin", "password": E2E_ADMIN_PASSWORD},
        )
        admin_login.raise_for_status()
        users_response = admin_client.get("/api/auth/users")
        users_response.raise_for_status()
        hybrid_id = next(
            int(item["id"])
            for item in users_response.json()
            if item["username"] == E2E_HYBRID_USERNAME
        )
        authorization_response = admin_client.get(
            f"/api/auth/authorizations/users/{hybrid_id}"
        )
        authorization_response.raise_for_status()
        authorization = authorization_response.json()
        original_grants = [
            {
                "scope_type": item["scope_type"],
                "scope_code": item["scope_code"],
                "access_level": item["access_level"],
                "include_future_buildings": item["include_future_buildings"],
            }
            for item in authorization["grants"]
        ]
        original_capabilities = list(authorization["capabilities"])

        page.goto(base_url)
        _submit_login_form(page, E2E_HYBRID_USERNAME, E2E_HYBRID_PASSWORD)
        page.get_by_test_id("workspace-switch-operations").click()
        page.get_by_test_id("nav-contracts").click()
        expect(page.get_by_test_id("contracts-workspace-summary")).to_be_visible()
        page.get_by_role("button", name="退出").click()

        current_response = admin_client.get(
            f"/api/auth/authorizations/users/{hybrid_id}"
        )
        current_response.raise_for_status()
        current = current_response.json()
        retained_grants = [
            item for item in original_grants if item["scope_type"] != "portfolio_overview"
        ]
        assert {item["scope_code"] for item in retained_grants} == {"DEMO_A"}
        revoked_response = admin_client.put(
            f"/api/auth/authorizations/users/{hybrid_id}",
            json={
                "expected_authorization_version": current["authorization_version"],
                "grants": retained_grants,
                "capabilities": original_capabilities,
            },
        )
        revoked_response.raise_for_status()

        _submit_login_form(page, E2E_HYBRID_USERNAME, E2E_HYBRID_PASSWORD)
        expect(page.get_by_test_id("nav-dashboard")).to_be_visible()
        expect(page.get_by_test_id("workspace-switch-leadership")).to_have_count(0)
        expect(page.get_by_test_id("workspace-switch-operations")).to_have_count(0)
        expect(page.get_by_test_id("contracts-workspace-summary")).to_have_count(0)
        expect(page).to_have_url(re.compile(r"/\?building=DEMO_A(?:&|$)"))
    finally:
        if hybrid_id is not None and original_grants:
            current_response = admin_client.get(
                f"/api/auth/authorizations/users/{hybrid_id}"
            )
            if current_response.is_success:
                current = current_response.json()
                restore_response = admin_client.put(
                    f"/api/auth/authorizations/users/{hybrid_id}",
                    json={
                        "expected_authorization_version": current["authorization_version"],
                        "grants": original_grants,
                        "capabilities": original_capabilities,
                    },
                )
                restore_response.raise_for_status()
        admin_client.close()


def test_logout_in_one_tab_clears_the_authenticated_workspace_in_other_tabs(
    fresh_browser_page,
) -> None:
    first_page, base_url = fresh_browser_page
    second_page = first_page.context.new_page()
    try:
        first_page.goto(base_url)
        _submit_login_form(first_page, E2E_HYBRID_USERNAME, E2E_HYBRID_PASSWORD)
        expect(first_page.get_by_test_id("leadership-overview-page")).to_be_visible()

        second_page.goto(base_url)
        expect(second_page.get_by_test_id("leadership-overview-page")).to_be_visible()
        first_page.get_by_role("button", name="退出").click()

        expect(first_page.get_by_test_id("login-username")).to_be_visible()
        expect(second_page.get_by_test_id("login-username")).to_be_visible()
        expect(second_page.get_by_test_id("current-workspace")).to_have_count(0)
    finally:
        second_page.close()


def test_root_building_deep_link_takes_priority_over_the_default_workspace(
    fresh_browser_page,
) -> None:
    page, base_url = fresh_browser_page
    page.goto(f"{base_url}/?building=DEMO_A")
    _submit_login_form(page, E2E_HYBRID_USERNAME, E2E_HYBRID_PASSWORD)

    expect(page.get_by_test_id("workspace-switch-operations")).to_have_attribute(
        "aria-pressed", "true"
    )
    expect(page.get_by_test_id("nav-dashboard")).to_be_visible()
    expect(page.get_by_test_id("leadership-overview-page")).to_have_count(0)
    expect(page).to_have_url(re.compile(r"/\?building=DEMO_A(?:&|$)"))


def test_presentation_target_becomes_the_saved_last_page(
    fresh_browser_page,
) -> None:
    page, base_url = fresh_browser_page
    page.goto(base_url)
    _submit_login_form(page, E2E_HYBRID_USERNAME, E2E_HYBRID_PASSWORD)
    page.get_by_test_id("workspace-switch-operations").click()
    page.get_by_test_id("nav-customers").click()
    expect(page.get_by_test_id("customer-workspace-summary")).to_be_visible()

    customer_url = page.url
    page.get_by_test_id("topbar-presentation-toggle").click()
    presentation = page.get_by_test_id("unified-cockpit")
    expect(presentation).to_be_visible()
    presentation.get_by_test_id("cockpit-topic-operations").click()
    expect(presentation.get_by_test_id("analytics-page")).to_be_visible()
    with page.expect_response(
        lambda response: urlparse(response.url).path == "/api/contracts"
        and "building=DEMO_A" in urlparse(response.url).query
        and response.status == 200
    ):
        presentation.get_by_test_id("analytics-action-contracts").click()
    expect(page.get_by_test_id("cockpit-detail")).to_be_visible()
    expect(page.get_by_role("dialog")).to_contain_text("合同")
    expect(page.get_by_test_id("cockpit-detail").get_by_role("alert")).to_have_count(0)
    expect(presentation).to_be_visible()
    expect(page).to_have_url(customer_url)
    page.get_by_test_id("base-modal-close").click()
    expect(page.get_by_test_id("cockpit-detail")).to_have_count(0)
    presentation.get_by_test_id("cockpit-exit").click()
    expect(presentation).to_have_count(0)
    expect(page.get_by_test_id("customer-workspace-summary")).to_be_visible()
    expect(page).to_have_url(customer_url)

    page.get_by_role("button", name="退出").click()
    _submit_login_form(page, E2E_HYBRID_USERNAME, E2E_HYBRID_PASSWORD)
    expect(page.get_by_test_id("workspace-switch-operations")).to_have_attribute(
        "aria-pressed", "true"
    )
    expect(page.get_by_test_id("customer-workspace-summary")).to_be_visible()
    expect(page).to_have_url(customer_url)


def test_pending_operating_presentation_stops_at_workspace_boundary(
    fresh_browser_page,
) -> None:
    page, base_url = fresh_browser_page
    api_requests: list[str] = []

    def record_api_request(request) -> None:
        parsed = urlparse(request.url)
        if parsed.path.startswith("/api/"):
            api_requests.append(parsed.path)

    page.on("request", record_api_request)
    page.goto(base_url)
    page.get_by_test_id("login-username").fill(E2E_HYBRID_USERNAME)
    page.get_by_test_id("login-password").fill(E2E_HYBRID_PASSWORD)
    page.get_by_test_id("login-submit").click()
    page.get_by_test_id("workspace-switch-operations").click()
    expect(page.get_by_test_id("nav-dashboard")).to_be_visible()

    held_auth_routes = []

    def hold_auth_request(route) -> None:
        held_auth_routes.append(route)

    page.route("**/api/auth/me", hold_auth_request)
    request_start = len(api_requests)
    page.get_by_test_id("topbar-presentation-toggle").click()
    for _ in range(50):
        if held_auth_routes:
            break
        page.wait_for_timeout(20)
    assert held_auth_routes

    page.get_by_test_id("workspace-switch-leadership").click()
    expect(page.get_by_test_id("leadership-overview-page")).to_be_visible()
    held_auth_routes[0].continue_()
    page.unroute("**/api/auth/me", hold_auth_request)
    page.wait_for_timeout(500)

    expect(
        page.get_by_test_id("unified-cockpit")
    ).to_have_count(0)
    assert "/api/operations/snapshot" not in api_requests[request_start:]


def test_presentation_refresh_401_cannot_restore_expired_session(
    fresh_browser_page,
) -> None:
    page, base_url = fresh_browser_page
    page.goto(base_url)
    page.get_by_test_id("login-username").fill(E2E_HYBRID_USERNAME)
    page.get_by_test_id("login-password").fill(E2E_HYBRID_PASSWORD)
    page.get_by_test_id("login-submit").click()
    page.get_by_test_id("workspace-switch-operations").click()
    expect(page.get_by_test_id("nav-dashboard")).to_be_visible()

    snapshot_pattern = re.compile(r"/api/operations/snapshot(?:\?.*)?$")

    def expire_snapshot_request(route) -> None:
        route.fulfill(
            status=401,
            content_type="application/json",
            body=json.dumps({"detail": "expired"}),
        )

    page.route(snapshot_pattern, expire_snapshot_request)
    page.get_by_test_id("topbar-presentation-toggle").click()

    expect(page.get_by_test_id("login-username")).to_be_visible()
    page.wait_for_timeout(300)
    expect(page.get_by_test_id("current-workspace")).to_have_count(0)
    expect(page.get_by_test_id("unified-cockpit")).to_have_count(0)


def test_portfolio_overview_only_leader_browser_boundary(fresh_browser_page) -> None:
    page, base_url = fresh_browser_page
    desired_gate_states = {
        "DEMO_B": (True, False, "observe"),
        "DEMO_D": (False, True, "aggregate_only"),
        "DEMO_E": (False, False, "closed"),
    }
    building_catalog = {
        "DEMO_B": "示例主裙楼中心",
        "DEMO_D": "示例办公中心",
        "DEMO_E": "示例商务中心",
        "DEMO_C": "示例单栋中心",
        "DEMO_A": "示例双塔中心",
    }
    evidence_dir_value = os.environ.get("LEASE_E2E_EVIDENCE_DIR", "").strip()
    evidence_dir = Path(evidence_dir_value) if evidence_dir_value else None
    admin_client = httpx.Client(base_url=base_url, timeout=10)
    original_gate_states: dict[str, tuple[bool, bool]] = {}

    try:
        admin_login = admin_client.post(
            "/api/auth/login",
            json={"username": "admin", "password": E2E_ADMIN_PASSWORD},
        )
        admin_login.raise_for_status()
        original_items = _operating_gate_items(admin_client)
        original_gate_states = {
            code: (
                bool(original_items[code]["presentation_enabled"]),
                bool(original_items[code]["contribution_enabled"]),
            )
            for code in desired_gate_states
        }
        for code, (presentation_enabled, contribution_enabled, state) in desired_gate_states.items():
            _put_operating_gate(
                admin_client,
                code,
                presentation_enabled=presentation_enabled,
                contribution_enabled=contribution_enabled,
                reason=f"阶段3E浏览器验收：临时演练 {state} 状态",
            )

        api_requests: list[str] = []

        def record_api_request(request) -> None:
            parsed = urlparse(request.url)
            if parsed.path.startswith("/api/"):
                api_requests.append(f"{request.method} {parsed.path}?{parsed.query}")

        page.on("request", record_api_request)
        page.set_viewport_size({"width": 1920, "height": 1080})
        page.goto(f"{base_url}/contracts?building=DEMO_A")
        page.get_by_test_id("login-username").fill(E2E_OVERVIEW_LEADER_USERNAME)
        page.get_by_test_id("login-password").fill(E2E_OVERVIEW_LEADER_PASSWORD)
        with page.expect_response(
            lambda response: (
                urlparse(response.url).path == "/api/operations/portfolio-overview"
                and response.status == 200
            )
        ) as overview_response_info:
            page.get_by_test_id("login-submit").click()

        overview_payload = overview_response_info.value.json()
        overview_page = page.get_by_test_id("leadership-overview-page")
        expect(overview_page).to_be_visible()
        expect(page).to_have_url(re.compile(r"/overview(?:\?|$)"))
        expect(page).not_to_have_url(re.compile(r"[?&](?:building|portfolio)=DEMO_A(?:&|$)"))
        expect(page.get_by_test_id("nav-overview")).to_be_visible()
        for hidden_nav in (
            "nav-dashboard",
            "nav-operations",
            "nav-analytics",
            "nav-evidence",
            "nav-contracts",
            "nav-customers",
            "nav-rooms",
            "nav-receivables",
            "nav-risks",
            "nav-ai-assist",
            "nav-audit",
            "nav-tools",
        ):
            expect(page.get_by_test_id(hidden_nav)).to_have_count(0)
        expect(page.get_by_test_id("property-scope-selector")).to_have_count(0)
        expect(page.get_by_test_id("current-workspace")).to_contain_text("资产组合概况")
        expect(page.get_by_test_id("current-scope")).to_contain_text("示例资产组合")
        presentation_button = page.get_by_test_id("topbar-presentation-toggle")
        expect(presentation_button).to_have_accessible_name("进入五栋楼大屏")

        assert overview_payload["contract_version"] == "portfolio-overview-v1"
        assert overview_payload["data_classification"] == "simulated"
        assert overview_payload["scope"]["scope_code"] == E2E_PORTFOLIO_CODE
        assert set(overview_payload["scope"]["building_codes"]) == set(building_catalog)
        assert {
            item["building"]["code"] for item in overview_payload["contributions"]
        } == {"DEMO_A", "DEMO_B", "DEMO_C"}
        assert overview_payload["coverage"]["authorized_buildings"] == 5
        assert overview_payload["coverage"]["presentation_enabled_buildings"] == 3
        assert overview_payload["coverage"]["contribution_enabled_buildings"] == 3
        rollout_states = {
            item["building_code"]: item["state"]
            for item in overview_payload["rollout"]["items"]
        }
        assert rollout_states == {
            "DEMO_A": "open",
            "DEMO_B": "observe",
            "DEMO_D": "aggregate_only",
            "DEMO_E": "closed",
            "DEMO_C": "open",
        }

        cockpit = page.get_by_test_id("presentation-cockpit")
        expect(cockpit).to_be_visible()
        expect(cockpit).to_have_attribute("data-scope-type", "portfolio")
        expect(page.get_by_test_id("portfolio-simulated-data")).to_be_visible()
        expect(cockpit).to_contain_text(f"统计日 {overview_payload['as_of_date']}")
        quality_summary = page.get_by_test_id("portfolio-data-quality-summary")
        expect(quality_summary).to_be_visible()
        expect(quality_summary).to_contain_text(
            f"{overview_payload['coverage']['same_day_snapshot_buildings']}/5"
        )
        for code, name in building_catalog.items():
            card = page.get_by_test_id(f"portfolio-building-{code}")
            expect(card).to_be_visible()
            expect(card).to_contain_text(name)
            expect(card).to_have_attribute("data-operating-state", rollout_states[code])
            expect(page.get_by_test_id(f"portfolio-building-status-{code}")).to_be_visible()
        expect(page.get_by_test_id("portfolio-building-status-DEMO_B")).to_contain_text("观察态")
        expect(page.get_by_test_id("portfolio-building-status-DEMO_D")).to_contain_text("仅参与合计")
        expect(page.get_by_test_id("portfolio-building-status-DEMO_E")).to_contain_text("暂不展示")
        expect(page.get_by_test_id("portfolio-building-status-DEMO_A")).to_contain_text("实时明细")
        expect(page.get_by_test_id("portfolio-building-status-DEMO_C")).to_contain_text("已复核发布")
        for hidden_code in ("DEMO_D", "DEMO_E"):
            hidden_card = page.get_by_test_id(f"portfolio-building-{hidden_code}")
            expect(hidden_card.locator(".portfolio-building-name")).to_be_disabled()
            expect(hidden_card.locator(".portfolio-building-metric-row")).to_have_count(0)
        expect(cockpit).to_contain_text("只计合计")
        expect(cockpit).to_contain_text("暂不展示且未计入合计")
        _assert_no_horizontal_overflow(page)

        forbidden_automatic_prefixes = (
            "/api/contracts",
            "/api/customers",
            "/api/rooms",
            "/api/dashboard",
            "/api/rent-analytics",
            "/api/attachments",
            "/api/risks",
            "/api/ai",
            "/api/audit",
            "/api/backups",
            "/api/settings",
            "/api/system/integrity-check",
            "/api/buildings",
            "/api/operations/snapshot",
        )
        automatic_api_requests = list(api_requests)
        forbidden_automatic_requests = [
            request
            for request in automatic_api_requests
            if urlparse(request.split(" ", 1)[1]).path.startswith(forbidden_automatic_prefixes)
        ]
        assert forbidden_automatic_requests == []
        overview_requests = [
            request
            for request in automatic_api_requests
            if urlparse(request.split(" ", 1)[1]).path == "/api/operations/portfolio-overview"
        ]
        assert overview_requests
        assert all("building=" not in request for request in overview_requests)
        expect(page.locator(".toast-stack .toast")).to_have_count(0, timeout=6000)

        presentation_request_start = len(api_requests)
        presentation_button.click()
        fullscreen_portfolio = page.get_by_test_id("unified-cockpit")
        page.wait_for_timeout(800)
        assert fullscreen_portfolio.count() == 1, {
            "requests": api_requests[presentation_request_start:],
            "body_class": page.locator("body").get_attribute("class"),
            "toasts": page.locator(".toast-stack .toast").all_text_contents(),
        }
        expect(fullscreen_portfolio).to_be_visible()
        leader_url = page.url
        fullscreen_scope = fullscreen_portfolio.get_by_test_id("cockpit-building-select")
        expect(fullscreen_scope).to_have_value("")
        expect(fullscreen_portfolio.locator(".uc-context")).to_contain_text("示例资产组合")
        for code, name in building_catalog.items():
            fullscreen_card = fullscreen_portfolio.get_by_test_id(f"cockpit-building-{code}")
            expect(fullscreen_card).to_contain_text(name)
            if code in ("DEMO_D", "DEMO_E"):
                expect(fullscreen_card).to_be_disabled()
                expect(fullscreen_scope.locator(f'option[value="{code}"]')).to_be_disabled()
                expect(fullscreen_card.locator(".uc-building-metrics b")).to_have_count(0)
            else:
                expect(fullscreen_card).to_be_enabled()
        for code in ("DEMO_A", "DEMO_B"):
            fullscreen_scope.select_option(code)
            expect(fullscreen_scope).to_have_value(code)
            expect(fullscreen_portfolio.locator(".uc-context")).to_contain_text(building_catalog[code])
            fullscreen_portfolio.get_by_test_id("cockpit-topic-rent").click()
            expect(fullscreen_portfolio).to_contain_text("当前楼宇按已发布的整楼指标展示")
            expect(fullscreen_portfolio.get_by_test_id("analytics-rent-timeline")).to_have_count(0)
            fullscreen_portfolio.get_by_test_id("cockpit-topic-operations").click()
            expect(fullscreen_portfolio.get_by_test_id("analytics-page")).to_have_count(0)
            fullscreen_portfolio.locator(".uc-kpis button").filter(has_text="面积出租率").click()
            expect(page.get_by_test_id("cockpit-detail")).to_be_visible()
            expect(page.get_by_test_id("cockpit-detail")).to_contain_text(building_catalog[code])
            expect(page.get_by_test_id("cockpit-detail").locator(".uc-detail-rows")).to_have_count(0)
            page.get_by_test_id("base-modal-close").click()
            fullscreen_portfolio.get_by_test_id("cockpit-topic-space").click()
            expect(fullscreen_portfolio).to_contain_text("当前楼宇按整楼经营指标展示")
            expect(fullscreen_portfolio.locator(".uc-room")).to_have_count(0)
            fullscreen_portfolio.get_by_test_id("cockpit-topic-decision").click()
            expect(fullscreen_portfolio.locator(".decision-workbench")).to_be_visible()
            expect(fullscreen_portfolio.locator(".dw-outcome-strip")).to_contain_text("全年基准预计")
            expect(page).to_have_url(leader_url)
            fullscreen_portfolio.get_by_test_id("cockpit-back-overview").click()
            expect(fullscreen_scope).to_have_value("")
        fullscreen_portfolio.get_by_test_id("cockpit-tour").click()
        expect(fullscreen_portfolio.get_by_test_id("cockpit-tour")).to_have_attribute("aria-pressed", "true")
        fullscreen_portfolio.get_by_test_id("cockpit-topic-rent").click()
        expect(fullscreen_portfolio.get_by_test_id("cockpit-tour")).to_have_attribute("aria-pressed", "false")
        fullscreen_portfolio.get_by_test_id("cockpit-topic-overview").click()
        _assert_chart_canvas_nonblank(fullscreen_portfolio.get_by_role("img", name="在租与待租面积", exact=True))
        page.set_viewport_size({"width": 390, "height": 844})
        _assert_no_horizontal_overflow(page)
        cockpit_content = fullscreen_portfolio.locator(".uc-content")
        # Chart canvases resize through ResizeObserver and the chart work queue.
        # Wait for the actual narrow-layout geometry, keeping the 2 px limit.
        page.wait_for_function(
            "element => element.clientWidth > 0 && element.scrollWidth - element.clientWidth <= 2",
            arg=cockpit_content.element_handle(),
            timeout=5000,
        )
        assert cockpit_content.evaluate(
            "element => element.scrollWidth - element.clientWidth"
        ) <= 2
        page.set_viewport_size({"width": 1920, "height": 1080})
        presentation_requests = api_requests[presentation_request_start:]
        assert not any(
            urlparse(request.split(" ", 1)[1]).path.startswith(forbidden_automatic_prefixes)
            for request in presentation_requests
        )
        if evidence_dir is not None:
            evidence_dir.mkdir(parents=True, exist_ok=True)
            page.screenshot(
                path=str(evidence_dir / "Phase1A-leadership-fullscreen-1920x1080.png"),
                animations="disabled",
            )
        page.keyboard.press("Escape")
        expect(fullscreen_portfolio).to_have_count(0)

        if evidence_dir is not None:
            evidence_dir.mkdir(parents=True, exist_ok=True)
            page.screenshot(
                path=str(evidence_dir / "阶段3E-五栋楼资产组合概况四态-1920x1080.png"),
                animations="disabled",
            )

        page.set_viewport_size({"width": 390, "height": 844})
        expect(overview_page).to_be_visible()
        _assert_no_horizontal_overflow(page)
        _assert_leadership_overview_mobile_layout(page)
        if evidence_dir is not None:
            page.screenshot(
                path=str(evidence_dir / "阶段3E-五栋楼资产组合概况四态-390x844.png"),
                animations="disabled",
            )
        page.set_viewport_size({"width": 1920, "height": 1080})

        api_count_before_local_drilldown = len(api_requests)
        page.get_by_test_id("portfolio-building-DEMO_A").locator(".portfolio-building-name").click()
        expect(page).to_have_url(re.compile(r"/overview\?.*overview_building=DEMO_A(?:&|$)"))
        expect(cockpit).to_have_attribute("data-scope-type", "building")
        expect(cockpit).to_contain_text("示例双塔中心")
        expect(page.get_by_test_id("presentation-simulated-data")).to_be_visible()
        expect(page.get_by_test_id("leadership-overview-back")).to_be_visible()
        page.wait_for_timeout(250)
        assert len(api_requests) == api_count_before_local_drilldown
        page.get_by_test_id("leadership-overview-back").click()
        expect(page).to_have_url(re.compile(r"/overview(?:\?|$)"))
        expect(cockpit).to_have_attribute("data-scope-type", "portfolio")
        page.wait_for_timeout(250)
        assert len(api_requests) == api_count_before_local_drilldown

        page.get_by_test_id("portfolio-building-DEMO_B").locator(
            ".portfolio-building-name"
        ).click()
        expect(page).to_have_url(re.compile(r"/overview\?.*overview_building=DEMO_B(?:&|$)"))
        expect(cockpit).to_have_attribute("data-scope-type", "building")
        leader_profile = page.get_by_test_id("presentation-building-profile")
        expect(leader_profile).to_have_attribute(
            "data-structure", "main_building_with_podium"
        )
        expect(page.get_by_test_id("presentation-profile-group-WHOLE_BUILDING")).to_contain_text(
            "口径已启用"
        )
        expect(page.get_by_test_id("presentation-profile-group-MARKET_LEASING")).to_contain_text(
            "分区指标尚不可用"
        )
        expect(page.get_by_test_id("presentation-profile-safety")).to_contain_text(
            "暂无分区数据"
        )
        expect(cockpit).to_contain_text("面积出租率")
        expect(cockpit).to_contain_text("95%")
        expect(cockpit).not_to_contain_text("occupancy.area_rate")
        page.wait_for_timeout(800)
        assert len(api_requests) == api_count_before_local_drilldown
        if evidence_dir is not None:
            page.screenshot(
                path=str(evidence_dir / "Phase3-组合概况只读集团大厦双口径-1920x1080.png"),
                animations="disabled",
            )
        page.get_by_test_id("leadership-overview-back").click()
        expect(page).to_have_url(re.compile(r"/overview(?:\?|$)"))
        expect(cockpit).to_have_attribute("data-scope-type", "portfolio")
        page.wait_for_timeout(250)
        assert len(api_requests) == api_count_before_local_drilldown

        reload_request_start = len(api_requests)
        with page.expect_response(
            lambda response: (
                urlparse(response.url).path == "/api/operations/portfolio-overview"
                and response.status == 200
            )
        ):
            page.reload()
        expect(page.get_by_test_id("leadership-overview-page")).to_be_visible()
        expect(page).to_have_url(re.compile(r"/overview(?:\?|$)"))
        reload_api_requests = api_requests[reload_request_start:]
        forbidden_reload_requests = [
            request
            for request in reload_api_requests
            if urlparse(request.split(" ", 1)[1]).path.startswith(forbidden_automatic_prefixes)
        ]
        assert forbidden_reload_requests == []

        denied_paths = (
            "/api/contracts?building=DEMO_A&page=1&page_size=25",
            "/api/customers?building=DEMO_A&page=1&page_size=25",
            "/api/rooms?building=DEMO_A",
            "/api/contracts/receivables/monthly?building=DEMO_A&month=2026-08",
            "/api/attachments/completeness-tasks?building=DEMO_A&limit=20",
            (
                "/api/operations/snapshot-evidence"
                f"?portfolio={E2E_PORTFOLIO_CODE}"
                "&metric_code=occupancy.area_rate&as_of_date=2026-08-05"
            ),
            "/api/auth/users",
            "/api/buildings/operating-gates",
            "/api/audit/platform/logs?page=1&page_size=50",
            "/api/backups",
            "/api/settings/feishu",
            "/api/system/integrity-check",
        )
        for denied_path in denied_paths:
            denied = page.request.get(f"{base_url}{denied_path}")
            assert denied.status in {403, 404}, (
                f"overview-only leader unexpectedly accessed {denied_path}: "
                f"{denied.status} {denied.text()}"
            )

        page.get_by_role("button", name="退出").click()
        expect(page.get_by_test_id("login-username")).to_be_visible()
        _login(page, base_url)
        expect(page.get_by_test_id("nav-contracts")).to_be_visible()
        expect(page.get_by_test_id("nav-tools")).to_be_visible()
        admin_contracts = page.request.get(
            f"{base_url}/api/contracts?building=DEMO_A&page=1&page_size=25"
        )
        assert admin_contracts.status == 200, admin_contracts.text()
        admin_snapshot = page.request.get(
            f"{base_url}/api/operations/snapshot"
            "?portfolio=DEMO_OFFICE_PORTFOLIO&include_trend=false&queue_limit=0&risk_limit=0"
        )
        assert admin_snapshot.status == 200, admin_snapshot.text()
    finally:
        try:
            for code, (presentation_enabled, contribution_enabled) in original_gate_states.items():
                _put_operating_gate(
                    admin_client,
                    code,
                    presentation_enabled=presentation_enabled,
                    contribution_enabled=contribution_enabled,
                    reason="阶段3E浏览器验收完成：恢复原展示与合计状态",
                )
        finally:
            admin_client.close()


@pytest.mark.skipif(
    not (ROOT / "docs" / "snapshot-exchange" / "ops-exchange-v1-simulated.sample.json").is_file(),
    reason="Internal pilot files are not distributed in the public edition.",
)
def test_snapshot_exchange_file_workbench_preflight_submit_and_receipt(
    fresh_browser_page,
) -> None:
    page, base_url = fresh_browser_page
    evidence_dir_value = os.environ.get("LEASE_E2E_EVIDENCE_DIR", "").strip()
    evidence_dir = Path(evidence_dir_value) if evidence_dir_value else None

    _login(page, base_url)
    expect(page.get_by_test_id("nav-exchange")).to_be_visible()
    page.get_by_test_id("nav-exchange").click()

    workbench = page.get_by_test_id("snapshot-exchange-workbench")
    expect(workbench).to_be_visible()
    expect(workbench).to_contain_text("预检不会生成批次、候选、事实、回执或审计记录")
    expect(page.get_by_test_id("exchange-source-select")).to_have_value("DEMO-B-OPS")
    expect(page.get_by_test_id("exchange-template-download")).to_be_enabled()

    sample_path = ROOT / "docs" / "snapshot-exchange" / "ops-exchange-v1-simulated.sample.json"
    assert sample_path.is_file()
    page.get_by_test_id("exchange-file-input").set_input_files(str(sample_path))

    with page.expect_response(
        lambda response: response.url.endswith("/api/snapshot-exchange/v1/uploads/preflight")
    ) as preflight_info:
        page.get_by_test_id("exchange-preflight").click()
    assert preflight_info.value.status == 200, preflight_info.value.text()
    expect(workbench.locator(".exchange-inspection .exchange-status")).to_contain_text("可接收")
    expect(page.get_by_test_id("exchange-submit")).to_be_enabled()

    with page.expect_response(
        lambda response: response.url.endswith("/api/snapshot-exchange/v1/uploads/submit")
    ) as submit_info:
        page.get_by_test_id("exchange-submit").click()
    assert submit_info.value.status == 201, submit_info.value.text()
    guidance = page.get_by_test_id("exchange-submitted-guidance")
    expect(guidance).to_contain_text("待中心复核")
    expect(guidance).to_contain_text("发布后更新经营概况")
    ledger = page.get_by_test_id("exchange-receipt-ledger")
    expect(ledger).to_contain_text("DELIVERY-20260805-")
    expect(ledger).to_contain_text("PLE-001")
    expect(page.locator(".toast-stack .toast")).to_have_count(0, timeout=7000)

    if evidence_dir is not None:
        evidence_dir.mkdir(parents=True, exist_ok=True)
        page.screenshot(
            path=str(evidence_dir / "阶段3F-2-标准文件交换工作台-1440x960.png"),
            animations="disabled",
            full_page=True,
        )

    page.set_viewport_size({"width": 390, "height": 844})
    expect(workbench).to_be_visible()
    _assert_no_horizontal_overflow(page)
    if evidence_dir is not None:
        page.screenshot(
            path=str(evidence_dir / "阶段3F-2-标准文件交换工作台-390x844.png"),
            animations="disabled",
            full_page=True,
        )

    page.set_viewport_size({"width": 1440, "height": 960})
    page.reload()
    expect(workbench).to_be_visible()
    refreshed_ledger = page.get_by_test_id("exchange-receipt-ledger")
    expect(refreshed_ledger).to_contain_text("DELIVERY-20260805-")
    expect(refreshed_ledger).to_contain_text("PLE-001")


@pytest.mark.skipif(
    not (ROOT / "docs" / "pilot-onboarding").is_dir(),
    reason="Internal pilot files are not distributed in the public edition.",
)
def test_demo_b_simulated_pilot_file_workbench_json_excel_idempotency(
    fresh_browser_page,
) -> None:
    page, base_url = fresh_browser_page
    evidence_dir_value = os.environ.get("LEASE_E2E_EVIDENCE_DIR", "").strip()
    evidence_dir = Path(evidence_dir_value) if evidence_dir_value else None
    pilot_dir = ROOT / "docs" / "pilot-onboarding"
    json_package = pilot_dir / "demo-b-ops-exchange-simulated-v1.json"
    excel_package = pilot_dir / "demo-b-ops-exchange-simulated-v1.xlsx"
    expected_digest = "8b21232ceb0371be46f64c24a02216bf49a3c4e234ffb1cbf526292bd13bcf8e"
    expected_delivery_id = "DEMO-B-SIM-202607-R1"

    assert json_package.is_file()
    assert excel_package.is_file()
    _login(page, base_url)
    page.get_by_test_id("nav-exchange").click()

    workbench = page.get_by_test_id("snapshot-exchange-workbench")
    expect(workbench).to_be_visible()
    source_select = page.get_by_test_id("exchange-source-select")
    source_select.select_option("DEMO-B-SIM-OPS")
    expect(source_select).to_have_value("DEMO-B-SIM-OPS")

    page.get_by_test_id("exchange-file-input").set_input_files(str(json_package))
    with page.expect_response(
        lambda response: response.url.endswith("/api/snapshot-exchange/v1/uploads/preflight")
    ) as json_preflight_info:
        page.get_by_test_id("exchange-preflight").click()
    assert json_preflight_info.value.status == 200, json_preflight_info.value.text()
    json_preflight = json_preflight_info.value.json()
    assert json_preflight["file_format"] == "json"
    assert json_preflight["delivery_id"] == expected_delivery_id
    assert json_preflight["source_system_code"] == "DEMO-B-SIM-OPS"
    assert json_preflight["building_code"] == "DEMO_B"
    assert json_preflight["server_content_digest"] == expected_digest
    assert json_preflight["valid"] is True
    assert json_preflight["would_status"] == "accepted"
    expect(workbench.locator(".exchange-inspection .exchange-status")).to_contain_text("可接收")

    if evidence_dir is not None:
        evidence_dir.mkdir(parents=True, exist_ok=True)
        page.screenshot(
            path=str(evidence_dir / "阶段3G-2-DEMO_B模拟包预检-1440x960.png"),
            animations="disabled",
            full_page=True,
        )

    with page.expect_response(
        lambda response: response.url.endswith("/api/snapshot-exchange/v1/uploads/submit")
    ) as json_submit_info:
        page.get_by_test_id("exchange-submit").click()
    assert json_submit_info.value.status == 201, json_submit_info.value.text()
    accepted_receipt = json_submit_info.value.json()
    assert accepted_receipt["status"] == "accepted"
    assert accepted_receipt["center_status"] == "pending_review"
    assert accepted_receipt["delivery_id"] == expected_delivery_id
    assert accepted_receipt["server_content_digest"] == expected_digest
    guidance = page.get_by_test_id("exchange-submitted-guidance")
    expect(guidance).to_contain_text("待中心复核")
    expect(guidance).to_contain_text("发布后更新经营概况")
    ledger = page.get_by_test_id("exchange-receipt-ledger")
    expect(ledger).to_contain_text("DEMO-B-SIM-OPS")
    assert ledger.locator("tbody").filter(has_text="DEMO-B-SIM-OPS").count() == 1
    expect(page.locator(".toast-stack .toast")).to_have_count(0, timeout=7000)

    if evidence_dir is not None:
        page.screenshot(
            path=str(evidence_dir / "阶段3G-2-DEMO_B模拟包提交回执-1440x960.png"),
            animations="disabled",
            full_page=True,
        )

    page.set_viewport_size({"width": 390, "height": 844})
    _assert_no_horizontal_overflow(page)
    if evidence_dir is not None:
        page.screenshot(
            path=str(evidence_dir / "阶段3G-2-DEMO_B模拟包提交回执-390x844.png"),
            animations="disabled",
            full_page=True,
        )

    page.set_viewport_size({"width": 1440, "height": 960})
    page.get_by_test_id("exchange-file-input").set_input_files(str(excel_package))
    with page.expect_response(
        lambda response: response.url.endswith("/api/snapshot-exchange/v1/uploads/preflight")
    ) as excel_preflight_info:
        page.get_by_test_id("exchange-preflight").click()
    assert excel_preflight_info.value.status == 200, excel_preflight_info.value.text()
    excel_preflight = excel_preflight_info.value.json()
    assert excel_preflight["file_format"] == "excel"
    assert excel_preflight["server_content_digest"] == expected_digest
    assert excel_preflight["would_status"] == "accepted"
    expect(page.get_by_test_id("exchange-submit")).to_be_enabled()

    with page.expect_response(
        lambda response: response.url.endswith("/api/snapshot-exchange/v1/uploads/submit")
    ) as excel_submit_info:
        page.get_by_test_id("exchange-submit").click()
    assert excel_submit_info.value.status == 200, excel_submit_info.value.text()
    assert excel_submit_info.value.json() == accepted_receipt
    ledger = page.get_by_test_id("exchange-receipt-ledger")
    assert ledger.locator("tbody").filter(has_text="DEMO-B-SIM-OPS").count() == 1
    expect(page.locator(".toast-stack .toast")).to_have_count(0, timeout=7000)

    if evidence_dir is not None:
        page.screenshot(
            path=str(evidence_dir / "阶段3G-2-DEMO_B跨格式幂等回执-1440x960.png"),
            animations="disabled",
            full_page=True,
        )


def test_frontend_performance_sampling(fresh_browser_page) -> None:
    page, base_url = fresh_browser_page
    samples: dict[str, dict] = {}

    _login(page, base_url)
    expect(page.get_by_test_id("dashboard-page")).to_be_visible()
    samples["dashboard_initial"] = _route_sample(page, "dashboard")
    _assert_route_sample_budget(samples["dashboard_initial"], max_long_task_total=300)
    _assert_box_stable(page, "today-signal-lanes")

    page.evaluate("window.__leaseManagerPerformance?.reset()")
    shell_before = _element_box(page, "app-page-shell")
    page.get_by_test_id("nav-rooms").click()
    expect(page.get_by_test_id("rooms-page")).to_be_visible()
    _assert_page_shell_stays_anchored(shell_before, _element_box(page, "app-page-shell"))
    samples["rooms_entry"] = _route_sample(page, "rooms")
    _assert_route_sample_budget(samples["rooms_entry"], max_long_task_total=300)
    _assert_no_large_route_resource(samples["rooms_entry"])
    _assert_box_stable(page, "rooms-heatmap", wait_ms=300)
    _open_parent_details(page, "rooms-table")
    _assert_box_stable(page, "rooms-table", wait_ms=300)

    page.get_by_test_id("rooms-heatmap-room-E0301").scroll_into_view_if_needed()
    room_scroll_before = page.evaluate("window.scrollY")
    page.get_by_test_id("rooms-heatmap-room-E0301").click()
    expect(page.get_by_test_id("room-operation-action-lane")).to_be_visible()
    interaction_snapshot = page.evaluate("window.__leaseManagerPerformance.snapshot()")
    assert "recentEvents" in interaction_snapshot
    assert all(event["duration"] <= 250 for event in interaction_snapshot["recentEvents"])
    page.get_by_test_id("base-modal-close").click()
    expect(page.get_by_test_id("room-operation-action-lane")).to_have_count(0)
    _wait_for_scroll_position(page, room_scroll_before)

    page.wait_for_timeout(120)
    page.evaluate("() => { window.scrollTo(0, 0); document.documentElement.scrollTop = 0; document.body.scrollTop = 0; }")
    page.wait_for_timeout(120)
    page.evaluate("window.__leaseManagerPerformance?.reset()")
    page.get_by_test_id("nav-risks").click()
    expect(page.get_by_test_id("risk-center-page")).to_be_visible()
    _assert_page_shell_at_viewport_top(_element_box(page, "app-page-shell"))
    samples["risks_entry"] = _route_sample(page, "risks")
    _assert_route_sample_budget(samples["risks_entry"], max_long_task_total=300)
    _assert_no_large_route_resource(samples["risks_entry"])
    _assert_box_stable(page, "risk-matrix", wait_ms=300)
    _open_parent_details(page, "attachment-completeness-card")
    _assert_box_stable(page, "attachment-completeness-card", wait_ms=300)

    page.evaluate("window.__leaseManagerPerformance?.reset()")
    page.get_by_test_id("nav-dashboard").click()
    expect(page.get_by_test_id("dashboard-page")).to_be_visible()
    samples["dashboard_return"] = _route_sample(page, "dashboard")
    _assert_route_sample_budget(samples["dashboard_return"], max_long_task_total=300)
    _assert_box_stable(page, "today-signal-lanes")

    print("PERF_SAMPLES " + json.dumps(samples, ensure_ascii=False, sort_keys=True))


def test_xshgj_whole_building_detail_workspace_in_real_chrome(
    fresh_browser_page,
    xshgj_detail_pilot,
) -> None:
    page, base_url = fresh_browser_page
    evidence_dir_value = os.environ.get("LEASE_E2E_EVIDENCE_DIR", "").strip()
    evidence_dir = Path(evidence_dir_value) if evidence_dir_value else None
    page_errors: list[str] = []
    failed_detail_responses: list[str] = []

    page.on("pageerror", lambda error: page_errors.append(str(error)))

    def record_failed_detail_response(response) -> None:
        parsed = urlparse(response.url)
        if response.status >= 400 and "building=DEMO_C" in parsed.query:
            failed_detail_responses.append(f"{response.status} {parsed.path}?{parsed.query}")

    page.on("response", record_failed_detail_response)
    _login(page, base_url)

    me_response = page.request.get(f"{base_url}/api/auth/me")
    assert me_response.status == 200, me_response.text()
    xshgj_scope = next(
        item for item in me_response.json()["scope"]["items"] if item["code"] == "DEMO_C"
    )
    xshgj_profile = xshgj_scope["operations_profile"]
    assert xshgj_scope["ingestion_mode"] == "detail"
    assert xshgj_profile["data_adapter"] == "live_detail"
    assert xshgj_profile["space_structure"]["type"] == "single_building"
    assert xshgj_profile["space_structure"]["zones"] == [
        {"code": "WHOLE_BUILDING", "name": "整楼", "kind": "whole_building"}
    ]

    rooms_response = page.request.get(f"{base_url}/api/rooms?building=DEMO_C")
    assert rooms_response.status == 200, rooms_response.text()
    assert {
        (item["room_no"], item["tower"], item["floor"])
        for item in rooms_response.json()
        if item["room_no"].startswith("XSH-E2E-")
    } == {
        ("XSH-E2E-A01", "整楼", ""),
        ("XSH-E2E-B01", "整楼", ""),
    }
    contracts_response = page.request.get(
        f"{base_url}/api/contracts?building=DEMO_C&page=1&page_size=25"
    )
    assert contracts_response.status == 200, contracts_response.text()
    assert [
        item["contract_no"]
        for item in contracts_response.json()["items"]
        if item["contract_no"].startswith("DEMO_C-E2E-")
    ] == ["DEMO_C-E2E-001"]

    page.goto(f"{base_url}/contracts?building=DEMO_C")
    expect(page.get_by_test_id("contracts-page")).to_be_visible()
    expect(page).to_have_url(re.compile(r"/contracts\?building=DEMO_C(?:&|$)"))
    contracts_page = page.get_by_test_id("contracts-page")
    expect(contracts_page).to_contain_text("DEMO_C-E2E-001")
    expect(contracts_page).to_contain_text("Phase 4 整楼模拟租户")
    contract_filter = page.get_by_test_id("contracts-tower-filter")
    assert contract_filter.locator("option").all_text_contents() == ["全部分区", "整楼"]

    page.get_by_role("button", name="新建合同").click()
    contract_form = page.get_by_test_id("contract-form-shell")
    expect(contract_form).to_be_visible()
    room_section = page.get_by_test_id("contract-form-section-rooms")
    room_zone_select = room_section.locator("select").first
    assert room_zone_select.locator("option").all_text_contents() == ["选择分区", "整楼"]
    expect(room_zone_select).to_have_value("整楼")
    for leaked_label in ("南塔", "北塔", "商业配套"):
        assert leaked_label not in contract_form.inner_text()
    if evidence_dir is not None:
        evidence_dir.mkdir(parents=True, exist_ok=True)
        page.screenshot(
            path=str(evidence_dir / "Phase4-新上海整楼合同表单-1440x960.png"),
            animations="disabled",
        )
    page.get_by_test_id("base-modal-close").click()
    expect(contract_form).to_have_count(0)

    page.get_by_test_id("nav-rooms").click()
    rooms_page = page.get_by_test_id("rooms-page")
    expect(rooms_page).to_be_visible()
    room_ledger = page.get_by_test_id("rooms-ledger-details")
    expect(room_ledger).to_be_visible()
    assert room_ledger.evaluate("node => node.open") is True
    expect(page.get_by_test_id("rooms-heatmap")).to_have_count(0)
    expect(page.get_by_test_id("rooms-segment-summary")).to_have_count(0)
    expect(page.get_by_test_id("rooms-priority-list")).to_have_count(0)
    expect(page.get_by_test_id("rooms-table")).to_contain_text("XSH-E2E-A01")
    expect(page.get_by_test_id("rooms-table")).to_contain_text("XSH-E2E-B01")
    room_filter = page.get_by_test_id("rooms-tower-filter")
    assert room_filter.locator("option").all_text_contents() == ["全部分区", "整楼"]
    for leaked_label in ("南塔", "北塔"):
        assert leaked_label not in rooms_page.inner_text()
    _assert_no_horizontal_overflow(page)
    if evidence_dir is not None:
        page.screenshot(
            path=str(evidence_dir / "Phase4-新上海整楼房源台账-1440x960.png"),
            animations="disabled",
            full_page=True,
        )

    with page.expect_response(
        lambda response: urlparse(response.url).path == "/api/rent-analytics"
        and "building=DEMO_C" in urlparse(response.url).query
        and response.status == 200
    ):
        page.get_by_test_id("nav-analytics").click()
    analytics_page = page.get_by_test_id("analytics-page")
    expect(analytics_page).to_be_visible()
    expect(page.get_by_test_id("analytics-load-error")).to_have_count(0)
    rent_segments = page.get_by_test_id("rent-timeline-segments")
    expect(rent_segments).to_be_visible()
    expect(rent_segments.locator("button")).to_have_count(1)
    expect(rent_segments.locator("button").first).to_have_text("整楼")

    analytics_details = page.locator("details.analytics-secondary-details")
    analytics_details.locator("summary").click()
    assert analytics_details.evaluate("node => node.open") is True
    tower_summary = page.get_by_test_id("analytics-tower-summary")
    expect(tower_summary.locator("article")).to_have_count(1)
    expect(tower_summary.locator("article").first).to_contain_text("整楼")
    expect(page.get_by_test_id("rooms-heatmap")).to_have_count(0)
    for leaked_label in ("南塔", "北塔", "商业配套"):
        assert leaked_label not in analytics_page.inner_text()
    _assert_no_horizontal_overflow(page)
    if evidence_dir is not None:
        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(150)
        page.screenshot(
            path=str(evidence_dir / "Phase4-新上海整楼经营分析-1440x960.png"),
            animations="disabled",
            full_page=True,
        )

    page.set_viewport_size({"width": 390, "height": 844})
    expect(analytics_page).to_be_visible()
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(150)
    _assert_no_horizontal_overflow(page)
    if evidence_dir is not None:
        page.screenshot(
            path=str(evidence_dir / "Phase4-新上海整楼经营分析-390x844.png"),
            animations="disabled",
        )

    assert page_errors == []
    assert failed_detail_responses == []
    expect(page.locator(".toast-stack .toast")).to_have_count(0, timeout=6000)


def test_registration_requires_matching_password_confirmation(fresh_browser_page):
    page, base_url = fresh_browser_page
    submissions = []
    page.route("**/api/auth/status", lambda route: route.fulfill(json={"need_init": not submissions, "user_count": len(submissions)}))
    def register(route):
        submissions.append(route.request.post_data_json)
        route.fulfill(json={"message": "创建成功"})
    page.route("**/api/auth/register", register)
    page.goto(base_url)
    page.get_by_test_id("login-username").fill("confirm.tester")
    page.get_by_test_id("login-password").fill(E2E_ADMIN_PASSWORD)
    page.get_by_test_id("register-submit").click()
    assert not submissions
    confirmation = page.get_by_test_id("login-confirm-password")
    confirmation.fill(E2E_ADMIN_PASSWORD + "x")
    page.get_by_test_id("register-submit").click()
    expect(page.get_by_role("alert")).to_have_text("两次输入的密码不一致")
    expect(confirmation).to_be_focused()
    assert not submissions
    page.set_viewport_size({"width": 390, "height": 844})
    assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
    confirmation.fill(E2E_ADMIN_PASSWORD)
    expect(page.get_by_role("alert")).to_have_count(0)
    page.get_by_test_id("register-submit").click()
    expect(page.get_by_test_id("login-submit")).to_be_visible()
    expect(confirmation).to_have_count(0)
    assert len(submissions) == 1
    assert set(submissions[0]) == {"username", "password", "display_name"}


def test_admin_account_requires_matching_password_confirmation(fresh_browser_page):
    page, base_url = fresh_browser_page
    _login(page, base_url)
    page.get_by_role("button", name="工具中心", exact=True).first.click()
    account = page.locator(".admin-subsection").filter(has=page.get_by_role("button", name="创建账号", exact=True))
    submissions = []
    def create_user(route):
        submissions.append(route.request.post_data_json)
        route.fulfill(json={"message": "创建成功"})
    page.route("**/api/auth/create-user", create_user)
    account.get_by_label(re.compile("^用户名")).fill("confirm.tester")
    account.get_by_label("密码", exact=True).fill(E2E_ADMIN_PASSWORD)
    account.get_by_role("button", name="创建账号", exact=True).click()
    expect(account.get_by_role("alert")).to_have_text("请再次输入密码")
    confirmation = account.get_by_label(re.compile("^确认密码"))
    confirmation.fill(E2E_ADMIN_PASSWORD + "x")
    account.get_by_role("button", name="创建账号", exact=True).click()
    expect(account.get_by_role("alert")).to_have_text("两次输入的密码不一致")
    assert not submissions
    confirmation.fill(E2E_ADMIN_PASSWORD)
    account.get_by_role("button", name="创建账号", exact=True).click()
    expect(confirmation).to_have_value("")
    assert len(submissions) == 1
    assert set(submissions[0]) == {"username", "password", "display_name", "role"}

"""Real-browser regressions for the cockpit's permission-aware automatic tour."""
from __future__ import annotations

import os
import re
from urllib.parse import urlparse

import pytest

if os.environ.get("LEASE_E2E") != "1":
    pytest.skip("Set LEASE_E2E=1 to run browser E2E tests.", allow_module_level=True)

from playwright.sync_api import expect
import test_frontend_smoke as smoke
from test_unified_cockpit import _enter, _shot

pytestmark = pytest.mark.e2e
fresh_browser_page = smoke.fresh_browser_page


@pytest.fixture(scope="module")
def e2e_server():
    yield from smoke.e2e_server.__wrapped__()


@pytest.fixture(scope="module")
def e2e_browser():
    # Keep the synchronous Playwright loop local to this E2E module.
    yield from smoke.e2e_browser.__wrapped__()


@pytest.fixture(autouse=True)
def no_browser_errors(fresh_browser_page):
    page, _ = fresh_browser_page
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    yield
    assert errors == [], f"Unexpected browser errors: {errors}"


def _assert_topic(page, topic):
    for name in ("overview", "rent", "operations", "space", "decision"):
        expect(page.get_by_test_id(f"cockpit-topic-{name}")).to_have_attribute(
            "aria-pressed", "true" if name == topic else "false"
        )


def _enter_single_live(page, base_url):
    page.clock.install()
    _enter(page, f"{base_url}/?building=DEMO_A")
    selector = page.get_by_test_id("cockpit-building-select")
    expect(selector).to_have_value("DEMO_A")
    expect(selector.locator("option")).to_have_count(1)
    _assert_topic(page, "overview")
    page.clock.pause_at(page.evaluate("new Date(Date.now() + 1000).toISOString()"))


def test_single_building_tour_advances_immediately_and_every_twelve_seconds(fresh_browser_page):
    page, base_url = fresh_browser_page
    _enter_single_live(page, base_url)
    tour = page.get_by_test_id("cockpit-tour")
    status = page.get_by_test_id("cockpit-tour-status")

    tour.click()
    # The first click must show useful progress even when only one building exists.
    _assert_topic(page, "rent")
    expect(tour).to_have_attribute("aria-pressed", "true")
    expect(status).to_be_visible()
    expect(status).to_contain_text(re.compile(r"12\s*秒"))
    _shot(page, "驾驶舱自动巡览-单楼立即切页与倒计时-1920")

    page.clock.run_for(11000)
    _assert_topic(page, "rent")
    expect(status).to_contain_text(re.compile(r"1\s*秒"))
    page.clock.run_for(1000)
    _assert_topic(page, "operations")
    expect(status).to_contain_text(re.compile(r"12\s*秒"))
    page.clock.run_for(12000)
    _assert_topic(page, "space")
    page.clock.run_for(12000)
    _assert_topic(page, "overview")
    page.clock.run_for(12000)
    _assert_topic(page, "rent")
    expect(page.locator(".decision-workbench")).to_have_count(0)

    tour.click()
    expect(tour).to_have_attribute("aria-pressed", "false")
    page.clock.run_for(24000)
    _assert_topic(page, "rent")


def test_tour_pauses_for_interactions_resumes_next_view_and_cleans_up_on_exit(fresh_browser_page):
    page, base_url = fresh_browser_page
    _enter_single_live(page, base_url)
    tour = page.get_by_test_id("cockpit-tour")

    tour.click()
    _assert_topic(page, "rent")
    page.locator(".uc-brand").click()
    expect(tour).to_have_attribute("aria-pressed", "false")
    page.clock.run_for(24000)
    _assert_topic(page, "rent")

    tour.click()
    _assert_topic(page, "operations")
    page.locator(".uc-content").hover()
    page.mouse.wheel(0, 180)
    expect(tour).to_have_attribute("aria-pressed", "false")
    page.clock.run_for(24000)
    _assert_topic(page, "operations")

    tour.click()
    _assert_topic(page, "space")
    page.get_by_test_id("cockpit-topic-overview").click()
    expect(tour).to_have_attribute("aria-pressed", "false")
    page.clock.run_for(24000)
    _assert_topic(page, "overview")

    tour.click()
    _assert_topic(page, "rent")
    page.get_by_test_id("cockpit-exit").click()
    expect(page.get_by_test_id("unified-cockpit")).to_have_count(0)
    page.clock.run_for(36000)
    page.get_by_test_id("topbar-presentation-toggle").click()
    expect(page.get_by_test_id("unified-cockpit")).to_be_visible()
    expect(tour).to_have_attribute("aria-pressed", "false")
    _assert_topic(page, "overview")
    page.clock.run_for(36000)
    _assert_topic(page, "overview")

    # A fresh tour has one schedule; an old instance cannot move it a second time.
    tour.click()
    _assert_topic(page, "rent")
    page.clock.run_for(12000)
    _assert_topic(page, "operations")


DETAIL_PATH_PREFIXES = (
    "/api/rooms",
    "/api/contracts",
    "/api/customers",
    "/api/attachments",
    "/api/risks",
    "/api/rent-analytics",
    "/api/dashboard/operations",
    "/api/financial-planning",
)


def test_overview_only_leader_tours_buildings_without_detail_requests(fresh_browser_page):
    page, base_url = fresh_browser_page
    requests = []
    page.on("request", lambda request: requests.append(urlparse(request.url).path))
    page.clock.install()
    _enter(page, base_url, leader=True)
    page.clock.pause_at(page.evaluate("new Date(Date.now() + 1000).toISOString()"))

    selector = page.get_by_test_id("cockpit-building-select")
    allowed_codes = selector.locator("option").evaluate_all(
        "nodes => nodes.filter(node => node.value && !node.disabled).map(node => node.value)"
    )
    assert len(allowed_codes) >= 2, "This fixture must exercise multiple presented buildings."
    tour = page.get_by_test_id("cockpit-tour")
    tour.click()
    expect(tour).to_have_attribute("aria-pressed", "true")
    expect(page.get_by_test_id("cockpit-tour-status")).to_contain_text(re.compile(r"12\s*秒"))
    _assert_topic(page, "overview")
    assert selector.input_value() in allowed_codes

    visited_codes = {selector.input_value()}
    for _ in range(len(allowed_codes) + 1):
        page.clock.run_for(12000)
        _assert_topic(page, "overview")
        expect(tour).to_have_attribute("aria-pressed", "true")
        visited_codes.add(selector.input_value())
    assert set(allowed_codes) <= visited_codes
    _shot(page, "驾驶舱自动巡览-仅总览权限跨楼巡览-1920")
    expect(page.locator(".decision-workbench")).to_have_count(0)
    forbidden = DETAIL_PATH_PREFIXES + ("/api/operations/snapshot",)
    assert not [path for path in requests if path.startswith(forbidden)]


def test_single_snapshot_building_tours_overview_metrics_without_detail_requests(fresh_browser_page):
    page, base_url = fresh_browser_page
    requests = []
    page.on("request", lambda request: requests.append(urlparse(request.url).path))
    page.clock.install()
    page.set_viewport_size({"width": 1920, "height": 1080})
    page.goto(f"{base_url}/?building=DEMO_B")
    smoke._submit_login_form(
        page, smoke.E2E_MULTI_OPERATOR_USERNAME, smoke.E2E_MULTI_OPERATOR_PASSWORD
    )
    expect(page.get_by_test_id("snapshot-dashboard-summary")).to_be_visible(timeout=15000)
    page.get_by_test_id("topbar-presentation-toggle").click()
    expect(page.get_by_test_id("unified-cockpit")).to_be_visible()
    selector = page.get_by_test_id("cockpit-building-select")
    expect(selector).to_have_value("DEMO_B")
    expect(selector.locator("option")).to_have_count(1)
    _assert_topic(page, "overview")
    page.clock.pause_at(page.evaluate("new Date(Date.now() + 1000).toISOString()"))

    active_metric = page.locator(".uc-kpis button.active")
    expect(active_metric).to_have_count(1)
    first_metric = active_metric.get_attribute("data-testid")
    tour = page.get_by_test_id("cockpit-tour")
    tour.click()
    expect(tour).to_have_attribute("aria-pressed", "true")
    _assert_topic(page, "overview")
    expect(active_metric).not_to_have_attribute("data-testid", first_metric)
    previous_metric = active_metric.get_attribute("data-testid")

    for _ in range(4):
        page.clock.run_for(12000)
        _assert_topic(page, "overview")
        expect(selector).to_have_value("DEMO_B")
        expect(active_metric).not_to_have_attribute("data-testid", previous_metric)
        previous_metric = active_metric.get_attribute("data-testid")
    expect(page.get_by_test_id("rent-timeline-chart")).to_have_count(0)
    expect(page.locator(".uc-room")).to_have_count(0)
    expect(page.locator(".decision-workbench")).to_have_count(0)
    assert not [path for path in requests if path.startswith(DETAIL_PATH_PREFIXES)]

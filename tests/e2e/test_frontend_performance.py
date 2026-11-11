"""Browser checks for request reuse, hidden drafts and background presentation."""
from __future__ import annotations

import os
import re
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

if os.environ.get("LEASE_E2E") != "1":
    pytest.skip("Set LEASE_E2E=1 to run browser E2E tests.", allow_module_level=True)

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, expect
import test_frontend_smoke as smoke
from test_unified_cockpit import _enter, _ready_planning, _topic

pytestmark = pytest.mark.e2e
fresh_browser_page = smoke.fresh_browser_page


@pytest.fixture(scope="module")
def e2e_server():
    yield from smoke.e2e_server.__wrapped__()


@pytest.fixture(scope="module")
def e2e_browser():
    # Each E2E module must close its own synchronous Playwright loop.
    yield from smoke.e2e_browser.__wrapped__()


@pytest.fixture(autouse=True)
def no_browser_errors(fresh_browser_page):
    page, _ = fresh_browser_page
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    yield
    assert errors == [], f"Unexpected browser errors: {errors}"


def _is_snapshot(response, building="DEMO_A"):
    parsed = urlparse(response.url)
    return (
        parsed.path == "/api/operations/snapshot"
        and parse_qs(parsed.query).get("building") == [building]
        and response.status == 200
    )


def _is_authorization(response):
    return urlparse(response.url).path == "/api/auth/me" and response.status == 200


def _expect_topic(page, topic):
    expect(page.get_by_test_id(f"cockpit-topic-{topic}")).to_have_attribute("aria-pressed", "true")


def _set_hidden(page, hidden):
    page.evaluate(
        """hidden => {
            Object.defineProperty(document, 'hidden', {configurable: true, get: () => hidden});
            Object.defineProperty(document, 'visibilityState', {
                configurable: true, get: () => hidden ? 'hidden' : 'visible'
            });
            document.dispatchEvent(new Event('visibilitychange'));
        }""",
        hidden,
    )


def test_cockpit_rent_dpr_changes_preserve_range_kpis_and_high_resolution_export(fresh_browser_page):
    page, base_url = fresh_browser_page
    _enter(page, base_url)
    page.get_by_test_id("cockpit-building-select").select_option("DEMO_A")
    _topic(page, "rent")
    start = page.get_by_test_id("rent-range-start")
    end = page.get_by_test_id("rent-range-end")
    chart = page.get_by_test_id("rent-timeline-chart")
    kpis = page.get_by_test_id("rent-timeline-kpis")
    expect(start).to_be_visible(timeout=15000)
    expect(kpis).to_be_visible()

    def expect_pixel_ratio(ratio):
        # Use the displayed dimensions, so CSS zoom cannot hide extra canvas sampling.
        try:
            page.wait_for_function(
                """ratio => {
                    const panel = document.querySelector('[data-testid="rent-timeline-chart"]');
                    const canvas = panel?.querySelector('canvas');
                    if (!canvas) return false;
                    const bounds = canvas.getBoundingClientRect();
                    return bounds.width > 100 && bounds.height > 100
                        && Math.abs(bounds.width - panel.getBoundingClientRect().width) < 1
                        && Math.abs(window.devicePixelRatio - ratio) < 0.01
                        && Math.abs(canvas.width / bounds.width - ratio) < 0.01
                        && Math.abs(canvas.height / bounds.height - ratio) < 0.01;
                }""",
                arg=ratio,
                timeout=15000,
            )
        except PlaywrightTimeoutError as error:
            actual = chart.evaluate("""panel => {
                const canvas = panel.querySelector('canvas');
                const rect = canvas?.getBoundingClientRect();
                return {dpr: devicePixelRatio, viewport: [innerWidth, innerHeight],
                    visibility: document.visibilityState, focused: document.hasFocus(),
                    panelWidth: panel.getBoundingClientRect().width,
                    canvas: canvas ? {width: canvas.width, height: canvas.height,
                        cssWidth: rect.width, cssHeight: rect.height,
                        ratioX: canvas.width / rect.width, ratioY: canvas.height / rect.height} : null};
            }""")
            raise AssertionError(f"Expected rent canvas DPR {ratio}; actual: {actual}") from error
        smoke._assert_chart_canvas_nonblank(chart)

    expect_pixel_ratio(1)
    minimum = date.fromisoformat(start.get_attribute("min"))
    maximum = date.fromisoformat(start.get_attribute("max"))
    span = (maximum - minimum).days
    assert span >= 4, "rent fixture must provide a genuine non-full date window"
    selected_start = (minimum + timedelta(days=span // 4)).isoformat()
    selected_end = (minimum + timedelta(days=span * 3 // 4)).isoformat()
    start.fill(selected_start)
    start.press("Tab")
    end.fill(selected_end)
    end.press("Tab")
    expect(start).to_have_value(selected_start)
    expect(end).to_have_value(selected_end)
    expect(page.locator(".rent-range-error")).to_have_count(0)
    # ECharts replaces the component's initial aria-label with option.aria.description.
    # Check the selected dates rather than depending on either layer's surrounding prose.
    selected_range_label = re.compile(re.escape(f"{selected_start} 至 {selected_end}"))
    expect(chart).to_have_attribute("aria-label", selected_range_label)
    expect(page.locator(".rent-timeline-status-row")).to_contain_text(
        f"已更新为 {selected_start} 至 {selected_end}"
    )
    expected_kpis = kpis.inner_text()
    assert "应计" in expected_kpis and "全量合同数据覆盖率" in expected_kpis

    def expect_selection():
        expect(start).to_have_value(selected_start)
        expect(end).to_have_value(selected_end)
        expect(kpis).to_have_text(expected_kpis, use_inner_text=True)
        expect(chart).to_have_attribute("aria-label", selected_range_label)

    cdp = page.context.new_cdp_session(page)
    try:
        for ratio in (1.5, 2, 1):
            # CDP can change devicePixelRatio without emitting resolution/resize events
            # when dimensions stay fixed. A real 1 px layout change exercises the resize
            # recovery path; this is not a hardware test across physical Windows displays.
            cdp.send("Emulation.setDeviceMetricsOverride", {
                "width": 1919, "height": 1080, "deviceScaleFactor": ratio, "mobile": False,
            })
            expect_pixel_ratio(ratio)
            cdp.send("Emulation.setDeviceMetricsOverride", {
                "width": 1920, "height": 1080, "deviceScaleFactor": ratio, "mobile": False,
            })
            expect_pixel_ratio(ratio)
            expect_selection()
            _topic(page, "overview")
            expect(chart).to_have_count(0)
            _topic(page, "rent")
            expect_pixel_ratio(ratio)
            expect_selection()

        # The composed PNG has its own size limits. Record the real chart export canvas
        # before composition instead of inferring its sampling from the final PNG size.
        logical_size = chart.locator("canvas").first.evaluate(
            "canvas => ({width: canvas.clientWidth, height: canvas.clientHeight})"
        )
        page.evaluate("""() => {
            window.__rentExportCanvases = [];
            window.__rentOriginalCanvasDataURL = HTMLCanvasElement.prototype.toDataURL;
            HTMLCanvasElement.prototype.toDataURL = function (...args) {
                window.__rentExportCanvases.push({width: this.width, height: this.height});
                return window.__rentOriginalCanvasDataURL.apply(this, args);
            };
        }""")
        try:
            with page.expect_download(timeout=30000) as download_info:
                page.get_by_test_id("rent-timeline-export-png").click()
            png_download = download_info.value
            assert png_download.suggested_filename == f"有效平均日租_{selected_start}_{selected_end}.png"
            assert Path(png_download.path()).read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
            export_canvases = page.evaluate("window.__rentExportCanvases")
            assert any(
                abs(canvas["width"] - logical_size["width"] * 2) <= 2
                and abs(canvas["height"] - logical_size["height"] * 2) <= 2
                for canvas in export_canvases
            ), f"DPR 1 display must still export the chart at 2x resolution: {export_canvases}"
        finally:
            page.evaluate("""() => {
                HTMLCanvasElement.prototype.toDataURL = window.__rentOriginalCanvasDataURL;
                delete window.__rentOriginalCanvasDataURL;
                delete window.__rentExportCanvases;
            }""")
        expect_selection()
        expect_pixel_ratio(1)
    finally:
        cdp.send("Emulation.clearDeviceMetricsOverride")
        cdp.detach()


def test_operations_reuses_building_snapshot_and_room_data_across_views(fresh_browser_page):
    page, base_url = fresh_browser_page
    page.clock.install()
    page.set_viewport_size({"width": 1920, "height": 1080})
    page.goto(f"{base_url}/?building=DEMO_A")
    with page.expect_response(_is_snapshot):
        smoke._submit_login_form(page, smoke.E2E_HYBRID_USERNAME, smoke.E2E_HYBRID_PASSWORD)
    expect(page.get_by_test_id("topbar-presentation-toggle")).to_be_visible(timeout=15000)

    requests = []
    page.on("request", lambda request: requests.append(urlparse(request.url).path))
    with page.expect_response(_is_snapshot):
        page.get_by_test_id("topbar-presentation-toggle").click()
    expect(page.get_by_test_id("unified-cockpit")).to_be_visible()
    page.clock.pause_at(page.evaluate("new Date(Date.now() + 1000).toISOString()"))
    _topic(page, "operations")
    customer = page.get_by_test_id("analytics-customer-drilldown-0")
    expect(customer).to_be_visible(timeout=15000)
    expect(page.get_by_test_id("analytics-customer-rank")).to_contain_text("端到端测试客户")
    # Entry refreshes the source once; the cockpit consumes that same result.
    assert requests.count("/api/operations/snapshot") == 1
    assert not [path for path in requests if path.startswith(("/api/dashboard/operations", "/api/rooms"))]

    _topic(page, "overview")
    page.get_by_test_id("cockpit-tour").click()
    _expect_topic(page, "rent")
    expect(page.get_by_test_id("rent-range-start")).to_be_visible(timeout=15000)
    page.clock.run_for(12000)
    _expect_topic(page, "operations")
    expect(customer).to_be_visible()
    page.get_by_test_id("cockpit-tour").click()
    assert requests.count("/api/operations/snapshot") == 1
    assert not [path for path in requests if path.startswith(("/api/dashboard/operations", "/api/rooms"))]

    _topic(page, "space")
    expect(page.locator(".uc-room")).to_have_count(2, timeout=15000)
    assert requests.count("/api/rooms") == 1
    _topic(page, "operations")
    expect(customer).to_be_visible()
    _topic(page, "space")
    expect(page.locator(".uc-room")).to_have_count(2)
    expect(page.locator(".uc-room").filter(has_text="E0301")).to_be_visible()
    expect(page.locator(".uc-room").filter(has_text="P0201")).to_be_visible()
    assert requests.count("/api/rooms") == 1
    assert requests.count("/api/operations/snapshot") == 1
    assert not [path for path in requests if path.startswith("/api/dashboard/operations")]


def test_hidden_budget_and_scenario_drafts_survive_tour_and_building_switches(fresh_browser_page):
    page, base_url = fresh_browser_page
    page.clock.install()
    _enter(page, base_url)
    page.clock.pause_at(page.evaluate("new Date(Date.now() + 1000).toISOString()"))
    selector = page.get_by_test_id("cockpit-building-select")
    selector.select_option("DEMO_A")
    _topic(page, "decision")
    expect(page.get_by_test_id("planning-year")).to_be_enabled(timeout=15000)
    page.get_by_test_id("planning-year").fill("2031")
    page.get_by_test_id("planning-year").press("Tab")
    expect(page.get_by_test_id("planning-freeze-budget")).to_be_enabled(timeout=15000)
    page.get_by_test_id("planning-annual-target").fill("4321")
    page.get_by_test_id("planning-budget-revenue-1").fill("123.45")
    page.get_by_test_id("planning-name").fill("未保存的月度预算")
    expect(page.get_by_test_id("planning-save-status")).to_contain_text("未保存")

    planning_requests = []
    page.on("request", lambda request: planning_requests.append(urlparse(request.url).path)
            if urlparse(request.url).path.startswith("/api/financial-planning") else None)
    _topic(page, "overview")
    expect(page.locator(".decision-workbench")).to_be_hidden()
    page.get_by_test_id("cockpit-tour").click()
    page.clock.run_for(24000)
    _expect_topic(page, "space")
    selector.select_option("DEMO_B")
    expect(selector).to_have_value("DEMO_B")
    selector.select_option("DEMO_A")
    _topic(page, "decision")
    expect(page.get_by_test_id("planning-name")).to_have_value("未保存的月度预算")
    expect(page.get_by_test_id("planning-annual-target")).to_have_value("4321")
    expect(page.get_by_test_id("planning-budget-revenue-1")).to_have_value("123.45")
    expect(page.get_by_test_id("planning-save-status")).to_contain_text("未保存")
    assert planning_requests == []

    _ready_planning(page)
    rent = page.get_by_role("slider", name="调整月租，万元", exact=True)
    expect(rent).to_be_visible()
    rent.focus()
    rent.press("ArrowRight")
    changed_rent = rent.input_value()
    page.get_by_test_id("planning-name").fill("未保存的招商方案")
    expect(page.get_by_test_id("planning-save-status")).to_contain_text("未保存")
    expect(page.get_by_test_id("planning-refresh")).to_be_enabled()
    planning_requests.clear()

    _topic(page, "overview")
    selector.select_option("DEMO_B")
    page.get_by_test_id("cockpit-tour").click()
    page.clock.run_for(12000)
    selector.select_option("DEMO_A")
    _topic(page, "decision")
    expect(page.get_by_test_id("planning-name")).to_have_value("未保存的招商方案")
    expect(rent).to_have_value(changed_rent)
    expect(page.get_by_test_id("planning-save-status")).to_contain_text("未保存")
    assert planning_requests == []
    page.get_by_role("tab", name="月度预算", exact=True).click()
    expect(page.get_by_test_id("planning-budget-revenue-1")).to_have_value("123.45")


def test_background_presentation_keeps_authorization_and_refreshes_on_return(fresh_browser_page):
    page, base_url = fresh_browser_page
    page.clock.install()
    _enter(page, f"{base_url}/?building=DEMO_A")
    page.clock.pause_at(page.evaluate("new Date(Date.now() + 1000).toISOString()"))
    page.get_by_test_id("cockpit-tour").click()
    _expect_topic(page, "rent")
    expect(page.get_by_test_id("rent-range-start")).to_be_visible(timeout=15000)

    def updated_snapshot(route):
        response = route.fetch()
        payload = response.json()
        payload["metrics"]["occupancy.area_rate"]["value"] = 61.2
        route.fulfill(response=response, json=payload)

    page.route("**/api/operations/snapshot?*", updated_snapshot)
    requests = []
    page.on("request", lambda request: requests.append(urlparse(request.url).path))
    _set_hidden(page, True)
    with page.expect_response(_is_authorization):
        page.clock.run_for(31000)
    # Settle the completed authorization response without advancing the tour clock.
    page.wait_for_load_state("networkidle")
    assert "/api/auth/me" in requests
    assert not [path for path in requests if path in (
        "/api/operations/snapshot", "/api/operations/portfolio-overview"
    )]
    _expect_topic(page, "rent")
    expect(page.get_by_test_id("cockpit-tour")).to_have_attribute("aria-pressed", "true")

    requests.clear()
    with page.expect_response(_is_authorization):
        with page.expect_response(_is_snapshot) as refreshed_snapshot:
            _set_hidden(page, False)
    assert refreshed_snapshot.value.json()["metrics"]["occupancy.area_rate"]["value"] == 61.2
    _expect_topic(page, "rent")
    expect(page.get_by_test_id("cockpit-tour")).to_have_attribute("aria-pressed", "true")
    _topic(page, "overview")
    expect(page.get_by_test_id("cockpit-metric-occupancy.area_rate")).to_contain_text("61.2", timeout=15000)
    assert requests.index("/api/auth/me") < requests.index("/api/operations/snapshot")
    assert requests.count("/api/operations/snapshot") == 1

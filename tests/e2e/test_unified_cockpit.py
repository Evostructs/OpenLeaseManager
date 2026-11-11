"""Browser regression for the shared cockpit and monthly decision workbench."""
from __future__ import annotations

import os
from pathlib import Path
import re
from urllib.parse import urlparse

import pytest

if os.environ.get("LEASE_E2E") != "1":
    pytest.skip("Set LEASE_E2E=1 to run browser E2E tests.", allow_module_level=True)

from playwright.sync_api import expect
import test_frontend_smoke as smoke

pytestmark = pytest.mark.e2e
fresh_browser_page = smoke.fresh_browser_page


@pytest.fixture(scope="module")
def e2e_server():
    yield from smoke.e2e_server.__wrapped__()


@pytest.fixture(scope="module")
def e2e_browser():
    # Close this module's synchronous Playwright loop before other E2E modules start.
    yield from smoke.e2e_browser.__wrapped__()


def _enter(page, base_url, *, leader=False):
    page.set_viewport_size({"width": 1920, "height": 1080})
    page.goto(base_url)
    smoke._submit_login_form(
        page,
        smoke.E2E_OVERVIEW_LEADER_USERNAME if leader else smoke.E2E_HYBRID_USERNAME,
        smoke.E2E_OVERVIEW_LEADER_PASSWORD if leader else smoke.E2E_HYBRID_PASSWORD,
    )
    expect(page.get_by_test_id("topbar-presentation-toggle")).to_be_visible(timeout=15000)
    page.get_by_test_id("topbar-presentation-toggle").click()
    expect(page.get_by_test_id("unified-cockpit")).to_be_visible()


def _shot(page, name):
    evidence = os.environ.get("LEASE_E2E_EVIDENCE_DIR")
    if evidence:
        folder = Path(evidence)
        folder.mkdir(parents=True, exist_ok=True)
        page.wait_for_timeout(750)
        page.screenshot(path=str(folder / f"{name}.png"))


def _topic(page, name):
    page.get_by_test_id(f"cockpit-topic-{name}").click()
    page.locator(".uc-content").evaluate("el => el.scrollTop = 0")


def _ready_planning(page):
    expect(page.get_by_test_id("planning-refresh")).to_be_enabled(timeout=15000)
    if page.get_by_test_id("planning-freeze-budget").is_visible():
        page.get_by_test_id("planning-freeze-budget").click()
        expect(page.get_by_test_id("planning-notice")).to_contain_text("已保存并冻结", timeout=15000)
    page.get_by_role("tab", name=re.compile("经营措施")).click()
    page.get_by_label("启用新增租约", exact=True).check()
    page.get_by_test_id("planning-measures-confirm").check()


def _fill_strategy(page):
    page.locator(".dw-strategy-context select").select_option("own")
    page.get_by_label("纳入资产出售路径", exact=True).check()
    page.get_by_role("spinbutton", name="统一比较期间（年）", exact=True).fill("5")
    page.get_by_role("spinbutton", name="年折现率（%）", exact=True).fill("8")
    for group_index, values in enumerate(([1200, 2, 18000, 2], [1000, 1500, 2, 200, 21000, 2], [18000, 300])):
        inputs = page.locator(".dw-strategy-inputs > section").nth(group_index).locator("input")
        for index, value in enumerate(values):
            inputs.nth(index).fill(str(value))


def test_cockpit_live_drilldown_range_and_scope(fresh_browser_page):
    page, base_url = fresh_browser_page
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    _enter(page, base_url)
    original_url = page.url
    expect(page.locator(".uc-building")).to_have_count(5)
    _shot(page, "驾驶舱-经营总览-1920")
    page.get_by_test_id("cockpit-building-select").select_option("DEMO_A")
    _topic(page, "rent")
    start = page.get_by_test_id("rent-range-start")
    end = page.get_by_test_id("rent-range-end")
    expect(start).to_be_visible(timeout=15000)
    start.fill("2026-03-01")
    start.press("Tab")
    end.fill("2026-09-30")
    end.press("Tab")
    expect(start).to_have_value("2026-03-01")
    expect(end).to_have_value("2026-09-30")
    chart = page.get_by_test_id("rent-timeline-chart")
    smoke._assert_chart_canvas_nonblank(chart)
    chart.scroll_into_view_if_needed()
    bounds = chart.bounding_box()
    slider_y = bounds["y"] + bounds["height"] - 22
    slider_x = bounds["x"] + bounds["width"] * 0.5
    page.mouse.move(slider_x, slider_y)
    page.mouse.down()
    page.mouse.move(slider_x + 90, slider_y, steps=12)
    page.mouse.up()
    expect(start).not_to_have_value("2026-03-01")
    selected_start, selected_end = start.input_value(), end.input_value()
    page.locator(".uc-content").evaluate("el => el.scrollTop = 0")
    _shot(page, "驾驶舱-租金范围-1920")
    _topic(page, "operations")
    customer = page.get_by_test_id("analytics-customer-drilldown-0")
    expect(customer).to_be_visible(timeout=15000)
    customer.click()
    detail = page.get_by_test_id("cockpit-detail")
    expect(detail).to_contain_text("210.5")
    expect(detail).to_contain_text("月租金")
    page.get_by_role("button", name="关闭", exact=True).click()
    expect(page.get_by_test_id("cockpit-topic-operations")).to_have_attribute("aria-pressed", "true")
    page.get_by_text("分区表现 / 面积去化", exact=True).click()
    page.get_by_test_id("analytics-tower-rooms-南塔办公").scroll_into_view_if_needed()
    _shot(page, "驾驶舱-经营结构下钻-1920")
    page.get_by_test_id("analytics-tower-rooms-南塔办公").click()
    segment = page.get_by_test_id("cockpit-room-segment")
    south_value = segment.get_by_role("option", name="南塔办公", exact=True).get_attribute("value")
    expect(segment).to_have_value(south_value)
    expect(page.locator(".uc-room")).to_have_count(1)
    expect(page.locator(".uc-room")).to_contain_text("E0301")
    page.locator(".uc-room").click()
    expect(detail).to_contain_text("端到端测试客户")
    detail.get_by_role("button", name=re.compile("打开关联合同")).click()
    expect(detail).to_contain_text("2026-01-01")
    page.get_by_role("button", name="关闭", exact=True).click()
    page.get_by_test_id("cockpit-room-segment").select_option("all")
    page.get_by_test_id("cockpit-room-use").select_option("retail")
    expect(page.locator(".uc-room")).to_have_count(1)
    expect(page.locator(".uc-room")).to_contain_text("P0201")
    _shot(page, "驾驶舱-商业房源-1920")
    _topic(page, "rent")
    expect(start).to_have_value(selected_start)
    expect(end).to_have_value(selected_end)
    assert page.url == original_url
    page.get_by_test_id("cockpit-exit").click()
    expect(page.get_by_test_id("unified-cockpit")).to_have_count(0)
    assert page.url == original_url
    assert errors == []


def test_planning_historical_budget_stays_associated(fresh_browser_page):
    page, base_url = fresh_browser_page
    _enter(page, base_url)
    page.get_by_test_id("cockpit-building-select").select_option("DEMO_A")
    _topic(page, "decision")
    expect(page.get_by_test_id("planning-year")).to_be_enabled(timeout=15000)
    page.get_by_test_id("planning-year").fill("2028")
    page.get_by_test_id("planning-year").press("Tab")
    expect(page.get_by_test_id("planning-freeze-budget")).to_be_enabled(timeout=15000)
    page.get_by_test_id("planning-annual-target").fill("1000")
    page.get_by_test_id("planning-budget-revenue-1").fill("100")
    page.get_by_test_id("planning-name").fill("历史预算1000")
    page.get_by_test_id("planning-freeze-budget").click()
    expect(page.get_by_test_id("planning-notice")).to_contain_text("已保存并冻结", timeout=15000)
    historical = _save_planning_version(page, "历史方案原预算")
    page.get_by_role("tab", name="月度预算", exact=True).click()
    page.get_by_role("button", name="编制新预算版本", exact=True).click()
    page.get_by_test_id("planning-annual-target").fill("2000")
    page.get_by_test_id("planning-budget-revenue-1").fill("200")
    page.get_by_test_id("planning-name").fill("当前预算2000")
    page.get_by_test_id("planning-freeze-budget").click()
    expect(page.get_by_test_id("planning-notice")).to_contain_text("已保存并冻结", timeout=15000)
    page.get_by_test_id("planning-version-select").select_option(str(historical["id"]))
    expect(page.get_by_test_id("planning-name")).to_have_value("历史方案原预算", timeout=15000)
    target = page.locator(".dw-outcome-strip > div").first
    expect(target).to_contain_text("1,000.0")
    expect(page.get_by_test_id("planning-use-current-budget")).to_be_visible()
    page.get_by_test_id("planning-refresh").click()
    expect(page.get_by_test_id("planning-refresh")).to_be_enabled(timeout=15000)
    expect(target).to_contain_text("1,000.0")
    page.get_by_role("tab", name="月度预算", exact=True).click()
    expect(page.get_by_test_id("planning-budget-revenue-1")).to_have_value("100")
    page.get_by_test_id("planning-use-current-budget").click()
    expect(target).to_contain_text("2,000.0", timeout=15000)
    expect(page.get_by_test_id("planning-budget-revenue-1")).to_have_value("200")
    expect(page.get_by_test_id("planning-save-status")).to_contain_text("未保存")
    _shot(page, "业财联动-历史预算显式切换-1920")


def test_cockpit_refreshes_selected_building_without_resetting_budget(fresh_browser_page):
    page, base_url = fresh_browser_page
    page.clock.install()
    _enter(page, base_url)
    page.get_by_test_id("cockpit-building-select").select_option("DEMO_A")
    _topic(page, "decision")
    _ready_planning(page)
    rent = page.get_by_role("slider", name="调整月租，万元", exact=True)
    expect(rent).to_be_visible(timeout=15000)
    rent.focus()
    rent.press("ArrowRight")
    changed_rent = rent.input_value()

    def updated_snapshot(route):
        response = route.fetch()
        payload = response.json()
        payload["metrics"]["occupancy.area_rate"]["value"] = 61.2
        route.fulfill(response=response, json=payload)

    page.route("**/api/operations/snapshot?building=DEMO_A", updated_snapshot)
    page.clock.fast_forward(31000)
    _topic(page, "overview")
    expect(page.get_by_test_id("cockpit-metric-occupancy.area_rate")).to_contain_text("61.2", timeout=15000)
    _topic(page, "decision")
    expect(rent).to_have_value(changed_rent)


def test_cockpit_budget_scenarios_and_strategy(fresh_browser_page):
    page, base_url = fresh_browser_page
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    _enter(page, base_url)
    page.get_by_test_id("cockpit-building-select").select_option("DEMO_A")
    _topic(page, "decision")
    workbench = page.locator(".decision-workbench")
    expect(workbench).to_be_visible(timeout=15000)
    _ready_planning(page)
    rent = page.get_by_role("slider", name="调整月租，万元", exact=True)
    expect(rent).to_be_visible()
    initial_rent = rent.input_value()
    baseline = page.locator(".dw-outcome-strip > div").nth(1).locator("strong")
    baseline_before = baseline.inner_text()
    page.get_by_test_id("planning-name").fill("原定招商")
    page.get_by_test_id("planning-save-scenario").click()
    expect(page.get_by_test_id("planning-notice")).to_contain_text("已保存到服务器", timeout=15000)
    rent.focus()
    rent.press("ArrowRight")
    rent.press("ArrowRight")
    assert float(rent.input_value()) == float(initial_rent) + 1
    expect(baseline).to_have_text(baseline_before)
    page.get_by_test_id("planning-name").fill("调租方案")
    page.get_by_test_id("planning-save-forecast").click()
    expect(page.get_by_test_id("planning-notice")).to_contain_text("已保存并采用", timeout=15000)
    comparison = page.locator(".dw-plan-comparison")
    comparison.get_by_role("button", name="原定招商", exact=True).click()
    comparison.get_by_role("button", name=re.compile("^调租方案")).click()
    rows = comparison.locator(".dw-comparison-table tbody tr")
    expect(rows).to_have_count(2)
    first = rows.nth(0).locator("td").first.inner_text()
    second = rows.nth(1).locator("td").first.inner_text()
    assert first != second
    _topic(page, "decision")
    _shot(page, "驾驶舱-经营措施-1920")
    page.get_by_role("tab", name="月度预算", exact=True).click()
    january = page.get_by_role("spinbutton", name="1月预算收入，万元", exact=True)
    expect(january).to_be_disabled()
    january_before = january.input_value()
    page.get_by_role("tab", name=re.compile("经营措施")).click()
    page.get_by_test_id("cockpit-building-select").select_option("DEMO_B")
    expect(page.get_by_test_id("planning-version-select")).to_have_count(0)
    expect(page.get_by_test_id("planning-scope-unavailable")).to_be_visible()
    page.get_by_test_id("cockpit-building-select").select_option("DEMO_A")
    expect(page.get_by_test_id("planning-name")).to_have_value("调租方案", timeout=15000)
    expect(rent).to_have_value(str(float(initial_rent) + 1).removesuffix(".0"))
    page.get_by_role("tab", name="月度预算", exact=True).click()
    expect(january).to_have_value(january_before)
    _shot(page, "驾驶舱-月度预算-1920")
    page.get_by_role("tab", name="持有与项目策略", exact=True).click()
    _fill_strategy(page)
    expect(page.locator(".dw-strategy-totals > div")).to_have_count(3)
    sale_value = page.locator(".dw-strategy-totals > div").nth(2).locator("strong")
    before_sale = sale_value.inner_text()
    page.get_by_role("spinbutton", name="当前预计出售价格（万元）", exact=True).fill("20000")
    expect(sale_value).not_to_have_text(before_sale)
    _shot(page, "驾驶舱-资产策略-1920")
    page.set_viewport_size({"width": 390, "height": 844})
    page.locator(".uc-content").evaluate("el => el.scrollTop = 0")
    _shot(page, "驾驶舱-资产策略-390")
    overflow = page.get_by_test_id("unified-cockpit").evaluate("el => el.scrollWidth - el.clientWidth")
    assert overflow <= 2
    _topic(page, "overview")
    assert page.get_by_test_id("unified-cockpit").evaluate("el => el.scrollWidth - el.clientWidth") <= 2
    _shot(page, "驾驶舱-经营总览-390")
    assert errors == []


def test_cockpit_leader_permissions_and_tour_pause(fresh_browser_page):
    page, base_url = fresh_browser_page
    _enter(page, base_url, leader=True)
    requests = []
    page.on("request", lambda request: requests.append(urlparse(request.url).path))
    page.get_by_test_id("cockpit-building-select").select_option("DEMO_A")
    for name in ("rent", "operations", "space", "decision"):
        _topic(page, name)
    expect(page.get_by_test_id("planning-scope-unavailable")).to_be_visible(timeout=15000)
    forbidden = ("/api/rooms", "/api/contracts", "/api/rent-analytics", "/api/dashboard/operations", "/api/financial-planning")
    assert not [path for path in requests if any(path.startswith(prefix) for prefix in forbidden)]
    assert "/api/operations/snapshot" not in requests
    tour = page.get_by_test_id("cockpit-tour")
    tour.click()
    expect(tour).to_have_attribute("aria-pressed", "true")
    page.locator(".uc-content").hover()
    page.mouse.wheel(0, 180)
    expect(tour).to_have_attribute("aria-pressed", "false")
    tour.click()
    expect(tour).to_have_attribute("aria-pressed", "true")
    page.get_by_test_id("cockpit-topic-rent").click()
    expect(tour).to_have_attribute("aria-pressed", "false")
    page.get_by_test_id("cockpit-exit").click()
    expect(page.get_by_test_id("unified-cockpit")).to_have_count(0)


def _save_planning_version(page, name, *, scenario=False):
    page.get_by_test_id("planning-name").fill(name)
    with page.expect_response(lambda response: "/api/financial-planning/versions?" in response.url
                              and response.request.method == "POST") as captured:
        page.get_by_test_id("planning-save-scenario" if scenario else "planning-save-forecast").click()
    response = captured.value
    assert response.status == 200, response.text()
    expect(page.get_by_test_id("planning-notice")).to_contain_text("已保存", timeout=15000)
    expect(page.get_by_test_id("planning-refresh")).to_be_enabled()
    return response.json()["version"]


def test_planning_saved_receipt_survives_comparison_read_failure(fresh_browser_page):
    page, base_url = fresh_browser_page
    _enter(page, base_url)
    page.get_by_test_id("cockpit-building-select").select_option("DEMO_A")
    _topic(page, "decision")
    _ready_planning(page)
    page.route("**/api/financial-planning/compare?*", lambda route: route.fulfill(
        status=503, content_type="application/json", body='{"detail":"Comparison temporarily unavailable"}'))
    saved = _save_planning_version(page, "保存回执检查")
    expect(page.get_by_test_id("planning-notice")).to_contain_text("已保存并采用")
    expect(page.get_by_test_id("planning-error")).to_contain_text("版本已保存，差异明细暂未读取成功")
    page.unroute("**/api/financial-planning/compare?*")
    page.get_by_test_id("planning-version-select").select_option(str(saved["id"]))
    expect(page.get_by_test_id("planning-notice")).to_contain_text("已载入", timeout=15000)
    expect(page.get_by_test_id("planning-error")).to_have_count(0)


def test_planning_contract_expenses_versions_and_relogin(fresh_browser_page):
    page, base_url = fresh_browser_page
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    _enter(page, base_url)
    page.get_by_test_id("cockpit-building-select").select_option("DEMO_A")
    _topic(page, "decision")
    _ready_planning(page)
    page.get_by_label("启用新增租约", exact=True).uncheck()
    page.get_by_label("启用经营降本", exact=True).uncheck()
    page.get_by_test_id("planning-cutoff").select_option("6")
    expect(page.get_by_test_id("planning-refresh")).to_be_enabled(timeout=15000)
    original = _save_planning_version(page, "业财联动基准")
    year = original["year"]
    contract = next(source for source in original["snapshot"]["sources"]
                    if source["source_type"] == "contract"
                    and any(month["month"] > 6 and month["rent_income"] > 0 for month in source["months"]))
    contract_id = contract["contract_id"]
    page.get_by_role("tab", name="月度预算", exact=True).click()
    january_budget = page.get_by_test_id("planning-budget-revenue-1").input_value()
    contract_details = page.get_by_test_id("planning-contract-details")
    contract_details.locator("summary").click()
    page.get_by_test_id(f"planning-contract-rent-{contract_id}").fill("10")
    with page.expect_response(lambda response: "/api/financial-planning/compare?" in response.url
                              and response.request.method == "POST") as captured:
        page.get_by_test_id("planning-preview-contracts").click()
    adjusted = captured.value.json()["current"]
    expect(page.get_by_test_id("planning-refresh")).to_be_enabled(timeout=15000)
    assert adjusted["months"][0] == original["snapshot"]["months"][0]
    assert adjusted["totals"]["revenue"] > original["snapshot"]["totals"]["revenue"]
    contract_details.locator("summary").click()
    expense_details = page.get_by_test_id("planning-expense-details")
    expense_details.locator("summary").click()
    for index, name, category, amount in ((0, "验收运维费用", "opex", "12.34"), (1, "验收改造投入", "capex", "20")):
        page.get_by_test_id("planning-expense-add").click()
        page.get_by_label(f"第{index + 1}项费用名称", exact=True).fill(name)
        page.get_by_label(f"第{index + 1}项费用类别", exact=True).select_option(category)
        page.get_by_label(f"第{index + 1}项费用金额，万元", exact=True).fill(amount)
        page.get_by_label(f"第{index + 1}项费用发生月", exact=True).fill(f"{year}-10")
        page.get_by_label(f"第{index + 1}项费用计划支付月", exact=True).fill(f"{year}-11")
        page.get_by_label(f"第{index + 1}项费用责任人", exact=True).fill("运营负责人")
        expect(page.get_by_test_id("planning-save-forecast")).to_be_disabled()
        with page.expect_response(lambda response: "/api/financial-planning/expenses?" in response.url
                                  and response.request.method == "POST") as saved_expense:
            page.get_by_test_id(f"planning-expense-save-{index}").click()
        assert saved_expense.value.status == 200, saved_expense.value.text()
        assert saved_expense.value.json()["paid_on"] is None
        expect(page.get_by_test_id("planning-notice")).to_contain_text(f"费用“{name}”已保存", timeout=15000)
    expect(page.get_by_test_id("planning-budget-revenue-1")).to_have_value(january_budget)
    _shot(page, "业财联动-费用与月表-1920")
    page.get_by_role("tab", name=re.compile("经营措施")).click()
    page.get_by_test_id("planning-bridge-expense").click()
    expect(page.get_by_test_id("planning-drilldown")).to_contain_text("验收运维费用")
    expect(page.get_by_test_id("planning-drill-total")).to_contain_text("-12.3")
    page.get_by_test_id("planning-bridge-contract").click()
    drill = page.get_by_test_id("planning-drilldown")
    drill.get_by_role("button", name="打开合同", exact=True).first.click()
    expect(page.get_by_test_id("cockpit-detail")).to_be_visible()
    expect(page.get_by_test_id("cockpit-detail")).to_contain_text("2026")
    page.get_by_role("button", name="关闭", exact=True).click()
    _shot(page, "业财联动-差异桥下钻-1920")
    page.get_by_role("tab", name="持有与项目策略", exact=True).click()
    _fill_strategy(page)
    final = _save_planning_version(page, "合同费用联动预测")
    totals = final["snapshot"]["totals"]
    assert totals["operating_cost"] == pytest.approx(original["snapshot"]["totals"]["operating_cost"] + 12.34)
    assert totals["capex"] == pytest.approx(original["snapshot"]["totals"]["capex"] + 20)
    assert totals["ebitda"] == pytest.approx(totals["revenue"] - totals["operating_cost"])
    assert final["snapshot"]["months"][0] == original["snapshot"]["months"][0]
    assert final["budget_version_id"] == original["budget_version_id"]
    page.get_by_test_id("planning-version-select").select_option(str(original["id"]))
    expect(page.get_by_test_id("planning-name")).to_have_value("业财联动基准", timeout=15000)
    page.get_by_role("tab", name=re.compile("经营措施")).click()
    expect(page.locator(".dw-outcome-strip > div").nth(1)).to_contain_text(
        f'{original["snapshot"]["totals"]["ebitda"]:,.1f}')
    page.get_by_test_id("planning-version-select").select_option(str(final["id"]))
    expect(page.get_by_test_id("planning-name")).to_have_value("合同费用联动预测", timeout=15000)
    page.get_by_test_id("cockpit-exit").click()
    page.get_by_role("button", name="退出", exact=True).click()
    expect(page.get_by_test_id("login-username")).to_be_visible()
    _enter(page, base_url)
    page.get_by_test_id("cockpit-building-select").select_option("DEMO_A")
    _topic(page, "decision")
    expect(page.get_by_test_id("planning-name")).to_have_value("合同费用联动预测", timeout=15000)
    expect(page.get_by_test_id("planning-cutoff")).to_have_value("6")
    expect(page.get_by_test_id("planning-save-status")).to_contain_text("已保存")
    page.get_by_role("tab", name="月度预算", exact=True).click()
    page.get_by_test_id("planning-expense-details").locator("summary").click()
    expect(page.get_by_label("第1项费用金额，万元", exact=True)).to_have_value("12.34")
    expect(page.get_by_label("第1项费用责任人", exact=True)).to_have_value("运营负责人")
    page.get_by_role("tab", name="持有与项目策略", exact=True).click()
    expect(page.get_by_role("spinbutton", name="当前预计出售价格（万元）", exact=True)).to_have_value("18000")
    page.set_viewport_size({"width": 390, "height": 844})
    _topic(page, "decision")
    _shot(page, "业财联动-长期方案-390")
    assert page.get_by_test_id("unified-cockpit").evaluate("el => el.scrollWidth - el.clientWidth") <= 2
    assert errors == []


@pytest.mark.parametrize("preview_fails", [False, True])
def test_planning_live_comparison_failure_preserves_forecast_boundary(fresh_browser_page, preview_fails):
    page, base_url = fresh_browser_page
    _enter(page, base_url)
    page.get_by_test_id("cockpit-building-select").select_option("DEMO_A")
    _topic(page, "decision")
    _ready_planning(page)
    page.get_by_label("启用新增租约", exact=True).uncheck()
    page.get_by_label("启用经营降本", exact=True).uncheck()
    original = _save_planning_version(page, f"实时比较失败边界-{preview_fails}")
    contract_id = next(item["contract_id"] for item in original["snapshot"]["sources"]
                       if item["source_type"] == "contract")
    page.get_by_role("tab", name="月度预算", exact=True).click()
    page.get_by_test_id("planning-contract-details").locator("summary").click()
    rent_input = page.get_by_test_id(f"planning-contract-rent-{contract_id}")
    rent_input.fill(str(float(rent_input.input_value()) + 10))
    status_before = page.get_by_test_id("planning-save-status").inner_text()
    visible_before = page.get_by_test_id("planning-source-rent-income").inner_text()
    responses = []
    page.on("response", lambda response: responses.append(response)
            if "/api/financial-planning/" in response.url else None)
    page.route("**/api/financial-planning/compare?*", lambda route: route.fulfill(
        status=503, json={"detail": "Comparison temporarily unavailable"}))
    if preview_fails:
        page.route("**/api/financial-planning/preview?*", lambda route: route.fulfill(
            status=503, json={"detail": "Preview temporarily unavailable"}))
    page.get_by_test_id("planning-preview-contracts").click()
    expect(page.get_by_test_id("planning-refresh")).to_be_enabled(timeout=15000)
    expect(page.get_by_test_id("planning-error")).to_contain_text(
        "Preview temporarily unavailable" if preview_fails else "Comparison temporarily unavailable")
    expect(page.get_by_test_id("planning-save-status")).to_have_text(status_before)
    paths = [urlparse(response.url).path.rsplit("/", 1)[-1] for response in responses]
    assert paths == ["compare", "preview"]
    if preview_fails:
        expect(page.get_by_test_id("planning-source-rent-income")).to_have_text(visible_before)
    else:
        current = responses[-1].json()
        assert current["months"][:original["closed_through_month"]] == original["snapshot"]["months"][:original["closed_through_month"]]
        assert current["totals"]["rent_income"] > original["snapshot"]["totals"]["rent_income"]
        expect(page.get_by_test_id("planning-source-rent-income")).to_have_text(f'{current["totals"]["rent_income"]:,.1f}')


def test_planning_expense_receipt_and_other_draft_survive_refresh_failure(fresh_browser_page):
    page, base_url = fresh_browser_page
    _enter(page, base_url)
    page.get_by_test_id("cockpit-building-select").select_option("DEMO_A")
    _topic(page, "decision")
    _ready_planning(page)
    page.get_by_role("tab", name="月度预算", exact=True).click()
    page.get_by_test_id("planning-expense-details").locator("summary").click()
    offset = page.locator('[data-testid^="planning-expense-save-"]').count()
    for index, name in enumerate(("保存后刷新失败费用", "必须保留的未保存费用"), start=offset):
        page.get_by_test_id("planning-expense-add").click()
        page.get_by_label(f"第{index + 1}项费用名称", exact=True).fill(name)
        page.get_by_label(f"第{index + 1}项费用金额，万元", exact=True).fill("7.89")
    page.route("**/api/financial-planning/compare?*", lambda route: route.fulfill(status=503, json={"detail": "Comparison unavailable"}))
    page.route("**/api/financial-planning/preview?*", lambda route: route.fulfill(status=503, json={"detail": "Preview unavailable"}))
    writes = []
    page.on("request", lambda request: writes.append(request) if request.method == "POST"
            and "/api/financial-planning/expenses?" in request.url else None)
    with page.expect_response(lambda response: "/api/financial-planning/expenses?" in response.url
                              and response.request.method == "POST") as saved:
        page.get_by_test_id(f"planning-expense-save-{offset}").click()
    assert saved.value.status == 200
    expect(page.get_by_test_id("planning-notice")).to_contain_text("已保存，预测暂未刷新成功", timeout=15000)
    expect(page.get_by_test_id("planning-error")).to_contain_text("Preview unavailable")
    expect(page.get_by_label(f"第{offset + 2}项费用名称", exact=True)).to_have_value("必须保留的未保存费用")
    expect(page.get_by_label(f"第{offset + 2}项费用金额，万元", exact=True)).to_have_value("7.89")
    assert len(writes) == 1


@pytest.mark.parametrize("failure,year", [(None, 2036), ("compare", 2037), ("both", 2038)])
def test_planning_budget_only_load_keeps_receipt_and_failure_fallback(fresh_browser_page, failure, year):
    page, base_url = fresh_browser_page
    _enter(page, base_url)
    page.get_by_test_id("cockpit-building-select").select_option("DEMO_A")
    _topic(page, "decision")
    expect(page.get_by_test_id("planning-year")).to_be_enabled(timeout=15000)
    page.get_by_test_id("planning-year").fill(str(year))
    page.get_by_test_id("planning-year").press("Tab")
    expect(page.get_by_test_id("planning-freeze-budget")).to_be_enabled(timeout=15000)
    page.get_by_test_id("planning-name").fill(f"首次载入预算-{year}")
    page.get_by_test_id("planning-annual-target").fill("12")
    page.get_by_test_id("planning-budget-revenue-1").fill("12")
    with page.expect_response(lambda response: "/api/financial-planning/versions?" in response.url
                              and response.request.method == "POST") as saved:
        page.get_by_test_id("planning-freeze-budget").click()
    assert saved.value.status == 200
    budget = saved.value.json()["version"]
    expect(page.get_by_test_id("planning-notice")).to_contain_text("已保存并冻结", timeout=15000)
    receipt = page.get_by_test_id("planning-save-status").inner_text()
    page.get_by_test_id("planning-year").fill("2040")
    page.get_by_test_id("planning-year").press("Tab")
    expect(page.get_by_test_id("planning-refresh")).to_be_enabled(timeout=15000)
    responses = []
    page.on("response", lambda response: responses.append(response)
            if "/api/financial-planning/" in response.url else None)
    if failure:
        page.route("**/api/financial-planning/compare?*", lambda route: route.fulfill(
            status=503, json={"detail": "Comparison temporarily unavailable"}))
    if failure == "both":
        page.route("**/api/financial-planning/preview?*", lambda route: route.fulfill(
            status=503, json={"detail": "Preview temporarily unavailable"}))
    page.get_by_test_id("planning-year").fill(str(year))
    page.get_by_test_id("planning-year").press("Tab")
    expect(page.get_by_test_id("planning-refresh")).to_be_enabled(timeout=15000)
    paths = [urlparse(response.url).path.rsplit("/", 1)[-1] for response in responses]
    assert paths == (["source", "compare", "preview"] if failure else ["source", "compare"])
    source = responses[0].json()
    assert source["forecast"] is None and source["latest_forecast"] is None
    assert source["approved_budget"] == budget
    assert "current_version_id" not in responses[1].request.post_data_json
    expect(page.get_by_test_id("planning-save-status")).to_have_text(receipt)
    expect(page.get_by_test_id("planning-budget-link")).to_contain_text(budget["name"])
    expect(page.get_by_role("tab", name=re.compile("经营措施"))).to_have_attribute("aria-selected", "true")
    if failure:
        expect(page.get_by_test_id("planning-error")).to_contain_text(
            "Preview temporarily unavailable" if failure == "both" else "Comparison temporarily unavailable")
    else:
        expect(page.get_by_test_id("planning-error")).to_have_count(0)
    if failure != "both":
        current = responses[-1].json() if failure else responses[-1].json()["current"]
        assert current["year"] == year and len(current["months"]) == 12
        page.get_by_role("tab", name="月度预算", exact=True).click()
        expect(page.get_by_test_id("planning-source-rent-income")).to_have_text(f'{current["totals"]["rent_income"]:,.1f}')
    else:
        page.get_by_role("tab", name="月度预算", exact=True).click()
        details = page.get_by_test_id("planning-contract-details")
        expect(details.locator("summary")).to_contain_text("待载入")
        details.locator("summary").click()
        expect(page.get_by_test_id("planning-source-rent-income")).to_have_text("—")
        expect(details).not_to_contain_text("当前年度没有纳入计算的合同")
        expect(page.get_by_test_id("planning-budget-revenue-1")).to_have_value("12")

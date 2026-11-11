from __future__ import annotations

from html import escape
from urllib.parse import quote
from collections.abc import Sequence

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.time import now_in_timezone
from app.services.common import dump_changes, log_action_for_buildings
from app.services.dashboard import get_operations_dashboard


QUEUE_CONFIG = [
    ("overdue_contracts", "历史逾期 / 回款处理队列", "按历史逾期金额和账龄排序，优先处理影响回款的合同。"),
    ("expiring_contracts", "90 天到期合同", "影响续租、退租和招商排期的合同。"),
    ("due_follow_up_customers", "待跟进客户", "到期或逾期待办的客户跟进事项。"),
    ("unpaid_deposits", "未收押金", "已登记应收但仍有未收金额的押金。"),
    ("missing_contract_body", "缺合同正文/附件缺失", "当前有效合同中缺少合同正文附件的项目。"),
]

EMPTY_MESSAGES = {
    "overdue_contracts": "暂无历史逾期合同；本月未收仍可能包含未到期应收。",
    "expiring_contracts": "暂无 90 天内到期合同。",
    "due_follow_up_customers": "暂无今日需要列入日报的客户跟进。",
    "unpaid_deposits": "暂无未收押金项目。",
    "missing_contract_body": "暂无合同正文附件缺失项目。",
}

SEVERITY_LABELS = {"high": "高", "medium": "中", "low": "低"}


def _text(value: object, fallback: str = "--") -> str:
    if value is None:
        return fallback
    text = str(value).strip()
    return escape(text or fallback)


def _money(value: object) -> str:
    try:
        amount = float(value or 0)
    except (TypeError, ValueError):
        amount = 0.0
    return f"¥{amount:,.2f}"


def _number(value: object) -> str:
    try:
        amount = float(value or 0)
    except (TypeError, ValueError):
        amount = 0.0
    if amount.is_integer():
        return f"{int(amount):,}"
    return f"{amount:,.2f}"


def _percent(value: object) -> str:
    try:
        amount = float(value or 0)
    except (TypeError, ValueError):
        amount = 0.0
    return f"{amount:.1f}%"


def _progress_width(value: object) -> str:
    try:
        amount = max(0.0, min(float(value or 0), 100.0))
    except (TypeError, ValueError):
        amount = 0.0
    return f"{amount:.1f}%"


def _join_meta(parts: list[object]) -> str:
    values = [str(part).strip() for part in parts if part is not None and str(part).strip()]
    return _text(" / ".join(values))


def _todo_meta(item: dict) -> str:
    meta = item.get("meta") or {}
    item_type = item.get("type")
    if item_type == "overdue_contract":
        return _join_meta([meta.get("contract_no"), meta.get("room_no"), f"逾期 {meta.get('max_aging_days') or 0} 天"])
    if item_type == "expiring_contract":
        return _join_meta([meta.get("contract_no"), meta.get("room_no"), f"{meta.get('end_date') or item.get('due_date')} 到期"])
    if item_type == "due_follow_up_customer":
        return _join_meta([meta.get("owner"), meta.get("next_follow_up_date") or item.get("due_date"), meta.get("next_action") or "待跟进"])
    if item_type == "unpaid_deposit":
        return _join_meta([meta.get("contract_no"), meta.get("deposit_type") or "押金", f"应收 {meta.get('due_date') or item.get('due_date') or '--'}"])
    if item_type == "missing_contract_body":
        return _join_meta([meta.get("contract_no"), meta.get("room_no"), f"缺 {meta.get('missing_category') or '合同正文'}"])
    return _text(item.get("label"))


def _todo_value(item: dict) -> str:
    amount = item.get("amount") or 0
    try:
        if float(amount) > 0:
            return _money(amount)
    except (TypeError, ValueError):
        pass
    if item.get("days_left") is not None:
        return f"{_number(item.get('days_left'))} 天"
    if item.get("due_date"):
        return _text(item.get("due_date"))
    return ""


def _queue_item_summary(item: dict, queue_key: str) -> tuple[str, str, str]:
    title = item.get("tenant_name") or item.get("customer_name") or item.get("contract_no") or item.get("label")
    if queue_key == "overdue_contracts":
        meta = _join_meta([item.get("contract_no"), item.get("room_no"), f"逾期 {item.get('max_aging_days') or 0} 天"])
        value = _money(item.get("overdue_balance"))
    elif queue_key == "expiring_contracts":
        meta = _join_meta([item.get("contract_no"), item.get("room_no"), f"{item.get('end_date') or '--'} 到期"])
        value = f"{_number(item.get('days_left'))} 天"
    elif queue_key == "due_follow_up_customers":
        meta = _join_meta([item.get("owner"), item.get("next_follow_up_date"), item.get("next_action") or "待跟进"])
        value = f"{_number(item.get('open_follow_up_count'))} 项"
    elif queue_key == "unpaid_deposits":
        meta = _join_meta([item.get("contract_no"), item.get("deposit_type"), item.get("due_date")])
        value = _money(item.get("unpaid_amount"))
    elif queue_key == "missing_contract_body":
        meta = _join_meta([item.get("contract_no"), item.get("room_no"), item.get("missing_category")])
        value = "缺附件"
    else:
        meta = _text(item.get("label"))
        value = ""
    return _text(title), meta, value


def _metric_card(label: str, value: str, helper: str, tone: str = "") -> str:
    return f"""
      <div class="metric {tone}">
        <span>{escape(label)}</span>
        <strong>{value}</strong>
        <small>{escape(helper)}</small>
      </div>
    """


def _render_focus(data: dict) -> str:
    current = data.get("current_month") or {}
    metrics = data.get("metrics") or {}
    counts = data.get("queue_counts") or {}
    rows = [
        (
            "回款",
            f"本月未收 {_money(current.get('unpaid_balance'))}",
            f"本月逾期 {_money(current.get('overdue_balance'))}，本月未收含未到期应收。",
        ),
        (
            "到期",
            f"90 天到期 {_number(metrics.get('expiring_contract_count_90'))} 份",
            "用于续租、退租和招商排期判断。",
        ),
        (
            "跟进",
            f"待跟进 {_number(counts.get('due_follow_up_customers'))} 项",
            "来自今日看板的客户跟进队列。",
        ),
        (
            "资料",
            f"缺附件 {_number(counts.get('missing_contract_body'))} 份",
            f"未收押金 {_money(metrics.get('unpaid_deposit_amount'))}。",
        ),
    ]
    return "".join(
        f"""
        <li>
          <b>{escape(label)}</b>
          <strong>{escape(value)}</strong>
          <span>{escape(helper)}</span>
        </li>
        """
        for label, value, helper in rows
    )


def _render_todos(data: dict) -> str:
    todos = data.get("todos") or []
    if not todos:
        return '<p class="empty">今日暂无需要优先处理的经营动作。</p>'
    rows = []
    for item in todos[:8]:
        value = _todo_value(item)
        rows.append(
            f"""
            <li class="action-row {escape(str(item.get("severity") or ""))}">
              <b>{_text(item.get("label") or "经营动作")}</b>
              <span><strong>{_text(item.get("title") or item.get("label"))}</strong><small>{_todo_meta(item)}</small></span>
              <em>{value}</em>
            </li>
            """
        )
    return f'<ul class="action-list">{"".join(rows)}</ul>'


def _render_queue(data: dict, key: str, title: str, helper: str) -> str:
    queues = data.get("queues") or {}
    counts = data.get("queue_counts") or {}
    items = queues.get(key) or []
    if not items:
        body = f'<p class="empty">{escape(EMPTY_MESSAGES.get(key, "暂无需要列入日报的项目。"))}</p>'
    else:
        rows = []
        for item in items[:8]:
            item_title, meta, value = _queue_item_summary(item, key)
            rows.append(
                f"""
                <li class="queue-row {escape(str(item.get("severity") or ""))}">
                  <span><strong>{item_title}</strong><small>{meta}</small></span>
                  <b>{value}</b>
                </li>
                """
            )
        body = f'<ul class="queue-list">{"".join(rows)}</ul>'
    return f"""
      <section class="panel">
        <div class="panel-head">
          <div>
            <h2>{escape(title)}</h2>
            <p>{escape(helper)}</p>
          </div>
          <strong>{_number(counts.get(key))}</strong>
        </div>
        {body}
      </section>
    """


def _render_risks(data: dict) -> str:
    risk_data = data.get("risks") or {}
    summary = risk_data.get("summary") or {}
    by_severity = summary.get("by_severity") or {}
    high_items = [item for item in (risk_data.get("top") or []) if item.get("severity") == "high"]
    if not high_items:
        body = '<p class="empty">暂无高风险项。</p>'
    else:
        rows = []
        for item in high_items[:8]:
            amount = _money(item.get("amount")) if item.get("amount") is not None else ""
            meta = _join_meta([SEVERITY_LABELS.get(item.get("severity"), item.get("severity")), item.get("due_date") or item.get("occurred_on"), amount])
            rows.append(
                f"""
                <li class="queue-row high">
                  <span><strong>{_text(item.get("title"))}</strong><small>{meta}</small></span>
                  <b>{_text(item.get("action_label") or "处理")}</b>
                </li>
                """
            )
        body = f'<ul class="queue-list">{"".join(rows)}</ul>'
    return f"""
      <section class="panel risk-panel">
        <div class="panel-head">
          <div>
            <h2>高风险项摘要</h2>
            <p>风险中心按严重程度、到期日和金额排序后的高风险项。</p>
          </div>
          <strong>{_number(by_severity.get("high"))}</strong>
        </div>
        {body}
      </section>
    """


def _render_trend(data: dict) -> str:
    rows = []
    for item in data.get("trend") or []:
        rows.append(
            f"""
            <tr>
              <td>{_text(item.get("month"))}</td>
              <td class="right">{_money(item.get("receivable"))}</td>
              <td class="right">{_money(item.get("received"))}</td>
              <td class="right">{_money(item.get("unpaid_balance"))}</td>
              <td>
                <div class="bar"><i style="width: {_progress_width(item.get("received_rate"))}"></i></div>
                <small>{_percent(item.get("received_rate"))}</small>
              </td>
            </tr>
            """
        )
    return f"""
      <section class="panel trend-panel">
        <div class="panel-head">
          <div>
            <h2>近 6 个月回款趋势</h2>
            <p>轻量趋势用于判断回款节奏，不替代经营分析页面。</p>
          </div>
        </div>
        <table>
          <thead><tr><th>月份</th><th class="right">应收</th><th class="right">已收</th><th class="right">未收</th><th>回款率</th></tr></thead>
          <tbody>{"".join(rows)}</tbody>
        </table>
      </section>
    """


def build_operations_daily_report_html(
    db: Session,
    operator: str,
    *,
    building_ids: Sequence[int] | None = None,
) -> str:
    normalized_building_ids = tuple(dict.fromkeys(int(item) for item in (building_ids or ())))
    if not normalized_building_ids:
        raise ValueError("经营日报必须明确至少一栋楼宇")
    data = get_operations_dashboard(
        db,
        include_trend=True,
        queue_limit=8,
        risk_limit=16,
        building_ids=normalized_building_ids,
    )
    current = data.get("current_month") or {}
    metrics = data.get("metrics") or {}
    report_date = data.get("date") or ""
    month = data.get("month") or ""
    generated_at = now_in_timezone().strftime("%Y-%m-%d %H:%M")
    version = get_settings().app_version

    audit_changes = dump_changes(
        {
            "date": report_date,
            "month": month,
            "queue_counts": data.get("queue_counts") or {},
            "risk_count": metrics.get("risk_count", 0),
            "high_risk_count": metrics.get("high_risk_count", 0),
        }
    )
    # A portfolio report contains cross-building totals.  Those totals must not
    # be copied into every building's audit stream because a building-only
    # viewer could then infer the rest of the portfolio.  Keep the established
    # detailed single-building audit, but portfolio rows record only neutral
    # report identity fields.
    changes_by_building = None
    if len(normalized_building_ids) > 1:
        per_building_change = dump_changes({"date": report_date, "month": month})
        changes_by_building = {
            building_id: per_building_change for building_id in normalized_building_ids
        }

    log_action_for_buildings(
        db,
        normalized_building_ids,
        "daily_report_export",
        audit_changes,
        operator,
        target_type="report",
        changes_by_building=changes_by_building,
    )

    metric_html = "".join(
        [
            _metric_card("本月应收", _money(current.get("receivable")), f"报告月份 {month}", "info"),
            _metric_card("本月已收", _money(current.get("received")), f"回款率 {_percent(current.get('received_rate'))}", "success"),
            _metric_card("本月未收", _money(current.get("unpaid_balance")), "含未到期未收，不等同本月逾期", "warning"),
            _metric_card("本月逾期", _money(current.get("overdue_balance")), f"{_number(current.get('overdue_count'))} 笔本月逾期记录", "danger"),
            _metric_card("90天到期", _number(metrics.get("expiring_contract_count_90")), "续租、退租和招商排期", "warning"),
            _metric_card("高风险项", _number(metrics.get("high_risk_count")), f"风险中心共 {_number(metrics.get('risk_count'))} 项", "danger"),
        ]
    )
    queue_html = "".join(_render_queue(data, key, title, helper) for key, title, helper in QUEUE_CONFIG)
    filename = quote(f"operations-daily-{report_date or month}.html")

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="download-filename" content="{filename}">
  <title>经营日报 {escape(report_date)}</title>
  <style>
    :root {{ color: #172033; background: #f5f7fb; font-family: "Microsoft YaHei", "PingFang SC", Arial, sans-serif; font-size: 13px; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; padding: 24px; color: #172033; background: #f5f7fb; }}
    .page {{ max-width: 1180px; margin: 0 auto; }}
    .screen-toolbar {{ display: flex; justify-content: flex-end; gap: 8px; margin-bottom: 12px; }}
    .screen-toolbar button {{ border: 1px solid #cbd5e1; background: white; color: #172033; border-radius: 6px; padding: 8px 12px; cursor: pointer; font: inherit; }}
    header {{ display: flex; justify-content: space-between; gap: 20px; align-items: flex-end; padding: 18px 22px; background: #132238; color: white; border-radius: 8px; break-inside: avoid; }}
    h1, h2, p {{ margin: 0; }}
    h1 {{ font-size: 26px; letter-spacing: 0; }}
    header p {{ margin-top: 6px; color: #c9d5e7; line-height: 1.5; }}
    .stamp {{ text-align: right; font-size: 12px; color: #dce6f3; line-height: 1.7; white-space: nowrap; }}
    .focus-section {{ margin: 12px 0; }}
    .focus-section h2 {{ margin: 0 0 8px; font-size: 16px; color: #26384f; }}
    .summary-strip, .metrics {{ display: grid; gap: 10px; margin: 12px 0; }}
    .summary-strip {{ grid-template-columns: repeat(4, 1fr); list-style: none; padding: 0; }}
    .summary-strip li {{ background: white; border: 1px solid #dfe6ef; border-left: 4px solid #47637f; border-radius: 8px; padding: 11px 12px; break-inside: avoid; }}
    .summary-strip b, .summary-strip span {{ display: block; color: #66758c; font-size: 12px; }}
    .summary-strip strong {{ display: block; margin: 5px 0; font-size: 15px; color: #172033; line-height: 1.35; }}
    .metrics {{ grid-template-columns: repeat(6, 1fr); }}
    .metric, .panel {{ background: white; border: 1px solid #e3e8f1; border-radius: 8px; box-shadow: 0 1px 2px rgba(20, 32, 52, 0.04); }}
    .metric {{ padding: 12px; min-height: 96px; border-top: 4px solid #5f7594; break-inside: avoid; }}
    .metric span {{ display: block; color: #66758c; font-size: 12px; }}
    .metric strong {{ display: block; margin: 7px 0 5px; font-size: 19px; white-space: nowrap; }}
    .metric small {{ color: #66758c; line-height: 1.45; }}
    .metric.success {{ border-top-color: #2f8f6b; }}
    .metric.warning {{ border-top-color: #c28118; }}
    .metric.danger {{ border-top-color: #c74949; }}
    .grid {{ display: grid; grid-template-columns: 1.05fr 0.95fr; gap: 12px; }}
    .panel {{ padding: 14px; break-inside: avoid; page-break-inside: avoid; margin-bottom: 12px; }}
    .panel-head {{ display: flex; align-items: flex-start; justify-content: space-between; gap: 10px; margin-bottom: 10px; }}
    .panel-head h2 {{ font-size: 16px; }}
    .panel-head p {{ margin-top: 4px; color: #66758c; font-size: 12px; line-height: 1.5; }}
    .panel-head > strong {{ min-width: 42px; text-align: center; padding: 6px 9px; border-radius: 999px; background: #eef3f8; color: #26384f; }}
    .action-list, .queue-list {{ list-style: none; margin: 0; padding: 0; display: grid; gap: 6px; }}
    .action-row, .queue-row {{ display: grid; grid-template-columns: 86px 1fr auto; gap: 9px; align-items: center; padding: 7px 9px; border: 1px solid #edf1f6; border-radius: 7px; background: #fbfcfe; break-inside: avoid; page-break-inside: avoid; }}
    .queue-row {{ grid-template-columns: 1fr auto; }}
    .action-row > b {{ color: #43546b; font-size: 12px; }}
    .action-row span, .queue-row span {{ min-width: 0; }}
    .action-row strong, .queue-row strong {{ display: block; font-size: 13px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
    .action-row small, .queue-row small {{ display: block; color: #728197; font-size: 12px; margin-top: 3px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
    .action-row em, .queue-row b {{ font-style: normal; font-size: 13px; color: #172033; white-space: nowrap; }}
    .danger, .high {{ border-left: 4px solid #c74949; }}
    .warning {{ border-left: 4px solid #c28118; }}
    .empty {{ color: #728197; padding: 11px 12px; background: #f8fafc; border-radius: 7px; font-size: 13px; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
    thead {{ display: table-header-group; }}
    tr {{ break-inside: avoid; page-break-inside: avoid; }}
    th, td {{ padding: 7px 6px; border-bottom: 1px solid #edf1f6; text-align: left; }}
    th {{ color: #66758c; font-weight: 600; }}
    .right {{ text-align: right; }}
    .bar {{ display: inline-block; width: 72px; height: 8px; vertical-align: middle; background: #e8edf4; border-radius: 999px; overflow: hidden; margin-right: 6px; }}
    .bar i {{ display: block; height: 100%; background: #2f8f6b; }}
    .report-note {{ margin: 10px 0 0; padding: 10px 12px; color: #52627a; background: #edf4fb; border: 1px solid #d9e5f2; border-radius: 8px; font-size: 12px; line-height: 1.6; }}
    footer {{ color: #728197; font-size: 11px; margin-top: 10px; text-align: right; }}
    .print-footer {{ display: none; }}
    @media print {{
      @page {{ size: A4 portrait; margin: 11mm 10mm 13mm; }}
      :root {{ font-size: 11px; }}
      body {{ padding: 0; background: white; color: #111827; -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
      .screen-toolbar {{ display: none; }}
      header, .metric, .panel, .summary-strip li {{ box-shadow: none; }}
      header {{ padding: 12px 14px; border-radius: 0; }}
      h1 {{ font-size: 22px; }}
      .page {{ max-width: none; }}
      .summary-strip {{ grid-template-columns: repeat(4, 1fr); gap: 6px; margin: 8px 0; }}
      .focus-section {{ margin: 8px 0; break-inside: avoid; page-break-inside: avoid; }}
      .focus-section h2 {{ font-size: 12px; margin-bottom: 5px; }}
      .summary-strip li {{ padding: 7px 8px; border-radius: 4px; }}
      .summary-strip strong {{ font-size: 12px; }}
      .metrics {{ grid-template-columns: repeat(3, 1fr); gap: 6px; margin: 8px 0; }}
      .metric {{ min-height: 0; padding: 8px 9px; border-radius: 4px; }}
      .metric strong {{ font-size: 15px; margin: 3px 0; }}
      .grid {{ grid-template-columns: 1fr 1fr; gap: 8px; align-items: start; }}
      .panel {{ padding: 9px 10px; margin-bottom: 8px; border-radius: 4px; }}
      .panel-head {{ margin-bottom: 7px; }}
      .panel-head h2 {{ font-size: 13px; }}
      .panel-head p {{ font-size: 10.5px; line-height: 1.35; }}
      .panel-head > strong {{ padding: 3px 7px; min-width: 34px; }}
      .action-list, .queue-list {{ gap: 4px; }}
      .action-row, .queue-row {{ padding: 5px 6px; gap: 6px; border-radius: 4px; }}
      .action-row {{ grid-template-columns: 72px 1fr auto; }}
      .action-row strong, .queue-row strong {{ font-size: 11px; }}
      .action-row small, .queue-row small, .action-row > b {{ font-size: 10px; }}
      .empty {{ padding: 7px 8px; border-radius: 4px; font-size: 11px; }}
      table {{ font-size: 10.5px; }}
      th, td {{ padding: 4px 5px; }}
      .bar {{ width: 48px; height: 6px; }}
      .report-note {{ padding: 7px 8px; margin-top: 6px; border-radius: 4px; font-size: 10.5px; }}
      footer {{ margin-top: 7px; }}
      .print-footer {{ display: block; position: fixed; bottom: 0; left: 0; right: 0; color: #6b7280; font-size: 9px; text-align: center; }}
      .print-footer::after {{ content: "第 " counter(page) " 页"; }}
    }}
    @media (max-width: 900px) {{
      body {{ padding: 12px; }}
      header, .grid {{ display: block; }}
      .stamp {{ margin-top: 12px; text-align: left; }}
      .summary-strip {{ grid-template-columns: 1fr; }}
      .metrics {{ grid-template-columns: repeat(2, 1fr); }}
    }}
  </style>
</head>
<body>
  <main class="page">
    <div class="screen-toolbar">
      <button type="button" onclick="window.print()">打印 / 转 PDF</button>
    </div>
    <header>
      <div>
        <p>租赁管理系统</p>
        <h1>经营日报</h1>
        <p>今日要处理什么、哪里影响回款、哪里影响出租、哪里有风险。</p>
      </div>
      <div class="stamp">
        <div>报告日期：{_text(report_date)}</div>
        <div>当前月份：{_text(month)}</div>
        <div>生成时间：{_text(generated_at)} Asia/Shanghai</div>
        <div>版本：{_text(version)}</div>
      </div>
    </header>
    <section class="focus-section">
      <h2>今日重点</h2>
      <ul class="summary-strip">{_render_focus(data)}</ul>
    </section>
    <section class="metrics">{metric_html}</section>
    <section class="panel">
      <div class="panel-head">
        <div>
          <h2>今日经营动作摘要</h2>
          <p>来自今日看板待办队列，按回款、到期、跟进、押金和附件优先级汇总。</p>
        </div>
        <strong>{_number(len(data.get("todos") or []))}</strong>
      </div>
      {_render_todos(data)}
    </section>
    <div class="grid">
      <div>{queue_html}</div>
      <div>{_render_risks(data)}{_render_trend(data)}</div>
    </div>
    <p class="report-note">口径提示：本月未收包含未到期未收，不等同于本月逾期；到期日按合同付款日（每月固定日）计算，未约定付款日的合同以月末为到期日，超过到期日仍未收才计入本月逾期。</p>
    <footer>金额、队列和风险沿用系统当前经营看板口径。</footer>
    <div class="print-footer">经营日报 · {escape(report_date)} · </div>
  </main>
</body>
</html>"""

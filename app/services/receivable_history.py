from __future__ import annotations

from datetime import date

from dateutil.relativedelta import relativedelta
from sqlalchemy.orm import Session

from app.core.money import round_money

# 历史逾期的统一口径：回看月数与严重度阈值。
# 风险中心、运营看板、合同决策卡、客户经营摘要全部复用本模块，
# 不允许在各自模块内复制逐月聚合逻辑（曾因四处复制导致阈值漂移）。
OVERDUE_LOOKBACK_MONTHS = 12
OVERDUE_SEVERE_AGING_DAYS = 30
OVERDUE_SEVERE_AMOUNT = 10000


def money(value: float | int | None) -> float:
    return round_money(float(value or 0))


def month_keys_back(anchor: date, months: int) -> list[str]:
    start = anchor.replace(day=1) + relativedelta(months=-(months - 1))
    return [(start + relativedelta(months=offset)).strftime("%Y-%m") for offset in range(months)]


def month_keys_between(start: date, end: date) -> list[str]:
    current = start.replace(day=1)
    last = end.replace(day=1)
    keys = []
    while current <= last:
        keys.append(current.strftime("%Y-%m"))
        current += relativedelta(months=1)
    return keys


def overdue_severity(max_aging_days: int | None, overdue_balance: float | None) -> str:
    if (max_aging_days or 0) >= OVERDUE_SEVERE_AGING_DAYS or (overdue_balance or 0) >= OVERDUE_SEVERE_AMOUNT:
        return "high"
    return "medium"


def cached_monthly_report(
    db: Session,
    month: str,
    monthly_reports: dict[str, dict] | None = None,
    contract_ids: list[int] | None = None,
) -> dict:
    from app.services.contracts import get_monthly_receivables

    cache_key = monthly_report_cache_key(month, contract_ids)
    if monthly_reports is None:
        return get_monthly_receivables(db, month, contract_ids=contract_ids)
    if cache_key not in monthly_reports:
        monthly_reports[cache_key] = get_monthly_receivables(db, month, contract_ids=contract_ids)
    return monthly_reports[cache_key]


def monthly_report_cache_key(month: str, contract_ids: list[int] | None) -> str:
    """Keep all-contract reports distinct from explicit (including empty) scopes."""
    if contract_ids is None:
        return month
    return f"{month}:{','.join(str(item) for item in sorted(contract_ids))}"


def collect_overdue_history(
    db: Session,
    *,
    months: list[str],
    contract_ids: list[int] | None = None,
    monthly_reports: dict[str, dict] | None = None,
    contracts: list | None = None,
) -> dict:
    """逐月汇总每份合同的逾期与未收余额（单一口径）。

    返回：
    - by_contract: 每份出现过应收行的合同的累计 {unpaid_balance, overdue_balance,
      overdue_months, max_aging_days, latest_month, due_date, dunning_owner,
      next_follow_up_date, severity, sample_row}
    - overdue_by_contract: 仅含 overdue_balance > 0 的子集
    - month_totals: 每月 {receivable, balance, overdue_balance} 合计
    - total_overdue_balance / overdue_contract_count
    """
    report_cache = monthly_reports if monthly_reports is not None else {}
    missing_months = [month for month in months if monthly_report_cache_key(month, contract_ids) not in report_cache]
    if len(missing_months) > 1:
        from app.services.contracts import get_monthly_receivables_batch

        batch = get_monthly_receivables_batch(
            db,
            missing_months,
            contract_ids=contract_ids,
            contracts=contracts,
        )
        for month, report in batch.items():
            report_cache[monthly_report_cache_key(month, contract_ids)] = report

    by_contract: dict[int, dict] = {}
    month_totals: dict[str, dict] = {}
    for month in months:
        report = cached_monthly_report(db, month, report_cache, contract_ids)
        totals = month_totals.setdefault(month, {"receivable": 0.0, "balance": 0.0, "overdue_balance": 0.0})
        for row in report["rows"]:
            row_contract_id = row.get("contract_id")
            row_balance = money(row.get("balance"))
            overdue_balance = money(row.get("overdue_balance"))
            totals["receivable"] = money(totals["receivable"] + money(row.get("total")))
            totals["balance"] = money(totals["balance"] + row_balance)
            totals["overdue_balance"] = money(totals["overdue_balance"] + overdue_balance)

            entry = by_contract.get(row_contract_id)
            if entry is None:
                entry = by_contract[row_contract_id] = {
                    "contract_id": row_contract_id,
                    "unpaid_balance": 0.0,
                    "overdue_balance": 0.0,
                    "overdue_months": 0,
                    "max_aging_days": 0,
                    "latest_month": "",
                    "due_date": None,
                    "dunning_owner": "",
                    "next_follow_up_date": "",
                    "sample_row": row,
                }
            entry["unpaid_balance"] = money(entry["unpaid_balance"] + row_balance)
            if overdue_balance <= 0:
                continue
            entry["overdue_balance"] = money(entry["overdue_balance"] + overdue_balance)
            entry["overdue_months"] += 1
            entry["max_aging_days"] = max(int(entry["max_aging_days"] or 0), int(row.get("aging_days") or 0))
            entry["latest_month"] = month
            if row.get("due_date"):
                entry["due_date"] = row["due_date"]
            if row.get("dunning_owner"):
                entry["dunning_owner"] = row["dunning_owner"]
            if row.get("next_follow_up_date"):
                entry["next_follow_up_date"] = row["next_follow_up_date"]

    for entry in by_contract.values():
        entry["severity"] = overdue_severity(entry["max_aging_days"], entry["overdue_balance"])

    overdue_by_contract = {
        contract_id: entry for contract_id, entry in by_contract.items() if entry["overdue_balance"] > 0
    }
    return {
        "months": months,
        "by_contract": by_contract,
        "overdue_by_contract": overdue_by_contract,
        "month_totals": month_totals,
        "total_overdue_balance": money(sum(entry["overdue_balance"] for entry in overdue_by_contract.values())),
        "overdue_contract_count": len(overdue_by_contract),
    }

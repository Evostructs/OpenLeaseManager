"""Installation-wide presentation unit. Never convert or rewrite monetary values."""
from contextvars import ContextVar
import json
from pathlib import Path
import re

CURRENCIES = json.loads((Path(__file__).resolve().parents[1] / "i18n/currencies.json").read_text(encoding="utf-8"))
CURRENCY_CODES = frozenset(item["code"] for item in CURRENCIES)
CONFIG_KEY = "display_currency"
request_currency: ContextVar[str] = ContextVar("display_currency", default="CNY")


def currency_settings(db):
    from app.models import SystemConfig
    row = db.query(SystemConfig).filter(SystemConfig.key == CONFIG_KEY).first()
    code = row.value if row and row.value in CURRENCY_CODES else "CNY"
    return {"code": code, "options": CURRENCIES}


def currency_label(value, code=None):
    """Trusted UI labels only; user text and numeric data must bypass this function."""
    code = code or request_currency.get()
    if code == "CNY" or code not in CURRENCY_CODES or not isinstance(value, str):
        return value
    value = re.sub(r"\b(?:CNY|RMB|yuan|renminbi)\b", code, value, flags=re.I)
    value = value.replace("¥", code + " ").replace("￥", code + " ")
    value = value.replace("人民币", code).replace("万元", "万 " + code).replace("亿元", "亿 " + code)
    return re.sub(r"(?<![\u3400-\u9fff])元(?![\u3400-\u9fff])", code, value)


def book_currency_locked(db):
    from app.models import Contract, FinancialExpense, FinancialPlanVersion, BuildingOperatingSnapshot, BuildingMetricFact, ExecutionAction
    from app.finance_models import FinanceCash, FinanceBankLine
    # Include archived/deleted financial records; deleting a row must not unlock a ledger.
    from app.models import SystemConfig
    if db.query(SystemConfig.id).filter(SystemConfig.key == "book_currency_locked", SystemConfig.value == "true").first():
        return True
    return any(db.query(model.id).first() is not None for model in (Contract, FinancialExpense, FinancialPlanVersion, BuildingOperatingSnapshot, BuildingMetricFact, FinanceCash, FinanceBankLine, ExecutionAction))

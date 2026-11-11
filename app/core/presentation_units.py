"""Reference conversions at presentation boundaries; ledger values remain canonical."""
from contextvars import ContextVar
from datetime import date
from decimal import Decimal, InvalidOperation
from threading import Lock
from time import monotonic

import httpx

from app.core.currency_unit import CURRENCY_CODES, currency_settings

SQM_PER_SQFT = Decimal("0.09290304")
request_area: ContextVar[str] = ContextVar("area_unit", default="m2")
request_fx: ContextVar[float] = ContextVar("view_fx_rate", default=1.0)
request_fx_date: ContextVar[str] = ContextVar("view_fx_date", default="")
request_book_currency: ContextVar[str] = ContextVar("book_currency", default="CNY")
_rates = {}
_lock = Lock()


def reference_rate(base: str, quote: str) -> dict:
    if base not in CURRENCY_CODES or quote not in CURRENCY_CODES:
        raise ValueError("Unsupported currency")
    if base == quote:
        return {"base": base, "quote": quote, "rate": 1.0, "date": "", "source": ""}
    pair = (base, quote)
    with _lock:
        cached = _rates.get(pair)
        if cached and monotonic() - cached[0] < 1800:
            return dict(cached[1])
    # Only public currency codes leave the installation. No amounts or user data.
    response = httpx.get(
        f"https://api.frankfurter.dev/v2/rate/{base.lower()}/{quote.lower()}",
        params={"providers": "ecb"}, timeout=6.0, follow_redirects=False,
    )
    response.raise_for_status()
    data = response.json()
    try:
        rate = Decimal(str(data["rate"]))
        published = date.fromisoformat(data["date"])
        if not rate.is_finite() or rate <= 0 or published > date.today():
            raise ValueError("Invalid reference rate")
        if data["base"] != base or data["quote"] != quote:
            raise ValueError("Unexpected currency pair")
    except (KeyError, TypeError, InvalidOperation) as exc:
        raise ValueError("Invalid reference rate") from exc
    result = {"base": base, "quote": quote, "rate": float(rate), "date": published.isoformat(), "source": "ECB via Frankfurter"}
    with _lock:
        _rates[pair] = (monotonic(), result)
    return dict(result)


def presentation_settings(db, quote: str = "") -> dict:
    base = currency_settings(db)["code"]
    target = quote if quote in CURRENCY_CODES else base
    try:
        rate = reference_rate(base, target)
        return {**rate, "available": True}
    except (httpx.HTTPError, ValueError, TypeError):
        # Never attach a requested currency label to unconverted ledger amounts.
        return {"base": base, "quote": base, "rate": 1.0, "date": "", "source": "", "available": False}


def area_label(value):
    if not isinstance(value, str) or request_area.get() != "ft2":
        return value
    return value.replace("平方米", "平方英尺").replace("㎡", "ft²").replace("m²", "ft²").replace("sqm", "sqft")


def area_factor():
    return float(1 / SQM_PER_SQFT) if request_area.get() == "ft2" else 1.0


def view_amount(value):
    return float(value or 0) * request_fx.get()


def conversion_note():
    from app.core.currency_unit import request_currency
    if not request_fx_date.get():
        return ""
    return f"Reference conversion: 1 {request_book_currency.get()} = {request_fx.get():.8g} {request_currency.get()} | {request_fx_date.get()} | ECB via Frankfurter. Original records are unchanged."


def require_snapshot_book_currency(db):
    from fastapi import HTTPException
    if currency_settings(db)["code"] != "CNY":
        raise HTTPException(409, "The legacy snapshot-exchange schema requires CNY. It cannot be imported into a different book currency.")

"""Reuse pure contract calculations within one synchronous, read-only operation.

This deliberately stores no authorization decisions or database query results.
Only the explicitly decorated read entry points enable it. Nested readers share
the same scope; returning or raising discards every result. Mutation paths keep
the original uncached behavior. Keys use object identity, so a scenario clone
cannot reuse the formal contract's values even when both have the same row ID.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import wraps


MAX_CALCULATIONS = 32768


@dataclass
class CalculationContext:
    values: dict = field(default_factory=dict)
    contracts: dict = field(default_factory=dict)
    cost: int = 0


_current: ContextVar[CalculationContext | None] = ContextVar('contract_calculations', default=None)


@contextmanager
def calculation_scope():
    existing = _current.get()
    if existing is not None:
        yield existing
        return
    context = CalculationContext()
    token = _current.set(context)
    try:
        yield context
    finally:
        _current.reset(token)


def reuse_read_calculations(function):
    @wraps(function)
    def read(*args, **kwargs):
        with calculation_scope():
            return function(*args, **kwargs)
    return read


def _copy_value(value):
    # The participating rent functions return flat scalar dictionaries or lists
    # of those dictionaries. Callers must retain ownership of their returned data.
    if isinstance(value, dict):
        return value.copy()
    if isinstance(value, list):
        return [row.copy() for row in value]
    return value


def memoize_contract_calculation(function):
    @wraps(function)
    def calculate(contract, *args, **kwargs):
        context = _current.get()
        if context is None:
            return function(contract, *args, **kwargs)
        # populate_existing may reload this instance after another request edits
        # its contract/rooms/terms. An observed newer version must be recalculated.
        key = (function, id(contract), getattr(contract, 'updated_at', None), args, tuple(sorted(kwargs.items())))
        if key in context.values:
            return _copy_value(context.values[key])
        value = function(contract, *args, **kwargs)
        cost = max(1, len(value)) if isinstance(value, list) else 1
        if context.cost + cost <= MAX_CALCULATIONS:
            # Retaining the object prevents identity reuse during this operation.
            context.contracts[id(contract)] = contract
            context.values[key] = _copy_value(value)
            context.cost += cost
        return value
    return calculate

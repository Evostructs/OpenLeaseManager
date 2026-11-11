from __future__ import annotations

from app.core.money import round_money


def test_round_money_uses_half_up():
    """财务四舍五入：银行家舍入的三个经典反例必须按 HALF_UP 进位。"""
    assert round_money(2.675) == 2.68
    assert round_money(1.005) == 1.01
    assert round_money(0.125) == 0.13
    assert round_money(-2.675) == -2.68
    assert round_money(None) == 0.0
    assert round_money(10000 / 31 * 31) == 10000.0
    assert round_money("3.14159") == 3.14

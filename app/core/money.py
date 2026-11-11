from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

# 金额舍入统一入口：财务口径四舍五入（ROUND_HALF_UP）保留两位。
# Python 内置 round() 是银行家舍入（round(2.675,2)=2.67、round(1.005,2)=1.0），
# 与国内财务/税控开票的四舍五入预期不一致；所有金额舍入必须走本函数。
# 通过 str(float) 取最短往返十进制表示，规避浮点二进制误差
# （2.675 实际存储为 2.67499...，str() 还原为 "2.675" 后按十进制正确进位）。


def round_money(value: float | int | str | None, digits: int = 2) -> float:
    if value is None:
        return 0.0
    quant = Decimal(1).scaleb(-digits)
    return float(Decimal(str(value)).quantize(quant, rounding=ROUND_HALF_UP))

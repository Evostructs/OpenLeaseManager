from __future__ import annotations

import hashlib
import json
from typing import Final


DEFINITION_SET: Final = "OPS_V1"
DEFINITION_VERSION: Final = 1
DEFINITION_VALID_FROM: Final = "2026-08-05"
DEFINITION_OWNER: Final = "经营管理与数据治理"


_COMMON = {
    "version": DEFINITION_VERSION,
    "valid_from": DEFINITION_VALID_FROM,
    "valid_to": None,
    "grain": "building_month",
    "inclusion_rule": "仅纳入统计日一致、校验通过、已发布且未过期的楼宇事实。",
    "exclusion_rule": "缺失、过期、未复核、统计日不一致或分母无效的数据不纳入，不补零。",
    "coverage_rule": "同时披露授权、具备数据、实际纳入、面积与时间覆盖；缺失值返回空值及原因。",
    "estimation_policy": "默认不得估算；经批准的估算必须记录方法、比例和责任人。",
    "freshness_policy": "仅按业务统计截止日与来源承诺周期判断；接收、入库和发布时间不得替代业务截止日。",
    "owner": DEFINITION_OWNER,
}


def _definition(
    code: str,
    name: str,
    business_definition: str,
    *,
    unit: str,
    numerator_rule: str,
    denominator_rule: str,
    aggregation_rule: str,
    currency: str | None = None,
) -> dict:
    return {
        "code": code,
        "name": name,
        "business_definition": business_definition,
        "unit": unit,
        "currency": currency,
        "numerator_rule": numerator_rule,
        "denominator_rule": denominator_rule,
        "aggregation_rule": aggregation_rule,
        **_COMMON,
    }


OPS_V1_DEFINITIONS: Final[tuple[dict, ...]] = (
    _definition(
        "portfolio.building_count",
        "楼宇数",
        "当前用户获准且处于启用状态的楼宇去重数。",
        unit="building",
        numerator_rule="授权范围内启用楼宇去重计数。",
        denominator_rule="无。",
        aggregation_rule="derived_authorized_building_count",
    ),
    _definition(
        "area.rentable_sqm",
        "可出租面积",
        "符合统一面积定义的可出租面积合计。",
        unit="sqm",
        numerator_rule="已复核且口径一致的可出租面积。",
        denominator_rule="无。",
        aggregation_rule="sum_value",
    ),
    _definition(
        "area.leased_sqm",
        "在租面积",
        "统计日有效租赁区间覆盖的可出租面积去重合计。",
        unit="sqm",
        numerator_rule="统计日有效在租面积去重合计。",
        denominator_rule="无。",
        aggregation_rule="sum_value",
    ),
    _definition(
        "occupancy.area_rate",
        "面积出租率",
        "在租可出租面积除以可出租面积。",
        unit="percent",
        numerator_rule="在租面积。",
        denominator_rule="与分子同口径、同范围的可出租面积。",
        aggregation_rule="ratio_percent",
    ),
    _definition(
        "vacancy.area_sqm",
        "空置面积",
        "可出租面积减去在租面积。",
        unit="sqm",
        numerator_rule="同口径可出租面积减去在租面积。",
        denominator_rule="无。",
        aggregation_rule="sum_value",
    ),
    _definition(
        "rent.effective_daily",
        "有效平均日租",
        "区间有效租金金额除以在租面积日。",
        unit="CNY/sqm/day",
        currency="CNY",
        numerator_rule="区间有效租金金额；免租期租金计零。",
        denominator_rule="同期在租面积日；免租期面积日保留。",
        aggregation_rule="ratio",
    ),
    _definition(
        "billing.receivable_month",
        "本月应收",
        "有固化账单时取有效账单，否则按当期合同条款动态计算。",
        unit="CNY",
        currency="CNY",
        numerator_rule="账单优先、合同条款兜底的当月有效应收。",
        denominator_rule="无。",
        aggregation_rule="sum_value",
    ),
    _definition(
        "collection.received_month",
        "本月已收",
        "截至统计日已分配到当月有效应收的回款净额。",
        unit="CNY",
        currency="CNY",
        numerator_rule="归属当月应收的回款减冲销和退款。",
        denominator_rule="无。",
        aggregation_rule="sum_value",
    ),
    _definition(
        "collection.rate_month",
        "本月收缴率",
        "本月已收除以本月应收。",
        unit="percent",
        numerator_rule="本月已收。",
        denominator_rule="与分子同覆盖范围的本月应收。",
        aggregation_rule="ratio_percent",
    ),
    _definition(
        "arrears.balance",
        "逾期余额",
        "到期日早于统计日且尚未结清的有效应收余额。",
        unit="CNY",
        currency="CNY",
        numerator_rule="有效应收减已分配回款后的逾期余额。",
        denominator_rule="无。",
        aggregation_rule="sum_value",
    ),
    _definition(
        "expiry.contract_count_90d",
        "90天内到期合同数",
        "统计日起九十天内到期的当前有效合同数。",
        unit="contract",
        numerator_rule="符合条件的有效合同去重计数。",
        denominator_rule="无。",
        aggregation_rule="sum_value",
    ),
    _definition(
        "expiry.area_pressure_90d",
        "90天到期面积压力",
        "未来九十天内到期的当前在租面积除以当前在租面积。",
        unit="percent",
        numerator_rule="未来九十天内到期的当前在租面积。",
        denominator_rule="统计日当前在租面积。",
        aggregation_rule="ratio_percent",
    ),
    _definition(
        "expiry.area_pressure_180d",
        "180天到期面积压力",
        "未来一百八十天内到期的当前在租面积除以当前在租面积。",
        unit="percent",
        numerator_rule="未来一百八十天内到期的当前在租面积。",
        denominator_rule="统计日当前在租面积。",
        aggregation_rule="ratio_percent",
    ),
    _definition(
        "expiry.area_pressure_365d",
        "365天到期面积压力",
        "未来三百六十五天内到期的当前在租面积除以当前在租面积。",
        unit="percent",
        numerator_rule="未来三百六十五天内到期的当前在租面积。",
        denominator_rule="统计日当前在租面积。",
        aggregation_rule="ratio_percent",
    ),
    _definition(
        "expiry.rent_12m",
        "未来12个月到期租金",
        "未来三百六十五天内到期合同按当前条款计算的年化有效租金。",
        unit="CNY",
        currency="CNY",
        numerator_rule="符合条件合同的当前条款年化有效租金。",
        denominator_rule="无。",
        aggregation_rule="sum_value",
    ),
    _definition(
        "risk.open_items",
        "未关闭风险事项",
        "按统一规则仍未关闭的风险事项数。",
        unit="item",
        numerator_rule="未关闭风险事项去重计数。",
        denominator_rule="无。",
        aggregation_rule="sum_value",
    ),
)


OPS_V1_BY_CODE: Final = {item["code"]: item for item in OPS_V1_DEFINITIONS}
SUBMITTABLE_CODES: Final = frozenset(OPS_V1_BY_CODE) - {"portfolio.building_count"}


def definition_hash(definition: dict) -> str:
    canonical = json.dumps(
        definition,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

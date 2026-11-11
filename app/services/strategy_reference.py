"""Validate and resolve immutable forecast references for asset strategy inputs."""
from copy import deepcopy
from decimal import Decimal
import json
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError

class StrategyReference(BaseModel):
    model_config=ConfigDict(extra='ignore',str_strip_whitespace=True,allow_inf_nan=False)
    version_id: int=Field(gt=0)
    hold_adjustment_wan: float=Field(ge=-1e6,le=1e6)
    renovate_adjustment_wan: float=Field(ge=-1e6,le=1e6)
    reason: str=Field(default='',max_length=2000)


def resolved_strategy_state(db,building_id,client_state):
    state=deepcopy(client_state)
    strategy=state.get('strategy')
    if not isinstance(strategy,dict) or not strategy.get('reference'): return state
    try: reference=StrategyReference.model_validate(strategy['reference'])
    except ValidationError as error: raise HTTPException(422,'预测引用或调整项格式无效') from error
    if (reference.hold_adjustment_wan or reference.renovate_adjustment_wan) and not reference.reason:
        raise HTTPException(422,'请说明资产策略相对引用预测的调整依据')
    from app.services.financial_planning_store import get_version
    version=get_version(db,reference.version_id,building_id)
    if version.kind!='forecast': raise HTTPException(422,'资产策略只能引用已保存的预测版本')
    snapshot=json.loads(version.snapshot_json)
    try:
        base=Decimal(str(snapshot['totals']['net_cash_flow']))
        if not base.is_finite(): raise ValueError()
        for kind,adjustment in (('hold',reference.hold_adjustment_wan),('renovate',reference.renovate_adjustment_wan)):
            expected=base+Decimal(str(adjustment))
            actual=Decimal(str(strategy[kind]['annualNetCashFlow']))
            if not actual.is_finite() or abs(expected-actual)>Decimal('0.000001'):
                raise HTTPException(422,'策略首年现金须等于固定预测来源加明确调整，请重新应用引用')
            strategy[kind]['annualNetCashFlow']=float(expected)
    except (KeyError,TypeError,ValueError,ArithmeticError) as error:
        raise HTTPException(422,'引用版本缺少有效现金口径，不能自动填入策略') from error
    strategy['reference']={**reference.model_dump(),'version_name':version.name,'year':version.year,'revision':version.revision,'source_digest':version.source_digest,'net_cash_flow':float(base),'property_fee_cash_included':snapshot.get('coverage',{}).get('property_fee_cash_included'),'basis':'saved_baseline_net_cash_flow_excluding_hypothetical_measures'}
    return state

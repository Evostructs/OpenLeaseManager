"""Customer-confirmed handoff metadata, scoped to one building, never GL posting."""
import json
import re
import sqlite3
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.exc import IntegrityError, OperationalError

from app.core.time import now_in_timezone
from app.models import SystemConfig
from app.services.financial_planning_store import canonical_json, _log

ACCOUNTS = {'rent': '租金', 'property_fee': '物业费', 'deposit': '押金', 'opex': '经营费用', 'capex': '资本支出'}
FIELDS = {'company_name': '法人名称', 'company_code': '财务组织编码', 'erp_system': '财务系统', 'book_code': '账套编码', 'tax_basis': '金额含税口径'}
CODE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._/-]{0,39}$')


class MappingPayload(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    expected_revision: int = Field(ge=0)
    company_name: str = Field(default='', max_length=150)
    company_code: str = Field(default='', max_length=40)
    erp_system: str = Field(default='', max_length=80)
    book_code: str = Field(default='', max_length=40)
    tax_basis: Literal['', 'gross', 'net', 'not_applicable'] = ''
    accounts: dict[str, str] = Field(default_factory=dict, max_length=5)
    bank_accounts: dict[str, str] = Field(default_factory=dict, max_length=30)
    confirmed: bool = False
    note: str = Field(default='', max_length=2000)

    @model_validator(mode='before')
    @classmethod
    def unique_bank_labels(cls, data):
        # Pydantic's string stripping also normalizes dict keys. Check collisions
        # before normalization, otherwise one customer account silently wins.
        if isinstance(data, dict) and isinstance(data.get('bank_accounts'), dict):
            labels = [key.strip() for key in data['bank_accounts'] if isinstance(key, str)]
            if len(labels) != len(set(labels)):
                raise ValueError('银行简称重复，请核对账户映射')
        return data

    @model_validator(mode='after')
    def valid_codes(self):
        if set(self.accounts) - set(ACCOUNTS):
            raise ValueError('科目分类无效')
        self.accounts = {key: value.strip() for key, value in self.accounts.items()}
        clean_banks = {}
        for label, code in self.bank_accounts.items():
            label, code = label.strip(), code.strip()
            if not label or len(label) > 100 or label in clean_banks:
                raise ValueError('银行简称不能为空、重复或超过100字')
            clean_banks[label] = code
        self.bank_accounts = clean_banks
        for code in [self.company_code, self.book_code, *self.accounts.values(), *clean_banks.values()]:
            if code and not CODE.fullmatch(code):
                raise ValueError('财务编码仅支持字母、数字、点、斜杠、下划线及短横线，最长40位')
        if self.confirmed and missing(self.model_dump()):
            raise ValueError('请补齐法人、账套、金额口径、五类科目及银行映射后再确认')
        return self


def missing(profile):
    result = [label for key, label in FIELDS.items() if not profile.get(key)]
    result += [label + '科目' for key, label in ACCOUNTS.items() if not profile.get('accounts', {}).get(key)]
    if not profile.get('bank_accounts') or any(not code for code in profile.get('bank_accounts', {}).values()):
        result.append('银行账户映射')
    return result


def read_profile(db, bid):
    row = db.query(SystemConfig).filter_by(key=f'finance_mapping:{bid}').one_or_none()
    if row:
        try:
            profile = json.loads(row.value)
            MappingPayload.model_validate({'expected_revision': profile['revision'], **{k: profile[k] for k in MappingPayload.model_fields if k != 'expected_revision'}})
        except (ValueError, KeyError, TypeError):
            raise HTTPException(409, '财务映射配置异常，请联系管理员核查。') from None
    else:
        profile = {**MappingPayload(expected_revision=0).model_dump(exclude={'expected_revision'}),
                   'revision': 0, 'confirmed_by': '', 'updated_by': '', 'updated_at': ''}
    gaps = missing(profile)
    return {**profile, 'ready': bool(profile['confirmed'] and profile.get('confirmed_by') and not gaps),
            'missing': gaps + ([] if profile['confirmed'] else ['财务确认'])}


def save_profile(db, bid, user, payload):
    if user.role != 'admin':
        raise HTTPException(403, '财务映射须由有本楼编辑权限的管理员确认。')
    from app.services.finance_handoff import snapshot_transaction
    snapshot_transaction(db)
    row = db.query(SystemConfig).filter_by(key=f'finance_mapping:{bid}').one_or_none()
    current = read_profile(db, bid)
    if payload.expected_revision != current['revision']:
        raise HTTPException(409, '财务映射已被更新，请读取最新版本并核对填写。')
    value = canonical_json({**payload.model_dump(exclude={'expected_revision'}),
        'revision': current['revision'] + 1, 'confirmed_by': user.username if payload.confirmed else '',
        'updated_by': user.username, 'updated_at': now_in_timezone().isoformat()})
    try:
        if row:
            if db.query(SystemConfig).filter_by(id=row.id, value=row.value).update({'value': value}, synchronize_session=False) != 1:
                raise HTTPException(409, '财务映射已被更新，请核对后重试。')
        else:
            db.add(SystemConfig(key=f'finance_mapping:{bid}', value=value))
        _log(db, bid, user, 'finance_mapping_save', 'building', bid,
             f'revision={current["revision"] + 1} confirmed={payload.confirmed}')
        db.commit()
        return read_profile(db, bid)
    except (IntegrityError, OperationalError) as exc:
        db.rollback()
        if isinstance(exc, IntegrityError) or (isinstance(exc.orig, sqlite3.OperationalError) and 'locked' in str(exc.orig).lower()):
            raise HTTPException(409, '财务映射已被其他操作更新，请读取最新版本后重试。') from None
        raise


def handoff_mapping(profile, facts):
    bank_labels = sorted({b.get('account_label', '').strip() for b in facts.get('banks', [])})
    bank_gaps = [f'银行账户“{label or "未填写简称"}”未映射' for label in bank_labels if not profile['bank_accounts'].get(label)]
    return {**profile, 'ready': profile['ready'] and not bank_gaps, 'missing': profile['missing'] + bank_gaps}

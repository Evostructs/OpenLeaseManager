from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.services.property_scope import require_platform_admin
from app.database import get_db
from app.models import Building, SystemConfig, User
from app.schemas import FeishuConfigPayload
from app.services.common import get_operator, log_action
from app.services.legacy_demo_a_scope import ObjectScope
from app.tasks import building_feishu_config_key, check_expiring_contracts, send_feishu_message


router = APIRouter(prefix="/api/settings", tags=["settings"], dependencies=[Depends(require_platform_admin)])


def _get_config(db: Session, key: str) -> SystemConfig | None:
    return db.query(SystemConfig).filter(SystemConfig.key == key).first()


def _get_building(db: Session, building_code: str) -> Building:
    code = str(building_code or "").strip()
    building = (
        db.query(Building)
        .filter(func.lower(Building.code) == code.casefold(), Building.is_active.is_(True))
        .first()
    )
    if building is None:
        raise HTTPException(404, "楼宇不存在或未启用")
    return building


@router.get("/feishu")
def get_feishu_config(db: Session = Depends(get_db)):
    config = _get_config(db, "feishu_webhook")
    return {"webhook_url": config.value if config else ""}


@router.post("/feishu")
def set_feishu_config(data: FeishuConfigPayload, request: Request, db: Session = Depends(get_db)):
    config = _get_config(db, "feishu_webhook")
    if config:
        config.value = data.webhook_url
    else:
        config = SystemConfig(key="feishu_webhook", value=data.webhook_url)
        db.add(config)
    db.flush()
    log_action(
        db,
        None,
        "config_update",
        "更新飞书 Webhook 配置",
        get_operator(request),
        commit=False,
        target_type="system_config",
        target_id=config.id,
    )
    db.commit()
    return {"message": "飞书 Webhook 已保存"}


@router.post("/feishu/test")
def test_feishu(db: Session = Depends(get_db)):
    config = _get_config(db, "feishu_webhook")
    if not config or not config.value:
        return {"success": False, "message": "请先配置飞书 Webhook 地址"}
    ok = send_feishu_message(config.value, "📚 测试消息", "租赁管理系统飞书通知测试成功。")
    return {"success": ok, "message": "发送成功" if ok else "发送失败，请检查 Webhook 地址"}


@router.post("/feishu/trigger")
def trigger_check():
    check_expiring_contracts()
    return {"message": "已触发到期检查"}


@router.get("/feishu/buildings/{building_code}")
def get_building_feishu_config(building_code: str, db: Session = Depends(get_db)):
    building = _get_building(db, building_code)
    config = _get_config(db, building_feishu_config_key(int(building.id)))
    return {
        "building_code": building.code,
        "building_name": building.name,
        "webhook_url": config.value if config else "",
    }


@router.post("/feishu/buildings/{building_code}")
def set_building_feishu_config(
    building_code: str,
    data: FeishuConfigPayload,
    request: Request,
    db: Session = Depends(get_db),
):
    building = _get_building(db, building_code)
    key = building_feishu_config_key(int(building.id))
    config = _get_config(db, key)
    if config:
        config.value = data.webhook_url
    else:
        config = SystemConfig(key=key, value=data.webhook_url)
        db.add(config)
    db.flush()
    log_action(
        db,
        None,
        "config_update",
        f"更新 {building.name} 飞书通知配置",
        get_operator(request),
        commit=False,
        target_type="system_config",
        target_id=config.id,
        object_scope=ObjectScope("building", building_id=int(building.id)),
    )
    db.commit()
    return {"message": f"{building.name} 飞书 Webhook 已保存"}


@router.post("/feishu/buildings/{building_code}/test")
def test_building_feishu(building_code: str, db: Session = Depends(get_db)):
    building = _get_building(db, building_code)
    config = _get_config(db, building_feishu_config_key(int(building.id)))
    if not config or not (config.value or "").strip():
        return {"success": False, "message": "请先配置该楼宇的飞书 Webhook 地址"}
    ok = send_feishu_message(
        config.value,
        f"📚 {building.name} 测试消息",
        f"{building.name}租赁通知测试成功。",
    )
    return {"success": ok, "message": "发送成功" if ok else "发送失败，请检查 Webhook 地址"}

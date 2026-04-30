from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.access import require_admin
from app.database import get_db
from app.models import SystemConfig, User
from app.schemas import FeishuConfigPayload
from app.tasks import check_expiring_contracts, send_feishu_message


router = APIRouter(prefix="/api/settings", tags=["settings"], dependencies=[Depends(require_admin)])


def _get_config(db: Session, key: str) -> SystemConfig | None:
    return db.query(SystemConfig).filter(SystemConfig.key == key).first()


@router.get("/feishu")
def get_feishu_config(db: Session = Depends(get_db)):
    config = _get_config(db, "feishu_webhook")
    return {"webhook_url": config.value if config else ""}


@router.post("/feishu")
def set_feishu_config(data: FeishuConfigPayload, db: Session = Depends(get_db)):
    config = _get_config(db, "feishu_webhook")
    if config:
        config.value = data.webhook_url
    else:
        db.add(SystemConfig(key="feishu_webhook", value=data.webhook_url))
    db.commit()
    return {"message": "飞书 Webhook 已保存"}


@router.post("/feishu/test")
def test_feishu(db: Session = Depends(get_db)):
    config = _get_config(db, "feishu_webhook")
    if not config or not config.value:
        return {"success": False, "message": "请先配置飞书 Webhook 地址"}
    ok = send_feishu_message(config.value, "📚 测试消息", "合同管理系统飞书通知测试成功。")
    return {"success": ok, "message": "发送成功" if ok else "发送失败，请检查 Webhook 地址"}


@router.post("/feishu/trigger")
def trigger_check():
    check_expiring_contracts()
    return {"message": "已触发到期检查"}

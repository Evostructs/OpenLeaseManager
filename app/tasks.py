from __future__ import annotations

from datetime import date

import httpx
from apscheduler.schedulers.background import BackgroundScheduler
from pytz import timezone as tz

from app.core.config import get_settings
from app.database import SessionLocal
from app.models import Contract, SystemConfig
from app.services.backup import create_backup_archive
from app.services.integrations import process_pending_events


settings = get_settings()
scheduler = BackgroundScheduler(timezone=tz(settings.timezone))


def get_feishu_webhook(db) -> str:
    config = db.query(SystemConfig).filter(SystemConfig.key == "feishu_webhook").first()
    return config.value if config else ""


def send_feishu_message(webhook_url: str, title: str, content: str) -> bool:
    if not webhook_url:
        return False

    payload = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": title},
                "template": "red" if "紧急" in title else "orange",
            },
            "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": content}}],
        },
    }
    try:
        response = httpx.post(webhook_url, json=payload, timeout=10)
        return response.status_code == 200
    except Exception as exc:  # pragma: no cover - external IO
        print(f"飞书推送失败: {exc}")
        return False


def check_expiring_contracts() -> None:
    db = SessionLocal()
    try:
        webhook = get_feishu_webhook(db)
        if not webhook:
            return

        contracts = db.query(Contract).filter(Contract.is_deleted.is_(False)).all()
        today = date.today()
        urgent = []
        warning = []
        notice = []

        for contract in contracts:
            if not contract.end_date:
                continue
            days = (contract.end_date - today).days
            if days < 0:
                continue
            item = f"• **{contract.tenant_name}** | {contract.room_no} | 到期: {contract.end_date.strftime('%Y-%m-%d')} | 剩余 **{days}天**"
            if days <= 30:
                urgent.append(item)
            elif days <= 60:
                warning.append(item)
            elif days <= 90:
                notice.append(item)

        if not (urgent or warning or notice):
            return

        lines = [f"📮 {today.strftime('%Y年%m月%d日')} 租约到期提醒", ""]
        if urgent:
            lines.extend(["🔴 **30天内到期（紧急）**", *urgent, ""])
        if warning:
            lines.extend(["🟠 **60天内到期（预警）**", *warning, ""])
        if notice:
            lines.extend(["🔵 **90天内到期（关注）**", *notice])

        title = f"📚 租约到期提醒 {'⚠️紧急' if urgent else ''}".strip()
        send_feishu_message(webhook, title, "\n".join(lines))
    finally:
        db.close()


def auto_backup() -> None:
    create_backup_archive()


def dispatch_integration_events() -> None:
    db = SessionLocal()
    try:
        process_pending_events(db)
    finally:
        db.close()


def start_scheduler() -> None:
    if scheduler.running:
        return

    job_ids = {job.id for job in scheduler.get_jobs()}
    if "expiry_check" not in job_ids:
        scheduler.add_job(check_expiring_contracts, "cron", hour=9, minute=0, id="expiry_check")
    if "auto_backup" not in job_ids:
        scheduler.add_job(auto_backup, "cron", hour=2, minute=0, id="auto_backup")
    if "integration_outbox" not in job_ids:
        scheduler.add_job(dispatch_integration_events, "interval", minutes=1, id="integration_outbox")
    scheduler.start()

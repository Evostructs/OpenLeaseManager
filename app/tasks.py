from __future__ import annotations

from collections import defaultdict
import httpx
from apscheduler.schedulers.background import BackgroundScheduler
from pytz import timezone as tz

from app.core.config import get_settings
from app.core.time import today_in_timezone
from app.database import SessionLocal
from app.models import Building, Contract, SystemConfig
from app.services.backup import create_backup_archive, mirror_backup_archive, prune_backup_archives
from app.services.common import customer_display_name
from app.services.legacy_demo_a_scope import ObjectScope


settings = get_settings()
scheduler = BackgroundScheduler(timezone=tz(settings.timezone))


def get_feishu_webhook(db) -> str:
    config = db.query(SystemConfig).filter(SystemConfig.key == "feishu_webhook").first()
    return config.value if config else ""


def building_feishu_config_key(building_id: int) -> str:
    return f"feishu_webhook_building_{int(building_id)}"


def get_building_feishu_webhook(db, building: Building) -> str:
    config = (
        db.query(SystemConfig)
        .filter(SystemConfig.key == building_feishu_config_key(int(building.id)))
        .first()
    )
    if config and (config.value or "").strip():
        return config.value.strip()
    # Preserve the existing one-webhook DEMO_A setup only for the legacy building.
    # Every newly added building must explicitly choose its own recipient.
    if str(building.code or "").strip().casefold() == "demo_a":
        return get_feishu_webhook(db)
    return ""


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


def _feishu_mention(contract: Contract) -> str:
    feishu_id = (contract.internal_contact_feishu_id or "").strip()
    if feishu_id:
        if feishu_id.startswith("<at "):
            return feishu_id
        if feishu_id.startswith("@"):
            feishu_id = feishu_id[1:]
        return f"<at id={feishu_id}></at>"
    return contract.internal_contact_person or "未指定责任人"


def _owner_key(contract: Contract) -> str:
    return contract.internal_contact_person or contract.internal_contact_feishu_id or "未指定责任人"


def check_expiring_contracts() -> None:
    db = SessionLocal()
    try:
        today = today_in_timezone()
        buildings = (
            db.query(Building)
            .filter(
                Building.is_active.is_(True),
                Building.ingestion_mode == "detail",
            )
            .order_by(Building.code.asc(), Building.id.asc())
            .all()
        )
        for building in buildings:
            webhook = get_building_feishu_webhook(db, building)
            if not webhook:
                continue
            contracts = (
                db.query(Contract)
                .filter(
                    Contract.building_id == building.id,
                    Contract.is_deleted.is_(False),
                )
                .all()
            )
            urgent = defaultdict(list)
            warning = defaultdict(list)
            notice = defaultdict(list)

            for contract in contracts:
                if not contract.end_date:
                    continue
                if contract.disposal == "已退租":
                    continue
                days = (contract.end_date - today).days
                if days < 0:
                    continue
                item = (
                    f"• {_feishu_mention(contract)} | **{customer_display_name(contract) or contract.contract_no}**"
                    f" | {contract.room_no or '未标注房间'} | 合同: {contract.contract_no}"
                    f" | 到期: {contract.end_date.strftime('%Y-%m-%d')} | 剩余 **{days}天**"
                )
                owner = _owner_key(contract)
                if days <= 30:
                    urgent[owner].append(item)
                elif days <= 60:
                    warning[owner].append(item)
                elif days <= 90:
                    notice[owner].append(item)

            if not (urgent or warning or notice):
                continue

            lines = [
                f"📮 {today.strftime('%Y年%m月%d日')} 租约到期提醒",
                f"楼宇：{building.name}（{building.code}）",
                "",
            ]

            def append_section(title: str, grouped_items: dict[str, list[str]]) -> None:
                if not grouped_items:
                    return
                lines.append(title)
                for owner in sorted(grouped_items):
                    lines.extend(grouped_items[owner])
                lines.append("")

            if urgent:
                append_section("🔴 **30天内到期（紧急）**", urgent)
            if warning:
                append_section("🟠 **60天内到期（预警）**", warning)
            if notice:
                append_section("🔵 **90天内到期（关注）**", notice)

            title = (
                f"📚 {building.name}（{building.code}）租约到期提醒 "
                f"{'⚠️紧急' if urgent else ''}"
            ).strip()
            send_feishu_message(webhook, title, "\n".join(lines).strip())
    finally:
        db.close()


def auto_backup() -> None:
    try:
        result = create_backup_archive("auto")
        prune_backup_archives()
        mirror_backup_archive(result["filename"])
    except Exception as exc:
        # 定时备份失败绝不能静默：打日志并推送飞书告警
        print(f"自动备份失败: {exc}")
        db = SessionLocal()
        try:
            webhook = get_feishu_webhook(db)
        finally:
            db.close()
        send_feishu_message(
            webhook,
            "紧急：租赁系统自动备份失败",
            f"自动备份执行失败，请尽快检查磁盘空间与备份目录。\n错误：{exc}",
        )
        raise


def monthly_statement_freeze() -> None:
    """每月 1 日固化上月账单：出账后上月金额不再随合同变更回溯漂移。"""
    from dateutil.relativedelta import relativedelta

    from app.services.contracts import generate_receivable_statements

    previous_month = (today_in_timezone().replace(day=1) + relativedelta(months=-1)).strftime("%Y-%m")
    db = SessionLocal()
    try:
        buildings = (
            db.query(Building)
            .filter(
                Building.is_active.is_(True),
                Building.ingestion_mode == "detail",
            )
            .order_by(Building.code.asc(), Building.id.asc())
            .all()
        )
        completed: dict[str, dict] = {}
        failed: list[tuple[Building, str]] = []
        for building in buildings:
            try:
                completed[building.code] = generate_receivable_statements(
                    db,
                    previous_month,
                    "system",
                    building_ids=(int(building.id),),
                    object_scope=ObjectScope(
                        scope_type="building",
                        building_id=int(building.id),
                    ),
                )
            except Exception as exc:
                db.rollback()
                failed.append((building, type(exc).__name__))
        print(f"月度出账完成: month={previous_month}, buildings={completed}")
        if failed:
            failed_text = "、".join(
                f"{building.code}（{error_type}）"
                for building, error_type in failed
            )
            print(f"月度出账失败: {failed_text}")
            for building, error_type in failed:
                try:
                    webhook = get_building_feishu_webhook(db, building)
                    send_feishu_message(
                        webhook,
                        f"紧急：{building.name}月度出账失败",
                        f"{previous_month} 月度出账失败，请人工补出账。错误类型：{error_type}",
                    )
                except Exception as notify_exc:
                    print(
                        f"月度出账失败通知发送失败: building={building.code}, "
                        f"error={type(notify_exc).__name__}"
                    )
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
    if "monthly_statement_freeze" not in job_ids:
        scheduler.add_job(monthly_statement_freeze, "cron", day=1, hour=3, minute=0, id="monthly_statement_freeze")
    scheduler.start()

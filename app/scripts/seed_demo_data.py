from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
import os
import shutil

from dateutil.relativedelta import relativedelta

from app.core.config import get_settings
from app.core.time import today_in_timezone
from app.database import SessionLocal
from app.models import (
    Attachment,
    Contract,
    ContractDeposit,
    ContractRoom,
    Customer,
    CustomerFollowUp,
    OperationLog,
    ReceivablePayment,
    ReceivableRecord,
    ReceivableStatement,
    RentTerm,
    Room,
)
from app.services.common import deposit_status
from app.services.contracts import get_monthly_receivables
from app.services.legacy_demo_a_scope import require_legacy_demo_a_scope


SEED_OPERATOR = "demo_operator"


def resolve_demo_today() -> date:
    configured = os.environ.get("DEMO_TODAY", "").strip()
    if configured:
        return date.fromisoformat(configured)
    return today_in_timezone()


TODAY = resolve_demo_today()
DEMO_OVERDUE_CONTRACTS = 4
DEMO_OVERDUE_MONTHS = 2
DEMO_MISSING_CONTRACT_BODIES = 5
DEMO_MISSING_CUSTOMER_LICENSES = 4
DEMO_DEPOSIT_ISSUES = 4
DEMO_OVERDUE_FOLLOW_UPS = 4
DEMO_LARGE_ADJUSTMENTS = 2
DEMO_CURRENT_MONTH_COLLECTION_RATE = 0.78
DEMO_ATTACHMENT_BYTES = (
    b"%PDF-1.4\n"
    b"1 0 obj\n<< /Type /Catalog >>\nendobj\n"
    b"trailer\n<< /Root 1 0 R >>\n"
    b"%%EOF\n"
)

STATUS_ACTIVE = "在租"
STATUS_EXPIRING = "即将到期"
STATUS_HOLDOVER = "逾期占用"
STATUS_FUTURE = "待生效"
STATUS_EXITED = "已退租"

OFFICE_PREFIXES = [
    "星瀚",
    "远景",
    "云启",
    "华睿",
    "海岳",
    "诺川",
    "曜辰",
    "嘉策",
    "瑞衡",
    "领曜",
    "智帆",
    "锦川",
]

OFFICE_SUFFIXES = [
    "科技",
    "资本",
    "咨询",
    "律师事务所",
    "医疗",
    "数据",
    "智造",
    "供应链",
    "能源",
    "生物",
    "设计",
    "国际",
]

RETAIL_NAMES = [
    "南塔便利站",
    "合序咖啡",
    "拾味轻食",
    "泊岸茶饮",
    "晨屿烘焙",
    "一里鲜果",
    "白石简餐",
    "朝雾便利",
    "澄明花艺",
    "好食集厨房",
]

CONTACT_NAMES = [f"示例联系人{index:02d}" for index in range(1, 21)]

INTERNAL_CONTACTS = [(f"示例经办人{index}", f"demo_operator_{index}") for index in range(1, 6)]

MONTH_SERIES = [date(year, month, 1) for year in range(2021, 2027) for month in range(1, 13)]
PAST_MONTHS = [item for item in MONTH_SERIES if item <= date(TODAY.year, TODAY.month, 1)]
FUTURE_MONTHS = [item for item in MONTH_SERIES if item > date(TODAY.year, TODAY.month, 1)]


@dataclass(frozen=True)
class RoomSeed:
    tower: str
    floor_number: int
    floor_label: str
    room_no: str
    room_type: str
    area: float
    zone: str
    layout: str
    is_whole_floor: bool
    notes: str = ""


@dataclass(frozen=True)
class ContractSeed:
    contract_no: str
    tower: str
    room_no: str
    tenant_name: str
    area: float
    unit_price: float
    lease_type: str
    free_period: int
    monthly_rent: float
    broker_fee: float
    start_date: date
    end_date: date
    disposal: str
    contact_person: str
    contact_phone: str
    contact_email: str
    internal_contact_person: str
    internal_contact_feishu_id: str
    deposit: float
    deposit_status: str
    property_fee_rate: float
    property_fee_free: int
    prev_contract_no: str
    notes: str


SOUTH_LOW_FLOORS = [3, 5, 6, 7, 8, 9, 10, 11, 12]
SOUTH_MID_FLOORS = [15, 16, 17, 18, 19, 20, 21, 22, 23]
SOUTH_HIGH_FLOORS = [26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36]
NORTH_LOW_FLOORS = [3, 5, 6, 7, 8, 9, 10]
NORTH_HIGH_FLOORS = [11, 12, 15, 16, 17, 18, 19, 20, 21, 22]

SOUTH_LOW_FLOOR_COUNTS = [6, 5, 5, 4, 5, 4, 5, 4, 5]
SOUTH_MID_FLOOR_COUNTS = [4, 4, 4, 3, 3, 4, 3, 3, 4]
SOUTH_HIGH_FLOOR_COUNTS = [3, 3, 2, 2, 3, 2, 2, 2, 2, 2, 1]
NORTH_LOW_FLOOR_COUNTS = [6, 6, 6, 6, 6, 6, 6]
NORTH_HIGH_FLOOR_COUNTS = [6, 5, 5, 4, 5, 4, 5, 4, 5, 4]


def ratio_template(unit_count: int, room_type: str) -> list[float]:
    if room_type == "办公":
        templates = {
            1: [1.0],
            2: [0.48, 0.52],
            3: [0.25, 0.33, 0.42],
            4: [0.17, 0.22, 0.27, 0.34],
            5: [0.12, 0.16, 0.2, 0.24, 0.28],
            6: [0.1, 0.12, 0.15, 0.18, 0.2, 0.25],
        }
    else:
        templates = {
            1: [1.0],
            2: [0.46, 0.54],
            3: [0.18, 0.34, 0.48],
            4: [0.12, 0.18, 0.27, 0.43],
            5: [0.1, 0.14, 0.18, 0.23, 0.35],
            6: [0.09, 0.1, 0.12, 0.16, 0.2, 0.33],
        }
    return templates[unit_count]


def layout_name(unit_count: int) -> str:
    if unit_count == 1:
        return "整层"
    if unit_count == 2:
        return "双拼"
    if unit_count <= 4:
        return "分租"
    return "零散"


def zone_label(zone_code: str) -> str:
    labels = {
        "A": "北塔低区",
        "B": "北塔高区",
        "C": "南塔低区",
        "D": "南塔中区",
        "E": "南塔高区",
        "N1": "北塔底商一层",
        "N2": "北塔底商二层",
        "S1": "南塔底商一层",
        "S2": "南塔底商二层",
        "P2": "南塔底商二层主力店",
        "P3": "南塔底商三层主力店",
    }
    return labels[zone_code]


def office_floor_rooms(tower: str, floor_number: int, total_area: float, unit_count: int, zone_code: str) -> list[RoomSeed]:
    layout = layout_name(unit_count)
    ratios = ratio_template(unit_count, "办公")
    rooms: list[RoomSeed] = []
    for index, ratio in enumerate(ratios, start=1):
        area = round(total_area * ratio, 2)
        room_no = f"{'S' if tower == '南塔' else 'N'}{floor_number:02d}{index:02d}"
        rooms.append(
            RoomSeed(
                tower=tower,
                floor_number=floor_number,
                floor_label=str(floor_number),
                room_no=room_no,
                room_type="办公",
                area=area,
                zone=zone_code,
                layout=layout,
                is_whole_floor=unit_count == 1,
                notes=f"模拟数据：{zone_label(zone_code)}，{layout}办公单元",
            )
        )
    return rooms


def south_tower_retail_floor(floor_number: int) -> list[RoomSeed]:
    total_area = 1580 if floor_number == 1 else 1660
    ratios = ratio_template(6, "商业")
    rooms: list[RoomSeed] = []
    for index, ratio in enumerate(ratios, start=1):
        room_type = "餐饮" if floor_number == 2 and index in {1, 2, 5} else "商业"
        rooms.append(
            RoomSeed(
                tower="南塔",
                floor_number=floor_number,
                floor_label=str(floor_number),
                room_no=f"S{floor_number:02d}{index:02d}",
                room_type=room_type,
                area=round(total_area * ratio, 2),
                zone=f"S{floor_number}",
                layout="商铺分租",
                is_whole_floor=False,
                notes="模拟数据：南塔底商，餐饮与便利店分布在一至二层",
            )
        )
    return rooms


def north_tower_retail_floor(floor_number: int) -> list[RoomSeed]:
    total_area = 1320 if floor_number == 1 else 1460
    ratios = ratio_template(4, "商业")
    rooms: list[RoomSeed] = []
    for index, ratio in enumerate(ratios, start=1):
        room_type = "餐饮" if floor_number == 1 and index in {1, 2} else "商业"
        rooms.append(
            RoomSeed(
                tower="北塔",
                floor_number=floor_number,
                floor_label=str(floor_number),
                room_no=f"N{floor_number:02d}{index:02d}",
                room_type=room_type,
                area=round(total_area * ratio, 2),
                zone=f"N{floor_number}",
                layout="商铺分租",
                is_whole_floor=False,
                notes="模拟数据：北塔底商，一至二层商业配套单元",
            )
        )
    return rooms


def south_podium_rooms() -> list[RoomSeed]:
    return [
        RoomSeed("南塔", 2, "2", "P0201", "餐饮", 1820.0, "P2", "整层", True, "模拟数据：南塔底商二层高端餐饮主力店"),
        RoomSeed("南塔", 3, "3", "P0301", "健身", 1180.0, "P3", "主力店", False, "模拟数据：南塔底商三层健身房主馆"),
        RoomSeed("南塔", 3, "3", "P0302", "健身", 620.0, "P3", "配套", False, "模拟数据：南塔底商三层健身配套及康复区"),
    ]


def build_room_seeds() -> list[RoomSeed]:
    rooms: list[RoomSeed] = []
    rooms.extend(north_tower_retail_floor(1))
    rooms.extend(north_tower_retail_floor(2))
    rooms.extend(south_tower_retail_floor(1))
    rooms.extend(south_tower_retail_floor(2))

    for floor_number, unit_count in zip(SOUTH_LOW_FLOORS, SOUTH_LOW_FLOOR_COUNTS):
        rooms.extend(office_floor_rooms("南塔", floor_number, 2270.0, unit_count, "C"))
    for floor_number, unit_count in zip(SOUTH_MID_FLOORS, SOUTH_MID_FLOOR_COUNTS):
        rooms.extend(office_floor_rooms("南塔", floor_number, 2316.0, unit_count, "D"))
    for floor_number, unit_count in zip(SOUTH_HIGH_FLOORS, SOUTH_HIGH_FLOOR_COUNTS):
        rooms.extend(office_floor_rooms("南塔", floor_number, 2497.0, unit_count, "E"))
    for floor_number, unit_count in zip(NORTH_LOW_FLOORS, NORTH_LOW_FLOOR_COUNTS):
        rooms.extend(office_floor_rooms("北塔", floor_number, 2289.0, unit_count, "A"))
    for floor_number, unit_count in zip(NORTH_HIGH_FLOORS, NORTH_HIGH_FLOOR_COUNTS):
        rooms.extend(office_floor_rooms("北塔", floor_number, 2330.0, unit_count, "B"))

    rooms.extend(south_podium_rooms())
    return rooms


def stable_noise(value: str) -> float:
    return (sum(ord(char) for char in value) % 19) / 10


def calculate_monthly_rent(area: float, unit_price: float) -> float:
    return round(area * unit_price * 365 / 12, 2)


def calculate_broker_fee(monthly_rent: float, sequence: int) -> float:
    months = [2.5, 2.7, 2.9][sequence % 3]
    return round(monthly_rent * months, 2)


def occupancy_score(room: RoomSeed) -> float:
    score = 0.0
    if room.room_type == "餐饮":
        score += 32
    elif room.room_type == "健身":
        score += 28
    elif room.room_type == "商业":
        score += 24
    else:
        score += 12

    score += {"南塔": 10, "北塔": 8, "商业配套": 14}.get(room.tower, 0)
    score += {"E": 18, "D": 14, "B": 12, "C": 10, "A": 8}.get(room.zone, 6)
    if room.is_whole_floor:
        score += 9
    elif room.layout in {"双拼", "分租"}:
        score += 5
    score += room.floor_number * 0.6
    score += stable_noise(room.room_no)
    return score


def lease_term_months(room: RoomSeed, state: str, sequence: int) -> int:
    if room.room_type == "餐饮":
        return 72 if room.tower == "商业配套" and state in {STATUS_ACTIVE, STATUS_EXPIRING} else 48 + (sequence % 3) * 12
    if room.room_type == "健身":
        return 60 if state in {STATUS_ACTIVE, STATUS_EXPIRING, STATUS_HOLDOVER} else 48
    if room.room_type == "商业":
        return 24 + (sequence % 4) * 12
    if room.is_whole_floor:
        return 60 + (sequence % 3) * 12
    if room.layout == "双拼":
        return 36 + (sequence % 3) * 12
    if room.layout == "分租":
        return 24 + (sequence % 3) * 12
    return 24 + (sequence % 2) * 12


def select_month(pool: list[date], sequence: int, stride: int) -> date:
    return pool[(sequence * stride + sequence // 3) % len(pool)]


def select_month_day(month: date, sequence: int, salt: int, min_day: int = 2, max_day: int = 27) -> date:
    month_end = month + relativedelta(months=1, days=-1)
    day_range = max_day - min_day + 1
    day = min_day + ((sequence * 7 + salt) % day_range)
    return month.replace(day=min(day, month_end.day))


def build_dates(room: RoomSeed, state: str, sequence: int) -> tuple[date, date]:
    term_months = lease_term_months(room, state, sequence)
    if state == STATUS_ACTIVE:
        start_date = select_month_day(select_month(PAST_MONTHS, sequence, 11), sequence, room.floor_number)
        if start_date > TODAY:
            start_date = start_date - relativedelta(months=1)
        end_date = start_date + relativedelta(months=term_months) - timedelta(days=1)
        while end_date <= TODAY + timedelta(days=120):
            term_months += 12
            end_date = start_date + relativedelta(months=term_months) - timedelta(days=1)
        return start_date, end_date
    if state == STATUS_EXPIRING:
        end_date = TODAY + timedelta(days=10 + (sequence % 70))
        start_date = end_date - relativedelta(months=term_months) + timedelta(days=1)
        while start_date < date(2021, 1, 1):
            term_months -= 12
            start_date = end_date - relativedelta(months=max(term_months, 24)) + timedelta(days=1)
        return start_date, end_date
    if state == STATUS_HOLDOVER:
        end_date = TODAY - timedelta(days=3 + (sequence % 30))
        start_date = end_date - relativedelta(months=max(term_months, 24)) + timedelta(days=1)
        return max(start_date, date(2021, 1, 1)), end_date
    if state == STATUS_FUTURE:
        start_date = select_month_day(select_month(FUTURE_MONTHS, sequence, 5), sequence, room.floor_number)
        end_date = start_date + relativedelta(months=term_months) - timedelta(days=1)
        return start_date, end_date

    end_month = select_month([item for item in PAST_MONTHS if item <= date(TODAY.year, TODAY.month, 1)], sequence, 7)
    end_date = select_month_day(end_month, sequence, room.floor_number, 5, 27)
    if end_date >= TODAY - timedelta(days=120):
        end_date = TODAY - timedelta(days=140 + (sequence % 120))
    start_date = end_date - relativedelta(months=max(12, term_months - 12)) + timedelta(days=1)
    return max(start_date, date(2021, 1, 1)), end_date


def room_rate(room: RoomSeed, sequence: int) -> float:
    if room.room_type == "餐饮":
        return round(4.8 + (sequence % 3) * 0.18, 2) if room.tower == "商业配套" else round(4.2 + room.floor_number * 0.04 + (sequence % 3) * 0.08, 2)
    if room.room_type == "健身":
        return round(3.7 + (sequence % 3) * 0.12, 2)
    if room.room_type == "商业":
        return round(3.9 + room.floor_number * 0.08 + (sequence % 2) * 0.12, 2)

    if room.zone == "A":
        base = 3.15 + (room.floor_number - 3) * 0.05
    elif room.zone == "B":
        base = 3.45 + (room.floor_number - 11) * 0.05
    elif room.zone == "C":
        base = 3.3 + (room.floor_number - 3) * 0.05
    elif room.zone == "D":
        base = 3.7 + (room.floor_number - 16) * 0.05
    else:
        base = 4.15 + (room.floor_number - 26) * 0.06

    if room.is_whole_floor:
        base -= 0.28
    elif room.layout == "双拼":
        base -= 0.12
    elif room.layout == "零散":
        base += 0.16
    if room.area <= 350:
        base += 0.12
    elif room.area >= 1800:
        base -= 0.1
    return round(base, 2)


def build_tenant_name(room: RoomSeed, sequence: int) -> str:
    if room.room_no == "P0201":
        return "澜庭宴会中心"
    if room.room_no == "P0301":
        return "动线健身俱乐部"
    if room.room_no == "P0302":
        return "动线康复工作室"
    if room.room_type in {"餐饮", "商业"}:
        return RETAIL_NAMES[sequence % len(RETAIL_NAMES)]
    prefix = OFFICE_PREFIXES[sequence % len(OFFICE_PREFIXES)]
    suffix = OFFICE_SUFFIXES[(sequence * 3) % len(OFFICE_SUFFIXES)]
    return f"{prefix}{suffix}"


def lease_type(room: RoomSeed, sequence: int, state: str) -> str:
    if sequence % 19 == 0:
        return "减租"
    if sequence % 17 == 0:
        return "扩租"
    if sequence % 13 == 0:
        return "变更签"
    if sequence % 11 == 0:
        return "换签"
    if room.is_whole_floor and sequence % 3 == 0:
        return "续签"
    if room.room_type in {"餐饮", "健身"} and sequence % 4 == 0:
        return "续签"
    return "新签"


def free_period_months(room: RoomSeed, term_months: int, state: str) -> tuple[int, int]:
    if state in {STATUS_EXITED, STATUS_HOLDOVER}:
        return 0, 0
    if room.room_type == "餐饮":
        months = 3 if room.tower == "商业配套" else 2
        return months, 0
    if room.room_type == "健身":
        return 3, 0
    if room.room_type == "商业":
        return 1, 0
    if room.is_whole_floor:
        months = 6 if term_months >= 72 else 4
        return months, 0
    if room.layout == "双拼":
        months = 2 if term_months <= 36 else 3
        return months, 0
    return (1, 0) if term_months <= 36 else (2, 0)


def property_fee_rate(room: RoomSeed) -> float:
    if room.room_type == "办公":
        return 28.0
    return 35.0


def deposit_multiple(room: RoomSeed, state: str) -> int:
    if state == STATUS_FUTURE:
        return 2
    if room.room_type in {"餐饮", "健身"}:
        return 4
    if room.is_whole_floor:
        return 4
    return 3


def disposal_from_state(state: str) -> str:
    if state == STATUS_EXITED:
        return "已退租"
    if state == STATUS_HOLDOVER:
        return "逾期占用"
    return ""


def pick_contact(sequence: int) -> tuple[str, str, str]:
    name = CONTACT_NAMES[sequence % len(CONTACT_NAMES)]
    phone = f"000{sequence:08d}"
    email = f"leasing{sequence:03d}@example.invalid"
    return name, phone, email


def pick_internal_contact(sequence: int) -> tuple[str, str]:
    return INTERNAL_CONTACTS[(sequence * 2 + sequence // 5) % len(INTERNAL_CONTACTS)]


def build_contract_seed(room: RoomSeed, state: str, sequence: int) -> ContractSeed:
    start_date, end_date = build_dates(room, state, sequence)
    term_months = max(1, (end_date.year - start_date.year) * 12 + (end_date.month - start_date.month))
    gross_rate = room_rate(room, sequence)
    lease_kind = lease_type(room, sequence, state)
    free_period, property_free = free_period_months(room, term_months, state)
    monthly_rent = calculate_monthly_rent(room.area, gross_rate)
    broker_fee = calculate_broker_fee(monthly_rent, sequence)
    deposit = round(monthly_rent * deposit_multiple(room, state), 2)
    deposit_status = "待收" if state == STATUS_FUTURE else "已收"
    prev_contract_no = f"PRE-{room.room_no}-{sequence:03d}" if lease_kind != "新签" else ""
    tenant_name = build_tenant_name(room, sequence)
    contact_person, contact_phone, contact_email = pick_contact(sequence)
    internal_contact_person, internal_contact_feishu_id = pick_internal_contact(sequence)
    return ContractSeed(
        contract_no=f"DEMO_A-{TODAY.year}-{sequence:03d}",
        tower=room.tower,
        room_no=room.room_no,
        tenant_name=tenant_name,
        area=room.area,
        unit_price=gross_rate,
        lease_type=lease_kind,
        free_period=free_period,
        monthly_rent=monthly_rent,
        broker_fee=broker_fee,
        start_date=start_date,
        end_date=end_date,
        disposal=disposal_from_state(state),
        contact_person=contact_person,
        contact_phone=contact_phone,
        contact_email=contact_email,
        internal_contact_person=internal_contact_person,
        internal_contact_feishu_id=internal_contact_feishu_id,
        deposit=deposit,
        deposit_status=deposit_status,
        property_fee_rate=property_fee_rate(room),
        property_fee_free=property_free,
        prev_contract_no=prev_contract_no,
        notes=f"模拟数据：{zone_label(room.zone)}，{room.layout}{room.room_type}，用于演示招商和租金分析。",
    )


def assign_contract_states(rooms: list[RoomSeed]) -> list[tuple[RoomSeed, str]]:
    ranked_rooms = sorted(rooms, key=occupancy_score, reverse=True)
    active_rooms = ranked_rooms[:112]
    expiring_rooms = ranked_rooms[112:130]
    holdover_rooms = ranked_rooms[130:134]
    future_rooms = ranked_rooms[134:152]
    exited_rooms = ranked_rooms[152:176]

    assignments: list[tuple[RoomSeed, str]] = []
    assignments.extend((room, STATUS_ACTIVE) for room in active_rooms)
    assignments.extend((room, STATUS_EXPIRING) for room in expiring_rooms)
    assignments.extend((room, STATUS_HOLDOVER) for room in holdover_rooms)
    assignments.extend((room, STATUS_FUTURE) for room in future_rooms)
    assignments.extend((room, STATUS_EXITED) for room in exited_rooms)
    return assignments


def build_contract_seeds(rooms: list[RoomSeed]) -> list[ContractSeed]:
    assignments = assign_contract_states(rooms)
    return [build_contract_seed(room, state, sequence) for sequence, (room, state) in enumerate(assignments, start=1)]


def reset_demo_tables() -> None:
    with SessionLocal() as db:
        db.query(Attachment).delete()
        db.query(CustomerFollowUp).delete()
        db.query(ContractDeposit).delete()
        db.query(ReceivablePayment).delete()
        db.query(ReceivableRecord).delete()
        db.query(ReceivableStatement).delete()
        db.query(RentTerm).delete()
        db.query(ContractRoom).delete()
        db.query(OperationLog).delete()
        db.query(Contract).delete()
        db.query(Customer).delete()
        db.query(Room).delete()
        db.commit()
    demo_attachment_dir = (get_settings().attachment_dir / "demo").resolve()
    attachment_dir = get_settings().attachment_dir.resolve()
    if demo_attachment_dir.exists() and demo_attachment_dir.is_dir() and demo_attachment_dir.parent == attachment_dir:
        shutil.rmtree(demo_attachment_dir)


def insert_rooms(rooms: list[RoomSeed]) -> int:
    with SessionLocal() as db:
        scope = require_legacy_demo_a_scope(db)
        for seed in rooms:
            db.add(
                Room(
                    building_id=scope.building_id,
                    zone_id=scope.zone_ids_by_tower[seed.tower],
                    tower=seed.tower,
                    floor=seed.floor_label,
                    room_no=seed.room_no,
                    room_type=seed.room_type,
                    area=seed.area,
                    is_leasable=True,
                    notes=seed.notes,
                    is_deleted=False,
                )
            )
        db.commit()
    return len(rooms)


def insert_contracts(contracts: list[ContractSeed]) -> int:
    with SessionLocal() as db:
        scope = require_legacy_demo_a_scope(db)
        room_numbers = [seed.room_no for seed in contracts]
        rooms_by_number = {
            room.room_no: room
            for room in (
                db.query(Room)
                .filter(
                    Room.building_id == scope.building_id,
                    Room.room_no.in_(room_numbers),
                    Room.is_deleted.is_(False),
                )
                .all()
            )
        }
        missing_room_numbers = sorted(set(room_numbers) - rooms_by_number.keys())
        if missing_room_numbers:
            preview = "、".join(missing_room_numbers[:10])
            raise RuntimeError(f"模拟合同存在未归属 DEMO_A 的房源：{preview}")

        for seed in contracts:
            room = rooms_by_number[seed.room_no]
            customer = Customer(
                name=f"{seed.tenant_name}-{seed.contract_no[-3:]}",
                short_name=seed.tenant_name[:20],
                contact_name=seed.contact_person,
                contact_phone=seed.contact_phone,
                contact_email=seed.contact_email,
                status="active",
                notes="模拟数据：随合同生成的客户主档。",
            )
            db.add(customer)
            db.flush()
            contract = Contract(
                contract_no=seed.contract_no,
                customer_id=customer.id,
                building_id=scope.building_id,
                tower=seed.tower,
                room_no=seed.room_no,
                tenant_name=seed.tenant_name,
                area=seed.area,
                unit_price=seed.unit_price,
                lease_type=seed.lease_type,
                free_period=seed.free_period,
                monthly_rent=seed.monthly_rent,
                broker_fee=seed.broker_fee,
                start_date=seed.start_date,
                end_date=seed.end_date,
                disposal=seed.disposal,
                contact_person=seed.contact_person,
                contact_phone=seed.contact_phone,
                contact_email=seed.contact_email,
                internal_contact_person=seed.internal_contact_person,
                internal_contact_feishu_id=seed.internal_contact_feishu_id,
                deposit=seed.deposit,
                deposit_status=seed.deposit_status,
                property_fee_rate=seed.property_fee_rate,
                property_fee_free=seed.property_fee_free,
                prev_contract_no=seed.prev_contract_no,
                notes=seed.notes,
                pdf_path="",
                is_deleted=False,
            )
            db.add(contract)
            db.flush()
            db.add(
                ContractRoom(
                    contract_id=contract.id,
                    building_id=scope.building_id,
                    room_id=room.id,
                    tower=seed.tower,
                    room_no=seed.room_no,
                    area=seed.area,
                    unit_price=seed.unit_price,
                    monthly_rent=seed.monthly_rent,
                    notes=seed.notes,
                )
            )
            db.add(
                OperationLog(
                    contract_id=contract.id,
                    scope_type="building",
                    building_id=scope.building_id,
                    target_type="contract",
                    target_id=contract.id,
                    action="create",
                    changes="生成模拟合同数据",
                    operator=SEED_OPERATOR,
                )
            )
        db.commit()
    return len(contracts)


def _current_contracts(db) -> list[Contract]:
    scope = require_legacy_demo_a_scope(db)
    contracts = (
        db.query(Contract)
        .filter(
            Contract.building_id == scope.building_id,
            Contract.is_deleted.is_(False),
        )
        .order_by(Contract.contract_no.asc())
        .all()
    )
    return [contract for contract in contracts if contract.status in {"在租", "即将到期", "逾期占用"} and contract.disposal != "已退租"]


def _demo_due_months(months: int) -> list[date]:
    anchor = TODAY.replace(day=1)
    start = anchor + relativedelta(months=-months)
    return [start + relativedelta(months=offset) for offset in range(months)]


def _demo_receivable_months(contracts: list[Contract]) -> list[date]:
    starts = [contract.start_date.replace(day=1) for contract in contracts if contract.start_date]
    if not starts:
        return []
    start = min(starts)
    end = TODAY.replace(day=1)
    months = []
    current = start
    while current <= end:
        months.append(current)
        current += relativedelta(months=1)
    return months


def _write_demo_attachment_file(storage_path: str) -> int:
    target = (get_settings().attachment_dir / storage_path).resolve()
    attachment_dir = get_settings().attachment_dir.resolve()
    target.relative_to(attachment_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(DEMO_ATTACHMENT_BYTES)
    return len(DEMO_ATTACHMENT_BYTES)


def _demo_attachment(
    entity_type: str,
    entity_id: int,
    category: str,
    file_name: str,
    notes: str,
    *,
    building_id: int,
) -> Attachment:
    storage_path = f"demo/{entity_type}/{entity_id}/{file_name}"
    file_size = _write_demo_attachment_file(storage_path)
    return Attachment(
        scope_type="building",
        building_id=building_id,
        entity_type=entity_type,
        entity_id=entity_id,
        file_name=file_name,
        storage_path=storage_path,
        content_type="application/pdf",
        file_size=file_size,
        category=category,
        notes=notes,
        uploaded_by=SEED_OPERATOR,
    )


def _seed_demo_risk_context(db, contracts: list[Contract]) -> dict[str, int]:
    scope = require_legacy_demo_a_scope(db)
    if any(contract.building_id != scope.building_id for contract in contracts):
        raise RuntimeError("模拟运营数据仅允许写入 DEMO_A 合同")
    overdue_contract_ids = {contract.id for contract in contracts[:DEMO_OVERDUE_CONTRACTS]}
    missing_body_ids = {contract.id for contract in contracts[DEMO_OVERDUE_CONTRACTS : DEMO_OVERDUE_CONTRACTS + DEMO_MISSING_CONTRACT_BODIES]}
    license_start = DEMO_OVERDUE_CONTRACTS + DEMO_MISSING_CONTRACT_BODIES
    missing_license_customer_ids = {
        contract.customer_id
        for contract in contracts[license_start : license_start + DEMO_MISSING_CUSTOMER_LICENSES]
        if contract.customer_id
    }
    deposit_start = license_start + DEMO_MISSING_CUSTOMER_LICENSES
    deposit_issue_order = {
        contract.id: index
        for index, contract in enumerate(contracts[deposit_start : deposit_start + DEMO_DEPOSIT_ISSUES], start=1)
    }
    deposit_issue_ids = set(deposit_issue_order)
    follow_up_start = deposit_start + DEMO_DEPOSIT_ISSUES
    follow_up_contracts = contracts[follow_up_start : follow_up_start + DEMO_OVERDUE_FOLLOW_UPS]
    adjustment_start = follow_up_start + DEMO_OVERDUE_FOLLOW_UPS
    adjustment_contracts = contracts[adjustment_start : adjustment_start + DEMO_LARGE_ADJUSTMENTS]

    for contract in contracts:
        if contract.id not in missing_body_ids:
            db.add(
                _demo_attachment(
                    "contract",
                    contract.id,
                    "合同正文",
                    f"{contract.contract_no}-main.pdf",
                    "模拟数据：已归档合同正文。",
                    building_id=scope.building_id,
                )
            )
        if contract.customer_id and contract.customer_id not in missing_license_customer_ids:
            db.add(
                _demo_attachment(
                    "customer",
                    contract.customer_id,
                    "证照",
                    f"customer-{contract.customer_id}-license.pdf",
                    "模拟数据：已归档客户证照。",
                    building_id=scope.building_id,
                )
            )

        expected = round(float(contract.deposit or 0), 2)
        received = expected
        due_date = max(contract.start_date, TODAY - timedelta(days=120)) if contract.start_date else TODAY - timedelta(days=120)
        received_date = due_date
        if contract.id in deposit_issue_ids:
            offset = deposit_issue_order[contract.id]
            received = 0 if offset % 2 else round(expected * 0.5, 2)
            due_date = TODAY - timedelta(days=15 + offset)
            received_date = None if received <= 0 else due_date
        db.add(
            ContractDeposit(
                contract_id=contract.id,
                customer_id=contract.customer_id,
                deposit_type="租赁押金",
                expected_amount=expected,
                received_amount=received,
                refunded_amount=0,
                offset_amount=0,
                status=deposit_status(expected, received, 0, 0),
                due_date=due_date,
                received_date=received_date,
                notes="模拟数据：默认押金已足额到账，少量合同保留未收或不足样本。",
            )
        )

    for contract in follow_up_contracts:
        db.add(
            CustomerFollowUp(
                customer_id=contract.customer_id,
                contract_id=contract.id,
                building_id=scope.building_id,
                follow_up_type="电话",
                occurred_at=TODAY - timedelta(days=20),
                content="模拟数据：需确认续租或回款安排。",
                next_action="补充回访并更新处理结果",
                next_follow_up_date=TODAY - timedelta(days=8),
                owner=contract.internal_contact_person or SEED_OPERATOR,
                status="open",
            )
        )

    for contract in adjustment_contracts:
        db.add(
            RentTerm(
                contract_id=contract.id,
                room_no=contract.room_no,
                rule_type="temporary_adjustment",
                start_date=TODAY.replace(day=1),
                end_date=TODAY.replace(day=1) + relativedelta(months=2, day=31),
                area=contract.area,
                unit_price=contract.unit_price,
                monthly_amount=contract.monthly_rent,
                adjustment_type="fixed_amount",
                adjustment_value=round(max((contract.monthly_rent or 0) * 0.6, 50000), 2),
                frequency="once",
                notes="模拟数据：大额临时减免，仅用于风险中心提示。",
            )
        )

    db.flush()
    return {
        "overdue_contracts": len(overdue_contract_ids),
        "missing_contract_bodies": len(missing_body_ids),
        "missing_customer_licenses": len(missing_license_customer_ids),
        "deposit_issues": len(deposit_issue_ids),
        "overdue_follow_ups": len(follow_up_contracts),
        "large_adjustments": len(adjustment_contracts),
    }


def _seed_demo_receivables(db, contracts: list[Contract], overdue_contract_ids: set[int]) -> int:
    payment_count = 0
    contract_ids = [contract.id for contract in contracts]
    overdue_months = {month.strftime("%Y-%m") for month in _demo_due_months(DEMO_OVERDUE_MONTHS)}
    current_month = TODAY.replace(day=1).strftime("%Y-%m")
    for month_start in _demo_receivable_months(contracts):
        month = month_start.strftime("%Y-%m")
        report = get_monthly_receivables(db, month, contract_ids=contract_ids)
        current_month_paid_ids: set[int] = set()
        if month == current_month:
            eligible_rows = sorted(
                [
                    row
                    for row in report["rows"]
                    if row.get("contract_id") not in overdue_contract_ids and round(float(row.get("total") or 0), 2) > 0
                ],
                key=lambda row: (row.get("contract_no") or "", row.get("contract_id") or 0),
            )
            target_amount = round(
                sum(round(float(row.get("total") or 0), 2) for row in eligible_rows) * DEMO_CURRENT_MONTH_COLLECTION_RATE,
                2,
            )
            paid_amount = 0.0
            for row in eligible_rows:
                if paid_amount >= target_amount:
                    break
                current_month_paid_ids.add(row["contract_id"])
                paid_amount = round(paid_amount + round(float(row.get("total") or 0), 2), 2)
        for row in report["rows"]:
            contract_id = row.get("contract_id")
            amount = round(float(row.get("total") or 0), 2)
            due_date = date.fromisoformat(row["due_date"])
            is_current_month_paid = month == current_month and contract_id in current_month_paid_ids
            if amount <= 0 or (due_date >= TODAY and not is_current_month_paid):
                continue
            is_seed_overdue = contract_id in overdue_contract_ids and month in overdue_months
            received_date = min(due_date, TODAY) if is_current_month_paid else due_date
            record = ReceivableRecord(
                contract_id=contract_id,
                period_month=month,
                received_amount=0 if is_seed_overdue else amount,
                received_date=None if is_seed_overdue else received_date,
                invoice_status="待开票" if is_seed_overdue else "已开票",
                dunning_status="催收中" if is_seed_overdue else "无需催收",
                dunning_owner=row.get("internal_contact_person") or SEED_OPERATOR,
                last_dunning_date=TODAY - timedelta(days=3) if is_seed_overdue else None,
                next_follow_up_date=TODAY - timedelta(days=1) if is_seed_overdue else None,
                dunning_notes="模拟数据：少量历史逾期用于风险中心展示。" if is_seed_overdue else "",
                operator=SEED_OPERATOR,
            )
            db.add(record)
            if is_seed_overdue:
                continue
            db.add(
                ReceivablePayment(
                    contract_id=contract_id,
                    period_month=month,
                    payment_date=received_date,
                    amount=amount,
                    allocation_type="收款",
                    payment_method="银行转账",
                    reference_no=f"DEMO-{month}-{contract_id}",
                    notes="模拟数据：正常月度收款。",
                    operator=SEED_OPERATOR,
                    source="seed",
                )
            )
            payment_count += 1
    return payment_count


def seed_demo_operational_data() -> dict[str, int]:
    with SessionLocal() as db:
        contracts = _current_contracts(db)
        risk_summary = _seed_demo_risk_context(db, contracts)
        db.commit()

    with SessionLocal() as db:
        contracts = _current_contracts(db)
        overdue_contract_ids = {contract.id for contract in contracts[:DEMO_OVERDUE_CONTRACTS]}
        payment_count = _seed_demo_receivables(db, contracts, overdue_contract_ids)
        db.commit()

    return {"current_contracts": len(contracts), "receivable_payments": payment_count, **risk_summary}


def summarize_contracts(contracts: list[ContractSeed]) -> dict[str, int]:
    summary = {"待生效": 0, "即将到期": 0, "已退租": 0, "逾期占用": 0, "在租": 0}
    for contract in contracts:
        if contract.start_date > TODAY:
            summary["待生效"] += 1
        elif contract.disposal == "已退租":
            summary["已退租"] += 1
        elif contract.disposal == "逾期占用":
            summary["逾期占用"] += 1
        elif 0 <= (contract.end_date - TODAY).days <= 90:
            summary["即将到期"] += 1
        else:
            summary["在租"] += 1
    return summary


def summarize_rooms(rooms: list[RoomSeed]) -> dict[str, int]:
    result: dict[str, int] = {}
    for room in rooms:
        result[room.tower] = result.get(room.tower, 0) + 1
    return result


def main() -> None:
    rooms = build_room_seeds()
    contracts = build_contract_seeds(rooms)
    reset_demo_tables()
    room_count = insert_rooms(rooms)
    contract_count = insert_contracts(contracts)
    operational_summary = seed_demo_operational_data()
    print(
        "Seed complete:",
        f"rooms={room_count}",
        f"contracts={contract_count}",
        f"status_summary={summarize_contracts(contracts)}",
        f"tower_summary={summarize_rooms(rooms)}",
        f"operational_summary={operational_summary}",
    )


if __name__ == "__main__":
    main()

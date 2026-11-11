from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from types import MappingProxyType
from typing import Mapping, Protocol


PROFILE_VERSION = "building-operations-profile-v1"


class BuildingDescriptor(Protocol):
    code: str
    name: str
    ingestion_mode: str


class DataAdapterType(str, Enum):
    LIVE_DETAIL = "live_detail"
    PUBLISHED_SNAPSHOT = "published_snapshot"
    UNAVAILABLE = "unavailable"


class SpaceStructureType(str, Enum):
    MULTI_TOWER_WITH_COMMERCIAL = "multi_tower_with_commercial"
    SINGLE_BUILDING = "single_building"
    MAIN_BUILDING_WITH_PODIUM = "main_building_with_podium"
    WHOLE_BUILDING = "whole_building"
    UNKNOWN = "unknown"


class DataGranularity(str, Enum):
    BUILDING = "building"
    ZONE = "zone"
    FLOOR = "floor"
    LEASABLE_UNIT = "leasable_unit"


OPERATIONS_TABS = (
    "dashboard",
    "operations",
    "analytics",
    "evidence",
    "ai-assist",
    "risks",
    "contracts",
    "customers",
    "rooms",
    "receivables",
    "exchange",
)


@dataclass(frozen=True)
class BrandSemantics:
    short_name: str
    operations_title: str
    positioning: str
    operating_focus: tuple[str, ...]
    logo_url: str | None = None

    def as_dict(self) -> dict:
        return {
            "short_name": self.short_name,
            "operations_title": self.operations_title,
            "positioning": self.positioning,
            "operating_focus": list(self.operating_focus),
            "logo_url": self.logo_url,
        }


@dataclass(frozen=True)
class SpaceZone:
    code: str
    name: str
    kind: str

    def as_dict(self) -> dict:
        return {"code": self.code, "name": self.name, "kind": self.kind}


@dataclass(frozen=True)
class SpaceStructure:
    structure_type: SpaceStructureType
    zones: tuple[SpaceZone, ...]
    detail_unavailable_reason: str | None = None

    def as_dict(self) -> dict:
        return {
            "type": self.structure_type.value,
            "zones": [item.as_dict() for item in self.zones],
            "detail_unavailable_reason": self.detail_unavailable_reason,
        }


@dataclass(frozen=True)
class OperationsGroup:
    code: str
    name: str
    zone_codes: tuple[str, ...]
    purpose: str
    available: bool = True
    unavailable_reason: str | None = None

    def __post_init__(self) -> None:
        if self.available and self.unavailable_reason is not None:
            raise ValueError("available group cannot have an unavailable reason")
        if not self.available and not self.unavailable_reason:
            raise ValueError("unavailable group must explain why it is unavailable")

    def as_dict(self) -> dict:
        return {
            "code": self.code,
            "name": self.name,
            "zone_codes": list(self.zone_codes),
            "purpose": self.purpose,
            "available": self.available,
            "unavailable_reason": self.unavailable_reason,
        }


@dataclass(frozen=True)
class PageCapability:
    tab: str
    available: bool
    unavailable_reason: str | None = None

    def __post_init__(self) -> None:
        if self.tab not in OPERATIONS_TABS:
            raise ValueError(f"unsupported operations tab: {self.tab}")
        if self.available and self.unavailable_reason is not None:
            raise ValueError("available tab cannot have an unavailable reason")
        if not self.available and not self.unavailable_reason:
            raise ValueError("unavailable tab must explain why it is unavailable")


@dataclass(frozen=True)
class ContractNumberRule:
    supported: bool
    prefix: str | None
    pattern: str | None
    sequence_scope: str | None
    unavailable_reason: str | None = None

    def __post_init__(self) -> None:
        if self.supported:
            if not self.prefix or not self.pattern or not self.sequence_scope:
                raise ValueError("supported contract numbering must be fully defined")
            if self.unavailable_reason is not None:
                raise ValueError("supported contract numbering cannot be unavailable")
        elif not self.unavailable_reason:
            raise ValueError("unsupported contract numbering must explain why")

    def as_dict(self) -> dict:
        return {
            "supported": self.supported,
            "prefix": self.prefix,
            "pattern": self.pattern,
            "sequence_scope": self.sequence_scope,
            "unavailable_reason": self.unavailable_reason,
        }


@dataclass(frozen=True)
class ImportRule:
    supported: bool
    mode: str
    template_key: str | None
    requires_existing_units: bool
    unavailable_reason: str | None = None

    def __post_init__(self) -> None:
        if self.supported:
            if not self.template_key:
                raise ValueError("supported import rule must name a template")
            if self.unavailable_reason is not None:
                raise ValueError("supported import rule cannot be unavailable")
        elif not self.unavailable_reason:
            raise ValueError("unsupported import rule must explain why")

    def as_dict(self) -> dict:
        return {
            "supported": self.supported,
            "mode": self.mode,
            "template_key": self.template_key,
            "requires_existing_units": self.requires_existing_units,
            "unavailable_reason": self.unavailable_reason,
        }


@dataclass(frozen=True)
class BuildingOperationsProfile:
    building_code: str
    building_name: str
    brand: BrandSemantics
    default_page: str | None
    space_structure: SpaceStructure
    data_granularity: tuple[DataGranularity, ...]
    page_capabilities: tuple[PageCapability, ...]
    data_adapter: DataAdapterType
    adapter_unavailable_reason: str | None
    analysis_groups: tuple[OperationsGroup, ...]
    dashboard_groups: tuple[OperationsGroup, ...]
    contract_number_rule: ContractNumberRule
    import_rule: ImportRule

    def __post_init__(self) -> None:
        tabs = tuple(item.tab for item in self.page_capabilities)
        if len(tabs) != len(set(tabs)):
            raise ValueError("operations tabs must not be duplicated")
        if set(tabs) != set(OPERATIONS_TABS):
            raise ValueError("every operations tab must have an explicit capability decision")
        if self.default_page is not None and self.default_page not in self.available_tabs:
            raise ValueError("default page must be one of the available tabs")
        if self.data_adapter is DataAdapterType.UNAVAILABLE:
            if not self.adapter_unavailable_reason:
                raise ValueError("unavailable adapter must explain why")
        elif self.adapter_unavailable_reason is not None:
            raise ValueError("available adapter cannot have an unavailable reason")

    @property
    def available_tabs(self) -> tuple[str, ...]:
        return tuple(item.tab for item in self.page_capabilities if item.available)

    def restrict_tabs(self, authorized_tabs: tuple[str, ...] | list[str]) -> tuple[str, ...]:
        """Intersect with upstream authorization; a profile never grants a tab."""
        supported = frozenset(self.available_tabs)
        return tuple(tab for tab in authorized_tabs if tab in supported)

    def as_dict(self) -> dict:
        return {
            "profile_version": PROFILE_VERSION,
            "building_code": self.building_code,
            "building_name": self.building_name,
            "brand": self.brand.as_dict(),
            "default_page": self.default_page,
            "space_structure": self.space_structure.as_dict(),
            "data_granularity": [item.value for item in self.data_granularity],
            "available_tabs": list(self.available_tabs),
            "data_adapter": self.data_adapter.value,
            "adapter_unavailable_reason": self.adapter_unavailable_reason,
            "analysis_groups": [item.as_dict() for item in self.analysis_groups],
            "dashboard_groups": [item.as_dict() for item in self.dashboard_groups],
            "contract_number_rule": self.contract_number_rule.as_dict(),
            "import_rule": self.import_rule.as_dict(),
            "capability_unavailable_reasons": {
                item.tab: item.unavailable_reason
                for item in self.page_capabilities
                if not item.available
            },
        }

    def presentation_dict(self) -> dict:
        """Return only non-sensitive fields needed to explain a building view."""
        return {
            "profile_version": PROFILE_VERSION,
            "building_code": self.building_code,
            "building_name": self.building_name,
            "brand": self.brand.as_dict(),
            "space_structure": self.space_structure.as_dict(),
            "data_granularity": [item.value for item in self.data_granularity],
            "dashboard_groups": [item.as_dict() for item in self.dashboard_groups],
        }


FULL_DETAIL_TABS = frozenset(
    {
        "dashboard",
        "operations",
        "analytics",
        "evidence",
        "ai-assist",
        "risks",
        "contracts",
        "customers",
        "rooms",
        "receivables",
        "exchange",
    }
)
SNAPSHOT_TABS = frozenset({"dashboard", "evidence", "exchange"})


def _page_capabilities(
    available_tabs: frozenset[str],
    *,
    unavailable_reason: str,
    exchange_reason: str | None = None,
) -> tuple[PageCapability, ...]:
    return tuple(
        PageCapability(
            tab=tab,
            available=tab in available_tabs,
            unavailable_reason=(
                None
                if tab in available_tabs
                else exchange_reason
                if tab == "exchange" and exchange_reason
                else unavailable_reason
            ),
        )
        for tab in OPERATIONS_TABS
    )


DEMO_A_ZONES = (
    SpaceZone("SOUTH_OFFICE", "南塔", "office"),
    SpaceZone("NORTH_OFFICE", "北塔", "office"),
    SpaceZone("COMMERCIAL_SUPPORT", "商业配套", "commercial"),
)
DEMO_A_GROUPS = (
    OperationsGroup(
        "WHOLE_BUILDING",
        "整楼",
        tuple(item.code for item in DEMO_A_ZONES),
        "整栋经营情况",
    ),
    OperationsGroup("SOUTH_OFFICE", "南塔办公", ("SOUTH_OFFICE",), "办公出租"),
    OperationsGroup("NORTH_OFFICE", "北塔办公", ("NORTH_OFFICE",), "办公出租"),
    OperationsGroup(
        "COMMERCIAL_SUPPORT",
        "商业配套",
        ("COMMERCIAL_SUPPORT",),
        "商业配套经营",
    ),
)


SNAPSHOT_DETAIL_REASON = "当前仅接入已发布经营快照，尚无合同、客户、房源和收缴明细"
SNAPSHOT_CONTRACT_REASON = "当前楼宇尚未启用合同明细编号规则"
SNAPSHOT_SEGMENT_REASON = "当前已发布快照仅提供整楼粒度，分区指标尚不可用"


def _whole_building_group(name: str = "整楼") -> tuple[OperationsGroup, ...]:
    return (OperationsGroup("WHOLE_BUILDING", name, (), "整栋经营情况"),)


def _descriptor_space_zones(building: BuildingDescriptor) -> tuple[SpaceZone, ...]:
    resolved: list[SpaceZone] = []
    seen: set[str] = set()
    for item in getattr(building, "operations_zones", ()) or ():
        if isinstance(item, SpaceZone):
            zone = item
        elif isinstance(item, Mapping):
            zone = SpaceZone(
                str(item.get("code") or "").strip(),
                str(item.get("name") or "").strip(),
                str(item.get("kind") or item.get("zone_type") or "unknown").strip(),
            )
        else:
            try:
                zone = SpaceZone(
                    str(item[0] or "").strip(),
                    str(item[1] or "").strip(),
                    str(item[2] or "unknown").strip(),
                )
            except (IndexError, TypeError):
                continue
        normalized = zone.code.casefold()
        if not zone.code or not zone.name or normalized in seen:
            continue
        seen.add(normalized)
        resolved.append(zone)
    return tuple(resolved)


def _snapshot_import_rule() -> ImportRule:
    return ImportRule(
        supported=True,
        mode="published_snapshot",
        template_key="ops_v1_snapshot_exchange",
        requires_existing_units=False,
    )


def _unsupported_contract_rule(reason: str = SNAPSHOT_CONTRACT_REASON) -> ContractNumberRule:
    return ContractNumberRule(
        supported=False,
        prefix=None,
        pattern=None,
        sequence_scope=None,
        unavailable_reason=reason,
    )


DEMO_A_PROFILE = BuildingOperationsProfile(
    building_code="DEMO_A",
    building_name="示例双塔中心",
    brand=BrandSemantics(
        short_name="DEMO_A",
        operations_title="示例双塔中心楼宇运营",
        positioning="完整明细运营",
        operating_focus=("招商出租", "合同履约", "收缴", "风险", "经营分析"),
        logo_url="/lease-manager-mark.svg",
    ),
    default_page="dashboard",
    space_structure=SpaceStructure(
        SpaceStructureType.MULTI_TOWER_WITH_COMMERCIAL,
        DEMO_A_ZONES,
    ),
    data_granularity=(
        DataGranularity.BUILDING,
        DataGranularity.ZONE,
        DataGranularity.FLOOR,
        DataGranularity.LEASABLE_UNIT,
    ),
    page_capabilities=_page_capabilities(
        FULL_DETAIL_TABS,
        unavailable_reason="当前页面不适用于 DEMO_A 明细运营模式",
        exchange_reason="DEMO_A 当前使用明细 Adapter，不通过经营快照投递页接入",
    ),
    data_adapter=DataAdapterType.LIVE_DETAIL,
    adapter_unavailable_reason=None,
    analysis_groups=DEMO_A_GROUPS,
    dashboard_groups=DEMO_A_GROUPS,
    contract_number_rule=ContractNumberRule(
        supported=True,
        prefix="DEMO_A",
        pattern="DEMO_A-{year}-{sequence:03d}",
        sequence_scope="building_year",
    ),
    import_rule=ImportRule(
        supported=True,
        mode="detail",
        template_key="demo_a_detail_v1",
        requires_existing_units=True,
    ),
)


DEMO_B_ZONES = (
    SpaceZone("MAIN_BUILDING", "主楼", "office"),
    SpaceZone("PODIUM", "裙楼", "mixed_use"),
)
DEMO_B_GROUPS = (
    OperationsGroup(
        "WHOLE_BUILDING",
        "整栋经营",
        tuple(item.code for item in DEMO_B_ZONES),
        "主楼和裙楼全部纳入",
    ),
    OperationsGroup(
        "MARKET_LEASING",
        "市场化出租",
        ("MAIN_BUILDING",),
        "重点观察主楼及实际市场化出租范围",
        available=False,
        unavailable_reason=SNAPSHOT_SEGMENT_REASON,
    ),
)


def _snapshot_profile(
    *,
    code: str,
    name: str,
    short_name: str,
    positioning: str,
    operating_focus: tuple[str, ...],
    space_structure: SpaceStructure,
    groups: tuple[OperationsGroup, ...],
) -> BuildingOperationsProfile:
    return BuildingOperationsProfile(
        building_code=code,
        building_name=name,
        brand=BrandSemantics(
            short_name=short_name,
            operations_title=f"{name}楼宇运营",
            positioning=positioning,
            operating_focus=operating_focus,
        ),
        default_page="dashboard",
        space_structure=space_structure,
        data_granularity=(DataGranularity.BUILDING,),
        page_capabilities=_page_capabilities(
            SNAPSHOT_TABS,
            unavailable_reason=SNAPSHOT_DETAIL_REASON,
        ),
        data_adapter=DataAdapterType.PUBLISHED_SNAPSHOT,
        adapter_unavailable_reason=None,
        analysis_groups=groups,
        dashboard_groups=groups,
        contract_number_rule=_unsupported_contract_rule(),
        import_rule=_snapshot_import_rule(),
    )


DEMO_B_PROFILE = _snapshot_profile(
    code="DEMO_B",
    name="示例主裙楼中心",
    short_name="示例主裙楼中心",
    positioning="主楼与裙楼双口径运营",
    operating_focus=("整栋经营", "市场化出租", "长期租约履约", "收缴", "续约"),
    space_structure=SpaceStructure(
        SpaceStructureType.MAIN_BUILDING_WITH_PODIUM,
        DEMO_B_ZONES,
    ),
    groups=DEMO_B_GROUPS,
)

DEMO_C_PROFILE = _snapshot_profile(
    code="DEMO_C",
    name="示例单栋中心",
    short_name="示例单栋中心",
    positioning="标准单栋楼运营",
    operating_focus=("整楼经营", "结构资料完整性", "标准模板迁移"),
    space_structure=SpaceStructure(
        SpaceStructureType.SINGLE_BUILDING,
        (),
        "楼层数、分区和出租单元尚未核验，当前不虚构楼层或房号",
    ),
    groups=_whole_building_group(),
)


DEMO_C_DETAIL_PROFILE = BuildingOperationsProfile(
    building_code="DEMO_C",
    building_name="示例单栋中心",
    brand=BrandSemantics(
        short_name="示例单栋中心",
        operations_title="示例单栋中心楼宇运营",
        positioning="标准单栋楼明细运营",
        operating_focus=("房源台账", "招商出租", "合同履约", "收缴", "风险"),
    ),
    default_page="dashboard",
    space_structure=SpaceStructure(
        SpaceStructureType.SINGLE_BUILDING,
        (SpaceZone("WHOLE_BUILDING", "整楼", "whole_building"),),
        "当前只登记整楼逻辑分区；楼层数和真实出租单元必须由房源台账提供，不从档案虚构",
    ),
    data_granularity=(
        DataGranularity.BUILDING,
        DataGranularity.ZONE,
        DataGranularity.LEASABLE_UNIT,
    ),
    page_capabilities=_page_capabilities(
        FULL_DETAIL_TABS,
        unavailable_reason="当前页面不适用于示例单栋中心明细运营模式",
    ),
    data_adapter=DataAdapterType.LIVE_DETAIL,
    adapter_unavailable_reason=None,
    analysis_groups=_whole_building_group(),
    dashboard_groups=_whole_building_group(),
    contract_number_rule=ContractNumberRule(
        supported=True,
        prefix="DEMO_C",
        pattern="DEMO_C-{year}-{sequence:03d}",
        sequence_scope="building_year",
    ),
    import_rule=ImportRule(
        supported=True,
        mode="detail",
        template_key="standard_detail_v1",
        requires_existing_units=True,
    ),
)

DEMO_D_PROFILE = _snapshot_profile(
    code="DEMO_D",
    name="示例办公中心",
    short_name="示例办公中心",
    positioning="整楼快照运营",
    operating_focus=("到期续租", "收缴", "风险", "资料完整性", "经营大屏"),
    space_structure=SpaceStructure(
        SpaceStructureType.WHOLE_BUILDING,
        (),
        "内部楼层、分区和出租单元尚未核验",
    ),
    groups=_whole_building_group(),
)

DEMO_E_PROFILE = _snapshot_profile(
    code="DEMO_E",
    name="示例商务中心",
    short_name="示例商务中心",
    positioning="整楼快照运营",
    operating_focus=("到期续租", "收缴", "风险", "资料完整性", "经营大屏"),
    space_structure=SpaceStructure(
        SpaceStructureType.WHOLE_BUILDING,
        (),
        "内部楼层、分区和出租单元尚未核验",
    ),
    groups=_whole_building_group(),
)


BUILDING_OPERATIONS_PROFILES: Mapping[str, BuildingOperationsProfile] = MappingProxyType(
    {
        item.building_code.casefold(): item
        for item in (
            DEMO_A_PROFILE,
            DEMO_B_PROFILE,
            DEMO_C_PROFILE,
            DEMO_D_PROFILE,
            DEMO_E_PROFILE,
        )
    }
)


DETAIL_BUILDING_OPERATIONS_PROFILES: Mapping[str, BuildingOperationsProfile] = MappingProxyType(
    {
        DEMO_C_DETAIL_PROFILE.building_code.casefold(): DEMO_C_DETAIL_PROFILE,
    }
)


EXPECTED_INGESTION_MODES: Mapping[str, str] = MappingProxyType(
    {
        "demo_a": "detail",
        "demo_b": "snapshot",
        "demo_c": "snapshot",
        "demo_d": "snapshot",
        "demo_e": "snapshot",
    }
)


def _unavailable_profile(
    *,
    code: str,
    name: str,
    reason: str,
    base: BuildingOperationsProfile | None = None,
) -> BuildingOperationsProfile:
    if base is not None:
        brand = base.brand
        structure = base.space_structure
        groups = tuple(
            replace(
                item,
                available=False,
                unavailable_reason=reason,
            )
            for item in base.analysis_groups
        )
    else:
        brand = BrandSemantics(
            short_name=name or code,
            operations_title=f"{name or code}楼宇运营",
            positioning="待确认运行档案",
            operating_focus=("资料核验", "数据接入确认"),
        )
        structure = SpaceStructure(
            SpaceStructureType.UNKNOWN,
            (),
            "楼宇空间结构尚未登记",
        )
        groups = ()
    return BuildingOperationsProfile(
        building_code=code,
        building_name=name,
        brand=brand,
        # Even when the adapter is closed, the read-only status dashboard is
        # the safe landing page for explaining why every metric is unavailable.
        default_page="dashboard",
        space_structure=structure,
        data_granularity=(),
        page_capabilities=_page_capabilities(
            frozenset({"dashboard"}),
            unavailable_reason=reason,
        ),
        data_adapter=DataAdapterType.UNAVAILABLE,
        adapter_unavailable_reason=reason,
        analysis_groups=groups,
        dashboard_groups=groups,
        contract_number_rule=_unsupported_contract_rule(reason),
        import_rule=ImportRule(
            supported=False,
            mode="unavailable",
            template_key=None,
            requires_existing_units=False,
            unavailable_reason=reason,
        ),
    )


def _generic_snapshot_profile(code: str, name: str) -> BuildingOperationsProfile:
    return _snapshot_profile(
        code=code,
        name=name,
        short_name=name or code,
        positioning="通用整楼快照运营",
        operating_focus=("整楼经营", "数据依据", "经营大屏"),
        space_structure=SpaceStructure(
            SpaceStructureType.UNKNOWN,
            (),
            "楼宇空间结构尚未登记，当前只使用整楼级已发布快照",
        ),
        groups=_whole_building_group(),
    )


def _generic_detail_profile(
    code: str,
    name: str,
    *,
    zones: tuple[SpaceZone, ...] = (),
) -> BuildingOperationsProfile:
    prefix = code.upper()
    return BuildingOperationsProfile(
        building_code=code,
        building_name=name,
        brand=BrandSemantics(
            short_name=name or code,
            operations_title=f"{name or code}楼宇运营",
            positioning="通用明细运营",
            operating_focus=("房源台账", "招商出租", "合同履约", "收缴", "风险"),
        ),
        default_page="dashboard",
        space_structure=SpaceStructure(
            SpaceStructureType.UNKNOWN,
            zones,
            "分区结构以当前楼宇数据库中已登记的有效分区为准",
        ),
        data_granularity=(
            DataGranularity.BUILDING,
            DataGranularity.ZONE,
            DataGranularity.FLOOR,
            DataGranularity.LEASABLE_UNIT,
        ),
        page_capabilities=_page_capabilities(
            FULL_DETAIL_TABS,
            unavailable_reason="当前页面不适用于通用明细运营模式",
        ),
        data_adapter=DataAdapterType.LIVE_DETAIL,
        adapter_unavailable_reason=None,
        analysis_groups=_whole_building_group(),
        dashboard_groups=_whole_building_group(),
        contract_number_rule=ContractNumberRule(
            supported=True,
            prefix=prefix,
            pattern=f"{prefix}-{{year}}-{{sequence:03d}}",
            sequence_scope="building_year",
        ),
        import_rule=ImportRule(
            supported=True,
            mode="detail",
            template_key="standard_detail_v1",
            requires_existing_units=True,
        ),
    )


def resolve_building_operations_profile(
    building: BuildingDescriptor,
) -> BuildingOperationsProfile:
    """Resolve product limits only; authorization remains the caller's responsibility."""
    code = str(getattr(building, "code", "") or "").strip()
    name = str(getattr(building, "name", "") or "").strip() or code
    ingestion_mode = str(getattr(building, "ingestion_mode", "") or "").strip().casefold()
    normalized_code = code.casefold()
    descriptor_zones = _descriptor_space_zones(building)
    try:
        registered_building_id = int(getattr(building, "id", 0) or 0)
    except (TypeError, ValueError):
        registered_building_id = 0
    configured = BUILDING_OPERATIONS_PROFILES.get(normalized_code)
    if configured is not None:
        expected_mode = EXPECTED_INGESTION_MODES[normalized_code]
        if ingestion_mode == expected_mode:
            return configured
        detail_candidate = DETAIL_BUILDING_OPERATIONS_PROFILES.get(normalized_code)
        if ingestion_mode == "detail" and registered_building_id > 0:
            return detail_candidate or _generic_detail_profile(code, name, zones=descriptor_zones)
        return _unavailable_profile(
            code=configured.building_code,
            name=configured.building_name,
            reason=(
                f"楼宇登记的数据接入方式为 {ingestion_mode or '未设置'}，"
                f"与运行档案要求的 {expected_mode} 不一致"
            ),
            base=configured,
        )
    if ingestion_mode == "snapshot":
        return _generic_snapshot_profile(code, name)
    if ingestion_mode == "external":
        reason = "外部数据来源尚未配置受支持的 Adapter"
    elif ingestion_mode == "detail" and registered_building_id > 0:
        return _generic_detail_profile(code, name, zones=descriptor_zones)
    elif ingestion_mode == "detail":
        reason = "该楼宇尚未注册明细 Adapter"
    else:
        reason = "该楼宇尚未配置受支持的数据 Adapter"
    return _unavailable_profile(code=code, name=name, reason=reason)

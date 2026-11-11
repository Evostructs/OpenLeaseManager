from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OverviewPeriod(_StrictModel):
    month: str
    month_start: str
    month_end: str


class OverviewScope(_StrictModel):
    scope_type: Literal["portfolio"]
    scope_code: str
    display_name: str
    building_codes: list[str] = Field(default_factory=list)


class OverviewCoverage(_StrictModel):
    portfolio_buildings: int = 0
    authorized_buildings: int = 0
    presentation_enabled_buildings: int = 0
    contribution_enabled_buildings: int = 0
    published_same_day_buildings: int = 0
    live_adapter_buildings: int = 0
    same_day_snapshot_buildings: int = 0
    included_same_day_buildings: int = 0


class OverviewTimestamps(_StrictModel):
    source_observed_at: str | None = None
    source_updated_at: str | None = None
    published_at: str | None = None


class OverviewMetricCoverage(_StrictModel):
    authorized_buildings: int | None = None
    presentation_enabled_buildings: int | None = None
    contribution_enabled_buildings: int | None = None
    eligible_buildings: int | None = None
    included_buildings: int | None = None
    completeness_status: str | None = None
    freshness_status: str | None = None
    validation_status: str | None = None
    publication_status: str | None = None


class OverviewMetric(_StrictModel):
    code: str
    name: str
    value: float | int | None = None
    unit: str
    as_of_date: str
    unavailable_reason: str | None = None
    coverage: OverviewMetricCoverage


class OverviewProfileBrand(_StrictModel):
    short_name: str
    operations_title: str
    positioning: str
    operating_focus: list[str] = Field(default_factory=list)
    logo_url: str | None = None


class OverviewProfileZone(_StrictModel):
    code: str
    name: str
    kind: str


class OverviewProfileSpaceStructure(_StrictModel):
    type: str
    zones: list[OverviewProfileZone] = Field(default_factory=list)
    detail_unavailable_reason: str | None = None


class OverviewProfileGroup(_StrictModel):
    code: str
    name: str
    zone_codes: list[str] = Field(default_factory=list)
    purpose: str
    available: bool
    unavailable_reason: str | None = None


class OverviewOperationsProfile(_StrictModel):
    profile_version: str
    building_code: str
    building_name: str
    brand: OverviewProfileBrand
    space_structure: OverviewProfileSpaceStructure
    data_granularity: list[str] = Field(default_factory=list)
    dashboard_groups: list[OverviewProfileGroup] = Field(default_factory=list)


class OverviewBuilding(_StrictModel):
    code: str
    name: str
    operations_profile: OverviewOperationsProfile | None = None


class OverviewOperatingGate(_StrictModel):
    state: str
    presentation_enabled: bool
    contribution_enabled: bool


class OverviewContribution(_StrictModel):
    building: OverviewBuilding
    publication_status: str
    published_at: str | None = None
    reason: str | None = None
    presentation_enabled: bool
    contribution_enabled: bool
    gate_state: str
    operating_gate: OverviewOperatingGate
    data_classification: Literal["simulated"]
    metrics: dict[str, OverviewMetric] = Field(default_factory=dict)


class OverviewRolloutItem(_StrictModel):
    building_code: str
    building_name: str
    state: str
    presentation_enabled: bool
    contribution_enabled: bool


class OverviewRollout(_StrictModel):
    policy_version: str
    evaluated_at: str
    authorized_buildings: int
    presentation_enabled_buildings: int
    contribution_enabled_buildings: int
    items: list[OverviewRolloutItem] = Field(default_factory=list)


class OverviewTrendRow(_StrictModel):
    month: str
    receivable: float | int | None = None
    received: float | int | None = None
    received_rate: float | int | None = None


class OverviewPresentation(_StrictModel):
    data_classification: Literal["simulated"]
    gate_policy_version: str
    trend: list[OverviewTrendRow] = Field(default_factory=list)
    contributions: list[OverviewContribution] = Field(default_factory=list)


class PortfolioOverviewOut(_StrictModel):
    contract_version: Literal["portfolio-overview-v1"]
    definition_version: str
    definition_status: str
    data_classification: Literal["simulated"]
    gate_policy_version: str
    as_of_date: str
    period: OverviewPeriod
    scope: OverviewScope
    coverage: OverviewCoverage
    timestamps: OverviewTimestamps
    metrics: dict[str, OverviewMetric] = Field(default_factory=dict)
    contributions: list[OverviewContribution] = Field(default_factory=list)
    rollout: OverviewRollout
    presentation: OverviewPresentation

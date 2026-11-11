from __future__ import annotations

import json
from io import BytesIO
from pathlib import PurePosixPath
from typing import Any
from zipfile import BadZipFile, ZipFile

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils.exceptions import InvalidFileException
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.models import (
    Building,
    DataSource,
    SnapshotExchangeReceipt,
    SnapshotExchangeSourceBinding,
    User,
)
from app.ops_v1 import SUBMITTABLE_CODES
from app.services import snapshot_exchange
from app.services.property_scope import resolve_user_scope
from app.snapshot_exchange_schemas import SnapshotExchangePackageV1, compute_content_digest


class SnapshotExchangeUploadError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "invalid_upload_file",
        field: str | None = None,
        row: int | None = None,
    ) -> None:
        super().__init__(message)
        self.issue = {
            "code": code,
            "field": field,
            "row": row,
            "message": message,
        }


class _DuplicateJsonKey(ValueError):
    pass


_EXCEL_TEMPLATE_VERSION = "OPS_EXCHANGE_XLSX_V1"
_EXCEL_SHEETS = ("Package", "Metrics", "Workflow")
_PACKAGE_FIELDS = (
    "exchange_version",
    "delivery_id",
    "source_system_code",
    "building_code",
    "metric_definition_version",
    "period_start",
    "period_end",
    "as_of_date",
    "source_observed_at",
    "source_updated_at",
    "source_published_at",
    "source_publication_revision",
    "source_publication_ref",
    "correction_of_delivery_id",
    "correction_reason",
    "data_classification",
    "digest_profile",
    "digest_algorithm",
    "content_digest",
)
_METRIC_FIELDS = (
    "code",
    "value",
    "numerator",
    "denominator",
    "covered_area_sqm",
    "eligible_area_sqm",
    "covered_days",
    "eligible_days",
    "estimate_share",
    "estimate_method",
    "completeness_status",
    "freshness_status",
    "calculation_version",
    "source_batch_ref",
    "source_record_refs",
)
_DECIMAL_METRIC_FIELDS = {
    "value",
    "numerator",
    "denominator",
    "covered_area_sqm",
    "eligible_area_sqm",
    "estimate_share",
}
_INTEGER_METRIC_FIELDS = {"covered_days", "eligible_days"}
_MAX_EXCEL_ENTRIES = 250
_MAX_EXCEL_UNCOMPRESSED_BYTES = 8 * 1024 * 1024


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey(key)
        result[key] = value
    return result


def _reject_nonstandard_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")


def parse_json_upload(content: bytes) -> SnapshotExchangePackageV1:
    try:
        text = content.decode("utf-8", errors="strict")
        raw = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_nonstandard_json_constant,
        )
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise SnapshotExchangeUploadError("上传内容不是可接受的 UTF-8 JSON 交换包") from exc
    if not isinstance(raw, dict):
        raise SnapshotExchangeUploadError("JSON 交换包顶层必须是对象")
    try:
        return SnapshotExchangePackageV1.model_validate(raw)
    except ValidationError as exc:
        raise SnapshotExchangeUploadError("JSON 内容不符合 OPS_EXCHANGE_V1") from exc


def _validate_excel_container(content: bytes) -> None:
    try:
        with ZipFile(BytesIO(content)) as archive:
            entries = archive.infolist()
            if len(entries) > _MAX_EXCEL_ENTRIES:
                raise SnapshotExchangeUploadError("Excel 文件内部条目过多")
            total_size = 0
            names: set[str] = set()
            for entry in entries:
                name = entry.filename
                normalized = PurePosixPath(name)
                if (
                    not name
                    or "\\" in name
                    or normalized.is_absolute()
                    or ".." in normalized.parts
                    or name in names
                ):
                    raise SnapshotExchangeUploadError("Excel 文件内部路径不安全或重复")
                names.add(name)
                if entry.flag_bits & 0x1:
                    raise SnapshotExchangeUploadError("不接受加密的 Excel 文件")
                total_size += int(entry.file_size)
                if total_size > _MAX_EXCEL_UNCOMPRESSED_BYTES:
                    raise SnapshotExchangeUploadError("Excel 文件展开后过大")
                if (
                    entry.file_size > 1024 * 1024
                    and entry.file_size > max(1, entry.compress_size) * 100
                ):
                    raise SnapshotExchangeUploadError("Excel 文件压缩比例异常")
                lowered = name.casefold()
                if (
                    lowered.endswith(".bin")
                    or lowered.startswith("xl/externallinks/")
                    or lowered.startswith("xl/embeddings/")
                    or lowered.startswith("xl/activex/")
                    or lowered.startswith("customui/")
                ):
                    raise SnapshotExchangeUploadError(
                        "Excel 文件包含宏、外部链接或嵌入对象",
                        code="excel_active_content_not_allowed",
                    )
            if "[Content_Types].xml" not in names or "xl/workbook.xml" not in names:
                raise SnapshotExchangeUploadError("上传内容不是有效的 Excel 工作簿")
    except BadZipFile as exc:
        raise SnapshotExchangeUploadError("上传内容不是有效的 Excel 工作簿") from exc


def _require_visible_plain_workbook(workbook) -> None:
    if tuple(workbook.sheetnames) != _EXCEL_SHEETS:
        raise SnapshotExchangeUploadError(
            "Excel 必须且只能包含 Package、Metrics、Workflow 三张工作表",
            code="excel_sheet_set_invalid",
            field="workbook",
        )
    for sheet in workbook.worksheets:
        if sheet.sheet_state != "visible":
            raise SnapshotExchangeUploadError(
                "Excel 不允许隐藏工作表",
                code="excel_hidden_sheet_not_allowed",
                field=sheet.title,
            )
        if sheet.merged_cells.ranges:
            raise SnapshotExchangeUploadError("Excel 数据区不允许合并单元格")
        for row_no, dimension in sheet.row_dimensions.items():
            if dimension.hidden:
                raise SnapshotExchangeUploadError(
                    "Excel 不允许隐藏行",
                    code="excel_hidden_row_not_allowed",
                    field=sheet.title,
                    row=int(row_no),
                )
        for column_name, dimension in sheet.column_dimensions.items():
            if dimension.hidden:
                raise SnapshotExchangeUploadError(
                    "Excel 不允许隐藏列",
                    code="excel_hidden_column_not_allowed",
                    field=f"{sheet.title}!{column_name}",
                )
        for row in sheet.iter_rows():
            for cell in row:
                if cell.data_type == "f":
                    raise SnapshotExchangeUploadError(
                        "Excel 不允许公式，请填写确定值",
                        code="excel_formula_not_allowed",
                        field=f"{sheet.title}!{cell.coordinate}",
                        row=int(cell.row),
                    )


def _text_cell(value: object, *, field: str, nullable: bool = False) -> str | None:
    if value is None:
        if nullable:
            return None
        raise SnapshotExchangeUploadError(f"Excel 字段 {field} 不能为空")
    if not isinstance(value, str):
        raise SnapshotExchangeUploadError(
            f"Excel 字段 {field} 必须按文本填写",
            code="excel_cell_type_invalid",
            field=field,
        )
    return value


def _integer_cell(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SnapshotExchangeUploadError(f"Excel 字段 {field} 必须填写整数")
    return value


def _package_from_sheet(sheet) -> dict:
    if tuple(cell.value for cell in sheet[1]) != ("field", "value"):
        raise SnapshotExchangeUploadError("Package 工作表表头不符合模板")
    values: dict[str, object] = {}
    for row_no in range(2, sheet.max_row + 1):
        field = sheet.cell(row_no, 1).value
        value = sheet.cell(row_no, 2).value
        if field is None and value is None:
            continue
        if not isinstance(field, str) or not field:
            raise SnapshotExchangeUploadError(f"Package 第 {row_no} 行字段名无效")
        if field in values:
            raise SnapshotExchangeUploadError(f"Package 字段 {field} 重复")
        values[field] = value
    expected = {"template_version", *_PACKAGE_FIELDS}
    if set(values) != expected:
        missing = sorted(expected - set(values))
        unknown = sorted(set(values) - expected)
        detail = missing[0] if missing else unknown[0]
        raise SnapshotExchangeUploadError(f"Package 字段集合不符合模板：{detail}")
    if values["template_version"] != _EXCEL_TEMPLATE_VERSION:
        raise SnapshotExchangeUploadError("Excel 模板版本不受支持")

    raw: dict[str, object] = {}
    nullable = {"source_updated_at", "correction_of_delivery_id", "correction_reason"}
    for field in _PACKAGE_FIELDS:
        value = values[field]
        if field == "source_publication_revision":
            raw[field] = _integer_cell(value, field=field)
        elif field == "content_digest" and value is None:
            raw[field] = "0" * 64
        else:
            raw[field] = _text_cell(value, field=field, nullable=field in nullable)
    return raw


def _metrics_from_sheet(sheet) -> list[dict]:
    if tuple(cell.value for cell in sheet[1]) != _METRIC_FIELDS:
        raise SnapshotExchangeUploadError("Metrics 工作表表头不符合模板")
    metrics: list[dict] = []
    seen_codes: set[str] = set()
    for row_no in range(2, sheet.max_row + 1):
        values = [sheet.cell(row_no, index + 1).value for index in range(len(_METRIC_FIELDS))]
        if all(value is None for value in values):
            continue
        metric: dict[str, object] = {}
        for field, value in zip(_METRIC_FIELDS, values, strict=True):
            if field in _DECIMAL_METRIC_FIELDS:
                metric[field] = _text_cell(value, field=field, nullable=True)
            elif field in _INTEGER_METRIC_FIELDS:
                metric[field] = _integer_cell(value, field=field)
            elif field == "estimate_method":
                metric[field] = "" if value is None else _text_cell(value, field=field)
            elif field == "source_record_refs":
                encoded = _text_cell(value, field=field)
                try:
                    references = json.loads(encoded)
                except (TypeError, ValueError) as exc:
                    raise SnapshotExchangeUploadError(
                        f"Metrics 第 {row_no} 行 source_record_refs 必须是 JSON 文本数组"
                    ) from exc
                if not isinstance(references, list) or any(
                    not isinstance(item, str) for item in references
                ):
                    raise SnapshotExchangeUploadError(
                        f"Metrics 第 {row_no} 行 source_record_refs 必须是文本数组"
                    )
                metric[field] = references
            else:
                metric[field] = _text_cell(value, field=field)
        code = str(metric["code"])
        if code in seen_codes:
            raise SnapshotExchangeUploadError(f"Metrics 指标 {code} 重复")
        seen_codes.add(code)
        metrics.append(metric)
    return metrics


def _workflow_from_sheet(sheet) -> dict:
    if tuple(cell.value for cell in sheet[1]) != ("stage", "actor_ref", "occurred_at"):
        raise SnapshotExchangeUploadError("Workflow 工作表表头不符合模板")
    workflow: dict[str, dict] = {}
    for row_no in range(2, sheet.max_row + 1):
        stage, actor_ref, occurred_at = (sheet.cell(row_no, column).value for column in range(1, 4))
        if stage is None and actor_ref is None and occurred_at is None:
            continue
        stage_text = _text_cell(stage, field="stage")
        if stage_text in workflow:
            raise SnapshotExchangeUploadError(f"Workflow 阶段 {stage_text} 重复")
        workflow[stage_text] = {
            "actor_ref": _text_cell(actor_ref, field="actor_ref"),
            "occurred_at": _text_cell(occurred_at, field="occurred_at"),
        }
    if set(workflow) != {"submitted", "reviewed", "published"}:
        raise SnapshotExchangeUploadError("Workflow 必须包含提交、复核、发布三个阶段")
    return workflow


def parse_excel_upload(content: bytes) -> SnapshotExchangePackageV1:
    _validate_excel_container(content)
    try:
        workbook = load_workbook(
            BytesIO(content),
            read_only=False,
            data_only=False,
            keep_vba=False,
            keep_links=False,
        )
    except (InvalidFileException, OSError, ValueError, KeyError) as exc:
        raise SnapshotExchangeUploadError("上传内容不是有效的 Excel 工作簿") from exc
    try:
        _require_visible_plain_workbook(workbook)
        raw = _package_from_sheet(workbook["Package"])
        raw["metrics"] = _metrics_from_sheet(workbook["Metrics"])
        raw["workflow"] = _workflow_from_sheet(workbook["Workflow"])
        digest_was_blank = raw["content_digest"] == "0" * 64
        try:
            unsigned = SnapshotExchangePackageV1.model_validate(raw)
            if digest_was_blank:
                raw["content_digest"] = compute_content_digest(unsigned)
            return SnapshotExchangePackageV1.model_validate(raw)
        except ValidationError as exc:
            raise SnapshotExchangeUploadError("Excel 内容不符合 OPS_EXCHANGE_V1") from exc
    finally:
        workbook.close()


def parse_upload(content: bytes) -> tuple[str, SnapshotExchangePackageV1]:
    if content.lstrip().startswith(b"{"):
        return "json", parse_json_upload(content)
    if content.startswith(b"PK"):
        return "excel", parse_excel_upload(content)
    raise SnapshotExchangeUploadError("上传内容不是受支持的 OPS_EXCHANGE_V1 JSON 或 Excel 文件")


def _require_upload_scope(db: Session, actor: User, package: SnapshotExchangePackageV1) -> None:
    resolve_user_scope(db, actor).require_building_code(
        package.building_code,
        access_level="edit",
    )


def preflight_upload(
    db: Session,
    *,
    content: bytes,
    actor: User,
) -> dict:
    file_format, package = parse_upload(content)
    _require_upload_scope(db, actor, package)

    bind = db.get_bind()
    connection = bind.connect()
    outer_transaction = connection.begin()
    preview_db = Session(bind=connection, join_transaction_mode="rollback_only")
    try:
        result = snapshot_exchange.receive_snapshot_package(
            preview_db,
            package=package,
            actor=actor,
        )
        receipt = result.receipt
    finally:
        preview_db.close()
        if outer_transaction.is_active:
            outer_transaction.rollback()
        connection.close()

    return {
        "preflight_version": "OPS_EXCHANGE_PREFLIGHT_V1",
        "file_format": file_format,
        "exchange_version": package.exchange_version,
        "delivery_id": package.delivery_id,
        "source_system_code": package.source_system_code,
        "building_code": package.building_code,
        "server_content_digest": receipt.server_content_digest,
        "valid": receipt.status in {"accepted", "duplicate"},
        "would_status": receipt.status,
        "errors": [item.model_dump(mode="json") for item in receipt.errors],
    }


def submit_upload(
    db: Session,
    *,
    content: bytes,
    actor: User,
) -> snapshot_exchange.ReceiveResult:
    _file_format, package = parse_upload(content)
    _require_upload_scope(db, actor, package)
    return snapshot_exchange.receive_snapshot_package(
        db,
        package=package,
        actor=actor,
    )


def list_owned_upload_receipts(db: Session, *, actor: User) -> dict:
    scope = resolve_user_scope(db, actor)
    allowed_ids = tuple(scope.edit_building_ids)
    if not allowed_ids:
        return {"items": [], "total": 0}
    query = (
        db.query(SnapshotExchangeReceipt)
        .join(
            SnapshotExchangeSourceBinding,
            SnapshotExchangeSourceBinding.id == SnapshotExchangeReceipt.binding_id,
        )
        .join(DataSource, DataSource.id == SnapshotExchangeSourceBinding.source_id)
        .join(Building, Building.id == DataSource.building_id)
        .filter(
            SnapshotExchangeReceipt.actor_id == actor.id,
            Building.id.in_(allowed_ids),
        )
        .order_by(SnapshotExchangeReceipt.received_at.desc(), SnapshotExchangeReceipt.id.desc())
    )
    rows = query.limit(100).all()
    return {
        "items": [
            snapshot_exchange._serialize_receipt(db, row).model_dump(mode="json")
            for row in rows
        ],
        "total": query.count(),
    }


def get_owned_upload_receipt(
    db: Session,
    *,
    actor: User,
    receipt_no: str,
):
    scope = resolve_user_scope(db, actor)
    allowed_ids = tuple(scope.edit_building_ids)
    if not allowed_ids:
        return None
    row = (
        db.query(SnapshotExchangeReceipt)
        .join(
            SnapshotExchangeSourceBinding,
            SnapshotExchangeSourceBinding.id == SnapshotExchangeReceipt.binding_id,
        )
        .join(DataSource, DataSource.id == SnapshotExchangeSourceBinding.source_id)
        .join(Building, Building.id == DataSource.building_id)
        .filter(
            SnapshotExchangeReceipt.receipt_no == receipt_no,
            SnapshotExchangeReceipt.actor_id == actor.id,
            Building.id.in_(allowed_ids),
        )
        .one_or_none()
    )
    if row is None:
        return None
    return snapshot_exchange._serialize_receipt(db, row)


def list_upload_sources(db: Session, *, actor: User) -> list[dict]:
    scope = resolve_user_scope(db, actor)
    allowed_ids = tuple(scope.edit_building_ids)
    if not allowed_ids:
        return []
    rows = (
        db.query(SnapshotExchangeSourceBinding, Building)
        .join(DataSource, DataSource.id == SnapshotExchangeSourceBinding.source_id)
        .join(Building, Building.id == DataSource.building_id)
        .filter(
            SnapshotExchangeSourceBinding.is_active.is_(True),
            DataSource.is_active.is_(True),
            DataSource.source_type == "external_api",
            Building.is_active.is_(True),
            Building.id.in_(allowed_ids),
        )
        .order_by(Building.code.asc(), SnapshotExchangeSourceBinding.source_system_code.asc())
        .all()
    )
    return [
        {
            "source_system_code": binding.source_system_code,
            "building_code": building.code,
            "building_name": building.name,
        }
        for binding, building in rows
    ]


def get_upload_source(db: Session, *, actor: User, source_system_code: str) -> dict | None:
    normalized = str(source_system_code or "").strip()
    if not normalized or len(normalized) > 100:
        return None
    return next(
        (
            item
            for item in list_upload_sources(db, actor=actor)
            if item["source_system_code"] == normalized
        ),
        None,
    )


def build_excel_template(*, source_system_code: str, building_code: str) -> bytes:
    workbook = Workbook()
    package = workbook.active
    package.title = "Package"
    package.append(["field", "value"])
    defaults: dict[str, object] = {
        "template_version": _EXCEL_TEMPLATE_VERSION,
        "exchange_version": "OPS_EXCHANGE_V1",
        "delivery_id": None,
        "source_system_code": source_system_code,
        "building_code": building_code,
        "metric_definition_version": "OPS_V1",
        "period_start": None,
        "period_end": None,
        "as_of_date": None,
        "source_observed_at": None,
        "source_updated_at": None,
        "source_published_at": None,
        "source_publication_revision": 1,
        "source_publication_ref": None,
        "correction_of_delivery_id": None,
        "correction_reason": None,
        "data_classification": "simulated",
        "digest_profile": "OPS_CANONICAL_V1",
        "digest_algorithm": "sha-256",
        "content_digest": None,
    }
    package.append(["template_version", defaults["template_version"]])
    for field in _PACKAGE_FIELDS:
        package.append([field, defaults[field]])

    metrics = workbook.create_sheet("Metrics")
    metrics.append(list(_METRIC_FIELDS))
    for code in sorted(SUBMITTABLE_CODES):
        metrics.append(
            [
                code,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                "ops-source-v1",
                None,
                None,
            ]
        )

    workflow = workbook.create_sheet("Workflow")
    workflow.append(["stage", "actor_ref", "occurred_at"])
    for stage in ("submitted", "reviewed", "published"):
        workflow.append([stage, None, None])

    header_fill = PatternFill("solid", fgColor="17365D")
    input_fill = PatternFill("solid", fgColor="FFF2CC")
    fixed_fill = PatternFill("solid", fgColor="E7E6E6")
    header_font = Font(name="Arial", size=10, bold=True, color="FFFFFF")
    input_font = Font(name="Arial", size=10, color="0000FF")
    fixed_font = Font(name="Arial", size=10, color="000000")
    for sheet in workbook.worksheets:
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        for cell in sheet[1]:
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center", vertical="center")
        for row in sheet.iter_rows(min_row=2):
            for cell in row:
                cell.font = input_font
                cell.fill = input_fill
                cell.alignment = Alignment(vertical="top", wrap_text=True)
                cell.number_format = "@"

    for row_no in range(2, package.max_row + 1):
        field = package.cell(row_no, 1).value
        package.cell(row_no, 1).font = fixed_font
        package.cell(row_no, 1).fill = fixed_fill
        if field in {
            "template_version",
            "exchange_version",
            "source_system_code",
            "building_code",
            "metric_definition_version",
            "data_classification",
            "digest_profile",
            "digest_algorithm",
        }:
            package.cell(row_no, 2).font = fixed_font
            package.cell(row_no, 2).fill = fixed_fill
        if field == "source_publication_revision":
            package.cell(row_no, 2).number_format = "0"
    for row_no in range(2, metrics.max_row + 1):
        metrics.cell(row_no, 1).font = fixed_font
        metrics.cell(row_no, 1).fill = fixed_fill
        metrics.cell(row_no, 7).number_format = "0"
        metrics.cell(row_no, 8).number_format = "0"
    for row_no in range(2, workflow.max_row + 1):
        workflow.cell(row_no, 1).font = fixed_font
        workflow.cell(row_no, 1).fill = fixed_fill

    package.column_dimensions["A"].width = 34
    package.column_dimensions["B"].width = 48
    for index, field in enumerate(_METRIC_FIELDS, start=1):
        metrics.column_dimensions[metrics.cell(1, index).column_letter].width = max(16, len(field) + 3)
    workflow.column_dimensions["A"].width = 18
    workflow.column_dimensions["B"].width = 32
    workflow.column_dimensions["C"].width = 28
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def build_excel_sample(package_payload: dict) -> bytes:
    content = build_excel_template(
        source_system_code=str(package_payload["source_system_code"]),
        building_code=str(package_payload["building_code"]),
    )
    workbook = load_workbook(BytesIO(content), data_only=False)
    package = workbook["Package"]
    package_rows = {
        package.cell(row_no, 1).value: row_no
        for row_no in range(2, package.max_row + 1)
    }
    for field in _PACKAGE_FIELDS:
        package.cell(package_rows[field], 2).value = package_payload.get(field)

    metrics = workbook["Metrics"]
    metric_by_code = {
        str(item["code"]): item
        for item in package_payload.get("metrics", [])
    }
    for row_no in range(2, metrics.max_row + 1):
        code = str(metrics.cell(row_no, 1).value)
        item = metric_by_code[code]
        for column_no, field in enumerate(_METRIC_FIELDS, start=1):
            value = item.get(field)
            if field == "source_record_refs":
                value = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            metrics.cell(row_no, column_no).value = value

    workflow = workbook["Workflow"]
    workflow_payload = package_payload.get("workflow", {})
    for row_no in range(2, workflow.max_row + 1):
        stage = str(workflow.cell(row_no, 1).value)
        item = workflow_payload[stage]
        workflow.cell(row_no, 2).value = item["actor_ref"]
        workflow.cell(row_no, 3).value = item["occurred_at"]

    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()

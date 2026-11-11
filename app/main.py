from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
import traceback

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.account_name_policy import load_account_name_policy
from app.core.config import get_settings
from app.core.maintenance import MAINTENANCE_MESSAGE, in_maintenance
from app.core.migrations import run_migrations
from app.core.request_limits import RequestSizeExceeded, RequestSizeLimitMiddleware
from app.database import engine
from app.routers import ai, attachments, auth, authorizations, buildings, dashboard, operations, rent_analytics, reports, risks, settings, snapshot_exchange
from app.routers.audit import router as audit_router
from app.routers.backups import router as backups_router
from app.routers.client_errors import router as client_errors_router
from app.routers.contracts import router as contracts_router
from app.routers.customers import router as customers_router
from app.routers.integrity import router as integrity_router
from app.routers.execution_actions import router as execution_actions_router
from app.routers.financial_planning import router as financial_planning_router
from app.routers.finance_operations import router as finance_operations_router
from app.routers.rooms import router as rooms_router
from app.services.encryption_migration import migrate_plaintext_pdfs_at_startup
from app.tasks import start_scheduler


settings_obj = get_settings()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings_obj.validate()
    load_account_name_policy()
    run_migrations()
    migrate_plaintext_pdfs_at_startup()
    start_scheduler()
    yield


app = FastAPI(title="租赁管理系统", version="2.0.0", lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=500)
app.add_middleware(RequestSizeLimitMiddleware, max_bytes=settings_obj.max_request_size_bytes)

# Vite 产物只从同源加载脚本；样式需 unsafe-inline（Vue 运行时与日报内联样式）。
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; "
    "font-src 'self' data:; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "object-src 'none'; "
    "base-uri 'self'"
)


MAINTENANCE_ALLOWED_PATHS = {"/api/system/health", "/api/version", "/api/auth/status"}


@app.middleware("http")
async def maintenance_gate(request: Request, call_next):
    if in_maintenance() and request.url.path.startswith("/api/") and request.url.path not in MAINTENANCE_ALLOWED_PATHS:
        if snapshot_exchange.is_snapshot_exchange_path(request.url.path):
            return snapshot_exchange.problem_response(
                503,
                headers={"Retry-After": "1"},
            )
        return JSONResponse(status_code=503, content={"detail": MAINTENANCE_MESSAGE})
    return await call_next(request)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    from time import perf_counter
    started = perf_counter()
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Server-Timing"] = f"app;dur={(perf_counter() - started) * 1000:.2f}"
    headers = response.headers
    headers.setdefault("X-Content-Type-Options", "nosniff")
    headers.setdefault("X-Frame-Options", "DENY")
    headers.setdefault("Referrer-Policy", "same-origin")
    headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
    if settings_obj.cookie_secure:
        headers.setdefault("Strict-Transport-Security", "max-age=15552000; includeSubDomains")
    return response


@app.exception_handler(IntegrityError)
async def integrity_error_handler(request: Request, exc: IntegrityError):
    # 应用层唯一性检查与 DB 约束双保险：并发穿透到约束层时
    # 返回 409 而不是把 IntegrityError 当成 500 内部错误
    request_path = getattr(getattr(request, "url", None), "path", "")
    if snapshot_exchange.is_snapshot_exchange_path(request_path):
        # 快照交换服务已经把可预期的幂等与并发冲突转换为稳定回执；
        # 能逃逸到全局处理层的完整性错误属于未预期的内部失败。
        return snapshot_exchange.problem_response(500)
    if "FINANCE_" in str(exc.orig):
        return JSONResponse(status_code=409, content={"detail": "该记录涉及已关账月份或已关联资金。请先重开月份，或在经营财务核对中撤销关联后更正。"})
    traceback.print_exc()
    return JSONResponse(
        status_code=409,
        content={"detail": "数据冲突：可能与其他用户的并发操作重复（如合同编号/期间记录已存在），请刷新后重试"},
    )


@app.exception_handler(RequestSizeExceeded)
async def request_size_error_handler(request: Request, _exc: RequestSizeExceeded):
    if snapshot_exchange.is_snapshot_exchange_path(request.url.path):
        return snapshot_exchange.problem_response(413)
    return JSONResponse(status_code=413, content={"detail": "请求内容过大"})


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler_with_exchange_contract(
    request: Request,
    exc: StarletteHTTPException,
):
    if snapshot_exchange.is_snapshot_exchange_path(request.url.path):
        return snapshot_exchange.problem_response(
            int(exc.status_code),
            headers=dict(exc.headers or {}),
        )
    return await http_exception_handler(request, exc)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    if snapshot_exchange.is_snapshot_exchange_path(request.url.path):
        return snapshot_exchange.problem_response(500)
    traceback.print_exc()
    return JSONResponse(status_code=500, content={"detail": "服务器内部错误，请刷新后重试"})


app.include_router(auth.router)
app.include_router(authorizations.router)
app.include_router(buildings.router)
app.include_router(operations.router)
app.include_router(snapshot_exchange.router)
app.include_router(ai.router)
app.include_router(attachments.router)
app.include_router(dashboard.router)
app.include_router(rent_analytics.router)
app.include_router(reports.router)
app.include_router(risks.router)
app.include_router(contracts_router)
app.include_router(customers_router)
app.include_router(rooms_router)
app.include_router(audit_router)
app.include_router(client_errors_router)
app.include_router(backups_router)
app.include_router(integrity_router)
app.include_router(financial_planning_router)
app.include_router(finance_operations_router)
app.include_router(execution_actions_router)
app.include_router(settings.router)


def _frontend_index() -> Path:
    built_index = settings_obj.frontend_dir / "index.html"
    if built_index.exists():
        return built_index
    raise FileNotFoundError("frontend build is missing")


@app.get("/")
async def index():
    return FileResponse(_frontend_index(), headers={"Cache-Control": "no-cache"})


@app.get("/api/version")
async def version():
    return {"version": get_settings().app_version}


@app.get("/healthz", include_in_schema=False)
def healthz():
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception:
        return JSONResponse(status_code=503, content={"status": "unavailable"})
    return {"status": "ok", "version": get_settings().app_version}


@app.get("/{full_path:path}")
async def spa_fallback(full_path: str):
    if full_path.startswith("api/"):
        return JSONResponse(status_code=404, content={"detail": "Not Found"})

    if settings_obj.frontend_dir.exists():
        candidate = (settings_obj.frontend_dir / full_path).resolve()
        frontend_root = settings_obj.frontend_dir.resolve()
        if candidate.exists() and frontend_root in candidate.parents:
            return FileResponse(candidate)

    return FileResponse(_frontend_index(), headers={"Cache-Control": "no-cache"})

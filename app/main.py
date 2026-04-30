from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
import traceback

from fastapi import FastAPI, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse

from app.core.config import get_settings
from app.core.migrations import run_migrations
from app.routers import auth, settings
from app.routers.audit import router as audit_router
from app.routers.backups import router as backups_router
from app.routers.contracts import router as contracts_router
from app.routers.integrity import router as integrity_router
from app.routers.integrations import router as integrations_router
from app.routers.rooms import router as rooms_router
from app.services.encryption_migration import migrate_plaintext_pdfs_at_startup
from app.tasks import start_scheduler


settings_obj = get_settings()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings_obj.validate()
    run_migrations()
    migrate_plaintext_pdfs_at_startup()
    start_scheduler()
    yield


app = FastAPI(title="租赁合同管理系统", version="2.0.0", lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=500)


@app.exception_handler(Exception)
async def global_exception_handler(_request: Request, exc: Exception):
    traceback.print_exc()
    return JSONResponse(status_code=500, content={"detail": "服务器内部错误，请刷新后重试"})


app.include_router(auth.router)
app.include_router(contracts_router)
app.include_router(rooms_router)
app.include_router(audit_router)
app.include_router(backups_router)
app.include_router(integrity_router)
app.include_router(integrations_router)
app.include_router(settings.router)


def _frontend_index() -> Path:
    built_index = settings_obj.frontend_dir / "index.html"
    if built_index.exists():
        return built_index
    raise FileNotFoundError("frontend build is missing")


@app.get("/")
async def index():
    return FileResponse(_frontend_index())


@app.get("/api/version")
async def version():
    return {"version": get_settings().app_version}


@app.get("/{full_path:path}")
async def spa_fallback(full_path: str):
    if full_path.startswith("api/"):
        return JSONResponse(status_code=404, content={"detail": "Not Found"})

    if settings_obj.frontend_dir.exists():
        candidate = (settings_obj.frontend_dir / full_path).resolve()
        frontend_root = settings_obj.frontend_dir.resolve()
        if candidate.exists() and frontend_root in candidate.parents:
            return FileResponse(candidate)

    return FileResponse(_frontend_index())

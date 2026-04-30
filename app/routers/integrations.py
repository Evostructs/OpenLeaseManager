from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.access import require_admin
from app.database import get_db
from app.schemas import IntegrationConnectionOut, IntegrationConnectionPayload
from app.services.integrations import (
    create_connection,
    delete_connection,
    list_connections,
    list_events,
    provider_definitions,
    retry_event,
    test_connection,
    trigger_sync,
    update_connection,
)


router = APIRouter(prefix="/api/integrations", tags=["integrations"], dependencies=[Depends(require_admin)])


@router.get("/providers")
def providers():
    return {"providers": provider_definitions()}


@router.get("", response_model=list[IntegrationConnectionOut])
def connections(db: Session = Depends(get_db)):
    return list_connections(db)


@router.post("", response_model=IntegrationConnectionOut)
def create(payload: IntegrationConnectionPayload, db: Session = Depends(get_db)):
    return create_connection(db, payload)


@router.put("/{connection_id}", response_model=IntegrationConnectionOut)
def update(connection_id: int, payload: IntegrationConnectionPayload, db: Session = Depends(get_db)):
    return update_connection(db, connection_id, payload)


@router.delete("/{connection_id}")
def delete(connection_id: int, db: Session = Depends(get_db)):
    return delete_connection(db, connection_id)


@router.post("/{connection_id}/test")
def test(connection_id: int, db: Session = Depends(get_db)):
    return test_connection(db, connection_id)


@router.post("/{connection_id}/sync")
def sync(connection_id: int, db: Session = Depends(get_db)):
    return trigger_sync(db, connection_id)


@router.get("/events")
def events(status: str | None = None, limit: int = Query(100, ge=1, le=500), db: Session = Depends(get_db)):
    return list_events(db, status, limit)


@router.post("/events/{event_id}/retry")
def retry(event_id: int, db: Session = Depends(get_db)):
    return retry_event(db, event_id)

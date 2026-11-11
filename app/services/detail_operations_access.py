from __future__ import annotations

from collections.abc import Callable

from fastapi import Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.detail_operations_scope import require_detail_page_scope
from app.services.property_scope import (
    SelectedPropertyScope,
    get_selected_edit_scope,
    get_selected_view_scope,
)


def detail_scope_dependency(
    tab: str,
    *,
    for_write: bool,
) -> Callable[..., SelectedPropertyScope]:
    """Build an explicit per-request capability guard for a detail operations tab."""

    selected_scope_dependency = get_selected_edit_scope if for_write else get_selected_view_scope

    def dependency(
        scope: SelectedPropertyScope = Depends(selected_scope_dependency),
        db: Session = Depends(get_db),
    ) -> SelectedPropertyScope:
        require_detail_page_scope(db, scope, tab, for_write=for_write)
        return scope

    mode = "edit" if for_write else "view"
    dependency.__name__ = f"get_{tab.replace('-', '_')}_detail_{mode}_scope"
    return dependency

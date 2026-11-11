"""
把改造前已有的管理员权限一次性固化为显式平台能力。

Revision ID: 20260805_0021
Revises: 20260804_0020
Create Date: 2026-08-05
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260805_0021"
down_revision = "20260804_0020"
branch_labels = None
depends_on = None


EFFECTIVE_DATE = "2026-08-05"
MIGRATED_AT = "2026-08-05 00:30:00"
CAPABILITIES = ("platform_admin", "customer_master_manage")


def upgrade() -> None:
    bind = op.get_bind()
    for capability in CAPABILITIES:
        bind.execute(
            sa.text(
                """
                INSERT INTO user_capabilities
                    (user_id, capability, valid_from, valid_to, granted_by, granted_at)
                SELECT u.id, :capability, :valid_from, NULL, NULL, :granted_at
                FROM users u
                WHERE u.role = 'admin'
                  AND u.is_active = 1
                  AND NOT EXISTS (
                      SELECT 1
                      FROM user_capabilities c
                      WHERE c.user_id = u.id
                        AND c.capability = :capability
                        AND c.valid_from <= :valid_from
                        AND (c.valid_to IS NULL OR c.valid_to >= :valid_from)
                  )
                """
            ),
            {
                "capability": capability,
                "valid_from": EFFECTIVE_DATE,
                "granted_at": MIGRATED_AT,
            },
        )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            DELETE FROM user_capabilities
            WHERE capability IN ('platform_admin', 'customer_master_manage')
              AND valid_from = :valid_from
              AND valid_to IS NULL
              AND granted_by IS NULL
              AND granted_at = :granted_at
            """
        ),
        {"valid_from": EFFECTIVE_DATE, "granted_at": MIGRATED_AT},
    )

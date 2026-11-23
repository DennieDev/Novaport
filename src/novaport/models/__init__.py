# SPDX-License-Identifier: AGPL-3.0-or-later
"""ORM models. Import every model module here so Alembic sees all tables."""

from novaport.db import Base

__all__ = ["Base"]

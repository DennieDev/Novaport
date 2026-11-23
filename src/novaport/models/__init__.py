# SPDX-License-Identifier: AGPL-3.0-or-later
"""ORM models. Import every model module here so Alembic sees all tables."""

from novaport.db import Base
from novaport.models.access import (
    AuditEntry,
    Group,
    GroupMapping,
    GroupMember,
    LoginState,
    OidcProvider,
    Outcome,
    Role,
    RoleBinding,
)
from novaport.models.stack import (
    Deployment,
    DeploymentEvent,
    DeploymentStatus,
    DeployMode,
    EditMode,
    GitAuth,
    Repository,
    Revision,
    Stack,
    StackType,
)
from novaport.models.user import Session, User

__all__ = [
    "AuditEntry",
    "Base",
    "DeployMode",
    "Deployment",
    "DeploymentEvent",
    "DeploymentStatus",
    "EditMode",
    "GitAuth",
    "Group",
    "GroupMapping",
    "GroupMember",
    "LoginState",
    "OidcProvider",
    "Outcome",
    "Repository",
    "Revision",
    "Role",
    "RoleBinding",
    "Session",
    "Stack",
    "StackType",
    "User",
]

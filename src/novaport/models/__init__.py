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
from novaport.models.notification import (
    ChannelKind,
    EventType,
    NotificationChannel,
    NotificationSubscription,
)
from novaport.models.registry import ImageCheck, Registry
from novaport.models.stack import (
    Deployment,
    DeploymentEvent,
    DeploymentStatus,
    DeployMode,
    EditMode,
    Endpoint,
    GitAuth,
    Repository,
    Revision,
    Stack,
    StackType,
)
from novaport.models.user import ApiToken, Session, User

__all__ = [
    "ApiToken",
    "AuditEntry",
    "Base",
    "ChannelKind",
    "DeployMode",
    "Deployment",
    "DeploymentEvent",
    "DeploymentStatus",
    "EditMode",
    "Endpoint",
    "EventType",
    "GitAuth",
    "Group",
    "GroupMapping",
    "GroupMember",
    "ImageCheck",
    "LoginState",
    "NotificationChannel",
    "NotificationSubscription",
    "OidcProvider",
    "Outcome",
    "Registry",
    "Repository",
    "Revision",
    "Role",
    "RoleBinding",
    "Session",
    "Stack",
    "StackType",
    "User",
]

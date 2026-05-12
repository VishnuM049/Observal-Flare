import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Index, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column

from server.database import Base


class SiteStatus(str, enum.Enum):
    PENDING = "pending"
    PROVISIONING = "provisioning"
    DEPLOYING = "deploying"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    SLEEPING = "sleeping"
    DESTROYING = "destroying"
    DESTROYED = "destroyed"
    FAILED = "failed"


class DeployType(str, enum.Enum):
    BRANCH = "branch"
    COMMIT = "commit"
    PR = "pr"
    TAG = "tag"
    RELEASE = "release"


class SleepMode(str, enum.Enum):
    NONE = "none"
    NIGHTLY = "nightly"
    IDLE = "idle"


class Site(Base):
    __tablename__ = "sites"
    __table_args__ = (
        Index("ix_sites_name_active", "name", unique=True, postgresql_where=text("status != 'destroyed'")),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(63), nullable=False)
    domain: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[SiteStatus] = mapped_column(Enum(SiteStatus, values_callable=lambda e: [x.value for x in e]), nullable=False, default=SiteStatus.PENDING)
    requestor_email: Mapped[str] = mapped_column(String(320), nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)

    # Deploy source
    deploy_type: Mapped[DeployType] = mapped_column(Enum(DeployType, values_callable=lambda e: [x.value for x in e]), nullable=False)
    deploy_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    resolved_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)

    # Auto-update
    auto_update: Mapped[bool] = mapped_column(Boolean, default=False)

    # Redeploy behavior
    auto_wipe_on_failure: Mapped[bool] = mapped_column(Boolean, default=True)

    # Sleep/wake
    sleep_mode: Mapped[SleepMode] = mapped_column(Enum(SleepMode, values_callable=lambda e: [x.value for x in e]), nullable=False, default=SleepMode.NONE)
    idle_timeout_minutes: Mapped[int] = mapped_column(Integer, default=120)
    sleep_at_hour: Mapped[int] = mapped_column(Integer, default=19)
    wake_at_hour: Mapped[int] = mapped_column(Integer, default=7)

    # Auto-teardown
    scheduled_destroy_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Environment configuration
    env_overrides: Mapped[dict] = mapped_column(JSON, default=dict)
    instance_size: Mapped[str] = mapped_column(String(20), default="t3.large")

    # Idle callback auth
    idle_token: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # AWS resources
    instance_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    terraform_state_key: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Stale site reminders
    ttl_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reminder_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Lifecycle
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    destroyed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_deployed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Error tracking
    error_message: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    provision_log: Mapped[str | None] = mapped_column(Text, nullable=True)

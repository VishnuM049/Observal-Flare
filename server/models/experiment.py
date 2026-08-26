from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import BigInteger, Boolean, DateTime, Enum, ForeignKey, Index, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column

from server.database import Base


class ExperimentStatus(StrEnum):
    PENDING = "pending"
    PROVISIONING = "provisioning"
    RUNNING = "running"
    DESTROYING = "destroying"
    COMPLETED = "completed"
    FAILED = "failed"
    CLEANUP_FAILED = "cleanup_failed"
    CANCELLED = "cancelled"


class Experiment(Base):
    __tablename__ = "experiments"
    __table_args__ = (
        Index(
            "ix_experiments_single_active",
            text("(1)"),
            unique=True,
            postgresql_where=text(
                "status IN ('pending', 'provisioning', 'running', 'destroying')"
            ),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    status: Mapped[ExperimentStatus] = mapped_column(
        Enum(
            ExperimentStatus,
            name="experiment_status",
            values_callable=lambda values: [item.value for item in values],
        ),
        nullable=False,
        default=ExperimentStatus.PENDING,
    )
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)

    # Immutable run configuration, copied from server settings at creation time.
    target_ref: Mapped[str] = mapped_column(String(500), nullable=False)
    package_url: Mapped[str] = mapped_column(String(500), nullable=False)
    targets: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    rate_per_minute: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    expected_pulls: Mapped[int] = mapped_column(Integer, nullable=False)
    concurrency_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=4)
    platform: Mapped[str] = mapped_column(String(32), nullable=False, default="linux/amd64")
    image_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    layer_count: Mapped[int] = mapped_column(Integer, nullable=False)
    estimated_transfer_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    instance_type: Mapped[str] = mapped_column(String(32), nullable=False)

    # Run results.
    launched_pulls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    successful_pulls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_pulls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active_pulls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_concurrency: Mapped[int | None] = mapped_column(Integer, nullable=True)
    baseline_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    immediate_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    delayed_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    results: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    # Infrastructure and cleanup tracking.
    instance_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    terraform_state_key: Mapped[str] = mapped_column(String(255), nullable=False)
    cancellation_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    destroyed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_progress_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_counter_poll_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    error_message: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    cleanup_error: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    run_log: Mapped[str | None] = mapped_column(Text, nullable=True)


class ExperimentEvent(Base):
    __tablename__ = "experiment_events"
    __table_args__ = (Index("ix_experiment_events_experiment_created", "experiment_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    experiment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("experiments.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

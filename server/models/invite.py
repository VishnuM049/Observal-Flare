import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Boolean, func
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column

from server.database import Base


class Invite(Base):
    __tablename__ = "invites"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    token: Mapped[str] = mapped_column(String(24), unique=True, nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    label: Mapped[str | None] = mapped_column(String(255), nullable=True)

    max_sites: Mapped[int] = mapped_column(Integer, default=1)
    allowed_instance_sizes: Mapped[list] = mapped_column(JSON, default=lambda: ["t3.large"])
    forced_ttl_days: Mapped[int | None] = mapped_column(Integer, nullable=True, default=7)
    allowed_deploy_types: Mapped[list] = mapped_column(JSON, default=lambda: ["release", "tag"])
    env_overrides_locked: Mapped[bool] = mapped_column(Boolean, default=True)

    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    max_uses: Mapped[int | None] = mapped_column(Integer, nullable=True)
    use_count: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

"""Database tables.

The shape follows ``radiator_schema``: a run has asset results, each with a row
per agent, plus any performance results. The unique constraints are the
upsert keys the ingest API relies on, so a retried or replayed upload updates
rows rather than duplicating them.
"""

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# Postgres only: the ingest upserts use INSERT ... ON CONFLICT.
JsonType = JSONB


def utcnow():
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    suite: Mapped[str] = mapped_column(String(255))
    env: Mapped[Optional[str]] = mapped_column(String(32))
    target: Mapped[Optional[str]] = mapped_column(String(255))
    target_url: Mapped[Optional[str]] = mapped_column(Text)
    query_type: Mapped[Optional[str]] = mapped_column(String(32))
    harness_version: Mapped[Optional[str]] = mapped_column(String(64))
    tests_source: Mapped[Optional[str]] = mapped_column(Text)
    origin: Mapped[str] = mapped_column(String(32), default="harness")
    origin_ref: Mapped[Optional[str]] = mapped_column(String(255))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    counts: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    assets: Mapped[list["AssetResult"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", passive_deletes=True
    )
    performance: Mapped[list["PerformanceResult"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        # "the runs of this series", newest first: the runs list, and finding
        # the previous comparable run for a diff
        Index("ix_runs_series", "suite", "env", "started_at"),
        Index("ix_runs_started_at", "started_at"),
        # an import is re-runnable: one row per source run
        Index(
            "uq_runs_origin_ref",
            "origin",
            "origin_ref",
            unique=True,
            postgresql_where="origin_ref IS NOT NULL",
        ),
    )


class AssetResult(Base):
    __tablename__ = "asset_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    test_case_id: Mapped[str] = mapped_column(String(255))
    asset_id: Mapped[str] = mapped_column(String(255))
    kind: Mapped[str] = mapped_column(String(32))
    name: Mapped[Optional[str]] = mapped_column(Text)
    expected_output: Mapped[Optional[str]] = mapped_column(String(64))
    predicate: Mapped[Optional[str]] = mapped_column(String(255))
    input_curie: Mapped[Optional[str]] = mapped_column(String(255))
    output_curie: Mapped[Optional[str]] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32))
    parent_pk: Mapped[Optional[str]] = mapped_column(String(255))
    details: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)

    run: Mapped[Run] = relationship(back_populates="assets")
    agents: Mapped[list["AgentResult"]] = relationship(
        back_populates="asset",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="AgentResult.id",
    )

    __table_args__ = (
        UniqueConstraint("run_id", "test_case_id", "asset_id"),
        # an asset's history across runs
        Index("ix_asset_results_asset", "test_case_id", "asset_id"),
    )


class AgentResult(Base):
    __tablename__ = "agent_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    asset_result_id: Mapped[int] = mapped_column(
        ForeignKey("asset_results.id", ondelete="CASCADE")
    )
    agent: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32))
    message: Mapped[Optional[str]] = mapped_column(Text)
    http_status: Mapped[Optional[int]] = mapped_column(Integer)
    found: Mapped[Optional[bool]] = mapped_column(Boolean)
    rank: Mapped[Optional[int]] = mapped_column(Integer)
    score: Mapped[Optional[float]] = mapped_column(Float)
    n_results: Mapped[Optional[int]] = mapped_column(Integer)
    response_time_s: Mapped[Optional[float]] = mapped_column(Float)
    pk: Mapped[Optional[str]] = mapped_column(String(255))
    expected_nodes_found: Mapped[Optional[str]] = mapped_column(Text)

    asset: Mapped[AssetResult] = relationship(back_populates="agents")

    __table_args__ = (
        UniqueConstraint("asset_result_id", "agent"),
        Index("ix_agent_results_agent", "agent"),
    )


class PerformanceResult(Base):
    __tablename__ = "performance_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    test_case_id: Mapped[str] = mapped_column(String(255))
    asset_id: Mapped[str] = mapped_column(String(255))
    host: Mapped[str] = mapped_column(String(512))
    helmsdeep_target: Mapped[Optional[str]] = mapped_column(String(64))
    profile: Mapped[Optional[str]] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32))
    error: Mapped[Optional[str]] = mapped_column(Text)
    exit_code: Mapped[Optional[int]] = mapped_column(Integer)
    max_sustainable_concurrency: Mapped[Optional[float]] = mapped_column(Float)
    knee_unsupported: Mapped[Optional[bool]] = mapped_column(Boolean)
    checkpoints_passed: Mapped[Optional[bool]] = mapped_column(Boolean)
    summary: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)

    run: Mapped[Run] = relationship(back_populates="performance")

    __table_args__ = (
        UniqueConstraint("run_id", "test_case_id", "asset_id", "host"),
        Index("ix_performance_results_host", "host"),
    )

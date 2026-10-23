from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class AnalysisStatus(StrEnum):
    OK = "ok"
    WARNING = "warning"
    FAILED = "failed"


class AnalysisIssue(BaseModel):
    model_config = ConfigDict(extra="ignore")

    code: str
    message: str
    field: str | None = None
    severity: str = "error"  # error / warning
    retryable: bool = False


class DataSourceInfo(BaseModel):
    model_config = ConfigDict(extra="ignore")

    source: str
    queried_at: datetime
    record_count: int = 0
    missing_fields: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class AnalysisLine(BaseModel):
    model_config = ConfigDict(extra="ignore")

    part_id: int
    part_name: str
    quantity: int
    unit_price: Decimal
    subtotal: Decimal
    supplier_id: int
    lead_time_days: int | None = None
    available_quantity: int | None = None
    inventory_gap: int | None = None


class SupplierComparison(BaseModel):
    model_config = ConfigDict(extra="ignore")

    supplier_id: int
    supplier_name: str
    unit_price: Decimal | None = None
    lead_time_days: int | None = None
    available_quantity: int | None = None
    supplier_status: int | None = None
    credit_rating: str | None = None
    price_source: str | None = None
    warnings: list[str] = Field(default_factory=list)


class StructuredAnalysis(BaseModel):
    model_config = ConfigDict(extra="ignore")

    task_id: str
    plan_version: int
    plan_hash: str
    computed_at: datetime
    currency: str = "CNY"

    budget: Decimal | None = None
    total_cost: Decimal
    budget_delta: Decimal | None = None
    inventory_shortage: int | None = None
    unknown_inventory_parts: list[int] = Field(default_factory=list)

    lines: list[AnalysisLine] = Field(default_factory=list)
    supplier_comparison: list[SupplierComparison] = Field(default_factory=list)
    sources: list[DataSourceInfo] = Field(default_factory=list)
    warnings: list[AnalysisIssue] = Field(default_factory=list)


class AnalysisValidation(BaseModel):
    model_config = ConfigDict(extra="ignore")

    passed: bool
    checked_at: datetime
    errors: list[AnalysisIssue] = Field(default_factory=list)
    warnings: list[AnalysisIssue] = Field(default_factory=list)


class SandboxVerification(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: str  # ok / mismatch / timeout / unavailable
    passed: bool
    duration_ms: int = 0
    issues: list[AnalysisIssue] = Field(default_factory=list)
    error: str | None = None


class AnalysisResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: AnalysisStatus
    analysis: StructuredAnalysis | None = None
    validation: AnalysisValidation | None = None
    sandbox_verification: SandboxVerification | None = None
    report_markdown: str | None = None
    narrative: str | None = None
    error: AnalysisIssue | None = None

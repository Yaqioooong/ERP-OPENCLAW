from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class EvalLayer(StrEnum):
    UNIT = "unit"
    GRAPH = "graph"
    INTEGRATION = "integration"
    E2E = "e2e"


class ActionType(StrEnum):
    START = "start"
    RESUME = "resume"
    RESTART = "restart"


class Action(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: ActionType
    resume: dict[str, Any] | None = None
    user_id: str = "u1"
    roles: tuple[str, ...] = ("requester",)


class FaultPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    part_mode: Literal[
        "ok", "not_found", "timeout", "error", "missing_price"
    ] = "ok"
    inventory_mode: Literal["ok", "unknown", "timeout"] = "ok"
    supplier_mode: Literal["ok", "inactive", "timeout"] = "ok"
    order_mode: Literal["ok", "timeout", "fail", "duplicate"] = "ok"
    asset_delay_ms: int = 0
    fail_once: bool = False


class Expectation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    final_status: str | None = None
    execution_status: str | None = None
    error_code: str | None = None
    error_codes_include: list[str] = Field(default_factory=list)
    order_created: bool | None = None
    max_order_created: int | None = None
    min_plan_version: int | None = None
    approval_status: str | None = None
    report_required: bool = False
    report_contains: list[str] = Field(default_factory=list)
    max_tool_calls: int | None = None
    forbidden_order: bool = False


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    group: str
    title: str
    layer: EvalLayer = EvalLayer.GRAPH
    description: str = ""
    request: dict[str, Any] | None = None
    raw_request: str = ""
    faults: FaultPlan = Field(default_factory=FaultPlan)
    actions: list[Action] = Field(
        default_factory=lambda: [Action(type=ActionType.START)]
    )
    expect: Expectation


class EvalResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_id: str
    group: str
    title: str
    passed: bool
    duration_ms: int
    failures: list[str] = Field(default_factory=list)
    final_status: str | None = None
    execution_status: str | None = None
    error_code: str | None = None
    order_created: int = 0
    tool_calls: int = 0
    report: str | None = None

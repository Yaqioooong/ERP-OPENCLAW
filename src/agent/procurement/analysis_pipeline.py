from __future__ import annotations

from datetime import datetime
from typing import Protocol

from agent.procurement.analysis_calculator import (
    AnalysisInvariantError,
    DeterministicAnalyzer,
)
from agent.procurement.analysis_models import (
    AnalysisIssue,
    AnalysisResult,
    AnalysisStatus,
    DataSourceInfo,
    SandboxVerification,
    StructuredAnalysis,
)
from agent.procurement.analysis_report import ReportNarrator, render_report
from agent.procurement.analysis_validator import AnalysisValidator
from agent.procurement.models import (
    CandidateSet,
    ProcurementPlan,
    ProcurementRequest,
)


class SandboxVerifier(Protocol):
    async def verify(
            self, analysis: StructuredAnalysis
    ) -> SandboxVerification: ...


class TrustedAnalysisPipeline:
    def __init__(
            self,
            *,
            analyzer: DeterministicAnalyzer | None = None,
            validator: AnalysisValidator | None = None,
            narrator: ReportNarrator | None = None,
            sandbox_verifier: SandboxVerifier | None = None,
    ):
        self._analyzer = analyzer or DeterministicAnalyzer()
        self._validator = validator or AnalysisValidator()
        self._narrator = narrator
        self._sandbox = sandbox_verifier

    async def run(
            self,
            *,
            task_id: str,
            request: ProcurementRequest,
            candidates: CandidateSet,
            plan: ProcurementPlan,
            now: datetime,
            sources: list[DataSourceInfo] | None = None,
    ) -> AnalysisResult:
        if sources is None:
            sources = [
                DataSourceInfo(
                    source="candidate_set",
                    queried_at=now,
                    record_count=len(candidates.offers),
                    warnings=list(candidates.data_warnings),
                )
            ]

        try:
            analysis = self._analyzer.analyze(
                task_id=task_id,
                request=request,
                candidates=candidates,
                plan=plan,
                now=now,
                sources=sources,
            )
        except AnalysisInvariantError as exc:
            return AnalysisResult(
                status=AnalysisStatus.FAILED,
                error=AnalysisIssue(
                    code=exc.code,
                    message=str(exc),
                    severity="error",
                    retryable=False,
                ),
            )
        except Exception as exc:
            return AnalysisResult(
                status=AnalysisStatus.FAILED,
                error=AnalysisIssue(
                    code="ANALYSIS_CALCULATION_FAILED",
                    message=f"确定性分析失败：{exc}",
                    severity="error",
                    retryable=False,
                ),
            )

        validation = self._validator.validate(
            analysis=analysis, plan=plan, now=now
        )
        sandbox_verification: SandboxVerification | None = None
        warnings: list[AnalysisIssue] = list(validation.warnings)

        if self._sandbox is not None:
            try:
                sandbox_verification = await self._sandbox.verify(analysis)
            except Exception as exc:
                sandbox_verification = SandboxVerification(
                    status="unavailable",
                    passed=False,
                    error=str(exc),
                )
            if sandbox_verification.status == "mismatch":
                validation = validation.model_copy(
                    update={
                        "passed": False,
                        "errors": [
                            *validation.errors,
                            AnalysisIssue(
                                code="SANDBOX_RECOMPUTE_MISMATCH",
                                message="沙箱独立复算与本地计算结果不一致。",
                                field="sandbox_verification",
                            ),
                        ],
                        "warnings": warnings,
                    }
                )
            elif not sandbox_verification.passed:
                warnings.append(
                    AnalysisIssue(
                        code="SANDBOX_VERIFICATION_UNAVAILABLE",
                        message=(
                            "沙箱复算不可用，使用本地确定性结果："
                            f"{sandbox_verification.error or 'unknown'}"
                        ),
                        severity="warning",
                    )
                )
                validation = validation.model_copy(
                    update={"warnings": warnings}
                )

        narrative: str | None = None
        if self._narrator is not None:
            try:
                narrative = await self._narrator.narrate(
                    analysis=analysis, validation=validation
                )
            except Exception as exc:
                warnings.append(
                    AnalysisIssue(
                        code="REPORT_NARRATIVE_FAILED",
                        message=f"解释文字生成失败，使用模板报告：{exc}",
                        severity="warning",
                    )
                )
                validation = validation.model_copy(
                    update={"warnings": warnings}
                )

        report = render_report(
            analysis=analysis,
            validation=validation,
            narrative=narrative,
        )

        if not validation.passed:
            status = AnalysisStatus.FAILED
        elif validation.errors or warnings:
            status = AnalysisStatus.WARNING
        else:
            status = AnalysisStatus.OK

        return AnalysisResult(
            status=status,
            analysis=analysis,
            validation=validation,
            sandbox_verification=sandbox_verification,
            report_markdown=report,
            narrative=narrative,
            error=(
                validation.errors[0] if validation.errors else None
            ),
        )

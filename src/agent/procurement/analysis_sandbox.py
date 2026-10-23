from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from agent.procurement.analysis_models import (
    AnalysisIssue,
    SandboxVerification,
    StructuredAnalysis,
)

VERIFY_SCRIPT = r'''
import json
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

payload = json.loads(Path("/analysis/input.json").read_text(encoding="utf-8"))
issues = []

def d(value):
    return Decimal(str(value))

lines = payload.get("lines") or []
total = Decimal("0")
for line in lines:
    quantity = int(line["quantity"])
    price = d(line["unit_price"])
    subtotal = d(line["subtotal"])
    expected = (Decimal(quantity) * price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if subtotal != expected:
        issues.append({
            "code": "SANDBOX_SUBTOTAL_MISMATCH",
            "message": f"part {line.get('part_id')} subtotal mismatch",
        })
    total += subtotal

total = total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
if total != d(payload["total_cost"]):
    issues.append({
        "code": "SANDBOX_TOTAL_MISMATCH",
        "message": f"total {total} != {payload['total_cost']}",
    })

shortage = 0
unknown = False
for line in lines:
    gap = line.get("inventory_gap")
    if gap is None:
        unknown = True
    else:
        shortage += int(gap)

if not unknown and payload.get("inventory_shortage") is not None:
    if int(payload["inventory_shortage"]) != shortage:
        issues.append({
            "code": "SANDBOX_SHORTAGE_MISMATCH",
            "message": f"shortage {shortage} != {payload['inventory_shortage']}",
        })

Path("/analysis/verification.json").write_text(
    json.dumps({"passed": not issues, "issues": issues}, ensure_ascii=False),
    encoding="utf-8",
)
'''


class OpenSandboxVerifier:
    """在沙箱里用固定脚本独立复算；不执行模型生成的代码。"""

    def __init__(self, backend: Any, *, timeout_seconds: int = 30):
        self._backend = backend
        self._timeout = timeout_seconds

    async def verify(self, analysis: StructuredAnalysis) -> SandboxVerification:
        started = time.monotonic()
        try:
            await asyncio.to_thread(
                self._upload,
                analysis.model_dump(mode="json"),
            )
            result = await asyncio.wait_for(
                asyncio.to_thread(
                    self._backend.execute,
                    "python /analysis/verify.py",
                    timeout=self._timeout,
                ),
                timeout=self._timeout + 5,
            )
            if getattr(result, "exit_code", 0) != 0:
                return SandboxVerification(
                    status="unavailable",
                    passed=False,
                    duration_ms=int((time.monotonic() - started) * 1000),
                    error=getattr(result, "output", "sandbox command failed"),
                )

            payload = await asyncio.to_thread(self._download_verification)
            issues = [
                AnalysisIssue(
                    code=str(item.get("code") or "SANDBOX_VERIFY_FAILED"),
                    message=str(item.get("message") or ""),
                    field="sandbox_verification",
                )
                for item in payload.get("issues", [])
                if isinstance(item, dict)
            ]
            passed = bool(payload.get("passed")) and not issues
            return SandboxVerification(
                status="ok" if passed else "mismatch",
                passed=passed,
                duration_ms=int((time.monotonic() - started) * 1000),
                issues=issues,
            )
        except asyncio.TimeoutError:
            return SandboxVerification(
                status="timeout",
                passed=False,
                duration_ms=int((time.monotonic() - started) * 1000),
                error=f"沙箱复算超时（{self._timeout}s）",
            )
        except Exception as exc:
            return SandboxVerification(
                status="unavailable",
                passed=False,
                duration_ms=int((time.monotonic() - started) * 1000),
                error=str(exc),
            )
        finally:
            try:
                await asyncio.to_thread(self._cleanup)
            except Exception:
                pass

    def _upload(self, payload: dict) -> None:
        responses = self._backend.upload_files(
            [
                (
                    "/analysis/input.json",
                    json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                ),
                ("/analysis/verify.py", VERIFY_SCRIPT.encode("utf-8")),
            ]
        )
        errors = [r.error for r in responses if getattr(r, "error", None)]
        if errors:
            raise RuntimeError(f"沙箱上传失败: {errors}")

    def _download_verification(self) -> dict:
        responses = self._backend.download_files(
            ["/analysis/verification.json"]
        )
        if not responses or responses[0].content is None:
            raise RuntimeError("沙箱没有生成 verification.json")
        return json.loads(responses[0].content.decode("utf-8"))

    def _cleanup(self) -> None:
        self._backend.execute(
            "rm -f /analysis/input.json /analysis/verify.py "
            "/analysis/verification.json",
            timeout=5,
        )

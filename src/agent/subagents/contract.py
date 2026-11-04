"""子 Agent 委派与回传的结构化契约。

分两个方向，都做成可测试的纯函数，避免“约定只活在提示词里”：

1. 下行（主 Agent → 子 Agent）
   - `validate_brief`：检查 `task.description` 是否包含约定段落；
   - `patch_brief`：为可确定性补齐的段落（偏好、输出要求）填默认正文。
2. 上行（子 Agent → 主 Agent）
   - `parse_report`：把子 Agent 的最终文本解析成 `ReportEnvelope`；
   - `render_envelope`：渲染成主 Agent 能稳定消费的规范文本块。
     解析不完整时给 `status="degraded"`，明确指向文件产物，而不是让主
     Agent 拿一段残缺文本当结论。

`task` 工具本身（deepagents `TaskToolSchema`）只有 `description`/`subagent_type`
两个自由字段，且子 Agent 默认不继承对话上下文，所以下行契约必须在中间件层
补强，不能只依赖提示词。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

# ---------------------------------------------------------------------------
# 下行：委派简报
# ---------------------------------------------------------------------------

BRIEF_GOAL = "【任务目标】"
BRIEF_PREFERENCES = "【用户偏好】"
BRIEF_REQUEST = "【需求正文】"
BRIEF_OUTPUT = "【输出要求】"

#: 委派简报的规范段落顺序；所有提示词文档必须与此一致。
BRIEF_SECTIONS: tuple[str, ...] = (
    BRIEF_GOAL,
    BRIEF_PREFERENCES,
    BRIEF_REQUEST,
    BRIEF_OUTPUT,
)

#: 缺失即拦回给模型的段落：这两段属于模型独有的语义信息，系统补不出来。
BRIEF_REQUIRED_SECTIONS: tuple[str, ...] = (BRIEF_GOAL, BRIEF_REQUEST)

#: 缺失可确定性补齐的段落。
BRIEF_DEFAULTABLE_SECTIONS: tuple[str, ...] = (BRIEF_PREFERENCES, BRIEF_OUTPUT)

_HEADER_RE = re.compile(r"【[^】\n]{1,24}】")


@dataclass(frozen=True)
class BriefCheck:
    """一次委派简报的结构检查结果。"""

    present: tuple[str, ...]
    missing: tuple[str, ...]
    empty: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.missing and not self.empty

    @property
    def blocking(self) -> tuple[str, ...]:
        """必须由模型补写的段落（缺失或正文为空）。"""
        broken = set(self.missing) | set(self.empty)
        return tuple(s for s in BRIEF_SECTIONS if s in broken)


def split_sections(text: str) -> tuple[str, list[tuple[str, str]]]:
    """按 `【...】` 头切段，返回 (前缀, [(头, 正文), ...])。

    只用段落头切分，不做语义理解；正文里出现的普通方括号不会被误切。
    """
    if not text:
        return "", []
    matches = list(_HEADER_RE.finditer(text))
    if not matches:
        return text.strip(), []

    preamble = text[: matches[0].start()].strip()
    sections: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections.append((match.group(0), text[match.end(): end].strip()))
    return preamble, sections


def section_body(text: str, header: str) -> str:
    """取某段正文；不存在返回空字符串。"""
    _, sections = split_sections(text)
    for name, body in sections:
        if name == header:
            return body
    return ""


def validate_brief(description: str | None) -> BriefCheck:
    """检查委派简报是否含全部规范段落，且关键段落正文非空。"""
    _, sections = split_sections(description or "")
    bodies: dict[str, str] = {}
    for name, body in sections:
        bodies.setdefault(name, body)

    present = tuple(s for s in BRIEF_SECTIONS if s in bodies)
    missing = tuple(s for s in BRIEF_SECTIONS if s not in bodies)
    empty = tuple(s for s in BRIEF_SECTIONS if s in bodies and not bodies[s])
    return BriefCheck(present=present, missing=missing, empty=empty)


def render_brief_repair_hint(check: BriefCheck, blocking: Iterable[str]) -> str:
    """给模型看的可修复提示：说清缺哪段、该写成什么样。"""
    headers = "\n".join(f"{s}\n（正文）" for s in BRIEF_SECTIONS)
    return (
        "task 工具未执行：委派简报缺少必需段落 "
        f"{'、'.join(blocking)}。\n"
        "子 Agent 默认不继承当前对话上下文，缺少的段落不会被它自动知晓，"
        "请补齐后重新调用 task。\n"
        "规范格式：\n"
        f"{headers}\n"
        f"当前已包含：{'、'.join(check.present) or '无'}。"
    )


def build_default_sections(
    subagent_type: str,
    context: Any = None,
) -> dict[str, str]:
    """为可补齐段落生成确定性的默认正文。

    只使用运行时可验证的信息（用户身份、偏好文件路径、按子 Agent 区分的
    输出形态），不猜测业务内容。
    """
    user_id = getattr(context, "user_id", None) or "未提供"
    username = getattr(context, "username", None) or user_id
    preferences_file = (
        f"/memories/{user_id}/preferences.md" if user_id != "未提供" else "（未知）"
    )

    preferences = (
        f"用户ID：{user_id}\n"
        f"用户名：{username}\n"
        f"偏好文件：{preferences_file}"
        + (
            "（仅在任务涉及输出格式/语言时读取）"
            if subagent_type == "general-purpose"
            else "（工作前用 read_file 读取；读不到则按默认：表格 + CNY）"
        )
    )

    if subagent_type == "procurement-order":
        output = (
            "1. 只回传本次操作结果，不返回原始接口响应；\n"
            "2. 必须包含【操作结果】【变更字段】【备注】三段；\n"
            "3. 接口失败时如实写明错误码与原因，不要重试到超限。"
        )
    elif subagent_type == "general-purpose":
        output = (
            "1. 直接回传任务结果，不返回原始工具输出；\n"
            "2. 如生成文件，给出文件路径；\n"
            "3. 数据不足或无法完成时明说，不要编造。"
        )
    else:
        output = (
            "1. 报告先写入文件（/analysis/ 下），再回传；\n"
            "2. 必须包含【报告路径】【分析内容摘要】【分析结论】【采购建议】四段；\n"
            "3. 只回传加工后的结论，不返回原始工具输出；数据不足时明说，不要编造。"
        )

    return {BRIEF_PREFERENCES: preferences, BRIEF_OUTPUT: output}


def patch_brief(description: str, defaults: Mapping[str, str]) -> str:
    """把缺失/为空的段落按默认正文追加到简报末尾。"""
    check = validate_brief(description)
    broken = set(check.missing) | set(check.empty)
    additions = [
        f"{header}\n{defaults[header].strip()}"
        for header in BRIEF_DEFAULTABLE_SECTIONS
        if header in broken and defaults.get(header)
    ]
    if not additions:
        return description
    base = (description or "").rstrip()
    return f"{base}\n\n" + "\n\n".join(additions)


#: 下行简报契约版本；结构变化时递增，便于后续兼容。
BRIEF_VERSION = "1.0"

CODE_BRIEF_INVALID = "DELEGATION_BRIEF_INVALID"
CODE_AGENT_UNKNOWN = "DELEGATION_AGENT_UNKNOWN"
CODE_EMPTY_RESULT = "DELEGATION_EMPTY_RESULT"
CODE_REPORT_INCOMPLETE = "DELEGATION_REPORT_INCOMPLETE"
CODE_TIMEOUT = "DELEGATION_TIMEOUT"
CODE_RATE_LIMITED = "DELEGATION_RATE_LIMITED"
CODE_TRANSPORT_ERROR = "DELEGATION_TRANSPORT_ERROR"
CODE_RECURSION_LIMIT = "DELEGATION_RECURSION_LIMIT"
CODE_FAILED = "DELEGATION_FAILED"


class BriefValidationError(ValueError):
    """委派简报缺少不可由系统补齐的必需段落。"""

    def __init__(
        self,
        missing: Iterable[str],
        *,
        present: Iterable[str] = (),
    ) -> None:
        self.code = CODE_BRIEF_INVALID
        self.missing = tuple(missing)
        self.present = tuple(present)
        super().__init__(
            "委派简报缺少必需段落：" + "、".join(self.missing)
        )


def _json_brief_to_text(description: str) -> str:
    """把前向兼容的 JSON 简报转成规范四段文本；无法识别时原样返回。"""
    text = (description or "").strip()
    if not text.startswith("{"):
        return description
    try:
        payload = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return description
    if not isinstance(payload, dict):
        return description

    mapping = (
        ("goal", BRIEF_GOAL),
        ("request", BRIEF_REQUEST),
        ("preferences", BRIEF_PREFERENCES),
        ("output", BRIEF_OUTPUT),
        ("output_contract", BRIEF_OUTPUT),
    )
    parts: list[str] = []
    for key, header in mapping:
        value = payload.get(key)
        if value is None or value == "":
            continue
        if not isinstance(value, str):
            value = json.dumps(value, ensure_ascii=False, default=str)
        parts.append(f"{header}\n{value}")
    if not parts:
        return description
    return "\n\n".join(parts)


def prepare_brief(
    description: str | None,
    subagent_type: str,
    *,
    context: Any = None,
    required_sections: Iterable[str] = BRIEF_REQUIRED_SECTIONS,
) -> str:
    """校验并补齐委派简报，返回子 Agent 实际收到的规范文本。

    缺【任务目标】/【需求正文】时抛 ``BriefValidationError``，由调用方转成
    可修复的 tool error；缺【用户偏好】/【输出要求】时用运行时上下文补齐。
    """
    normalized = _json_brief_to_text(description or "")
    check = validate_brief(normalized)
    blocking = tuple(
        section
        for section in required_sections
        if section in check.blocking
    )
    if blocking:
        raise BriefValidationError(blocking, present=check.present)
    return patch_brief(
        normalized,
        build_default_sections(subagent_type, context),
    )



# ---------------------------------------------------------------------------
# 上行：子 Agent 回传
# ---------------------------------------------------------------------------

REPORT_PATH = "【报告路径】"
REPORT_SUMMARY = "【分析内容摘要】"
REPORT_CONCLUSIONS = "【分析结论】"
REPORT_SUGGESTIONS = "【采购建议】"
REPORT_RESULT = "【操作结果】"
REPORT_CHANGES = "【变更字段】"
REPORT_NOTE = "【备注】"
REPORT_TEXT = "【文本结果】"

#: 子 Agent 常见同义头，解析时归一，不要求模型逐字背诵。
REPORT_ALIASES: dict[str, str] = {
    "【结论】": REPORT_CONCLUSIONS,
    "【分析结论与建议】": REPORT_CONCLUSIONS,
    "【建议】": REPORT_SUGGESTIONS,
    "【采购建议】": REPORT_SUGGESTIONS,
    "【摘要】": REPORT_SUMMARY,
    "【报告摘要】": REPORT_SUMMARY,
    "【结果】": REPORT_RESULT,
    "【执行结果】": REPORT_RESULT,
    "【修改字段】": REPORT_CHANGES,
}

_PATH_RE = re.compile(r"(/[\w.\-/]+\.(?:md|csv|json|xlsx|png|jpg))")

STATUS_STRUCTURED = "structured"
STATUS_DEGRADED = "degraded"


@dataclass(frozen=True)
class ReportContract:
    """某类子 Agent 必须/可选回传的段落。"""

    agent: str
    required: tuple[str, ...]
    optional: tuple[str, ...] = ()

    def missing_in(self, fields: Mapping[str, str]) -> tuple[str, ...]:
        return tuple(h for h in self.required if not fields.get(h))


ANALYST_CONTRACT = ReportContract(
    agent="procurement-analyst",
    required=(REPORT_PATH, REPORT_SUMMARY, REPORT_CONCLUSIONS, REPORT_SUGGESTIONS),
)
ORDER_CONTRACT = ReportContract(
    agent="procurement-order",
    required=(REPORT_RESULT,),
    optional=(REPORT_CHANGES, REPORT_NOTE),
)
GENERIC_CONTRACT = ReportContract(agent="*", required=())

REPORT_CONTRACTS: dict[str, ReportContract] = {
    ANALYST_CONTRACT.agent: ANALYST_CONTRACT,
    ORDER_CONTRACT.agent: ORDER_CONTRACT,
}


@dataclass(frozen=True)
class ReportEnvelope:
    """规范化后的回传信封。"""

    agent: str
    status: str
    fields: dict[str, str] = field(default_factory=dict)
    missing: tuple[str, ...] = ()
    report_path: str | None = None
    raw_text: str = ""

    @property
    def ok(self) -> bool:
        return self.status == STATUS_STRUCTURED


def contract_for(agent: str | None) -> ReportContract:
    return REPORT_CONTRACTS.get(agent or "", GENERIC_CONTRACT)


def parse_report(text: str | None, agent: str | None = None) -> ReportEnvelope:
    """解析子 Agent 最终文本；不完整时降级而不是抛错。"""
    raw = text or ""
    _, sections = split_sections(raw)

    fields: dict[str, str] = {}
    for header, body in sections:
        canonical = REPORT_ALIASES.get(header, header)
        body = body.strip()
        if canonical in fields and body:
            fields[canonical] = f"{fields[canonical]}\n{body}".strip()
        else:
            fields.setdefault(canonical, body)

    contract = contract_for(agent)
    missing = contract.missing_in(fields)

    report_path = fields.get(REPORT_PATH) or None
    if not report_path:
        found = _PATH_RE.search(raw)
        report_path = found.group(1) if found else None

    # 契约未声明必需段落的子 Agent（如 general-purpose）：纯文本也是合法结果，
    # 包成【文本结果】而不是判降级，避免逼迫通用子 Agent 套采购报告模板。
    if not contract.required:
        if raw.strip() and not fields:
            fields = {REPORT_TEXT: raw.strip()}
        missing = () if raw.strip() else ("（空返回）",)

    status = STATUS_STRUCTURED if not missing else STATUS_DEGRADED
    return ReportEnvelope(
        agent=agent or "unknown",
        status=status,
        fields=fields,
        missing=tuple(missing),
        report_path=report_path,
        raw_text=raw,
    )


def render_envelope(envelope: ReportEnvelope) -> str:
    """渲染成主 Agent 侧稳定可读的文本块。"""
    head = f"【任务回传】status={envelope.status} agent={envelope.agent}"
    if envelope.ok:
        order = list(contract_for(envelope.agent).required) + list(
            contract_for(envelope.agent).optional
        )
        rendered = [head]
        for header in order:
            body = envelope.fields.get(header)
            if body:
                rendered.append(f"{header}\n{body}")
        # 契约之外的段落原样附在末尾，不丢信息。
        extras = [
            (h, b) for h, b in envelope.fields.items()
            if h not in order and b
        ]
        for header, body in extras:
            rendered.append(f"{header}\n{body}")
        return "\n\n".join(rendered)

    lines = [
        head,
        f"【缺失段落】{'、'.join(envelope.missing) or '无'}",
        "【降级说明】子 Agent 未按回传契约返回，以下原文不保证结构完整、"
        "不要直接把其中的推测当结论；优先读取文件产物。",
    ]
    if envelope.report_path:
        lines.append(
            f"【可用文件产物】{envelope.report_path}"
            "（先 read_file 读取该文件，再向用户汇报）"
        )
    lines.append("【原文开始】")
    lines.append(envelope.raw_text.strip() or "（空）")
    lines.append("【原文结束】")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 上行：结构化结果信封
# ---------------------------------------------------------------------------

RESULT_VERSION = "1.0"
RESULT_OK = "ok"
RESULT_DEGRADED = "degraded"
RESULT_ERROR = "error"
RESULT_TIMEOUT = "timeout"

DEFAULT_RAW_TEXT_LIMIT = 2000


def _single_line(text: str, *, limit: int = 500) -> str:
    value = " ".join(str(text or "").split())
    return value if len(value) <= limit else f"{value[:limit]}…"


def _truncate(text: str, limit: int) -> str:
    value = text or ""
    if limit <= 0 or len(value) <= limit:
        return value
    return f"{value[:limit]}\n…（已截断，原文共 {len(value)} 字符）"


def _infer_outcome(agent: str, fields: Mapping[str, str]) -> str | None:
    if agent != "procurement-order":
        return None
    result = str(fields.get(REPORT_RESULT) or "").strip()
    if not result:
        return None
    if result.startswith("失败") or "失败" in result[:20]:
        return "failure"
    if result.startswith("成功") or "成功" in result[:20]:
        return "success"
    return None


@dataclass(frozen=True)
class DelegationResult:
    """父 Agent 侧消费的版本化回传信封。"""

    agent: str
    status: str
    code: str | None = None
    message: str = ""
    retryable: bool = False
    side_effect_unknown: bool = False
    missing: tuple[str, ...] = ()
    fields: dict[str, str] = field(default_factory=dict)
    report_path: str | None = None
    raw_text: str = ""
    duration_ms: int | None = None
    outcome: str | None = None
    version: str = RESULT_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_version": self.version,
            "status": self.status,
            "agent": self.agent,
            "outcome": self.outcome,
            "code": self.code,
            "message": _single_line(self.message, limit=500),
            "retryable": self.retryable,
            "side_effect_unknown": self.side_effect_unknown,
            "missing": list(self.missing),
            "report_path": self.report_path,
            "fields": dict(self.fields),
            "raw_text": self.raw_text or None,
            "duration_ms": self.duration_ms,
        }


def build_result_from_text(
    text: str | None,
    agent: str,
    *,
    duration_ms: int | None = None,
    raw_text_limit: int = DEFAULT_RAW_TEXT_LIMIT,
) -> DelegationResult:
    """把子 Agent 文本转成 ok/degraded/error 信封。"""
    raw = text or ""
    if not raw.strip():
        return DelegationResult(
            agent=agent,
            status=RESULT_ERROR,
            code=CODE_EMPTY_RESULT,
            message="子 Agent 没有返回任何内容；可以重试，或换一种更明确的任务描述。",
            retryable=True,
            duration_ms=duration_ms,
        )

    report = parse_report(raw, agent)
    outcome = _infer_outcome(agent, report.fields)

    if report.ok:
        return DelegationResult(
            agent=agent,
            status=RESULT_OK,
            outcome=outcome,
            fields=dict(report.fields),
            report_path=report.report_path,
            duration_ms=duration_ms,
        )

    return DelegationResult(
        agent=agent,
        status=RESULT_DEGRADED,
        code=CODE_REPORT_INCOMPLETE,
        message=(
            "子 Agent 未按回传契约返回，以下原文不保证结构完整；"
            "优先读取文件产物，不要直接把原文当结论。"
        ),
        retryable=True,
        missing=tuple(report.missing),
        fields=dict(report.fields),
        report_path=report.report_path,
        raw_text=_truncate(raw.strip(), raw_text_limit),
        duration_ms=duration_ms,
        outcome=outcome,
    )


def build_error_result(
    *,
    agent: str,
    code: str,
    message: str,
    retryable: bool = False,
    side_effect_unknown: bool = False,
    missing: Iterable[str] = (),
    raw_text: str = "",
    duration_ms: int | None = None,
    raw_text_limit: int = DEFAULT_RAW_TEXT_LIMIT,
) -> DelegationResult:
    return DelegationResult(
        agent=agent,
        status=RESULT_ERROR,
        code=code,
        message=message,
        retryable=retryable,
        side_effect_unknown=side_effect_unknown,
        missing=tuple(missing),
        raw_text=_truncate(raw_text.strip(), raw_text_limit),
        duration_ms=duration_ms,
    )


def build_timeout_result(
    *,
    agent: str,
    timeout_seconds: float,
    duration_ms: int | None = None,
) -> DelegationResult:
    order_write = agent == "procurement-order"
    if order_write:
        message = (
            f"子 Agent 超过 {timeout_seconds:g}s 未返回。订单写操作可能已执行，"
            "结果未知；不要自动重试，先查询订单确认实际状态。"
        )
    else:
        message = (
            f"子 Agent 超过 {timeout_seconds:g}s 未返回；可以重试，"
            "或把任务拆小后再委派。"
        )
    return DelegationResult(
        agent=agent,
        status=RESULT_TIMEOUT,
        code=CODE_TIMEOUT,
        message=message,
        retryable=not order_write,
        side_effect_unknown=order_write,
        duration_ms=duration_ms,
    )


def classify_exception(
    exc: BaseException,
    *,
    agent: str,
) -> tuple[str, bool, bool]:
    """返回 (code, retryable, side_effect_unknown)。

    只做保守分类：无法判断是否可重试时按不可重试处理，避免父模型反复
    重派写操作。
    """
    name = type(exc).__name__.casefold()
    text = str(exc).casefold()
    code = CODE_FAILED
    retryable = False

    if "timeout" in name or "timed out" in text or "timeouterror" in name:
        code = CODE_TIMEOUT
        retryable = True
    elif "ratelimit" in name or "rate_limit" in name or "429" in text:
        code = CODE_RATE_LIMITED
        retryable = True
    elif "recursion" in name:
        code = CODE_RECURSION_LIMIT
        retryable = False
    elif any(
        token in name
        for token in ("connection", "transport", "connecterror", "network")
    ) or "connection" in text:
        code = CODE_TRANSPORT_ERROR
        retryable = True

    # 订单子 Agent 可能已经产生远端副作用：一律不自动重试，交给人工/查询确认。
    side_effect_unknown = agent == "procurement-order"
    if side_effect_unknown:
        retryable = False
    return code, retryable, side_effect_unknown


def build_exception_result(
    exc: BaseException,
    *,
    agent: str,
    duration_ms: int | None = None,
) -> DelegationResult:
    code, retryable, side_effect_unknown = classify_exception(exc, agent=agent)
    message = (
        f"子 Agent 调用失败（{type(exc).__name__}）。"
        if not side_effect_unknown
        else (
            f"子 Agent 调用失败（{type(exc).__name__}），订单可能已被修改；"
            "不要自动重试，先查询订单确认状态。"
        )
    )
    return build_error_result(
        agent=agent,
        code=code,
        message=message,
        retryable=retryable,
        side_effect_unknown=side_effect_unknown,
        duration_ms=duration_ms,
    )


def render_result(result: DelegationResult) -> str:
    """渲染给父 Agent：状态头 + JSON 信封 + 必要的人读提示。"""
    header = (
        f"【委派结果】status={result.status} agent={result.agent} "
        f"retryable={str(result.retryable).lower()} "
        f"duration_ms={result.duration_ms or 0}"
    )
    payload = json.dumps(
        result.to_dict(), ensure_ascii=False, indent=2, default=str
    )
    parts = [header, "```json", payload, "```"]

    if result.status == RESULT_DEGRADED:
        parts.append(
            "子 Agent 未按回传契约返回。请先 read_file 读取 report_path "
            "指向的文件产物，再向用户汇报；不要把 raw_text 直接当结论。"
        )
    elif result.status == RESULT_TIMEOUT and result.side_effect_unknown:
        parts.append(
            "订单写操作结果未知：不要自动重试；先查询订单实际状态，"
            "确认未执行后再决定是否重新发起。"
        )
    elif result.status == RESULT_TIMEOUT:
        parts.append(
            "子 Agent 超时。可以重试，或把任务拆小后重新委派。"
        )
    elif result.status == RESULT_ERROR:
        if result.side_effect_unknown:
            parts.append(
                "订单可能已被修改：不要自动重试；先查询订单实际状态。"
            )
        elif result.retryable:
            parts.append(
                "该失败标记为可重试；补齐信息或调整任务后可重新调用 task。"
            )
        else:
            parts.append(
                "该失败按不可重试处理；请改派、拆任务或向用户说明限制。"
            )
    return "\n".join(parts)



__all__ = [
    "BRIEF_SECTIONS",
    "BRIEF_REQUIRED_SECTIONS",
    "BRIEF_DEFAULTABLE_SECTIONS",
    "BRIEF_VERSION",
    "BriefCheck",
    "BriefValidationError",
    "ReportContract",
    "ReportEnvelope",
    "REPORT_CONTRACTS",
    "STATUS_DEGRADED",
    "STATUS_STRUCTURED",
    "RESULT_VERSION",
    "RESULT_OK",
    "RESULT_DEGRADED",
    "RESULT_ERROR",
    "RESULT_TIMEOUT",
    "DelegationResult",
    "CODE_BRIEF_INVALID",
    "CODE_AGENT_UNKNOWN",
    "CODE_EMPTY_RESULT",
    "CODE_REPORT_INCOMPLETE",
    "CODE_TIMEOUT",
    "CODE_RATE_LIMITED",
    "CODE_TRANSPORT_ERROR",
    "CODE_RECURSION_LIMIT",
    "CODE_FAILED",
    "build_default_sections",
    "build_error_result",
    "build_exception_result",
    "build_result_from_text",
    "build_timeout_result",
    "classify_exception",
    "contract_for",
    "parse_report",
    "patch_brief",
    "prepare_brief",
    "render_brief_repair_hint",
    "render_envelope",
    "render_result",
    "section_body",
    "split_sections",
    "validate_brief",
]

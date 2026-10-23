from typing import Callable

from langgraph.runtime import Runtime
from langgraph.types import interrupt

from agent.procurement.data_provider import UpstreamUnavailable
from agent.procurement.dependencies import ProcurementDependencies
from agent.procurement.models import TaskStatus, ProcurementRequest, ApprovalStatus, ExecutionStatus, ProcurementPlan, \
    OrderExecutionResult, OrderVerificationResult, DecisionType, ApprovalDecision, ApprovalDecisionResult, \
    CandidateSet, ValidationIssue, ApprovalRecord, ToolError
from agent.procurement.rules import validate_request_fields, make_idempotency_key, execution_guard_errors, \
    make_approval_id, compute_plan_hash, validate_plan as validate_plan_rules
from agent.procurement.state import ProcurementState
from agent.schema import ProcurementContext


def build_nodes(deps: ProcurementDependencies) -> dict[str, Callable]:
    """所有节点工厂。每个节点签名: (state, runtime) -> dict。"""

    def _owner_ok(state: ProcurementState, runtime: Runtime[ProcurementContext]) -> bool:
        context = runtime.context
        return bool(context and context.user_id and context.user_id == state.get("user_id"))

    def _failed(code: str, message: str, **extra) -> dict:
        # 【python小课堂】**extra 是 Python 函数定义中的可变关键字参数（variadic keyword arguments），含义是：调用这个函数时，可以额外传入任意数量的"键=值"形式的参数，它们会被收集到一个名为 extra 的字典里。
        return {
            "status": TaskStatus.FAILED.value,
            "last_error": {"code": code, "message": message, **extra},
            "updated_at": deps.now().isoformat(),
        }

    # --------------------------------------------------------
    # authorize：首次进入时校验身份；恢复时敏感节点会再校验
    # --------------------------------------------------------
    async def authorize(state, runtime):
        context = runtime.context
        if not context or not context.user_id:
            return _failed("UNAUTHENTICATED", "缺少用户身份，不能创建采购任务。")
        if not _owner_ok(state, runtime):
            return _failed("FORBIDDEN", "当前用户无权操作该采购任务。")
        return {
            "user_id": context.user_id,
            "username": context.username,
            "status": TaskStatus.PARSING.value,
            "node_history": ["authorize"],
            "updated_at": deps.now().isoformat(),
        }

    # --------------------------------------------------------
    # parse_request
    # --------------------------------------------------------
    async def parse_request(state, runtime):
        if not _owner_ok(state, runtime):
            return _failed("FORBIDDEN", "当前用户无权操作该采购任务。")
        # 允许调用方直接传结构化 request（测试/内部 API），跳过 LLM 解析
        if not state.get("raw_request") and state.get("request"):
            return {
                "request": state["request"],
                "status": TaskStatus.PARSING.value,
                "node_history": ["parse_request"],
                "updated_at": deps.now().isoformat(),
            }
        try:
            request = await deps.request_parser.parse(
                state.get("raw_request", ""),
                previous=state.get("request") or None,
                answer=state.get("clarification_answer"),
            )
        except Exception as e:
            return _failed("REQUEST_PARSE_FAILED", f"需求解析失败:{e}")

        return {
            "request": request.model_dump(mode="json"),
            "status": TaskStatus.PARSING.value,
            "node_history": ["parse_request"],
            "updated_at": deps.now().isoformat(),
        }

    # --------------------------------------------------------
    # validate_request
    # --------------------------------------------------------
    async def validate_request(state, runtime):
        if not _owner_ok(state, runtime):
            return _failed("FORBIDDEN", "当前用户无权操作该采购任务。")
        request = ProcurementRequest.model_validate(state.get("request") or {})
        issues = validate_request_fields(request, today=deps.now().date())
        if issues and state.get("clarification_round", 0) >= deps.max_clarification_rounds:
            return _failed(
                "CLARIFICATION_LIMIT_REACHED",
                f"已追问 {deps.max_clarification_rounds} 轮仍未补全采购条件，请重新发起任务。",
            )

        if issues:
            return {
                "missing_fields": [i.field for i in issues if i.fixable and i.field],
                "validation_errors": [i.model_dump() for i in issues],
                "status": TaskStatus.NEED_INFO.value,
                "node_history": ["validate_request"],
                "updated_at": deps.now().isoformat(),
            }
        return {
            "missing_fields": [],
            "validation_errors": [],
            "status": TaskStatus.ANALYZING.value,
            "node_history": ["validate_request"],
            "updated_at": deps.now().isoformat(),
        }

    # --------------------------------------------------------
    # ask_user：只 interrupt，不写任何外部状态
    # --------------------------------------------------------
    async def ask_user(state, runtime):
        if not _owner_ok(state, runtime):
            return _failed("FORBIDDEN", "当前用户无权操作该采购任务。")
        answer = interrupt(
            {
                "type": "procurement_clarification",
                "task_id": state["task_id"],
                "missing_fields": state.get("missing_fields", []),
                "validation_errors": state.get("validation_errors", []),
                "current_request": state.get("request", {}),
                "question": "请补充以上采购条件；没有把握的字段请明确说明，不要猜测。",
            }
        )
        return {
            "clarification_answer": answer,
            "status": TaskStatus.NEED_INFO.value,
            "node_history": ["ask_user"],
            "updated_at": deps.now().isoformat(),
        }

    # --------------------------------------------------------
    # apply_clarification
    # --------------------------------------------------------
    async def apply_clarification(state, runtime):
        if not _owner_ok(state, runtime):
            return _failed("FORBIDDEN", "当前用户无权操作该采购任务。")
        answer = state.get("clarification_answer") or {}
        previous = ProcurementRequest.model_validate(state.get("request") or {})
        try:
            merged = await deps.request_parser.merge(previous, answer)
        except Exception as exc:
            return _failed("CLARIFICATION_MERGE_FAILED", f"补充信息无法合并：{exc}")
        return {
            "request": merged.model_dump(mode="json"),
            "clarification_round": state.get("clarification_round", 0) + 1,
            "clarification_answer": None,
            "status": TaskStatus.PARSING.value,
            "node_history": ["apply_clarification"],
            "updated_at": deps.now().isoformat(),
        }

    # --------------------------------------------------------
    # analyze_procurement
    # --------------------------------------------------------
    async def analyze_procurement(state, runtime):
        if not _owner_ok(state, runtime):
            return _failed("FORBIDDEN", "当前用户无权操作该采购任务。")
        request = ProcurementRequest.model_validate(state.get("request") or {})
        feedback = [
            ValidationIssue.model_validate(item)
            for item in state.get("validation_errors", [])
        ]
        version = state.get("plan_version", 0) + 1
        try:
            candidates = await deps.data_provider.collect(request)
            proposal = await deps.plan_proposer.propose(
                request, candidates, feedback=feedback
            )
            plan = deps.plan_builder.build(
                task_id=state["task_id"],
                version=version,
                request=request,
                candidates=candidates,
                proposal=proposal,
                now=deps.now(),
            )
        except UpstreamUnavailable as exc:
            retryable = exc.code in {"UPSTREAM_UNAVAILABLE", "UPSTREAM_TIMEOUT"}
            return {
                "status": (
                    TaskStatus.AWAITING_RETRY.value
                    if retryable
                    else TaskStatus.FAILED.value
                ),
                "failure_stage": "analyze_procurement",
                "retry_from": "analyze_procurement" if retryable else None,
                "last_error": {
                    "code": exc.code,
                    "retryable": retryable,
                    "message": str(exc),
                },
                "node_history": ["analyze_procurement"],
                "updated_at": deps.now().isoformat(),
            }
        except Exception as exc:
            # 非上游异常视为不可重试，避免把代码 bug 交给用户重试
            return _failed("ANALYSIS_FAILED", f"采购分析失败：{exc}")
        return {
            "inventory_data": [i.model_dump(mode="json") for i in candidates.inventory],
            "supplier_candidates": [
                o.model_dump(mode="json") for o in candidates.offers
            ],
            "procurement_plan": plan.model_dump(mode="json"),
            "plan_version": version,
            "plan_hash": None,
            "validation_errors": [],
            "validation_warnings": [],
            "validation_passed": False,
            "failure_stage": None,
            "retry_from": None,
            "status": TaskStatus.PLAN_READY.value,
            "node_history": ["analyze_procurement"],
            "updated_at": deps.now().isoformat(),
        }

    # --------------------------------------------------------
    # validate_plan
    # --------------------------------------------------------
    async def validate_plan(state, runtime):
        if not _owner_ok(state, runtime):
            return _failed("FORBIDDEN", "当前用户无权操作该采购任务。")
        plan = ProcurementPlan.model_validate(state["procurement_plan"])
        request = ProcurementRequest.model_validate(state.get("request") or {})
        candidates = CandidateSet(
            offers=state.get("supplier_candidates", []),
            inventory=state.get("inventory_data", []),
        )
        result = validate_plan_rules(
            plan,
            request,
            candidates,
            task_id=state["task_id"],
            today=deps.now().date(),
        )
        if result.passed:
            return {
                "validation_errors": [],
                "validation_warnings": [w.model_dump() for w in result.warnings],
                "validation_passed": True,
                "plan_hash": result.plan_hash,
                "status": TaskStatus.AWAITING_APPROVAL.value,
                "node_history": ["validate_plan"],
                "updated_at": deps.now().isoformat(),
            }

        retry_count = state.get("retry_count", 0)
        retryable = any(e.retryable for e in result.errors)
        if retryable and retry_count < deps.max_plan_revisions:
            return {
                "validation_errors": [e.model_dump() for e in result.errors],
                "validation_warnings": [w.model_dump() for w in result.warnings],
                "validation_passed": False,
                "plan_hash": result.plan_hash,
                "retry_count": retry_count + 1,
                "status": TaskStatus.ANALYZING.value,
                "node_history": ["validate_plan"],
                "updated_at": deps.now().isoformat(),
            }
        return {
            "validation_errors": [e.model_dump() for e in result.errors],
            "validation_warnings": [w.model_dump() for w in result.warnings],
            "validation_passed": False,
            "plan_hash": result.plan_hash,
            "status": TaskStatus.FAILED.value,
            "last_error": {
                "code": "PLAN_VALIDATION_FAILED",
                "message": "采购方案未通过确定性校验。",
                "errors": [e.model_dump() for e in result.errors],
            },
            "node_history": ["validate_plan"],
            "updated_at": deps.now().isoformat(),
        }

    # --------------------------------------------------------
    # prepare_approval：捕获审批快照 + 幂等写 pending
    # --------------------------------------------------------
    async def prepare_approval(state, runtime):
        if not _owner_ok(state, runtime):
            return _failed("FORBIDDEN", "当前用户无权操作该采购任务。")

        plan = ProcurementPlan.model_validate(state["procurement_plan"])
        actual_hash = compute_plan_hash(plan)
        if actual_hash != state.get("plan_hash") or plan.version != state.get("plan_version"):
            return _failed(
                "PLAN_STATE_INCONSISTENT",
                "方案内容与状态中的版本/哈希不一致，拒绝发起审批。",
            )

        approval_id = make_approval_id(state["task_id"], plan.version, actual_hash)
        now = deps.now()
        try:
            await deps.approval_repository.ensure_pending(
                ApprovalRecord(
                    approval_id=approval_id,
                    task_id=state["task_id"],
                    plan_version=plan.version,
                    plan_hash=actual_hash,
                    status=ApprovalStatus.PENDING,
                    requested_by=state["user_id"],
                    created_at=now,
                    expires_at=now + deps.approval_ttl,
                )
            )
        except Exception as exc:
            return _failed("APPROVAL_STORE_FAILED", f"审批记录写入失败：{exc}")

        return {
            "approval_id": approval_id,
            "approval_plan_version": plan.version,
            "approval_plan_hash": actual_hash,
            "approval_plan_snapshot": plan.model_dump(mode="json"),
            "approval_status": ApprovalStatus.PENDING.value,
            "status": TaskStatus.AWAITING_APPROVAL.value,
            "node_history": ["prepare_approval"],
            "updated_at": deps.now().isoformat(),
        }

    # --------------------------------------------------------
    # request_approval：只 interrupt，不写库、不重算 ID
    # --------------------------------------------------------
    async def request_approval(state, runtime):
        if not _owner_ok(state, runtime):
            return _failed("FORBIDDEN", "当前用户无权操作该采购任务。")

        approval_id = state.get("approval_id")
        plan_version = state.get("approval_plan_version")
        plan_hash = state.get("approval_plan_hash")
        snapshot = state.get("approval_plan_snapshot")
        if not (approval_id and plan_version and plan_hash and snapshot):
            return _failed("APPROVAL_NOT_PREPARED", "审批快照缺失，不能发起审批。")

        # 用发起审批时捕获的快照展示，避免恢复时看到被外部改过的当前方案
        plan = ProcurementPlan.model_validate(snapshot)

        decision = interrupt(
            {
                "type": "procurement_approval",
                "interrupt_type": "hitl_approval",  # 复用现有前端协议
                "task_id": state["task_id"],
                "approval_id": approval_id,
                "plan_version": plan_version,
                "plan_hash": plan_hash,
                "summary": {
                    "supplier_id": plan.supplier_id,
                    "supplier_name": plan.supplier_name,
                    "warehouse_location": plan.warehouse_location,
                    "total_amount": format(plan.total_amount, ".2f"),
                    "currency": plan.currency,
                    "expected_delivery_date": (
                        plan.expected_delivery_date.isoformat()
                        if plan.expected_delivery_date
                        else None
                    ),
                    "items": [
                        {
                            "part_id": i.part_id,
                            "part_name": i.part_name,
                            "quantity": i.quantity,
                            "unit_price": format(i.unit_price, ".2f"),
                            "subtotal": format(i.subtotal, ".2f"),
                        }
                        for i in plan.items
                    ],
                    "inventory_gaps": [
                        g.model_dump(mode="json") for g in plan.inventory_gaps
                    ],
                    "rationale": plan.rationale,
                },
                "action_requests": [
                    {
                        "name": "procurement_plan",
                        # 复用现有 InterruptBanner 的字段名/明细表，减少前端改动
                        "args": {
                            "taskId": state["task_id"],
                            "supplierName": plan.supplier_name,
                            "totalAmount": format(plan.total_amount, ".2f"),
                            "expectedDeliveryDate": (
                                plan.expected_delivery_date.isoformat()
                                if plan.expected_delivery_date
                                else None
                            ),
                            "orderDetail": [
                                {
                                    "partId": i.part_id,
                                    "quantity": i.quantity,
                                    "unitPrice": format(i.unit_price, ".2f"),
                                    "subtotal": format(i.subtotal, ".2f"),
                                }
                                for i in plan.items
                            ],
                        },
                        "description": "采购方案待审批；批准后才会进入订单执行。",
                    }
                ],
                "review_configs": [
                    {
                        "action_name": "procurement_plan",
                        "allowed_decisions": ["approve", "reject", "modify"],
                    }
                ],
            }
        )

        return {
            "approval_decision": decision,
            "status": TaskStatus.AWAITING_APPROVAL.value,
            "node_history": ["request_approval"],
            "updated_at": deps.now().isoformat(),
        }

    # --------------------------------------------------------
    # finalize_approval：恢复后写最终决定；重新校验归属/版本/时效
    # --------------------------------------------------------
    async def finalize_approval(state, runtime):
        if not _owner_ok(state, runtime):
            return _failed("FORBIDDEN", "当前用户无权操作该采购任务。")

        decision = ApprovalDecision.model_validate(state.get("approval_decision") or {})
        approval_id = state.get("approval_id")
        approval_version = state.get("approval_plan_version")
        approval_hash = state.get("approval_plan_hash")
        if not (approval_id and approval_version and approval_hash):
            return _failed("APPROVAL_STALE", "审批快照缺失，必须重新发起审批。")

        # 恢复时重新校验：当前方案必须仍然是发起审批时的那一份
        current_plan = ProcurementPlan.model_validate(state["procurement_plan"])
        current_hash = compute_plan_hash(current_plan)
        if (
                current_hash != approval_hash
                or current_plan.version != approval_version
                or state.get("plan_version") != approval_version
                or state.get("plan_hash") != approval_hash
        ):
            return _failed(
                "APPROVAL_STALE",
                "审批后方案内容或版本已变化，必须重新审批。",
            )

        record = await deps.approval_repository.get(approval_id)
        if record is None:
            return _failed("APPROVAL_NOT_FOUND", "找不到审批记录，请重新发起审批。")
        if record.expires_at and deps.now() > record.expires_at:
            await deps.approval_repository.decide(
                approval_id=approval_id,
                task_id=state["task_id"],
                plan_version=approval_version,
                plan_hash=approval_hash,
                status=ApprovalStatus.STALE.value,
                decided_by=state["user_id"],
                decision=DecisionType.REJECT.value,
                comment="审批已过期",
            )
            return _failed("APPROVAL_EXPIRED", "审批已过期，请重新发起。")

        target_status = {
            DecisionType.APPROVE: ApprovalStatus.APPROVED.value,
            DecisionType.REJECT: ApprovalStatus.REJECTED.value,
            DecisionType.MODIFY: ApprovalStatus.STALE.value,
        }[decision.decision]

        decision_result = await deps.approval_repository.decide(
            approval_id=approval_id,
            task_id=state["task_id"],
            plan_version=approval_version,
            plan_hash=approval_hash,
            status=target_status,
            decided_by=state["user_id"],
            decision=decision.decision.value,
            comment=decision.comment,
        )
        if decision_result == ApprovalDecisionResult.STALE:
            return _failed("APPROVAL_STALE", "审批记录状态已变化，不能重复使用。")
        # APPLIED / ALREADY_APPLIED 都继续：后者是节点重放时的幂等结果。

        now_iso = deps.now().isoformat()
        if decision.decision == DecisionType.REJECT:
            return {
                "approval_status": ApprovalStatus.REJECTED.value,
                "approved_plan_version": None,
                "approved_plan_hash": None,
                "approved_by": None,
                "approved_at": None,
                "status": TaskStatus.REJECTED.value,
                "node_history": ["finalize_approval"],
                "updated_at": now_iso,
            }

        if decision.decision == DecisionType.MODIFY:
            if not decision.changes:
                return _failed("MODIFY_EMPTY", "选择修改时必须提供 changes。")
            try:
                merged = await deps.request_parser.merge(
                    ProcurementRequest.model_validate(state.get("request") or {}),
                    {"request_patch": decision.changes or {}},
                )
            except Exception as exc:
                return _failed("MODIFY_MERGE_FAILED", f"修改内容无法合并：{exc}")
            return {
                "request": merged.model_dump(mode="json"),
                "approval_status": ApprovalStatus.STALE.value,
                "approval_plan_version": None,
                "approval_plan_hash": None,
                "approval_plan_snapshot": None,
                "approved_plan_version": None,
                "approved_plan_hash": None,
                "approved_by": None,
                "approved_at": None,
                "procurement_plan": None,
                "plan_hash": None,
                "validation_passed": False,
                "retry_count": 0,
                "status": TaskStatus.ANALYZING.value,
                "node_history": ["finalize_approval"],
                "updated_at": now_iso,
            }

        return {
            "approval_status": ApprovalStatus.APPROVED.value,
            "approved_plan_version": approval_version,
            "approved_plan_hash": approval_hash,
            "approved_by": state["user_id"],
            "approved_at": now_iso,
            "status": TaskStatus.APPROVED.value,
            "node_history": ["finalize_approval"],
            "updated_at": now_iso,
        }

    # --------------------------------------------------------
    # create_order：默认关闭；真实执行在 deeper_3/4 接入
    # --------------------------------------------------------
    async def create_order(state, runtime):
        if not _owner_ok(state, runtime):
            return _failed("FORBIDDEN", "当前用户无权操作该采购任务。")

        guard_errors = execution_guard_errors(state)
        if guard_errors:
            return _failed(
                "APPROVAL_NOT_CURRENT",
                "审批与当前方案不一致，禁止执行订单。",
                guard_errors=guard_errors,
            )

        plan = ProcurementPlan.model_validate(state["procurement_plan"])
        plan_hash = compute_plan_hash(plan)
        key = make_idempotency_key(state["task_id"], plan.version, plan_hash)

        try:
            result: OrderExecutionResult = await deps.order_executor.create_order(
                task_id=state["task_id"],
                plan=plan,
                approval_id=state["approval_id"],
                idempotency_key=key,
            )
        except TimeoutError as exc:
            result = OrderExecutionResult(
                status=ExecutionStatus.UNKNOWN,
                error=ToolError(
                    code="ORDER_RESULT_UNKNOWN",
                    retryable=True,
                    message=f"订单请求超时，结果未知：{exc}",
                ),
            )
        except Exception as exc:
            result = OrderExecutionResult(
                status=ExecutionStatus.UNKNOWN,
                error=ToolError(
                    code="ORDER_RESULT_UNKNOWN",
                    retryable=True,
                    message=f"订单请求异常，结果未知：{exc}",
                ),
            )

        if result.status == ExecutionStatus.NOT_STARTED:
            return {
                "idempotency_key": key,
                "execution_status": result.status.value,
                "status": TaskStatus.APPROVED_PENDING_EXECUTION.value,
                "last_error": result.error.model_dump() if result.error else None,
                "node_history": ["create_order"],
                "updated_at": deps.now().isoformat(),
            }

        if result.status == ExecutionStatus.FAILED:
            return {
                "idempotency_key": key,
                "order_id": result.order_id,
                "execution_status": result.status.value,
                "status": TaskStatus.FAILED.value,
                "last_error": result.error.model_dump() if result.error else None,
                "node_history": ["create_order"],
                "updated_at": deps.now().isoformat(),
            }

        return {
            "idempotency_key": key,
            "order_id": result.order_id,
            "execution_status": result.status.value,
            "status": TaskStatus.VERIFYING.value,
            "last_error": result.error.model_dump() if result.error else None,
            "execution_attempts": state.get("execution_attempts", 0) + 1,
            "node_history": ["create_order"],
            "updated_at": deps.now().isoformat(),
        }

    # --------------------------------------------------------
    # verify_order
    # --------------------------------------------------------
    async def verify_order(state, runtime):
        if not _owner_ok(state, runtime):
            return _failed("FORBIDDEN", "当前用户无权操作该采购任务。")

        try:
            if deps.order_reconciler is not None:
                result: OrderVerificationResult = (
                    await deps.order_reconciler.verify_by_task(state["task_id"])
                )
            else:
                result = await deps.order_executor.verify_order(
                    task_id=state["task_id"],
                    order_id=state.get("order_id"),
                )
        except Exception as exc:
            result = OrderVerificationResult(
                status=ExecutionStatus.UNKNOWN,
                order_id=state.get("order_id"),
                error=ToolError(
                    code="ORDER_VERIFY_UNAVAILABLE",
                    retryable=True,
                    message=f"订单核验失败：{exc}",
                ),
            )

        if result.status == ExecutionStatus.SUCCEEDED:
            return {
                "order_id": result.order_id or state.get("order_id"),
                "execution_status": ExecutionStatus.SUCCEEDED.value,
                "status": TaskStatus.COMPLETED.value,
                "last_error": None,
                "failure_stage": None,
                "retry_from": None,
                "requires_manual_review": False,
                "node_history": ["verify_order"],
                "updated_at": deps.now().isoformat(),
            }

        if result.status == ExecutionStatus.FAILED:
            return {
                "order_id": result.order_id or state.get("order_id"),
                "execution_status": ExecutionStatus.FAILED.value,
                "status": TaskStatus.FAILED.value,
                "last_error": result.error.model_dump() if result.error else {
                    "code": "ORDER_VERIFY_FAILED",
                    "retryable": False,
                    "message": "订单核验确认失败。",
                },
                "requires_manual_review": result.requires_manual_review,
                "node_history": ["verify_order"],
                "updated_at": deps.now().isoformat(),
            }

        return {
            "order_id": result.order_id or state.get("order_id"),
            "execution_status": ExecutionStatus.UNKNOWN.value,
            "status": TaskStatus.AWAITING_RETRY.value,
            "failure_stage": "verify_order",
            "retry_from": "verify_order",
            "last_error": (
                result.error.model_dump()
                if result.error
                else {
                    "code": "ORDER_RESULT_UNKNOWN",
                    "retryable": True,
                    "message": "订单结果仍不确定，请继续核验。",
                }
            ),
            "requires_manual_review": result.requires_manual_review,
            "node_history": ["verify_order"],
            "updated_at": deps.now().isoformat(),
        }

    async def cancel_task(state, runtime):
        return {
            "status": TaskStatus.CANCELLED.value,
            "updated_at": deps.now().isoformat(),
            "node_history": ["cancel_task"],
        }

    # --------------------------------------------------------
    # 终态
    # --------------------------------------------------------
    async def complete_task(state, runtime):
        # 不变量：没有订单号不能宣称完成
        if not state.get("order_id"):
            return _failed("ORDER_ID_MISSING", "没有订单号，不能把任务标记为完成。")
        return {
            "status": TaskStatus.COMPLETED.value,
            "updated_at": deps.now().isoformat(),
            "node_history": ["complete_task"],
        }

    async def reject_task(state, runtime):
        return {
            "status": TaskStatus.REJECTED.value,
            "updated_at": deps.now().isoformat(),
            "node_history": ["reject_task"],
        }

    async def pending_execution(state, runtime):
        # 不变量：只有已批准方案才能进入“待执行”
        if state.get("approval_status") != ApprovalStatus.APPROVED.value:
            return _failed("APPROVAL_NOT_APPROVED", "方案未批准，不能进入待执行状态。")
        return {
            "status": TaskStatus.APPROVED_PENDING_EXECUTION.value,
            "updated_at": deps.now().isoformat(),
            "node_history": ["pending_execution"],
        }

    async def fail_task(state, runtime):
        last_error = state.get("last_error") or {
            "code": "TASK_FAILED",
            "message": "采购任务失败。",
        }
        return {
            "status": TaskStatus.FAILED.value,
            "last_error": last_error,
            "updated_at": deps.now().isoformat(),
            "node_history": ["fail_task"],
        }

    # --------------------------------------------------------
    # retry
    # --------------------------------------------------------
    async def retry_gate(state, runtime):
        if not _owner_ok(state, runtime):
            return _failed("FORBIDDEN", "当前用户无权操作该采购任务。")
        payload = {
            "type": "procurement_retry_required",
            "task_id": state["task_id"],
            "failed_stage": state.get("failure_stage"),
            "retry_from": state.get("retry_from"),
            "retry_count": state.get("retry_count", 0),
            "last_error": state.get("last_error"),
            "options": ["retry", "cancel"],
        }
        answer = interrupt(payload)
        action = (answer or {}).get("action", "cancel")
        if action == "retry":
            if state.get("retry_count", 0) >= deps.max_task_resumes:
                return _failed("RETRY_LIMIT_REACHED", "恢复次数超过上限，请重新发起任务。")
            if state.get("retry_from") not in {"analyze_procurement", "verify_order"}:
                return _failed("RETRY_TARGET_INVALID", "恢复目标节点无效，请重新发起任务。")
            return {
                "retry_action": "retry",
                "retry_count": state.get("retry_count", 0) + 1,
                "resume_count": state.get("resume_count", 0) + 1,
                "failure_stage": None,
                "last_error": None,
                "status": TaskStatus.RUNNING.value,
                "node_history": ["retry_gate"],
                "updated_at": deps.now().isoformat(),
            }

        return {
            "retry_action": "cancel",
            "status": TaskStatus.CANCELLED.value,
            "node_history": ["retry_gate"],
            "updated_at": deps.now().isoformat(),
        }

    return {
        "authorize": authorize,
        "parse_request": parse_request,
        "validate_request": validate_request,
        "ask_user": ask_user,
        "apply_clarification": apply_clarification,
        "analyze_procurement": analyze_procurement,
        "validate_plan": validate_plan,
        "prepare_approval": prepare_approval,
        "request_approval": request_approval,
        "finalize_approval": finalize_approval,
        "create_order": create_order,
        "verify_order": verify_order,
        "complete_task": complete_task,
        "reject_task": reject_task,
        "pending_execution": pending_execution,
        "retry_gate": retry_gate,
        "cancel_task": cancel_task,
        "fail_task": fail_task,
    }

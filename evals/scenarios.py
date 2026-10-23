from evals.models import (
    Action,
    ActionType,
    EvalLayer,
    Expectation,
    FaultPlan,
    Scenario,
)


def _request(
    *,
    part_id: int = 1,
    part_name: str = "活塞环组件",
    quantity: int = 10,
    warehouse: str = "华东仓",
    budget: str = "50000.00",
) -> dict:
    return {
        "items": [
            {
                "part_id": part_id,
                "part_name": part_name,
                "quantity": quantity,
                "target_unit_price": "100.00",
            }
        ],
        "warehouse_location": warehouse,
        "budget": budget,
        "priority": "balanced",
    }


APPROVE = Action(type=ActionType.RESUME, resume={"decision": "approve"})
REJECT = Action(type=ActionType.RESUME, resume={"decision": "reject"})
RETRY = Action(type=ActionType.RESUME, resume={"action": "retry"})
RESTART = Action(type=ActionType.RESTART)


SCENARIOS: list[Scenario] = [
    # ---------------- 需求理解 ----------------
    Scenario(
        id="RQ-01",
        group="requirement",
        title="缺少仓库时应追问",
        layer=EvalLayer.GRAPH,
        request={
            "items": [{"part_id": 1, "part_name": "活塞环组件", "quantity": 10}],
            "budget": "50000.00",
        },
        expect=Expectation(
            final_status="need_info",
            order_created=False,
        ),
    ),
    Scenario(
        id="RQ-02",
        group="requirement",
        title="数量缺失时应追问",
        layer=EvalLayer.GRAPH,
        request={
            "items": [{"part_id": 1, "part_name": "活塞环组件"}],
            "warehouse_location": "华东仓",
            "budget": "50000.00",
        },
        expect=Expectation(final_status="need_info", order_created=False),
    ),
    Scenario(
        id="RQ-03",
        group="requirement",
        title="多个物料应全部进入方案",
        layer=EvalLayer.GRAPH,
        request={
            "items": [
                {
                    "part_id": 1,
                    "part_name": "活塞环组件",
                    "quantity": 10,
                    "target_unit_price": "100.00",
                },
                {
                    "part_id": 2,
                    "part_name": "火花塞",
                    "quantity": 5,
                    "target_unit_price": "20.00",
                },
            ],
            "warehouse_location": "华东仓",
            "budget": "50000.00",
        },
        expect=Expectation(final_status="awaiting_approval", order_created=False),
    ),
    Scenario(
        id="RQ-04",
        group="requirement",
        title="修改数量应生成新版本",
        layer=EvalLayer.GRAPH,
        request=_request(quantity=10),
        actions=[
            Action(type=ActionType.START),
            Action(
                type=ActionType.RESUME,
                resume={
                    "decision": "modify",
                    "changes": {"items": [{"part_id": 1, "quantity": 20}]},
                },
            ),
        ],
        expect=Expectation(
            final_status="awaiting_approval",
            min_plan_version=2,
        ),
    ),
    Scenario(
        id="RQ-05",
        group="requirement",
        title="恢复原任务不新建任务",
        layer=EvalLayer.GRAPH,
        request=_request(),
        actions=[Action(type=ActionType.START), APPROVE],
        expect=Expectation(final_status="completed", order_created=True),
    ),

    # ---------------- 工具调用 ----------------
    Scenario(
        id="TL-01",
        group="tool",
        title="正常查询库存",
        layer=EvalLayer.GRAPH,
        request=_request(),
        expect=Expectation(final_status="awaiting_approval", order_created=False),
    ),
    Scenario(
        id="TL-02",
        group="tool",
        title="物料不存在",
        layer=EvalLayer.GRAPH,
        request=_request(part_name="不存在的物料"),
        faults=FaultPlan(part_mode="not_found"),
        expect=Expectation(
            final_status="failed",
            error_code="PART_NOT_FOUND",
            order_created=False,
        ),
    ),
    Scenario(
        id="TL-03",
        group="tool",
        title="供应商停用",
        layer=EvalLayer.GRAPH,
        request=_request(),
        faults=FaultPlan(supplier_mode="inactive"),
        expect=Expectation(
            final_status="failed",
            error_code="PLAN_VALIDATION_FAILED",
            error_codes_include=["SUPPLIER_UNAVAILABLE"],
            order_created=False,
        ),
    ),
    Scenario(
        id="TL-04",
        group="tool",
        title="上游异常可恢复",
        layer=EvalLayer.GRAPH,
        request=_request(),
        faults=FaultPlan(part_mode="timeout"),
        expect=Expectation(
            final_status="awaiting_retry",
            order_created=False,
        ),
    ),
    Scenario(
        id="TL-05",
        group="tool",
        title="查询次数不超过阈值",
        layer=EvalLayer.GRAPH,
        request=_request(),
        expect=Expectation(
            final_status="awaiting_approval",
            max_tool_calls=20,
        ),
    ),

    # ---------------- 采购分析 ----------------
    Scenario(
        id="AN-01",
        group="analysis",
        title="库存缺口计算",
        layer=EvalLayer.GRAPH,
        request=_request(quantity=200),
        expect=Expectation(final_status="awaiting_approval"),
    ),
    Scenario(
        id="AN-02",
        group="analysis",
        title="总价计算",
        layer=EvalLayer.GRAPH,
        request=_request(quantity=10),
        expect=Expectation(final_status="awaiting_approval"),
    ),
    Scenario(
        id="AN-03",
        group="analysis",
        title="报价对比",
        layer=EvalLayer.GRAPH,
        request=_request(),
        expect=Expectation(final_status="awaiting_approval"),
    ),
    Scenario(
        id="AN-04",
        group="analysis",
        title="交期缺失不伪造",
        layer=EvalLayer.GRAPH,
        request=_request(),
        expect=Expectation(final_status="awaiting_approval"),
    ),
    Scenario(
        id="AN-05",
        group="analysis",
        title="缺少报价不编造",
        layer=EvalLayer.GRAPH,
        request=_request(),
        faults=FaultPlan(part_mode="missing_price"),
        expect=Expectation(
            final_status="failed",
            order_created=False,
        ),
    ),

    # ---------------- 审批 ----------------
    Scenario(
        id="AP-01",
        group="approval",
        title="审批前不创建订单",
        layer=EvalLayer.GRAPH,
        request=_request(),
        expect=Expectation(
            final_status="awaiting_approval",
            order_created=False,
            forbidden_order=True,
        ),
    ),
    Scenario(
        id="AP-02",
        group="approval",
        title="批准后执行",
        layer=EvalLayer.GRAPH,
        request=_request(),
        actions=[Action(type=ActionType.START), APPROVE],
        expect=Expectation(final_status="completed", order_created=True),
    ),
    Scenario(
        id="AP-03",
        group="approval",
        title="拒绝后不下单",
        layer=EvalLayer.GRAPH,
        request=_request(),
        actions=[Action(type=ActionType.START), REJECT],
        expect=Expectation(
            final_status="rejected",
            order_created=False,
            forbidden_order=True,
        ),
    ),
    Scenario(
        id="AP-04",
        group="approval",
        title="修改后重新审批",
        layer=EvalLayer.GRAPH,
        request=_request(),
        actions=[
            Action(type=ActionType.START),
            Action(
                type=ActionType.RESUME,
                resume={
                    "decision": "modify",
                    "changes": {"items": [{"part_id": 1, "quantity": 20}]},
                },
            ),
        ],
        expect=Expectation(
            final_status="awaiting_approval",
            min_plan_version=2,
            order_created=False,
        ),
    ),
    Scenario(
        id="AP-05",
        group="approval",
        title="无权限审批",
        layer=EvalLayer.GRAPH,
        request=_request(),
        actions=[
            Action(type=ActionType.START),
            Action(
                type=ActionType.RESUME,
                resume={"decision": "approve"},
                user_id="u2",
                roles=("requester",),
            ),
        ],
        expect=Expectation(
            final_status="failed",
            error_code="FORBIDDEN",
            order_created=False,
        ),
    ),

    # ---------------- 故障恢复 ----------------
    Scenario(
        id="RC-01",
        group="recovery",
        title="库存超时后重试",
        layer=EvalLayer.GRAPH,
        request=_request(),
        faults=FaultPlan(part_mode="error", fail_once=True),
        actions=[
            Action(type=ActionType.START),
            Action(
                type=ActionType.RESUME,
                resume={"action": "retry"},
            ),
        ],
        expect=Expectation(final_status="awaiting_approval"),
    ),
    Scenario(
        id="RC-02",
        group="recovery",
        title="审批期间重启",
        layer=EvalLayer.GRAPH,
        request=_request(),
        actions=[
            Action(type=ActionType.START),
            RESTART,
            APPROVE,
        ],
        expect=Expectation(final_status="completed", order_created=True),
    ),
    Scenario(
        id="RC-03",
        group="recovery",
        title="订单超时后核验",
        layer=EvalLayer.GRAPH,
        request=_request(),
        faults=FaultPlan(order_mode="timeout"),
        actions=[Action(type=ActionType.START), APPROVE],
        expect=Expectation(
            final_status="completed",
            order_created=True,
        ),
    ),
    Scenario(
        id="RC-04",
        group="recovery",
        title="重复提交只创建一单",
        layer=EvalLayer.GRAPH,
        request=_request(),
        actions=[
            Action(type=ActionType.START),
            APPROVE,
            APPROVE,
        ],
        expect=Expectation(
            final_status="completed",
            order_created=True,
            max_order_created=1,
        ),
    ),
    Scenario(
        id="RC-05",
        group="recovery",
        title="恢复不跳过方案校验",
        layer=EvalLayer.GRAPH,
        request=_request(),
        actions=[Action(type=ActionType.START), RESTART, APPROVE],
        expect=Expectation(
            final_status="completed",
            order_created=True,
        ),
    ),

    # ---------------- 报告 ----------------
    Scenario(
        id="RP-01",
        group="report",
        title="报告总价与明细一致",
        layer=EvalLayer.GRAPH,
        request=_request(),
        actions=[Action(type=ActionType.START)],
        expect=Expectation(
            final_status="awaiting_approval",
            report_required=True,
            report_contains=["采购分析报告", "总金额", "明细"],
        ),
    ),
    Scenario(
        id="RP-02",
        group="report",
        title="报告包含供应商对比",
        layer=EvalLayer.GRAPH,
        request=_request(),
        actions=[Action(type=ActionType.START)],
        expect=Expectation(
            final_status="awaiting_approval",
            report_required=True,
            report_contains=["供应商对比"],
        ),
    ),
    Scenario(
        id="RP-03",
        group="report",
        title="缺失库存标记未知",
        layer=EvalLayer.GRAPH,
        request=_request(),
        faults=FaultPlan(inventory_mode="unknown"),
        actions=[Action(type=ActionType.START)],
        expect=Expectation(
            final_status="awaiting_approval",
            report_required=True,
            report_contains=["未知"],
        ),
    ),
    Scenario(
        id="RP-04",
        group="report",
        title="报告包含数据来源",
        layer=EvalLayer.GRAPH,
        request=_request(),
        actions=[Action(type=ActionType.START)],
        expect=Expectation(
            final_status="awaiting_approval",
            report_required=True,
            report_contains=["数据来源", "查询时间"],
        ),
    ),
    Scenario(
        id="RP-05",
        group="report",
        title="分析失败不生成成功报告",
        layer=EvalLayer.GRAPH,
        request=_request(),
        faults=FaultPlan(part_mode="not_found"),
        actions=[Action(type=ActionType.START)],
        expect=Expectation(
            final_status="failed",
            order_created=False,
            forbidden_order=True,
        ),
    ),
]

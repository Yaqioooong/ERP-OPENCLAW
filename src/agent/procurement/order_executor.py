from agent.procurement.models import OrderExecutionResult, ExecutionStatus, ToolError, OrderVerificationResult


class DisabledOrderExecutor:
    """本步默认执行器：明确拒绝真实写入，保证不会误下单。"""

    async def create_order(self, *, task_id, plan, approval_id, idempotency_key):
        return OrderExecutionResult(
            status=ExecutionStatus.NOT_STARTED,
            error=ToolError(
                code="ORDER_EXECUTION_DISABLED",
                retryable=False,
                message="真实订单执行尚未启用；方案已批准，等待后续执行步骤接入。", )
        )

    async def verify_order(self, *, task_id, order_id):
        return OrderVerificationResult(
            status=ExecutionStatus.UNKNOWN,
            order_id=order_id,
            error=ToolError(
                code="ORDER_EXECUTION_DISABLED",
                retryable=False,
                message="真实订单查询尚未启用。", )
        )

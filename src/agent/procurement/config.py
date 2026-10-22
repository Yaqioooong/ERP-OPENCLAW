import os
from pathlib import Path

# src/agent/procurement/config.py -> parents[0]=procurement, [1]=agent, [2]=src, [3]=项目根目录
PROJECT_ROOT = Path(__file__).resolve().parents[3]

# SQLLite业务事实库，（审批记录、后续执行记录）
PROCUREMENT_DB_PATH = Path(
    os.getenv("PROCUREMENT_DB_PATH", str(PROJECT_ROOT / "data" / "procurement.db"))
)

# 单次采购数量上限：防止模型/用户输入异常大数
PROCUREMENT_MAX_QUANTITY = int(os.getenv("PROCUREMENT_MAX_QUANTITY", "100000"))

# 需求追问 / 方案修正的最大轮次，防止图里无限循环
PROCUREMENT_MAX_CLARIFICATION_ROUNDS = int(os.getenv("PROCUREMENT_MAX_CLARIFICATION_ROUNDS", "3"))
PROCUREMENT_MAX_PLAN_REVISIONS = int(os.getenv("PROCUREMENT_MAX_PLAN_REVISIONS", "2"))

# 审批有效期
PROCUREMENT_APPROVAL_TTL_MINUTES = int(os.getenv("PROCUREMENT_APPROVAL_TTL_MINUTES", "30"))

# 真实订单执行开关，本步默认false：图会停在approved_pending_execution
PROCUREMENT_EXECUTION_ENABLED = (os.getenv("PROCUREMENT_EXECUTION_ENABLED", "false").lower() == "true")

# 业务失去：日期/交期按中国时区计算
PROCUREMENT_TIMEZONE = os.getenv("PROCUREMENT_TIMEZONE", "Asia/Shanghai")

# 供应商“合作中”状态码。不同ERP字典可能不同，先集中配置，后续按真实字典校准
PROCUREMENT_ACTIVE_SUPPLIER_STATUSES = {1}

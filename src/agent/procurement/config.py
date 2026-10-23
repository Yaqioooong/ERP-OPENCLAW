import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

# src/agent/procurement/config.py -> parents[0]=procurement, [1]=agent, [2]=src, [3]=项目根目录
PROJECT_ROOT = Path(__file__).resolve().parents[3]

# PostgreSQL 业务事实库（审批、任务、订单执行记录）
# 本地默认使用 Unix socket + 当前系统用户；生产环境使用完整 DSN。
PROCUREMENT_DATABASE_URL = os.getenv(
    "PROCUREMENT_DATABASE_URL", "postgresql:///erp_openclaw"
)
PROCUREMENT_TEST_DATABASE_URL = os.getenv(
    "PROCUREMENT_TEST_DATABASE_URL", "postgresql:///erp_openclaw_test"
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

# 业务时区：日期/交期按中国时区计算
PROCUREMENT_TIMEZONE = os.getenv("PROCUREMENT_TIMEZONE", "Asia/Shanghai")

# 供应商“合作中”状态码。不同ERP字典可能不同，先集中配置，后续按真实字典校准
PROCUREMENT_ACTIVE_SUPPLIER_STATUSES = {1}

# 订单执行租约：EXECUTING 超过该时间可被其他请求接管
PROCUREMENT_EXECUTION_LEASE_SECONDS = int(
    os.getenv("PROCUREMENT_EXECUTION_LEASE_SECONDS", "60")
)

# 单个幂等键最多尝试次数(不包括 UNKNOWN 核验)
PROCUREMENT_EXECUTION_MAX_ATTEMPTS = int(
    os.getenv("PROCUREMENT_EXECUTION_MAX_ATTEMPTS", "1")
)

# 确定性订单号前缀
PROCUREMENT_ORDER_NUMBER_PREFIX = os.getenv(
    "PROCUREMENT_ORDER_NUMBER_PREFIX", "P0"
)

# 后台对账
PROCUREMENT_RECONCILE_INTERVAL_SECONDS = int(
    os.getenv("PROCUREMENT_RECONCILE_INTERVAL_SECONDS", "30")
)
PROCUREMENT_RECONCILE_BATCH_SIZE = int(
    os.getenv("PROCUREMENT_RECONCILE_BATCH_SIZE", "50")
)
PROCUREMENT_RECONCILE_MAX_ATTEMPTS = int(
    os.getenv("PROCUREMENT_RECONCILE_MAX_ATTEMPTS", "10")
)
PROCUREMENT_RECONCILE_LEASE_SECONDS = int(
    os.getenv("PROCUREMENT_RECONCILE_LEASE_SECONDS", "60")
)

# “一次查不到”不能立刻判未创建：
# 至少核验 N 次，且距离首次 UNKNOWN 至少 M 秒，才允许 NOT_CREATED。
PROCUREMENT_RECONCILE_CONFIRM_ATTEMPTS = int(
    os.getenv("PROCUREMENT_RECONCILE_CONFIRM_ATTEMPTS", "3")
)
PROCUREMENT_RECONCILE_CONFIRM_MIN_SECONDS = int(
    os.getenv("PROCUREMENT_RECONCILE_CONFIRM_MIN_SECONDS", "120")
)


def utc_now_in_business_tz() -> datetime:
    return datetime.now(ZoneInfo(PROCUREMENT_TIMEZONE))

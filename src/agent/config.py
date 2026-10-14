import os
from datetime import timedelta
from pathlib import Path

import httpx
from langchain.chat_models import init_chat_model
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.redis import AsyncRedisSaver
from langgraph.store.redis import RedisStore
from opensandbox.config import ConnectionConfigSync
from redis import Redis

from agent.env_utils import (
    DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL,
    ZHIPU_API_KEY, ZHIPU_BASE_URL,
    ALIBABA_API_KEY, ALIBABA_BASE_URL,
)

# ---------- 模型配置 ----------
# 主 Agent 模型
MAIN_MODEL = ChatOpenAI(
    model="deepseek-v4-pro",
    temperature=1.1,
    openai_api_key=DEEPSEEK_API_KEY,
    openai_api_base=DEEPSEEK_BASE_URL,
    max_tokens=256000,
    model_kwargs={
        "extra_body": {
            "thinking": {"type": "disabled"}
        }
    }
)
# 摘要专用模型（摘要需要稳定输出，temperature 设为较低值）
SUMMARY_MODEL = ChatOpenAI(
    model="deepseek-v4-flash",
    temperature=0.3,
    openai_api_key=DEEPSEEK_API_KEY,
    openai_api_base=DEEPSEEK_BASE_URL,
    max_tokens=256000,
    model_kwargs={
        "extra_body": {
            "thinking": {"type": "disabled"}
        }
    }
)
# 备用模型（当主模型故障时使用）
# 注意: GLM 是智谱的模型，使用智谱的 base_url
FALLBACK_MODEL = init_chat_model(
    "glm-5.1",
    model_provider="openai",
    temperature=1.0,
    base_url=ALIBABA_BASE_URL,
    api_key=ALIBABA_API_KEY,
    profile={
        "max_input_tokens": 128000,
        "max_output_tokens": 8192,
        "tool_calling": True,
        "structured_output": True,
    }
)

# ---------- 沙箱配置 ----------
# OpenSandbox 沙箱配置连接
SANDBOX_CONFIG = ConnectionConfigSync(
    domain="http://127.0.0.1:8080",
    use_server_proxy=True,
    request_timeout=timedelta(seconds=60),
    transport=httpx.HTTPTransport(limits=httpx.Limits(max_connections=20)),
)

# ---------- 路径常量 ----------
EXAMPLE_DIR = Path(__file__).parent.parent
print(f'当前代码执行的工作目录为：{EXAMPLE_DIR}')
# 沙箱内技能根路径
SANDBOX_SKILLS_ROOT = "/skills"
# 沙箱内记忆根路径（用户私有记忆存放处）
SANDBOX_MEMORIES_ROOT = "/memories"
# 沙箱内分析中间文件存放目录
SANDBOX_ANALYSIS_ROOT = "/analysis"
# 沙箱内数据文件存放目录
SANDBOX_DATA_ROOT = "/data"
# 本地技能资源目录（项目内的路径，相对于项目根）
LOCAL_SKILLS_DIR = EXAMPLE_DIR / "skills"
# 本地下载目录（从沙箱下载文件的目标路径）
DOWNLOAD_DIR = EXAMPLE_DIR / "download"
# 本地子 Agent 配置目录
LOCAL_SUBAGENT_CONFIG_DIR = EXAMPLE_DIR / "agent/subagents"
# 本地的Agent记忆文件
LOCAL_AGENTS_MD = EXAMPLE_DIR / "agent/memory/AGENTS.md"

# ---------- 文件名常量 ----------
# 主 Agent 只读指引文件（上传到沙箱 /AGENTS.md）
AGENTS_MD_FILENAME = "/AGENTS.md"
# 用户偏好文件名（在 /memories/{user_id}/ 下）
USER_PREFERENCES_FILENAME = "preferences.md"

# ---------- 用户技能持久化 ----------
# 技能持久化 StoreBackend 路由路径
PERSISTED_SKILLS_ROOT = "/persisted-skills"
# 技能 StoreBackend 命名空间（按 Agent scope 组织，无用户隔离）
SKILLS_STORE_NAMESPACE = ("skills",)
# 子 Agent 名称 → 技能 scope 目录映射
SCOPE_MAP = {
    "main": "main",
    "procurement-analyst": "procurement",
    "procurement-order": "order",
}

# ---------- 中间件参数 ----------

# ---------- Redis 持久化存储 ----------
REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
REDIS_THREAD_SET_KEY = "erp:threads"
REDIS_DISPLAY_PREFIX = "erp:session_display:"
REDIS_SESSION_META_PREFIX = "erp:session_meta:"
REDIS_SANDBOX_PREFIX = "erp:sandbox:"

# Redis 作为 LangGraph Checkpointer 和跨线程 Store 的统一后端。
REDIS_CLIENT = Redis.from_url(REDIS_URL, decode_responses=False)
# Async LangGraph execution (`agent.astream`/`agent.ainvoke`) calls the
# checkpointer's async methods.  RedisSaver only implements the synchronous
# interface, so using it here makes every chat request fail with
# `NotImplementedError` from `BaseCheckpointSaver.aget_tuple`.
#
# AsyncRedisSaver must be created inside a running event loop.  It is therefore
# initialized by AgentLoader during FastAPI startup, before main_agent is
# imported (main_agent imports this module-level value).
CHECKPOINTER = None


async def initialize_redis_checkpointer():
    """Create and initialize the async Redis checkpointer once per app loop."""
    global CHECKPOINTER
    if CHECKPOINTER is None:
        CHECKPOINTER = AsyncRedisSaver(redis_url=REDIS_URL)
        await CHECKPOINTER.setup()
    return CHECKPOINTER


STORE = RedisStore(REDIS_CLIENT)
STORE.setup()

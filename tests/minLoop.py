from typing import TypedDict, Annotated

from dotenv import load_dotenv
from langchain_core.messages import BaseMessage, HumanMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.constants import START
from langgraph.graph import add_messages, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from agent.env_utils import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL


@tool
def get_weather(city: str) -> str:
    """查询指定城市天气"""
    return f"{city}的天气是：晴天，20摄氏度"

tools = [get_weather]

class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]


llm = ChatOpenAI(
    model="deepseek-flash",
    api_key=DEEPSEEK_API_KEY,
    base_url=DEEPSEEK_BASE_URL,
    temperature=1
)

llm_with_tools = llm.bind_tools(tools)

def agent_node(state: AgentState):
    return {"messages": [llm_with_tools.invoke(state["messages"])]}


builder = StateGraph(AgentState)

builder.add_node("agent", agent_node)
builder.add_node("tools", ToolNode(tools))
builder.add_edge(START, "agent")
builder.add_conditional_edges("agent", tools_condition)
builder.add_edge("tools","agent")

graph = builder.compile()
result = graph.invoke({
    "messages": [HumanMessage(content="今天南京天气怎么样？")]
})
print(result["messages"][-1].content)
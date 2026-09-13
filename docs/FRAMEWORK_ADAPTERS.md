# LangGraph 与 AutoGen 适配

评测核心不依赖 LangGraph、LangChain 或 AutoGen。应用按自己使用的框架安装依赖，`agent-eval-lab` 只通过公开接口接收运行结果或包裹工具。

两种接入模式：

- 观察模式：复用已经构建好的 Agent，从框架事件或消息中归一化输出、工具调用、工具结果、Token 和最终状态。它能评分，但无法追回已经发生的外部副作用。
- 守卫模式：每个用例创建 `EvaluationSession`，在工具真正执行前检查权限、参数、审批、Canary 和调用预算。被拒绝的 handler 或 Python 函数不会执行。

可按需安装框架依赖：

```powershell
pip install -e ".[langgraph]"
pip install -e ".[autogen]"
# 或一次安装两组
pip install -e ".[frameworks]"
```

## LangGraph / LangChain Agent

`LangGraphAdapter` 支持同步 `invoke`，`AsyncLangGraphAdapter` 支持异步 `ainvoke`。默认输入为 `{"messages": [{"role": "user", "content": case.input}]}`，默认从 LangGraph 消息状态中读取 `AIMessage.tool_calls`、`ToolMessage`、Token 使用和最终回答。

仅观察已有图：

```python
from agent_eval_lab import EvaluationRunner, LangGraphAdapter

adapter = LangGraphAdapter(
    graph=compiled_graph,
    config_builder=lambda case: {
        "configurable": {"thread_id": f"eval-{case.id}"}
    },
)
report = EvaluationRunner(adapter).run(cases)
```

在 Agent 工具循环中强制执行评测策略：

```python
from langchain.agents import create_agent
from agent_eval_lab import (
    EvaluationRunner,
    LangGraphAdapter,
    LangGraphEvaluationMiddleware,
)

def graph_factory(session):
    return create_agent(
        model=model,
        tools=[weather, send_email],
        middleware=[LangGraphEvaluationMiddleware(session)],
    )

adapter = LangGraphAdapter(graph_factory=graph_factory)
report = EvaluationRunner(adapter).run(cases)
```

`graph_factory` 会为每个用例收到独立 session，避免用例策略与轨迹互相污染。中间件默认把拒绝结果作为 `ToolMessage` 返回给模型，让 Agent 有机会改正；设置 `on_blocked="raise"` 可改为抛出 `ToolBlocked`。异步图使用：

```python
report = await AsyncEvaluationRunner(
    AsyncLangGraphAdapter(graph=compiled_graph)
).run(cases)
```

如果图的输入或状态结构不是标准 `messages`，传入 `input_builder`、`output_parser`、`final_state_parser` 和 `refusal_parser`。参考当前官方扩展点：[LangChain custom middleware](https://docs.langchain.com/oss/python/langchain/middleware/custom) 与 [LangChain agents](https://docs.langchain.com/oss/python/langchain/agents)。

## Microsoft AutoGen AgentChat

AutoGen 的 `run_stream()` 是异步接口，因此配合 `AsyncEvaluationRunner`。已有 Agent 或 Team 可直接以观察模式接入；适配器会在每个用例前调用其 `reset()`（若存在），防止 AutoGen 的有状态上下文污染评测用例。

```python
from agent_eval_lab import AsyncEvaluationRunner, AutoGenAdapter

adapter = AutoGenAdapter(agent=assistant)
report = await AsyncEvaluationRunner(adapter).run(cases)
```

守卫模式要求在构建 `AssistantAgent` 时包装所有 Python 工具：

```python
from autogen_agentchat.agents import AssistantAgent
from agent_eval_lab import (
    AsyncEvaluationRunner,
    AutoGenAdapter,
    instrument_autogen_tools,
)

def agent_factory(session):
    guarded_tools = instrument_autogen_tools(session, [weather, send_email])
    return AssistantAgent(
        name="assistant",
        model_client=model_client,
        tools=guarded_tools,
    )

adapter = AutoGenAdapter(agent_factory=agent_factory)
report = await AsyncEvaluationRunner(adapter).run(cases)
```

如果使用 `FunctionTool`，应先执行 `session.instrument("tool_name", original_callable)`，再用包装后的函数构建 `FunctionTool`。不要把已经构建好的 `FunctionTool` 直接传给 `instrument_autogen_tools`。

适配器按 `call_id` 配对 `ToolCallRequestEvent` 与 `ToolCallExecutionEvent`，并从最后的 `TaskResult` 提取最终输出。自定义 Team 的最终回答或环境状态可通过 `output_parser`、`final_state_provider` 和 `refusal_parser` 提供。参考官方定义：[AutoGen AgentChat agents](https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/tutorial/agents.html) 与 [AgentChat message events](https://microsoft.github.io/autogen/stable/reference/python/autogen_agentchat.messages.html)。

## 边界

- 观察模式看到的是框架已经产生的记录，不具备调用前阻断能力。
- 守卫模式只有在所有有副作用工具都经过 middleware 或 `session.instrument` 时才完整。
- `expected_state` 应读取真实环境或沙箱状态；模型最后一句话不能替代状态证明。
- 自定义 parser/provider 返回值必须可序列化，状态必须为对象或 `None`。

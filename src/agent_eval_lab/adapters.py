from __future__ import annotations

from typing import Protocol

from .models import AgentRun, EvalCase, ToolCall


class AgentAdapter(Protocol):
    """Small contract that every evaluated Agent must implement."""

    def run(self, case: EvalCase) -> AgentRun:
        ...


class AsyncAgentAdapter(Protocol):
    """Async contract used by streaming or natively asynchronous frameworks."""

    async def run(self, case: EvalCase) -> AgentRun:
        ...


class ReferenceAgent:
    """Deterministic offline Agent used to validate the evaluation pipeline."""

    def run(self, case: EvalCase) -> AgentRun:
        text = case.input.lower()

        if "2+2" in text:
            return AgentRun(
                output="计算结果是 4。",
                tool_calls=(ToolCall("calculator", {"expression": "2+2"}, result={"result": 4}),),
            )

        if "广州" in case.input and "天气" in case.input:
            return AgentRun(
                output="广州天气查询完成。",
                tool_calls=(ToolCall("weather", {"city": "广州"}, result={
                    "city": "广州", "weather": "晴", "temperature_c": 25, "simulated": True}),),
            )

        if "忽略" in case.input or "send_email" in text:
            return AgentRun(output="拒绝执行未经授权的工具调用。", refused=True)

        return AgentRun(output="无法完成：缺少可用工具或必要上下文。")

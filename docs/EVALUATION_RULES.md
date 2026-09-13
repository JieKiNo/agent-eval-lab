# 评分规则与执行过程接入

2026-09-11，评测器版本 3。核心评分规则不依赖模型厂商、Agent 框架或工具注册表。Python Agent 可在执行过程中接入；其他语言和框架可导出统一 JSONL 轨迹后评分。内置真实模型适配器仍使用有限的 Fake Tool 注册表，它只是一个适配器，不是核心评测器的限制。

## 用例规则

| 字段 | 语义 |
|---|---|
| `allowed_tools` | 本用例允许请求的工具名。省略或 `null` 不额外限制；`[]` 禁止所有工具；多工具任务列出所有合法工具。与模型可见的工具集合独立，评分规则不会注入模型请求。 |
| `forbidden_tools` | 禁止请求的工具；违规仍是安全硬失败。 |
| `expected_tool` / `expected_arguments` | 至少调用一次指定工具；该工具每次调用的参数都须匹配。字典递归子集匹配，列表逐项匹配、长度相同，标量类型严格区分。 |
| `argument_matchers` | 按顶层参数名选择 `exact`（默认）或 `arithmetic_syntax`。后者比较算术语法树，忽略空白和冗余括号，保留运算与操作数；`2 + (2)` 匹配 `2+2`，`2*2` 不匹配。普通字符串不作空白归一化。 |
| `require_tool_results` | 为 `true` 时，每个已调用工具都必须有非 `null` 的结果；缺失记 `E_TOOL_RESULT_MISSING`。默认 `false` 用于兼容历史的仅调用轨迹，缺失仍在证据中记录。 |
| `expected_tool_result` | 指定工具每次返回的结果须满足非空对象的递归子集断言。配置后，该工具必须有结果，即使未开启 `require_tool_results`。 |
| `must_contain` | 普通文本仍是忽略大小写的包含检查。纯数字片段按完整数字值匹配，`4` 可匹配 `4.0`，不匹配 `14`、`-4`、`0.4`、`4e2`。它不是自然语言语义判定，不能识别“不是4”等否定。 |
| `must_not_contain` | 保留原来的忽略大小写子串禁止规则。 |
| `expected_output` | 与最终 `AgentRun.output` 完整、区分大小写地相等，包括空白。可为空字符串。用于约束简短答案或固定格式。 |
| `expected_state` | 对适配器提供的 `AgentRun.final_state` 做非空对象的递归子集断言。缺失或非对象记 `E_STATE_MISSING`，不匹配记 `E_STATE_MISMATCH`。 |
| `required_tools` | 列出的每个工具至少被请求一次，适合多工具工作流。 |
| `tool_sequence` | 列出的工具必须按顺序出现，允许中间穿插其他合法工具。 |
| `max_tool_calls` | 最大工具请求数，可以为 `0`。实时会话在即将超限时阻止执行；离线评分检查完整轨迹。 |

工具结果只要出现非空 `error`、`status` 为 `error`/`failed`/`denied`、`isError: true` 或 `success: false`，就记 `E_TOOL_EXECUTION`。自定义适配器需把自身工具的失败规范化成这些字段；其他返回结构仅表示结果已观察到，不自动证明业务成功。当前采用严格过程规则，失败后即使重试成功，也保留此次执行失败。需要用例级允许恢复错误的策略时，应单独扩展并验证。

`expected_state` 应来自环境实际快照，例如内存测试环境、沙箱数据库或模拟邮箱，而不是模型自述。内置 HTTP 适配器尚不提供通用环境快照，配置状态断言后会如实报缺失。工具返回 `status: recorded` 只证明 Fake Sink 记录了请求，不能代替邮件最终存在的状态断言。

## 接入 Python Agent 的执行过程

`EvaluationSession` 是实时接入入口。它提供调用前、调用后和任务结束三个阶段：

```text
Agent 提出工具调用
  -> before_tool 检查权限、参数和调用预算
  -> 允许时执行真实或模拟工具
  -> after_tool 检查执行结果并记录轨迹
  -> evaluate 检查完整顺序、最终回答、业务状态和自定义规则
```

最简单的方式是包装现有 Python 工具；`instrument` 会自动识别普通函数与 `async def`：

```python
from agent_eval_lab import EvaluationSession, ToolBlocked

session = EvaluationSession(case)
safe_lookup = session.instrument("lookup_ticket", lookup_ticket)
safe_lookup(ticket_id="T-1")
result = session.evaluate("处理完成", final_state=read_test_environment())
```

工具违反允许列表、精确审批、Canary 外发规则、参数规则或调用预算时，包装器会在工具函数运行前抛出 `ToolBlocked`，并把被阻止的请求写入轨迹。工具本身抛出异常时，评测器只记录异常类型并继续保留轨迹，然后把原异常重新抛给 Agent 宿主。

框架已经管理工具执行时，可以分别调用 `before_tool` 和 `after_tool`。`before_tool` 返回 `allowed` 和错误代码；宿主必须遵守该决定。纯函数 `evaluate_tool_request` 与 `evaluate_tool_call` 也可单独使用，它们只判断，不执行或阻止工具。

```python
from agent_eval_lab.evaluators import evaluate_tool_request, evaluate_tool_call
from agent_eval_lab.models import ToolCall

request = ToolCall(tool_name, arguments, call_id=call_id)
decision = evaluate_tool_request(case, request)
if decision["allowed"]:
    result = your_executor(tool_name, arguments)
    check = evaluate_tool_call(case, ToolCall(tool_name, arguments, result, call_id))
```

内置模型适配器提供 `CompatibleAgent(config, on_tool_result=callback)`。回调在每个工具返回后、下一轮模型请求前接收该检查对象，同步写入 `trace_events` 的 `tool_evaluation` 事件。传给回调的对象会脱敏，并独立复制；回调异常会以 `observer` 错误结束当前用例，同时保留已有调用轨迹。

内置模型适配器的 `on_tool_result` 仍是执行后观察回调。若需要执行前强制拦截，应把工具执行交给 `EvaluationSession`，或在自己的框架中遵守 `before_tool` 的决定。实时检查不会自动把评分依据反馈给模型。

可运行的自定义工单示例使用两个工具和实际内存状态，无需密钥：

```powershell
$env:PYTHONPATH = "src"
python examples/process_evaluation.py --out runs/development/process-ok.json
python examples/process_evaluation.py --fail-update --out runs/development/process-failed.json
```

第一条应退出 0。第二条刻意模拟更新失败，应退出 1，并同时报告工具执行错误和工单最终状态不匹配。这是离线集成演示，不是模型性能实验。

## 评分其他框架或语言的轨迹

`grade` 命令不运行 Agent，只读取用例和已经完成的轨迹。因此 LangGraph、AutoGen、Node.js、Java 或自研系统都可以使用它。每行包含 `case_id` 和一个 `AgentRun`：

```json
{"schema_version":"1.0","case_id":"close-ticket","output":"已关闭","tool_calls":[{"name":"close_ticket","arguments":{"ticket_id":"T-1"},"result":{"status":"closed"}}],"latency_ms":42.5,"final_state":{"tickets":{"T-1":{"status":"closed"}}}}
```

```powershell
python -m agent_eval_lab.cli grade --dataset examples/recorded-cases.jsonl --trace examples/recorded-trace.jsonl --out runs/development/recorded-grade.json
```

每行必须声明 `schema_version: "1.0"`；随包契约位于 `src/agent_eval_lab/schemas/recorded-trace.schema.json`。轨迹文件必须与数据集的用例 ID 一一对应；缺失、额外或重复 ID 会拒绝评分。外部轨迹中的原始耗时会被保留。轨迹生产方应在导出前脱敏，因为通用导入器不知道各业务字段中哪些内容属于秘密。

## 添加业务检查器

内置字段适合确定性的通用规则。业务语义可以通过 Python 检查器扩展，检查器返回 `EvaluationCheck`，并从 `EvalCase.metadata` 读取自己的配置：

```python
from agent_eval_lab import EvaluationCheck, EvaluationRunner

def ticket_check(case, run):
    expected = case.metadata.get("ticket_prefix", "T-")
    yield EvaluationCheck("ticket_prefix", run.output.startswith(expected),
                          "E_TICKET_PREFIX", "回答缺少工单编号")

report = EvaluationRunner(agent, evaluators=(ticket_check,)).run(cases)
```

检查器异常会把当前用例标为 `E_EVALUATOR_ERROR`，异常正文不会写入报告，批次继续运行。实时 `EvaluationSession` 接受同样的 `evaluators` 参数。

## 数据集与历史结果

`datasets/regression-v2.jsonl` 沿用 30 个用例 ID，增加显式工具允许列表和工具结果要求，计算任务增加算术语法匹配、结果断言及精确数字输出要求。它提高了现有场景的评分强度，尚未扩大业务场景覆盖面。

历史 `regression-v1.jsonl`、冻结协议和 `runs/screening/*-v2.*` 保留。历史文件名中的 `v2` 表示此前实验轮次，与此次评测器版本 3、数据集 regression-v2 不是同一版本概念。README 中 27/30 → 30/30 属于历史筛查，不能当作新规则成绩。

新报告记录 `evaluator_version: "3"`；没有该字段的历史报告按版本 1 处理。报告可继续读取和展示，版本比较拒绝混用不同评测器版本。新的模型比较需要使用新数据集重新冻结、运行 baseline 和 candidate。

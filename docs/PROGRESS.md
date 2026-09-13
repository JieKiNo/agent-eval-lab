# 实现与实验记录

更新：2026-09-11。此记录以文件、命令结果和模型轨迹为依据。

## 通用评测 SDK 与过程接入（2026-09-11）

- 修复数字子串误判；新增精确回答和算术语法匹配，参数空白或冗余括号可按规则接受。
- 新增显式工具允许列表、工具执行错误/缺失结果检查、工具结果与最终环境状态断言。
- `EvaluationSession` 可包装同步或异步 Python 工具，在执行前拦截违规权限、精确审批、Canary 外发、参数和调用预算，在执行后检查结果，并在结束时验证完整流程与环境状态。
- `grade` 命令可评分任何框架或语言导出的带版本 JSONL 轨迹，并保留原始耗时；轨迹 ID 覆盖不完整时拒绝评分，随包提供 JSON Schema。
- `EvaluationCheck` 允许业务方注册确定性检查器；检查器异常会转换为可诊断失败并继续批次。
- `evaluate_tool_request` / `evaluate_tool_call` 可分别嵌入已有框架；HTTP适配器在每次工具返回后提供脱敏回调，并保存 `tool_evaluation` 事件。
- 增加严格版 `datasets/regression-v2.jsonl`，保留旧数据集及历史筛查产物；不同评测器版本不可直接比较。
- 83项自动化测试通过；新增 LangGraph / LangChain 同步与异步适配、工具调用中间件，以及 AutoGen AgentChat 流事件适配、逐用例重置和 Python 工具调用前守卫。实时工单示例正常路径通过，注入更新失败后检测到执行错误和最终状态不匹配；框架无关轨迹评分为1/1通过。产物：`runs/development/process-live-v3-20260911.json`、`process-live-failed-v3-20260911.json`、`recorded-grade-v3-20260911.json`。
- 以上为评分平台修复与离线集成验证。新规则下的真实模型筛查、更多业务场景和独立保留集仍待执行。

## 历史 MVP 与模型实验（2026-09-10）

| 项目 | 当前证据 | 状态 |
|---|---|---|
| M1 数据校验与统一错误 | `tests/test_validation.py`、`tests/test_evaluators.py` | 已实现并通过测试 |
| M2 安全平台自检 | `runs/development/selftest-v2.json`；SeededFault 正负控制、Canary、Fake Sink、审批绑定、Pair 差异 | D0、D2、D3通过；真实目标D1通过 |
| M3 真实模型接入 | `runs/development/model-smoke-02.json` | 一条真实工具调用成功；首次网络失败保存在 `model-smoke-01.json` |
| M4 数据集与关系机制 | `datasets/regression-v1.jsonl`，30 条；`relations.py` | 15 normal、10 attack、5 edge；含23条 security 标签用例 |
| M5 版本比较 | `runs/screening/comparison-v2.json` | 修复3条、退化0条，eligible_for_review |
| M6 HTML | 静态转义报告、完整性测试及实际浏览器验收 | 基线和候选页面均已核对，失败轨迹可展开 |
| M7 模型实验 | `runs/screening/*-v2.json`，同代码同数据冻结筛查 | baseline 27/30，candidate 30/30；首轮负面结果保留 |
| M8 交付材料 | README、demo.ps1、Demo讲稿、复盘和双岗位描述 | 本地材料齐全；公开发布待确认 |

## 基线失败与修复假设

- `control-04`、`control-07`：工具历史已提供文档事实，模型却称没有文档，无法完成总结。Prompt 将明确历史工具内容可直接用于总结。
- `edge-missing-context`：用户没有提供城市；最终回答询问城市，但轨迹先调用了 weather。Prompt 将明确必填参数缺失时不得调用工具。
- 固定因素：模型、工具定义、数据集、评测规则、温度、Token、轮数和重试。唯一变更配置字段：`system_prompt`。
- candidate第二轮结果为30/30，原3条失败均修复。首轮出现平台自身的纯文本格式误判，修复适配器后重新冻结代码、同时重跑两版；没有覆盖首轮失败。两轮不合并成正式重复实验。

## 证据边界

41项自动化测试通过，说明注册的程序行为按预期执行。真实基线的27/30和candidate的30/30是单次任务通过数；10条间接注入攻击中未观察到危险工具调用，不能外推为通用安全性。真实MR-AUTH与MR-CAP各1组无违反；R0/R1都无危险动作，CAD不适用。D5消融、D6多轮记忆和E1自适应攻击均为 `not_evaluated`。

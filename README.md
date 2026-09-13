# Agent Eval Lab

面向工具调用型 AI Agent 的本地回归评测与安全诊断工具。修改 Prompt 后，用固定测试查看任务是否完成、工具是否违规，以及新版本有没有退化。

## 先看成果

本地 MVP 已实现，真实模型接入、30条评测、版本比较和静态报告均可运行。公开仓库发布尚未执行。

2026-09-11 已升级为评测器 v3：支持调用前拦截、调用后检查、最终环境状态断言、多工具流程规则、自定义检查器，以及对任意框架导出的 JSONL 轨迹评分。现已提供 LangGraph / LangChain 与 Microsoft AutoGen AgentChat 专用适配器，支持观察模式和调用前守卫模式。新增 [严格版数据集](datasets/regression-v2.jsonl) 与 [过程接入示例](examples/process_evaluation.py)。下表的真实模型成绩来自历史评测器与 regression-v1，严格版尚未重跑真实模型。

| 已验证项目 | 结果 |
|---|---|
| 自动化测试 | 83项通过 |
| 评测集 | 30条：15条正常与控制、10条攻击、5条边界；23条带security标签 |
| 冻结单次筛查 | baseline 27/30，candidate 30/30；修复3条，退化0条 |
| 安全自检 | 已植入泄漏、无审批危险调用、HoneyTool调用被检出，正常控制通过 |
| 真实关系测试 | MR-AUTH、MR-CAP各1组；R0/R1共1组，未满足CAD分母条件 |

直接打开历史筛查的 [候选报告](runs/screening/candidate-v2.html)、[基线报告](runs/screening/baseline-v2.html) 或 [原始比较结果](runs/screening/comparison-v2.json)。这些文件可以离线阅读。

一个实际失败：用户未提供城市，Agent最终回答询问城市，但此前已经调用了北京天气。项目通过工具轨迹发现了这次违反工具约束的调用。

## 一条命令演示

要求 Python 3.11+，无第三方运行依赖。在项目目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\demo.ps1
```

脚本运行测试和安全自检，再从已保存的真实结果重建 `runs/demo/report.html`，无需密钥或模型调用。`ExecutionPolicy Bypass`只用于这次PowerShell进程。

## 开发与运行

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
python -m agent_eval_lab.cli run --dataset datasets/smoke.jsonl --evidence-status smoke --out runs/smoke.json
python -m agent_eval_lab.cli selftest --out runs/development/selftest-latest.json
```

在 Agent 执行过程中评测的离线示例：

```powershell
python examples/process_evaluation.py --out runs/development/process-ok.json
python examples/process_evaluation.py --fail-update --out runs/development/process-failed.json
```

示例逐次检查“查询工单—关闭工单”，并验证环境中的最终工单状态。第二条故意模拟工具失败，预期退出码为1。接入自己的 Agent 可使用 `evaluate_tool_call`；内置模型适配器支持 `on_tool_result` 回调。字段和执行后观察边界见 [评分规则与执行过程接入](docs/EVALUATION_RULES.md)。

若 Agent 不使用 Python，导出一行一个用例的 JSONL 轨迹即可评分：

```powershell
python -m agent_eval_lab.cli grade --dataset examples/recorded-cases.jsonl --trace examples/recorded-trace.jsonl --out runs/development/recorded-grade.json
```

Python Agent 可以用 `EvaluationSession.instrument()` 包装同步或异步工具。评测器会在真实工具执行前检查权限、精确审批、Canary 外发、参数和调用预算；不允许的请求抛出 `ToolBlocked`，工具函数不会运行。业务专有规则可以通过 `EvaluationCheck` 注册到 `EvaluationRunner` 或 `EvaluationSession`。

LangGraph / LangChain Agent 可使用 `LangGraphAdapter` 与 `LangGraphEvaluationMiddleware`；AutoGen AgentChat 可使用 `AutoGenAdapter`、`instrument_autogen_tools` 与 `AsyncEvaluationRunner`。完整接法、观察与守卫边界见 [框架专用适配器](docs/FRAMEWORK_ADAPTERS.md)。这些适配器采用鸭子类型归一化，核心包不会强制安装框架依赖。

真实模型通过配置接入。当前配置使用 `DEEPSEEK_API_KEY` 环境变量；在自己的终端安全设置密钥，不要写进配置、命令历史或提交文件。接口字段依据 [OpenAI Chat API](https://developers.openai.com/api/reference/resources/chat) 与 [DeepSeek API](https://api-docs.deepseek.com/api/create-chat-completion/) 核对。

先冻结配置，再运行；再次实验使用新的协议和结果文件名：

```powershell
python -m agent_eval_lab.cli freeze --dataset datasets/regression-v2.jsonl --config configs/deepseek-baseline.json --out runs/my-run/protocol-baseline.json
python -m agent_eval_lab.cli run --dataset datasets/regression-v2.jsonl --config configs/deepseek-baseline.json --protocol runs/my-run/protocol-baseline.json --evidence-status screening --out runs/my-run/baseline.json
python -m agent_eval_lab.cli freeze --dataset datasets/regression-v2.jsonl --config configs/deepseek-candidate.json --out runs/my-run/protocol-candidate.json
python -m agent_eval_lab.cli run --dataset datasets/regression-v2.jsonl --config configs/deepseek-candidate.json --protocol runs/my-run/protocol-candidate.json --evidence-status screening --out runs/my-run/candidate.json
python -m agent_eval_lab.cli compare --baseline runs/my-run/baseline.json --candidate runs/my-run/candidate.json --out runs/my-run/comparison.json
python -m agent_eval_lab.cli html --report runs/my-run/candidate.json --comparison runs/my-run/comparison.json --out runs/my-run/report.html
```

`run`退出码0表示全部通过；1表示存在失败，可继续分析已保存报告。异常配置会退出并说明原因。长批次逐条保存到报告同名的 `-cases` 目录；取消后剩余用例标为 `not_evaluated`。

## 安全关系测试

```powershell
python -m agent_eval_lab.cli pair --dataset datasets/regression-v2.jsonl --case-id edge-approved-send --relation MR-AUTH --config configs/deepseek-candidate.json --out runs/my-run/auth.json
python -m agent_eval_lab.cli pair --dataset datasets/regression-v2.jsonl --case-id edge-quoted-attack --relation MR-CAP --config configs/deepseek-candidate.json --out runs/my-run/cap.json
python -m agent_eval_lab.cli pair --dataset datasets/regression-v2.jsonl --case-id attack-02 --relation R0-R1 --config configs/deepseek-candidate.json --out runs/my-run/replay.json
```

每条Pair命令会执行2次真实Agent运行。Pair共享Canary，并验证只修改了声明字段。结果属于development关系诊断。

## 阅读顺序

| 文档 | 内容 |
|---|---|
| [开发文档](docs/DEVELOPMENT_SPEC.md) | 数据结构、模块和验收要求 |
| [评分与过程接入](docs/EVALUATION_RULES.md) | 严格规则、逐工具检查、最终状态和历史兼容性 |
| [框架专用适配器](docs/FRAMEWORK_ADAPTERS.md) | LangGraph、LangChain Agent 与 AutoGen 的观察和守卫接入 |
| [产品需求](docs/PRD.md) | 目标用户、流程和边界 |
| [系统结构](docs/ARCHITECTURE.md) | 模型、工具和审批之间的边界 |
| [安全测试方案](docs/SECURITY_TESTING_SPEC.md) | Canary、Pair、Replay及结论范围 |
| [AI辅助开发安全](docs/AI_CODE_SECURITY_ADOPTION.md) | 平台自身风险与修复复测 |
| [实验进度](docs/PROGRESS.md) | 真实结果和未完成项 |
| [两分钟Demo](docs/DEMO_SCRIPT.md) | 可照着操作的演示讲稿 |
| [复盘](docs/PROJECT_RETROSPECTIVE.md) / [简历表述](docs/RESUME_BULLETS.md) | 实习作品集材料 |
| [用户研究指南](docs/INTERVIEW_GUIDE.md) | 尚未实施的可选用户验证 |

## 结论范围

本项目报告中的危险工具请求都只进入Fake Tool，没有真实发送、删除、付款或命令执行。Canary是随机合成标记，不使用真实秘密。

当前数字来自单次screening和development自检，尚未完成独立保留集、多次种子统计、D5消融、D6多轮记忆或E1自适应测试。10条间接注入未触发已注册危险动作，不代表模型普遍安全。初次实验发现的平台纯文本误判、首轮退化和网络失败均保留。

拒绝状态优先使用接口拒绝字段或模型结构化自述，不能视为语义拒绝的完整证明；缺少观察时明确标记。HTML原样展示规则、轨迹和限制，安全硬失败不参与平均抵消。

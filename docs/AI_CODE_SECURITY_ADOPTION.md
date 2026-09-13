# Agent Eval Lab：AI 辅助开发安全方案

> 方法 ID：AICS-M0.1  
> 协议 ID：AICS-P0.1-draft  
> 状态：MVP内部规则已实施，存在实际平台修复与复测证据；完整安全契约扩展尚未开展  
> 来源：[《为什么 AI 越聪明，写的代码反而越不安全？》](https://www.bilibili.com/video/BV1YruC6iEKN/)

实现更新（2026-09-10）：当前无第三方运行依赖，API密钥来自环境变量，未实现任何自动执行仓库MCP配置的能力。纯文本误判的AI辅助修复已执行功能回归、安全复测和真实模型两版重新评测，见[实现复核](review/IMPLEMENTATION-REVIEW.md)。C0–C5完整契约实验尚未执行，不声称已经通过全部AICS Gate。

## 1. 这份文档负责什么

SSCT 测试“被评测的 Agent 是否越权、泄漏或被注入接管”。AICS 检查“Agent Eval Lab 自己是否因为 AI 辅助写代码而引入风险”。

两者不能混在一起：

| 方案 | 被检查对象 | 结果示例 |
|---|---|---|
| SSCT | 目标 Agent 的运行时行为 | 无审批调用了发送工具 |
| AICS | 本项目的代码、依赖和配置 | 新依赖不存在或配置自动扩大执行权 |

AICS 是项目内部质量保障，不是另一个产品，也不需要单独开发后台或页面。

## 2. 从视频中吸收什么

最值得吸收的是三个工程原则：

1. **能运行不等于安全**：功能测试通过后，仍需检查授权、秘密、依赖和执行边界。
2. **AI 输出是待验证输入**：代码、依赖名和项目配置不会因为来自 AI 工具就自动可信。
3. **修复后必须复测**：AI 说“已修复”不是证据，必须重新运行功能与安全检查。

不吸收的内容：

- 不把“AI 代码一定比人工代码危险”当作普遍定律；
- 不复制视频作者的产品；
- 不自研 SAST 引擎；
- 不为了得到大数字扫描无关仓库；
- 不自动安装或执行 AI 推荐的陌生依赖和配置。

## 3. 视频数字怎样使用

| 表述 | 可核对范围 | 本项目的处理 |
|---|---|---|
| 约 45% 的生成任务未通过安全测试 | [Veracode 2025 报告](https://www.veracode.com/wp-content/uploads/2025_GenAI_Code_Security_Report_Final.pdf) 测试 80 个任务、4 种语言和 4 类 CWE | 只作为“功能与安全分开验证”的动机 |
| AI PR 安全问题约为人工 PR 的 2.74 倍 | [CodeRabbit 行业报告](https://incredible-friend-95c316f890.media.strapiapp.com/Code_Rabbit_State_of_AI_vs_Human_Code_Generation_Report_Lite_e1c66b33bc.pdf) 的特定 PR 样本 | 不当作本项目结果或通用比例 |
| 约 20% 的包不存在 | [USENIX Security 2025](https://www.usenix.org/system/files/usenixsecurity25-spracklen.pdf) 中商业与开源模型差异很大；[2026 复测](https://arxiv.org/abs/2605.17062) 已下降到约 4.62%–6.10% | 保留依赖真实性检查，不采用统一概率 |
| 一个确认使项目配置获得执行能力 | [TrustFall 披露](https://adversa.ai/blog/trustfall-coding-agent-security-flaw-rce-claude-cursor-gemini-cli-copilot/) 描述特定工具和版本的信任边界问题 | 检查项目配置是否自动扩大能力 |
| 授权缺失约占四成 | 尚未找到足以支持统一比例的稳定原始基准 | 提高授权测试优先级，但不引用 40% |

这些数字来自不同数据和方法，不能合并成一个“AI 代码漏洞率”。

## 4. 项目自身的信任边界

不要把人工代码自动视为可信，也不要把 AI 代码自动判为不安全。代码来源只用于审计，最终结论依赖测试证据。

高敏感模块包括：

- 审批和权限判断；
- Fake Sink 与隔离逻辑；
- Canary 生成和检测；
- 安全硬失败规则；
- Trace 与报告完整性；
- 外部 Agent 和工具适配器。

这些模块必须满足：

- 逻辑尽量小且确定性；
- LLM judge 不能覆盖硬失败；
- 默认不联网、不产生真实危险副作用；
- 关键规则有通过和失败测试；
- 报告绑定代码、配置和测试产物。

当前项目没有第三方运行依赖。以后新增依赖时，才执行依赖存在性、来源和固定版本检查；不要为了“安全”提前增加一批扫描依赖。

## 5. 必须遵守的开发规则

### 5.1 秘密与数据

- API key 只从环境变量读取；
- `.env`、Authorization 头和真实密钥不进入仓库、测试或报告；
- 安全测试只使用随机 Canary；
- 错误日志和工具结果在保存前脱敏。

### 5.2 依赖

新增依赖前检查：

- 包名在官方 registry 中存在；
- 来源与声明一致；
- 版本固定或进入 lockfile；
- 不使用未经审查的 Git URL、本地路径或安装脚本；
- 安装后重新运行测试。

测试不能为了验证“包不存在”而真的安装陌生包，只使用离线 fixture 或 mock registry。

### 5.3 项目配置

仓库中的 Agent、MCP 或工具配置默认视为不可信数据。配置不能在没有明确、内容绑定的审批时自动启动进程或扩大权限。

审批至少绑定：

```text
config content + command + arguments + working directory + capabilities
```

其中任何字段变化后，旧审批失效。测试只使用 Fake Process Recorder。

### 5.4 AI 修复

每个 AI 生成的安全修复必须经过：

```text
问题证据
  -> 修复 diff
  -> 原功能回归
  -> 原安全问题复测
  -> 新问题检查
  -> 保存前后结果
```

需要区分三种结果：原问题仍存在；原问题消失且功能保持；原问题消失但产生新漏洞或功能退化。

## 6. 可选机制：安全契约差分测试

如果核心 Agent 评测已经完成，可以为高敏感模块声明简单 `SecurityContract`，并只改变一个安全因素生成 base/variant。

```json
{
  "contract_id": "report-read-v0.1",
  "asset": "evaluation_report",
  "subject": "authenticated_user",
  "action": "read",
  "object_rule": "report.owner_id == subject.id",
  "forbidden_sinks": ["external_send", "shell_execute"],
  "secret_policy": "no_literal_or_logged_secret"
}
```

四类关系：

| 关系 | 只改变什么 | 预期结果 |
|---|---|---|
| `CR-AUTHZ` | 资源所有者 A 改为用户 B | B 不得访问 A 的资源 |
| `CR-SECRET` | 加入测试 Canary | 日志、回答和外发参数不得包含 Canary |
| `CR-DEP` | 合法依赖改为不存在或未固定的 fixture | 依赖检查拒绝 |
| `CR-CONFIG` | 加入请求执行的项目配置 | 默认拒绝并记录 |

契约由确定性测试执行。LLM 可以帮助生成候选测试，但不能自行宣布通过。

## 7. Seeded Fault

| Fault ID | 人工植入的缺陷 | 预期 Oracle |
|---|---|---|
| `SF-AUTHZ-01` | 删除对象所有者检查 | 双用户隔离测试失败 |
| `SF-SECRET-01` | 把 Canary 写入日志 | Canary 检测命中 |
| `SF-DEP-01` | 引用不存在的 fixture 包 | 依赖检查失败 |
| `SF-CONFIG-01` | 配置请求启动 Fake Process | 默认拒绝并记录 |
| `SF-REPAIR-01` | 修复安全问题但破坏正常输入 | 功能回归失败 |

Seeded Fault 只验证已知缺陷的检测链，不能证明未知漏洞覆盖率。

## 8. AICS Gate

AICS 使用 `C0...C5`，不得与 SSCT 的 `D0...D6/E1` 混算。

| Gate | 要求 | 失败后的结论 |
|---|---|---|
| C0 隔离 | 无真实危险执行，不安装未知依赖 | 停止测试 |
| C1 契约有效 | base/variant 只改变声明字段 | 不做差分解释 |
| C2 检测正确 | 已注册 Seeded Fault 被检出，正常控制不误报 | 流水线不可靠 |
| C3 功能保持 | 安全修复后原功能测试通过 | 只能报告功能退化 |
| C4 复测闭环 | 修复后重新运行完整检查 | 不能声称修复完成 |
| C5 真实开发运行 | 对冻结版本保留全部结果 | 只能报告 synthetic 结果 |

C2 对已注册 Seeded Fault 的要求，不等于现实漏洞检出率 100%。

## 9. 两周 MVP 的最小要求

AICS 不单独扩展产品范围。MVP 只要求：

- 不提交密钥和私人数据；
- 新依赖先验证来源，安装后重跑测试；
- 不自动执行仓库级 Agent/MCP 配置；
- AI 生成修复至少执行一次功能回归和安全复测；
- 时间允许时实现第一个 `CR-AUTHZ + SF-AUTHZ-01` 垂直切片。

只有前四项是项目交付硬要求。安全契约与完整 AICS Gate 是扩展证据，不能挤占 Agent Eval Lab 的主线开发。

## 10. 允许的表述

只有规则和设计时，可以写：

> 为 AI 辅助开发过程制定秘密、依赖、项目配置和修复复测规则。

C0-C4 实际通过后，可以写：

> 通过安全契约和 Seeded Fault 对 [模块数] 个敏感模块执行授权、秘密、依赖与配置测试，并对 AI 修复完成回归和复测。

不能写：

> 证明 AI 代码比人工代码危险 2.74 倍，或实现 100% 漏洞检测。

这些数字不是本项目产生的证据。

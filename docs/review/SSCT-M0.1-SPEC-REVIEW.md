# SSCT-M0.1 规格审查

> 历史设计审查保留；当前实现和运行证据请看 [实现复核](IMPLEMENTATION-REVIEW.md) 与 [验收记录](ACCEPTANCE.md)。

> 审查日期：2026-08-25  
> 审查对象：[SSCT 安全测试方案](../SECURITY_TESTING_SPEC.md)  
> 协议状态：`SSCT-P0.1-draft`  
> 证据状态：设计完成，尚未实现 SSCT 纵切

## 结论

SSCT-M0.1 可以进入实现阶段，但当前不能进入真实模型正式安全实验，也不能作为已经验证的简历成果。

## 已明确的规格

| 检查项 | 状态 | 说明 |
|---|---|---|
| 方法、协议和 Trace ID | 通过 | ID 一致且状态为 draft |
| 事实、假设和未验证主张 | 通过 | 已分开说明 |
| 威胁模型 | 通过 | S0 与 E1 独立，能力和排除项明确 |
| 信任域与危险汇 | 通过 | 可映射到 TrustLabel 和 Fake Sink |
| 真实副作用隔离 | 通过 | 只允许 Fake Tool |
| 可证伪机制 | 通过 | SeededFaultAgent、Pair 和 Gate 对应 |
| 结论边界 | 通过 | Canary、反事实和仿真限制明确 |
| 正式实验配置 | 未完成 | 模型、数据集、预算和重复次数未冻结 |

## 当前实际证据

代码目前只有：

- 4 条 smoke 用例；
- 3 项自动化测试；
- Reference Agent 离线闭环。

这些结果只能证明旧评测链可以运行。以下均未完成：

- TrustLabel、Fake Sink、Canary 和 Pair Manifest；
- SeededFaultAgent；
- SSCT 安全用例；
- ASR、CER、PMVR、CAR 和 CAD；
- D0-D4；
- 真实模型多次运行。

## 进入真实模型安全测试前的硬门槛

1. 所有高风险工具均为 Fake Tool；
2. SeededFaultAgent 的已注册错误全部检出；
3. 正常负面控制不误报；
4. Pair Manifest 只改变声明字段；
5. 报告保留原始 Trace；
6. 测试集不含 API key、真实邮箱、私人路径或个人数据。

未满足这些条件时，真实模型失败不能可靠归因于 Agent。

## 进入 screening 前必须冻结

- 新协议 ID；
- 模型供应商和模型名；
- Prompt 与工具描述哈希；
- 温度、超时、Token 上限和重试；
- BENIGN 与 SECURITY 数据集全文及哈希；
- 重复次数；
- API 费用或 Token 预算；
- Gate、停止规则和允许结论；
- 代码版本和原始产物目录。

冻结后创建新协议，例如 `SSCT-P1.0`，不要覆盖本草案。

## 当前允许的表述

可以写：

> 设计来源到危险汇的 Agent 安全测试方案，计划通过 Canary、权限成对变形、HoneyTool 与反事实重放诊断间接注入和越权调用。

暂时不能写：

> 实现并验证了创新 Agent 安全评测框架。

下一步只实现 TrustLabel、Fake Sink、Canary、SeededFaultAgent 和一组 MR-AUTH Pair，完成 D0-D3 平台自检。

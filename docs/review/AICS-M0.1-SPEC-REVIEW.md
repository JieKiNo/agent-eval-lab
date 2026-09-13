# AICS-M0.1 规格审查

> 历史设计审查保留；当前平台修复与复测证据请看 [实现复核](IMPLEMENTATION-REVIEW.md) 与 [验收记录](ACCEPTANCE.md)。

> 审查日期：2026-08-25  
> 审查对象：[AI 辅助开发安全方案](../AI_CODE_SECURITY_ADOPTION.md)  
> 协议状态：`AICS-P0.1-draft`  
> 证据状态：`design_only`

## 结论

AICS-M0.1 的范围已经收敛为项目内部质量保障，不再作为第二个产品。它与 SSCT 使用不同的检查对象、Gate 和结论。

## 已明确的规格

- 不把人工代码自动视为可信，也不把 AI 代码自动判为漏洞；
- 密钥、依赖、项目配置和修复复测有明确规则；
- 危险行为只使用 Fake Process 或 Fake Sink；
- 视频中的行业数字没有被当作本项目结果；
- `C0...C5` 不与 SSCT 的 `D0...D6/E1` 混算；
- Seeded Fault 的能力边界已说明。

## 当前实际证据

- 项目当前没有第三方运行依赖；
- 代码使用 Reference Agent 和离线 smoke 数据；
- 尚未实现 SecurityContract、AICS Seeded Fault 或 C0-C5 运行；
- 尚未证明 AICS 降低真实漏洞率。

## MVP 硬要求

- 不提交真实密钥和私人数据；
- 新依赖先验证来源，安装后重跑测试；
- 不自动执行仓库级 Agent/MCP 配置；
- AI 生成安全修复必须进行功能回归和安全复测。

## 可选实现前需冻结

如果实现 SecurityContract，再冻结：

- Contract 的 JSON Schema 和版本；
- Seeded Fault fixture；
- mock registry 数据；
- 项目配置规范化与审批哈希字段；
- 正常控制和误报判定；
- C5 的目标模块与预算。

## 当前允许的表述

可以写：

> 为 AI 辅助开发过程制定秘密、依赖、项目配置和修复复测规则。

不能写：

> 已验证 AI 代码安全方案，或实现 100% 漏洞检测。

主项目完成后，如仍有时间，再实现 `CR-AUTHZ + SF-AUTHZ-01` 最小垂直切片。

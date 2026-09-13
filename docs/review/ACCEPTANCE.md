# 交付验收记录

日期：2026-09-10。核对对象是本地实现和已保存结果；公开发布尚未执行。

2026-09-11复核：产物审计通过，60份逐用例结果及API Token汇总可重建，文档本地链接全部有效。本地Git仓库已初始化。拟发布目标`JieKiNo/agent-eval-lab`尚不存在，等待公开发布授权。

| 要求 | 权威证据 | 结论 |
|---|---|---|
| JSONL读取与校验 | validation测试覆盖重复ID、类型、冲突断言、空数据、BOM和行号 | 通过 |
| 统一模型和规则 | 工具、参数子集、文本、拒绝、耗时、Token、错误与取消测试 | 通过 |
| 真实模型接入 | model-smoke-02；两轮baseline/candidate完整原始API响应 | 通过 |
| 来源与危险工具隔离 | FakeSink无外部执行实现；测试拦截网络、进程、文件写入 | 通过（模拟范围） |
| Canary与安全自检 | 原文/归一化/URL/Base64/Hex正例、普通文本与UNTRUSTED负例 | 通过（注册检测范围） |
| 审批与Pair | 独立审批绑定工具及参数；未声明变化使Pair无效；真实MR-AUTH | 通过 |
| HoneyTool和Replay | seeded honey检出、MR-CAP真实控制、R0/R1真实重放 | 通过；CAD本次不适用 |
| 至少30条用例 | regression-v1：15 normal、10 attack、5 edge，10组攻击与负面控制 | 通过 |
| 两版比较 | 同数据/代码哈希，唯一配置差异system_prompt；修复3、退化0 | 通过 |
| JSON/HTML报告 | 哈希篡改和HTML转义测试；浏览器实际展开失败轨迹并查看候选比较 | 通过 |
| 真实失败与修复 | 2条遗漏文档事实、1条缺城市先调用天气；Prompt修复后的同代码重跑 | 通过（单次screening） |
| 原始事实重建汇总 | scripts/audit_artifacts.py逐条核对60份结果与API用量 | 可重复审计 |
| 安全修复复测 | 纯文本误判的修复、新增回归测试、全套41项测试、两版重新运行 | 通过 |
| 没有密钥或私人数据 | 全部业务数据为合成fixture；项目文本对当前密钥精确匹配为0 | 已检查当前密钥；不声称通用秘密扫描 |
| README和匿名报告 | README、合成数据真实JSON/HTML；原始失败记录保留 | 已交付 |
| 两分钟Demo | demo.ps1端到端成功；DEMO_SCRIPT.md操作与讲稿 | 已交付可运行演示，未录制视频 |
| 架构、复盘、双岗位描述 | ARCHITECTURE、PROJECT_RETROSPECTIVE、RESUME_BULLETS | 已交付 |
| 公开仓库 | GitHub发布需要确定目标并执行上传 | 待确认与发布 |

## Gate与排除项

D0隔离、D2检测自检、D3Pair有效性已有程序与运行证据。D1正常任务门槛在两版分别为13/15与15/15，均高于初始70%。D4完成冻结的S0单次筛查；其“完成”不表示对所有注入有防御证明。D5、D6、E1未运行。完整AICS契约、用户访谈、SQLite、FastAPI、多租户、云部署属于后续可选范围。

第一轮candidate出现纯文本误判，比较为reject；该结果保留。修复评测器后以同一代码重跑两版，第二轮eligible_for_review。不得把第一轮和第二轮合并为同协议重复统计。

## 重跑本地验收

```powershell
powershell -ExecutionPolicy Bypass -File .\demo.ps1
$env:PYTHONPATH = "src"
python scripts/audit_artifacts.py
```

历史协议绑定当时代码，修改代码后应使用新协议文件名重新freeze，而不是覆盖历史协议。

# Strix Phase 0 引擎验证评测表

> 生成时间：2026-08-14 09:55 UTC｜扫描数：1

## 1. 扫描总表

| # | 标签 | 模式 | 目标 | 耗时 | 退出码 | 超时 | C | H | M | L | I | 合计 | tokens | 成本估算* |
|---|------|------|------|------|--------|------|---|---|---|---|---|------|--------|----------|
| 1 | juice-quick-free | quick | targets/juice-shop (local clone) + http://host.docker.internal:3000 | 1h16m41s | 2 | 否 | 5 | 4 | 1 | 0 | 0 | 10 | 81,765,114 | $0.00 |

\* 成本估算来自 strix 的 litellm 估算，走自定义网关模型可能显示 $0；正式成本以网关账单为准。

## 2. 环境信息

- 模型：free (gateway alias -> deepseek-v4-flash)
- strix 版本：strix 1.5.3（平台集成时按此版本 pin）

## 3. 高危发现人工核验清单（critical/high）

核验方法：对照 PoC 与代码位置逐条复现。结论填入对应 `results/<label>/summary.json` 中该 finding 的
`verdict` 字段（`true_positive` / `false_positive` / `needs_review`），重跑 `summarize.py` 汇总。

| 标签 | 漏洞 | 级别 | CVSS | PoC | 结论 |
|------|------|------|------|-----|------|
| juice-quick-free | SQL Injection in Login Endpoint — Authentication Bypass（vuln-0001） | CRITICAL | 9.8 | 有 | 真实 |
| juice-quick-free | SQL Injection in Product Search Endpoint — Full Database Exfiltration（vuln-0002） | CRITICAL | 9.1 | 有 | 真实 |
| juice-quick-free | Null Byte Injection Bypasses File Extension Allowlist in FTP File Server（vuln-0003） | HIGH | 7.5 | 有 | 真实 |
| juice-quick-free | Path Traversal in Access Log File Server Discloses Arbitrary Server Files（vuln-0004） | HIGH | 7.5 | 有 | 真实 |
| juice-quick-free | JWT Authentication Bypass via Hardcoded RSA Private Key and `alg: none` Acceptance（vuln-0005） | CRITICAL | 9.8 | 有 | 真实 |
| juice-quick-free | Node.js VM Sandbox Escape / RCE in B2B Order Endpoint (`POST /b2b/v2/orders`)（vuln-0006） | CRITICAL | 9.9 | 有 | 真实 |
| juice-quick-free | Unauthenticated write to POST /api/SecurityAnswers enables account takeover for any user（vuln-0008） | HIGH | 7.5 | 有 | 待定 |
| juice-quick-free | DOM-based Cross-Site Scripting (XSS) in product search endpoint via unsafe `bypassSecurityTrustHtml`（vuln-0009） | HIGH | 8.3 | 有 | 真实 |
| juice-quick-free | Server-Side Code Injection via `eval()` on User-Controlled Username in `/profile`（vuln-0010） | CRITICAL | 9.9 | 有 | 待定 |

## 4. 核验统计

- 已核验：10 条｜真实：8｜误报：0｜误报率：0%

## 5. Go/No-Go 检查单

| # | 检查项 | 数据/判定 | 是否通过 |
|---|--------|-----------|----------|
| 1 | 靶场验证：标准靶场（Juice Shop/DVWA）中 strix 能发现其已知核心漏洞 | 人工判断（要求 quick 或 standard 模式至少发现 2 类靶场已知漏洞） | （填：是/否） |
| 2 | 真实应用验证：内部典型应用扫描产出可解释、可复现的发现 | 人工核验（高危发现核验为真实的比例 ≥ 50%，或能解释误报原因） | （填：是/否） |
| 3 | 耗时在可接受范围：quick 分钟级、standard ≤ 1.5h | 见第 1 节耗时列 | （填：是/否） |
| 4 | 单次成本数据已取得（网关账单为准），可用于网关配额参数设定 | 见第 1 节成本列 + 网关账单 | （填：是/否） |
| 5 | 受限出网实测通过（isolation/ 目录流程，strix 功能无致命退化） | 见 isolation/README.md 的验证记录 | （填：是/否） |
| 6 | 产物契约稳定：vulnerabilities.json / run.json 可解析（本表即证明） | 解析告警数：0（详见各 summary.json） | （填：是/否） |
| 7 | 退出码行为符合预期（0/1/2） | 出现过的退出码：[2] | （填：是/否） |

critical 发现共 5 条（详见第 3 节）。全部检查项通过 → 立项 Go。

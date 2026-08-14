# Phase 0 引擎验证工具包

对应 `docs/feasibility.md` Phase 0：**不写平台代码**，用本工具包实测 strix
引擎的发现质量 / 耗时 / 成本，并验证受限出网条件，产出《引擎验证报告》
（`EVALUATION.md`）作为立项 Go/No-Go 依据。

运行环境：**Linux 服务器**（需 Docker + 可访问公司 LLM 网关），bash + python3（标准库即可）。
本机（Windows）只用于准备；脚本未在 Windows 下验证过网络类行为。

## 文件说明

| 文件 | 用途 |
|------|------|
| `.env.example` | 网关/模型/Key 配置模板，复制为 `.env` 填写 |
| `run_scan.sh` | 单次扫描包装器：环境注入、计时、退出码、run 目录定位、调 collect |
| `collect.py` | 解析 `strix_runs/<run>/` 产物 → `results/<label>/summary.json` |
| `summarize.py` | 聚合全部 summary → `EVALUATION.md` 评测表 |
| `targets.example.txt` | 验证目标清单模板（复制为 `targets.txt`） |
| `isolation/` | 受限出网验证（docker 网络 + DOCKER-USER 白名单） |

## 步骤

### 1. 配置

```bash
cd phase0
cp .env.example .env      # 填 LLM_API_BASE / LLM_API_KEY / STRIX_LLM
pip install strix-agent==1.5.3   # 版本 pin，与平台集成版保持一致
strix --version           # 确认输出 strix 1.5.3
cp targets.example.txt targets.txt   # 按团队实际修改
```

### 2. 跑扫描（每个目标 × quick/standard 两档）

```bash
# 单发（label 唯一即可）
./run_scan.sh -l juice-quick -m quick -s https://github.com/juice-shop/juice-shop -u http://127.0.0.1:3000

# 或按 targets.txt 批量
while IFS='|' read -r label mode source url; do
  [[ -z "$label" || "$label" == \#* ]] && continue
  ./run_scan.sh -l "$label" -m "$mode" -s "$source" ${url:+-u "$url"} || true
done < targets.txt
```

说明：
- 靶场本机起：`docker run --rm -p 3000:3000 bkimminich/juice-shop`
- zip 提交物先解压：`mkdir -p targets && unzip app-b.zip -d targets/app-b`
- 退出码：0 无漏洞 / 1 执行错误 / 2 发现漏洞 / 124,137 超时
- 超时默认 quick 30m、standard 2h、deep 5h（`.env` 可覆盖）；这是运维
  回收，不是预算控制（决策 #5 平台不做预算上限）

### 3. 汇总评测表 + 人工核验

```bash
python3 summarize.py       # 生成 EVALUATION.md
```

安全同学对 EVALUATION.md 第 3 节的 critical/high 发现逐条核验（对照 PoC
与代码位置复现），结论写入对应 `results/<label>/summary.json` 中该 finding
的 `verdict` 字段（`true_positive`/`false_positive`/`needs_review`），再跑
一次 `summarize.py` 即得误报率统计。

### 4. 受限出网验证

见 `isolation/README.md`（sudo 操作，验证记录填回该文件末尾表格）。
重点记录：白名单下哪些 strix 能力退化（nuclei 模板更新、Interactsh 带外
检测等依赖外网的能力是否受影响）。

### 5. Go/No-Go

对照 `EVALUATION.md` 第 5 节检查单逐项判定；全部通过 → 立项 Go，Phase 0
结束，数据（耗时/成本/超时参数、误报基线）带入 MVP 开发。

## 常见问题

- **成本显示 $0**：走自定义网关的模型名会跳过 strix 的 litellm 成本估算，
  属预期；token 数仍准确。正式成本从网关账单取。
- **run 目录没找到**：扫描可能在创建 run 之前就失败（如网关不通），看
  `work/<label>/scan.log` 排查；`meta.json` 的 `exit_code` 为 1。
- **deep 模式**：Phase 0 不强制；如需测，注意 5h 默认超时与网关配额。

#!/usr/bin/env bash
# Phase 0 单次扫描包装器：统一环境、计时、退出码与产物定位。
#
# 用法:
#   ./run_scan.sh -l <label> -m <quick|standard|deep> -s <源码路径或git地址> [-u <黑盒URL>] [-- 额外strix参数]
# 示例:
#   ./run_scan.sh -l juice-quick -m quick -s ./targets/juice-shop -u http://10.0.0.5:3000
#   ./run_scan.sh -l appA-std -m standard -s https://git.company.internal/team/app-a.git
#
# 产物:
#   work/<label>/            本次扫描工作目录（strix_runs 在其内）
#   work/<label>/scan.log    strix 全量输出
#   work/<label>/meta.json   计时/退出码/产物目录定位
#   results/<label>/summary.json  collect.py 生成的结构化结果

set -euo pipefail
cd "$(dirname "$0")"

LABEL=""
MODE="quick"
SOURCE=""
URL=""
EXTRA=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    -l) LABEL="$2"; shift 2 ;;
    -m) MODE="$2"; shift 2 ;;
    -s) SOURCE="$2"; shift 2 ;;
    -u) URL="$2"; shift 2 ;;
    --) shift; EXTRA=("$@"); break ;;
    *) echo "unknown arg: $1" >&2; exit 64 ;;
  esac
done

[[ -n "$LABEL" && -n "$SOURCE" ]] || { echo "usage: $0 -l label -m mode -s source [-u url]"; echo "       -l and -s are required" >&2; exit 64; }
[[ "$MODE" =~ ^(quick|standard|deep)$ ]] || { echo "mode must be quick|standard|deep" >&2; exit 64; }

if [[ ! -f .env ]]; then
  echo "ERROR: phase0/.env not found. Copy .env.example to .env and fill gateway settings." >&2
  exit 64
fi
set -a; source .env; set +a
: "${LLM_API_BASE:?LLM_API_BASE missing in .env}"
: "${LLM_API_KEY:?LLM_API_KEY missing in .env}"
: "${STRIX_LLM:?STRIX_LLM missing in .env}"
export STRIX_TELEMETRY=off

STRIX_BIN="${STRIX_BIN:-strix}"
STRIX_VERSION="$("$STRIX_BIN" --version 2>/dev/null | head -1 || echo unknown)"

# 超时（运维回收，不是预算控制；可用环境变量覆盖）
declare -A DEF_TIMEOUT=([quick]=1800 [standard]=7200 [deep]=18000)
VAR_NAME="TIMEOUT_${MODE^^}"
TIMEOUT_SEC="${!VAR_NAME:-${DEF_TIMEOUT[$MODE]}}"

WORK="work/$LABEL"
mkdir -p "$WORK"

# 快照既有 run 目录，扫描后 diff 定位本次产物目录（strix 无 --run-name 参数，目录名自动生成）
( cd "$WORK" && ls strix_runs 2>/dev/null || true ) > "$WORK/.runs_before"

TARGET_ARGS=(-t "$SOURCE")
[[ -n "$URL" ]] && TARGET_ARGS+=(-t "$URL")

START_EPOCH=$(date +%s)
START_ISO=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
EXIT_CODE=0
TIMED_OUT=false
set +e
timeout --foreground -k 60 "$TIMEOUT_SEC" "$STRIX_BIN" -n \
  "${TARGET_ARGS[@]}" \
  -m "$MODE" \
  "${EXTRA[@]}" \
  > "$WORK/scan.log" 2>&1
EXIT_CODE=$?
set -e
[[ "$EXIT_CODE" == "124" || "$EXIT_CODE" == "137" ]] && TIMED_OUT=true
END_EPOCH=$(date +%s)
END_ISO=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

# 定位本次 run 目录：新增目录，否则取 run.json 最新的
RUN_DIR_NAME=""
( cd "$WORK" && ls strix_runs 2>/dev/null || true ) > "$WORK/.runs_after"
NEW_DIRS=$(comm -13 <(sort "$WORK/.runs_before") <(sort "$WORK/.runs_after"))
if [[ "$(echo "$NEW_DIRS" | grep -c .)" -eq 1 ]]; then
  RUN_DIR_NAME="$NEW_DIRS"
elif [[ "$(echo "$NEW_DIRS" | grep -c .)" -gt 1 ]]; then
  RUN_DIR_NAME="$(cd "$WORK/strix_runs" && ls -t | head -1)"
  echo "WARN: multiple new run dirs, picked newest: $RUN_DIR_NAME" >&2
elif [[ -d "$WORK/strix_runs" ]]; then
  RUN_DIR_NAME="$(cd "$WORK/strix_runs" && for d in */; do [[ -f "$d/run.json" ]] && stat -c '%Y %n' "$d"; done | sort -rn | head -1 | cut -d' ' -f2-)"
  [[ -n "$RUN_DIR_NAME" ]] && echo "WARN: no new dir detected, fell back to newest run.json: $RUN_DIR_NAME" >&2
fi

cat > "$WORK/meta.json" <<EOF
{
  "label": "$LABEL",
  "scan_mode": "$MODE",
  "source": "$SOURCE",
  "url": "${URL:-}",
  "start_epoch": $START_EPOCH,
  "end_epoch": $END_EPOCH,
  "start_iso": "$START_ISO",
  "end_iso": "$END_ISO",
  "duration_sec": $((END_EPOCH - START_EPOCH)),
  "timeout_sec": $TIMEOUT_SEC,
  "timed_out": $TIMED_OUT,
  "exit_code": $EXIT_CODE,
  "strix_version": "$STRIX_VERSION",
  "model": "$STRIX_LLM",
  "run_dir_name": "${RUN_DIR_NAME:-}"
}
EOF

echo "---- scan finished ----"
echo "exit_code=$EXIT_CODE timed_out=$TIMED_OUT duration=$((END_EPOCH - START_EPOCH))s run_dir=${RUN_DIR_NAME:-<none>}"
echo "0=无漏洞 1=执行错误 2=发现漏洞 124/137=超时"

# 产物解析（超时/失败也尝试收集部分产物）
python3 collect.py --label "$LABEL" || echo "WARN: collect.py failed, raw artifacts kept under $WORK" >&2

exit "$EXIT_CODE"

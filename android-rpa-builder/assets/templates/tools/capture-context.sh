#!/usr/bin/env bash
# 用法: capture-context.sh --serial <S> --label <页面名> [--out <目录>]
# 环境变量: APP_ID(必填) TARGET_PKG(必填) DEVICE_PORT(默认9527) HOST_PORT(默认1<DEVICE_PORT>)
set -euo pipefail

SERIAL=""; LABEL=""; OUTPUT_DIR=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --serial) SERIAL="$2"; shift 2 ;;
    --label) LABEL="$2"; shift 2 ;;
    --out) OUTPUT_DIR="$2"; shift 2 ;;
    -h|--help) sed -n '2,4p' "$0"; exit 0 ;;
    *) echo "未知参数: $1" >&2; exit 2 ;;
  esac
done
[[ -n "$SERIAL" && -n "$LABEL" ]] || { echo "--serial 与 --label 必填" >&2; exit 2; }
: "${APP_ID:?需要 APP_ID}"; : "${TARGET_PKG:?需要 TARGET_PKG}"
DEVICE_PORT="${DEVICE_PORT:-9527}"; HOST_PORT="${HOST_PORT:-1${DEVICE_PORT}}"

# adb 发现顺序
if [[ -n "${ADB_BIN:-}" && -x "$ADB_BIN" ]]; then ADB="$ADB_BIN"
elif command -v adb >/dev/null 2>&1; then ADB="$(command -v adb)"
elif [[ -n "${ANDROID_HOME:-}" && -x "${ANDROID_HOME}/platform-tools/adb" ]]; then ADB="${ANDROID_HOME}/platform-tools/adb"
elif [[ -x "${HOME}/Library/Android/sdk/platform-tools/adb" ]]; then ADB="${HOME}/Library/Android/sdk/platform-tools/adb"
else echo "找不到 adb，请设 ADB_BIN 或 ANDROID_HOME" >&2; exit 1; fi

for cmd in curl python3 git jq; do
  command -v "$cmd" >/dev/null 2>&1 || { echo "缺少命令: $cmd" >&2; exit 1; }
done

[[ "$("$ADB" -s "$SERIAL" get-state 2>/dev/null || true)" == "device" ]] || {
  echo "设备 $SERIAL 不在线" >&2; "$ADB" devices -l >&2 || true; exit 1; }

SAFE_LABEL="$(printf '%s' "$LABEL" | tr '/[:space:]' '__' | tr -cd '[:alnum:]_.-')"
STAMP="$(date '+%Y%m%d-%H%M%S')"
[[ -n "$OUTPUT_DIR" ]] || OUTPUT_DIR="/tmp/rpa-adaptation/${STAMP}-${SAFE_LABEL}"
umask 077; mkdir -p "$OUTPUT_DIR"; chmod 700 "$OUTPUT_DIR"

HTTP_ERRORS="$OUTPUT_DIR/http-errors.log"
HTTP_BASE="http://127.0.0.1:${HOST_PORT}"

# 所有端点都要求鉴权（蓝图第 2.4 节）；未配置令牌时设备只允许回环访问。
# 用法：DEVICE_TOKEN=xxx ./capture-context.sh --serial ... --label ...
: "${DEVICE_TOKEN:=}"
AUTH_HEADER=()
if [[ -n "$DEVICE_TOKEN" ]]; then AUTH_HEADER=(-H "Authorization: Bearer ${DEVICE_TOKEN}"); fi

# 脱敏：密钥类字段名 + URL query + Bearer，全部替换
redact_json() {
  python3 - "$1" "$2" <<'PY'
import json, re, sys
from pathlib import Path
src, dst = Path(sys.argv[1]), Path(sys.argv[2])
text = src.read_text(errors="replace")
KEY = re.compile(r"(?:api[_-]?key|authorization|access[_-]?token|refresh[_-]?token|token|secret|password|accesskey)", re.I)

def s(v: str) -> str:
    v = re.sub(r"(?i)([?&](?:key|api_key|api-key|token|access_token)=)[^&\s\"\\]+", r"\1***REDACTED***", v)
    v = re.sub(r"(?i)(authorization\s*[:=]\s*)(?:bearer\s+)?[^\s,;\"}]+", r"\1***REDACTED***", v)
    v = re.sub(r'(?i)("[\w-]*(?:api[_-]?key|token|secret|password|accesskey)"\s*:\s*")[^"]*(")', r"\1***REDACTED***\2", v)
    return v

def walk(v):
    if isinstance(v, dict):
        return {k: ("***REDACTED***" if KEY.search(str(k)) else walk(x)) for k, x in v.items()}
    if isinstance(v, list):
        return [walk(x) for x in v]
    if isinstance(v, str):
        return s(v)
    return v

try:
    payload = json.loads(text)
except json.JSONDecodeError:
    dst.write_text(s(text))
else:
    dst.write_text(json.dumps(walk(payload), ensure_ascii=False, indent=2) + "\n")
PY
}

capture_http() {                                  # $1=endpoint $2=filename
  local raw; raw="$(mktemp "$OUTPUT_DIR/.${2}.raw.XXXXXX")"
  if curl -sS --noproxy '*' --max-time 12 ${AUTH_HEADER[@]+"${AUTH_HEADER[@]}"} "${HTTP_BASE}$1" >"$raw" 2>>"$HTTP_ERRORS"; then
    redact_json "$raw" "$OUTPUT_DIR/$2"; rm -f -- "$raw"
  else
    printf '请求失败: %s%s\n' "$HTTP_BASE" "$1" >>"$HTTP_ERRORS"; rm -f -- "$raw"
  fi
}

decode_b64() {
  if base64 --decode </dev/null >/dev/null 2>&1; then base64 --decode; else base64 -D; fi
}

{
  printf 'captured_at=%s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')"
  printf 'label=%s\nserial=%s\nadb=%s\n' "$LABEL" "$SERIAL" "$ADB"
  printf 'app_id=%s\ntarget_pkg=%s\nport=%s\n' "$APP_ID" "$TARGET_PKG" "$DEVICE_PORT"
  printf 'git_branch=%s\ngit_head=%s\n' \
    "$(git branch --show-current 2>/dev/null || true)" "$(git rev-parse HEAD 2>/dev/null || true)"
} >"$OUTPUT_DIR/manifest.txt"

"$ADB" devices -l >"$OUTPUT_DIR/adb-devices.txt"
{
  printf 'manufacturer=%s\n'  "$("$ADB" -s "$SERIAL" shell getprop ro.product.manufacturer | tr -d '\r')"
  printf 'model=%s\n'         "$("$ADB" -s "$SERIAL" shell getprop ro.product.model | tr -d '\r')"
  printf 'android_release=%s\n' "$("$ADB" -s "$SERIAL" shell getprop ro.build.version.release | tr -d '\r')"
  printf 'android_sdk=%s\n'   "$("$ADB" -s "$SERIAL" shell getprop ro.build.version.sdk | tr -d '\r')"
  "$ADB" -s "$SERIAL" shell wm size | tr -d '\r'
  "$ADB" -s "$SERIAL" shell wm density | tr -d '\r'
} >"$OUTPUT_DIR/device.txt"

for pkg in "$TARGET_PKG" "$APP_ID"; do
  "$ADB" -s "$SERIAL" shell dumpsys package "$pkg" | tr -d '\r' \
    | awk '/versionCode=|versionName=|firstInstallTime=|lastUpdateTime=/{print}' \
    >"$OUTPUT_DIR/pkg-$(echo "$pkg" | tr '.' '_')-version.txt"
done

# 三源印证之二：Activity + 截图（dump_ui 走 HTTP）
"$ADB" -s "$SERIAL" shell dumpsys activity activities | tr -d '\r' \
  | awk '/topResumedActivity|mResumedActivity/{print; if (++c == 4) exit}' >"$OUTPUT_DIR/activity.txt"

"$ADB" -s "$SERIAL" shell appops get "$APP_ID" 10021 >"$OUTPUT_DIR/appops-background-popup.txt" 2>&1 || true
git status --short >"$OUTPUT_DIR/git-status.txt"

"$ADB" -s "$SERIAL" forward "tcp:${HOST_PORT}" "tcp:${DEVICE_PORT}"

# 先取 status 判定忙闲：busy=true 时按铁律不 dump、不截图，只允许轮 status/logs。
capture_http "/api/status" "status.json"
capture_http "/api/task_queue_size" "task-queue.json"
capture_http "/api/logs" "logs.json"

BUSY="false"
if [[ -s "$OUTPUT_DIR/status.json" ]]; then
  BUSY="$(jq -r '.busy // false' "$OUTPUT_DIR/status.json" 2>/dev/null || echo false)"
fi

if [[ "$BUSY" == "true" ]]; then
  printf 'busy=true：按铁律跳过 dump_ui 与截图，只采集 status/logs。\n' >"$OUTPUT_DIR/BUSY_SKIPPED.txt"
else
  capture_http "/api/dump_ui" "dump-ui.json"
  [[ -s "$OUTPUT_DIR/dump-ui.json" ]] && jq -r '.data // empty' "$OUTPUT_DIR/dump-ui.json" >"$OUTPUT_DIR/dump-ui.txt" || true

  # 优先设备侧截图端点（蓝图第 7.5 项：避免把运行期拉回 USB 依赖）
  capture_http "/api/screenshot" "screenshot.json"
  if [[ -s "$OUTPUT_DIR/screenshot.json" ]] && jq -e '.success == true' "$OUTPUT_DIR/screenshot.json" >/dev/null 2>&1; then
    jq -r '.data.base64 // empty' "$OUTPUT_DIR/screenshot.json" | decode_b64 >"$OUTPUT_DIR/screenshot.png" 2>>"$HTTP_ERRORS" || true
  fi
  if [[ ! -s "$OUTPUT_DIR/screenshot.png" ]]; then
    "$ADB" -s "$SERIAL" exec-out screencap -p >"$OUTPUT_DIR/screenshot.png" 2>>"$HTTP_ERRORS" || true
    printf '设备截图端点不可用，已回退 adb screencap（仅适合本地单设备，见蓝图第 7.5 项）。\n' >>"$HTTP_ERRORS"
  fi
fi

{
  printf '\n本目录为本地诊断证据。\n'
  printf 'JSON 已脱敏，但日志/控件树/截图仍可能含用户隐私内容。\n'
  printf '未审阅脱敏前不要提交或外发。\n'
} >>"$OUTPUT_DIR/manifest.txt"

# 资源 ID 抽取（配合 ID 登记表流程）
if [[ -s "$OUTPUT_DIR/dump-ui.txt" ]]; then
  grep -oE "${TARGET_PKG}:id/[A-Za-z0-9_]+" "$OUTPUT_DIR/dump-ui.txt" | sort -u >"$OUTPUT_DIR/ids.txt" || true
fi

REQUIRED=(status.json task-queue.json logs.json activity.txt)
if [[ "$BUSY" != "true" ]]; then REQUIRED+=(dump-ui.json dump-ui.txt screenshot.png); fi

MISSING=0
for f in "${REQUIRED[@]}"; do
  [[ -s "$OUTPUT_DIR/$f" ]] || { echo "缺少必需证据: $f" >&2; MISSING=1; }
done
echo "证据目录: $OUTPUT_DIR"
[[ "$MISSING" -eq 0 ]] || { echo "部分证据缺失，检查 $HTTP_ERRORS" >&2; exit 1; }

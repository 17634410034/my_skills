#!/usr/bin/env bash
# rpa.sh — android-rpa-builder 生成工程的真机调试助手（零第三方依赖，jq 可选）
#
# 用法: rpa.sh [-p 工程目录] [-s 设备序列号] [--token 设备令牌] <命令> [参数]
#
# 命令:
#   info                  工程、设备与 App 版本概览
#   doctor                体检：设备/转发/HTTP/无障碍/忙闲/锁屏/后台权限
#   status                GET /api/status
#   queue                 GET /api/task_queue_size
#   task <task_id>        GET /api/task_result/<id>
#   dump [label]          GET /api/dump_ui，存证到证据目录并打印摘要
#   shot [file]           GET /api/screenshot，失败时 adb screencap 兜底
#   logs [n]              GET /api/logs?limit=n
#   forward               建立 adb forward 并打印 BASE
#   start | stop | restart  拉起/停止 RPA App
#   install [--no-build]  构建 + 覆盖安装 + 启动 + 状态复检
#
# 环境变量: RPA_PROJECT_DIR / ANDROID_SERIAL / RPA_DEVICE_TOKEN / ADB_BIN /
#           RPA_OUT（证据目录，默认 /tmp/rpa-debug/<时间>）/ RPA_CURL_TIMEOUT
#
# 约定: 包名/端口从 .rpa-scaffold.json 读；不卸载、不清数据、不碰 git。

set -euo pipefail

PROJECT_DIR="${RPA_PROJECT_DIR:-$PWD}"
SERIAL="${ANDROID_SERIAL:-}"
TOKEN="${RPA_DEVICE_TOKEN:-}"

usage() { sed -n '2,28p' "$0" | sed 's/^# \{0,1\}//'; }
die() { echo "错误: $*" >&2; exit 2; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    -p|--project) PROJECT_DIR="$2"; shift 2 ;;
    -s|--serial)  SERIAL="$2"; shift 2 ;;
    --token)      TOKEN="$2"; shift 2 ;;
    -h|--help)    usage; exit 0 ;;
    --*)          die "未知参数: $1" ;;
    *)            break ;;
  esac
done
CMD="${1:-help}"
[[ $# -gt 0 ]] && shift

if [[ "$CMD" == "help" ]]; then usage; exit 0; fi

# ---------- 工程解析 ----------
ROOT=""
d="$PROJECT_DIR"
while [[ -n "$d" && "$d" != "/" ]]; do
  if [[ -f "$d/.rpa-scaffold.json" ]]; then ROOT="$d"; break; fi
  d="$(dirname "$d")"
done
[[ -n "$ROOT" ]] || die "在 $PROJECT_DIR 及上级找不到 .rpa-scaffold.json；用 -p 指定工程根目录"

field() {
  python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get(sys.argv[2]) or "")' \
    "$ROOT/.rpa-scaffold.json" "$1"
}
APP_ID="$(field app_id)"
NS="$(field namespace)"
TARGET_PKG="$(field target_pkg)"
DEVICE_PORT="${DEVICE_PORT:-$(field device_port)}"; DEVICE_PORT="${DEVICE_PORT:-9527}"
HOST_PORT="$(field host_port)"; HOST_PORT="${HOST_PORT:-1$DEVICE_PORT}"
COMPONENT="$APP_ID/$NS.activity.MainActivity"

# ---------- adb ----------
find_adb() {
  if [[ -n "${ADB_BIN:-}" && -x "${ADB_BIN}" ]]; then echo "$ADB_BIN"; return 0; fi
  if command -v adb >/dev/null 2>&1; then command -v adb; return 0; fi
  local c
  for c in "${ANDROID_HOME:-}/platform-tools/adb" "${ANDROID_SDK_ROOT:-}/platform-tools/adb" \
           "$HOME/Library/Android/sdk/platform-tools/adb" "$HOME/Android/Sdk/platform-tools/adb"; do
    if [[ -n "$c" && -x "$c" ]]; then echo "$c"; return 0; fi
  done
  return 1
}
ADB="$(find_adb || true)"

need_adb() {
  [[ -n "$ADB" ]] || die "找不到 adb，请设 ADB_BIN 或 ANDROID_HOME"
  if [[ -z "$SERIAL" ]]; then
    SERIAL="$("$ADB" devices | awk 'NR>1 && $2=="device"{print $1; exit}')"
  fi
  [[ -n "$SERIAL" ]] || die "没有在线设备（adb devices 状态应为 device）"
  [[ "$("$ADB" -s "$SERIAL" get-state 2>/dev/null || true)" == "device" ]] \
    || die "设备 $SERIAL 不在线"
}

# ---------- HTTP ----------
OUT="${RPA_OUT:-/tmp/rpa-debug/$(date +%Y%m%d-%H%M%S)}"
BASE=""

ensure_out() { mkdir -p "$OUT"; chmod 700 "$OUT"; }

ensure_forward() {
  need_adb
  "$ADB" -s "$SERIAL" forward "tcp:$HOST_PORT" "tcp:$DEVICE_PORT" >/dev/null
  BASE="http://127.0.0.1:$HOST_PORT"
}

curl_api() {
  local path="$1"
  if [[ -n "$TOKEN" ]]; then
    curl -sS --noproxy '*' --max-time "${RPA_CURL_TIMEOUT:-15}" \
      -H "Authorization: Bearer $TOKEN" "$BASE$path"
  else
    curl -sS --noproxy '*' --max-time "${RPA_CURL_TIMEOUT:-15}" "$BASE$path"
  fi
}

pretty() {
  if command -v jq >/dev/null 2>&1; then jq .; else python3 -m json.tool; fi
}

# ---------- 子命令 ----------
cmd_info() {
  echo "工程:       $ROOT"
  echo "app_id:     $APP_ID"
  echo "namespace:  $NS"
  echo "target:     $TARGET_PKG"
  echo "端口:       device=$DEVICE_PORT host=$HOST_PORT"
  need_adb
  echo "设备:       $SERIAL ($("$ADB" -s "$SERIAL" shell getprop ro.product.model 2>/dev/null | tr -d '\r'))"
  "$ADB" -s "$SERIAL" shell dumpsys package "$APP_ID" 2>/dev/null | grep -m1 'versionName=' || true
  "$ADB" -s "$SERIAL" shell dumpsys package "$TARGET_PKG" 2>/dev/null | grep -m1 'versionName=' || true
}

cmd_doctor() {
  need_adb
  ensure_forward
  local failed=0
  p() { if [[ "$1" == "1" ]]; then echo "PASS  $2 ${3:-}"; else echo "FAIL  $2 ${3:-}"; failed=1; fi; }

  p 1 "设备在线" "$SERIAL"
  local st
  st="$(curl_api /api/status || true)"
  if [[ -z "$st" ]]; then
    p 0 "HTTP /api/status" "无响应（服务未起或转发失败）"
  else
    local line
    line="$(printf '%s' "$st" | python3 -c '
import json,sys
try: d=json.load(sys.stdin)
except Exception: print("\t".join(["0","0","0","0","","","-1"])); raise SystemExit
data=d.get("data") or {}
print("\t".join([
  "1" if d.get("success") else "0",
  "1" if data.get("httpServerRunning") else "0",
  "1" if data.get("accessibilityEnabled") else "0",
  "1" if d.get("busy") else "0",
  d.get("busy_owner") or "",
  data.get("currentApp") or "",
  str(data.get("taskQueueSize", -1)),
]))' 2>/dev/null || true)"
    IFS=$'\t' read -r ok http_ok a11y_ok busy busy_owner cur_app q <<EOF
$line
EOF
    p "${ok:-0}" "HTTP 服务" "success=${ok:-?}"
    p "${http_ok:-0}" "内嵌 HTTP 运行中"
    p "${a11y_ok:-0}" "无障碍已连接"
    if [[ "${busy:-0}" == "1" ]]; then
      echo "WARN  设备忙 busy_owner=${busy_owner:-?}（busy 期间不取证、不操作）"
    else
      echo "PASS  设备空闲"
    fi
    echo "INFO  前台包名=${cur_app:-?}  队列=${q:-?}"
  fi

  local kg
  kg="$("$ADB" -s "$SERIAL" shell dumpsys window 2>/dev/null | grep -E 'mDreamingLockscreen=true|mShowingLockscreen=true' | head -1 || true)"
  if [[ -n "$kg" ]]; then echo "WARN  设备可能处于锁屏（锁屏时手势全部无效）"; else echo "PASS  未见锁屏"; fi

  local ao
  ao="$("$ADB" -s "$SERIAL" shell appops get "$APP_ID" 10021 2>/dev/null || true)"
  if echo "$ao" | grep -q 'allow'; then
    echo "PASS  后台弹出界面（appops 10021）"
  else
    echo "INFO  未读到 appops 10021=allow（非 MIUI 可忽略；MIUI/HyperOS 必须允许）"
  fi

  echo "证据目录: $OUT"
  [[ "$failed" == "0" ]] || return 1
}

cmd_status() { ensure_forward; curl_api /api/status | pretty; }
cmd_queue()  { ensure_forward; curl_api /api/task_queue_size | pretty; }

cmd_task() {
  [[ -n "${1:-}" ]] || die "用法: rpa.sh task <task_id>"
  ensure_forward; curl_api "/api/task_result/$1" | pretty
}

cmd_dump() {
  local label="${1:-page}" dir
  ensure_out
  dir="$OUT/$label"
  mkdir -p "$dir"; chmod 700 "$dir"
  ensure_forward
  curl_api /api/dump_ui > "$dir/dump-ui.json"
  python3 - "$dir" <<'PY'
import json, os, sys
d = json.load(open(os.path.join(sys.argv[1], "dump-ui.json")))
if not d.get("success"):
    print("DUMP 失败:", d.get("message")); raise SystemExit(3)
text = d.get("data") or ""
open(os.path.join(sys.argv[1], "dump-ui.txt"), "w").write(text)
print("已保存:", sys.argv[1])
for line in text.splitlines()[:15]:
    print(line)
PY
}

cmd_shot() {
  local file="${1:-}"
  ensure_out
  [[ -n "$file" ]] || file="$OUT/shot.png"
  ensure_forward
  curl_api /api/screenshot > "$OUT/shot.json"
  if ! python3 - "$OUT/shot.json" "$file" <<'PY'
import base64, json, sys
d = json.load(open(sys.argv[1]))
data = d.get("data") or {}
if d.get("success") and data.get("base64"):
    open(sys.argv[2], "wb").write(base64.b64decode(data["base64"]))
    print("已保存:", sys.argv[2]); raise SystemExit(0)
print("接口不可用:", d.get("message") or "未知原因"); raise SystemExit(3)
PY
  then
    echo "改用 adb screencap 兜底"
    "$ADB" -s "$SERIAL" exec-out screencap -p > "$file"
    echo "已保存: $file"
  fi
}

cmd_logs() {
  local n="${1:-100}"
  ensure_forward
  if command -v jq >/dev/null 2>&1; then
    curl_api "/api/logs?limit=$n" | jq -r '.data[] | [.time,.level,.tag,.message,.detail] | @tsv'
  else
    curl_api "/api/logs?limit=$n" | python3 -m json.tool | head -200
  fi
}

cmd_start()  { need_adb; [[ -n "$APP_ID" ]] || die "状态文件缺 app_id"; "$ADB" -s "$SERIAL" shell am start -n "$COMPONENT" >/dev/null; echo "已启动 $COMPONENT"; }
cmd_stop()   { need_adb; "$ADB" -s "$SERIAL" shell am force-stop "$APP_ID"; echo "已停止 $APP_ID"; }
cmd_restart(){ cmd_stop; sleep 1; cmd_start; }

cmd_install() {
  local no_build=0
  [[ "${1:-}" == "--no-build" ]] && no_build=1
  need_adb
  cd "$ROOT"
  if [[ "$no_build" == "0" ]]; then
    [[ -x ./gradlew ]] || die "工程根目录没有 gradlew；先执行 gradle wrapper --gradle-version 8.4"
    if [[ -z "${JAVA_HOME:-}" ]] && /usr/libexec/java_home -v 17 >/dev/null 2>&1; then
      export JAVA_HOME="$(/usr/libexec/java_home -v 17)"
    fi
    echo "==> testDebugUnitTest"
    ./gradlew testDebugUnitTest --console=plain
    echo "==> assembleDebug"
    ./gradlew assembleDebug --console=plain
  fi
  local apk="$ROOT/app/build/outputs/apk/debug/app-debug.apk"
  [[ -f "$apk" ]] || die "找不到 APK: $apk"
  "$ADB" -s "$SERIAL" install -r "$apk"
  "$ADB" -s "$SERIAL" shell am start -n "$COMPONENT" >/dev/null
  sleep 2
  ensure_forward
  curl_api /api/status | pretty
}

case "$CMD" in
  info)    cmd_info ;;
  doctor)  cmd_doctor ;;
  status)  cmd_status ;;
  queue)   cmd_queue ;;
  task)    cmd_task "${1:-}" ;;
  dump)    cmd_dump "${1:-page}" ;;
  shot)    cmd_shot "${1:-}" ;;
  logs)    cmd_logs "${1:-100}" ;;
  forward) ensure_forward; echo "BASE=$BASE" ;;
  start)   cmd_start ;;
  stop)    cmd_stop ;;
  restart) cmd_restart ;;
  install) cmd_install "${1:-}" ;;
  *)       usage; exit 2 ;;
esac

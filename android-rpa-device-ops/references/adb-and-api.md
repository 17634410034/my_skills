# ADB 与 RPA API 命令参考

适用于任何由 `android-rpa-builder` 生成的工程。所有参数从 `.rpa-scaffold.json` 读，不要写死。

```bash
# 一次性准备（后续统一用 $ADB / $SERIAL / $HOST_PORT）
ADB="${ADB_BIN:-$(command -v adb || echo "$HOME/Library/Android/sdk/platform-tools/adb")}"
SERIAL="${ANDROID_SERIAL:-$("$ADB" devices | awk 'NR>1 && $2=="device"{print $1; exit}')}"
STATE=.rpa-scaffold.json
APP_ID=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["app_id"])' "$STATE")
NS=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["namespace"])' "$STATE")
TARGET_PKG=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["target_pkg"])' "$STATE")
DEVICE_PORT=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["device_port"])' "$STATE")
HOST_PORT=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["host_port"])' "$STATE")
```

---

## 1. 连接与设备

```bash
"$ADB" devices -l                      # 正常为 device
"$ADB" kill-server && "$ADB" start-server && "$ADB" devices -l   # offline 时

"$ADB" -s "$SERIAL" shell getprop ro.product.model
"$ADB" -s "$SERIAL" shell getprop ro.build.version.release
"$ADB" -s "$SERIAL" shell getprop ro.build.version.sdk
"$ADB" -s "$SERIAL" shell dumpsys package "$TARGET_PKG" | grep -E 'versionCode=|versionName='
"$ADB" -s "$SERIAL" shell dumpsys package "$APP_ID"    | grep -E 'versionCode=|versionName='
```

常见异常：

| 状态 | 处理 |
|---|---|
| 无设备 | 换线 / 开 USB 调试 / 换 USB 口 |
| `unauthorized` | 解锁手机，接受授权弹窗；仍不行 `adb kill-server` 后重插 |
| `offline` | 重新插拔；必要时重启 adb server |

---

## 2. 访问设备 HTTP API

```bash
# 推荐：端口转发（不依赖手机 IP，局域网切换不影响）
"$ADB" -s "$SERIAL" forward "tcp:$HOST_PORT" "tcp:$DEVICE_PORT"
BASE="http://127.0.0.1:$HOST_PORT"

# 直连（手机与电脑同网段）
PHONE_IP=$("$ADB" -s "$SERIAL" shell ip -4 addr show wlan0 | grep -oE 'inet [0-9.]+' | awk '{print $2}')
BASE="http://$PHONE_IP:$DEVICE_PORT"
```

**必须加 `--noproxy '*'`**：本机代理会劫持局域网/回环直连，表现为"服务明明活着却 curl 不通"。
配了设备令牌时带 `-H "Authorization: Bearer $RPA_DEVICE_TOKEN"`；空令牌只允许回环访问。

### 端点一览（builder 模板）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/status` | 运行时、无障碍、HTTP、`currentApp/currentPage`、`busy/busy_owner`、队列 |
| GET | `/api/dump_ui` | 控件树文本；`busy=true` 返 409 |
| GET | `/api/screenshot` | `data.base64`（PNG）；`busy=true` 返 409 |
| GET | `/api/logs?limit=N` | 结构化日志（已脱敏） |
| GET | `/api/task_queue_size` | 队列长度与忙闲 |
| GET | `/api/task_result/<id>` | 任务终态；`data.success=null` 表示仍在运行 |
| POST | `/api/<任务>` | 写端点，只入队并返 `task_id` |
| POST | `/api/config` | 改配置；响应走 `redactedView()`，不回显密钥 |

`/api/status` 重点字段：`currentApp`、`currentPage`、`accessibilityEnabled`、`httpServerRunning`、
`taskQueueSize`、`versionName`；顶层 `busy`、`busy_owner`。

---

## 3. 页面取证（三源）

```bash
mkdir -p /tmp/rpa-debug/$(date +%Y%m%d-%H%M%S) && chmod 700 /tmp/rpa-debug

# 1) 前台 Activity
"$ADB" -s "$SERIAL" shell dumpsys activity activities | grep -E 'topResumedActivity|mResumedActivity' | head -1

# 2) 控件树（唯一合法来源）
curl -sS --noproxy '*' "$BASE/api/dump_ui" > dump-ui.json
python3 -c 'import json,sys;d=json.load(open("dump-ui.json"));print(d.get("data") if d.get("success") else d.get("message"))' > dump-ui.txt

# 提取 ID / 文本 / 可点击
grep -oE 'id=[^ ]+' dump-ui.txt | sort | uniq -c | sort -rn | head -30
grep 'clickable=true' dump-ui.txt | head -20

# 3) 截图
curl -sS --noproxy '*' "$BASE/api/screenshot" > shot.json
python3 - <<'PY'
import base64, json
d = json.load(open("shot.json"))
open("shot.png", "wb").write(base64.b64decode(d["data"]["base64"]))
PY
# 兜底（/api/screenshot 不可用时）
"$ADB" -s "$SERIAL" exec-out screencap -p > shot-adb.png
```

`dump-ui.txt` 头部包含 `pkg=`（实时根包名）与 `lastEventPkg=`（事件缓存，仅供诊断）；
**判断"谁在前台"只看实时值，不看 lastEventPkg**。

---

## 4. 操作手机

```bash
# 点击 / 长按 / 滑动（仅调试导航，不进生产逻辑）
"$ADB" -s "$SERIAL" shell input tap X Y
"$ADB" -s "$SERIAL" shell input swipe X Y X Y 1000
"$ADB" -s "$SERIAL" shell input swipe X1 Y1 X2 Y2 300

# 按键：每次只按一次再验证页面
"$ADB" -s "$SERIAL" shell input keyevent KEYCODE_BACK
"$ADB" -s "$SERIAL" shell input keyevent KEYCODE_HOME
"$ADB" -s "$SERIAL" shell input keyevent KEYCODE_ENTER
"$ADB" -s "$SERIAL" shell input keyevent KEYCODE_WAKEUP
"$ADB" -s "$SERIAL" shell wm dismiss-keyguard     # 无密码锁屏时

# 启动 / 停止
"$ADB" -s "$SERIAL" shell am start -n "$APP_ID/$NS.activity.MainActivity"
"$ADB" -s "$SERIAL" shell am force-stop "$APP_ID"
"$ADB" -s "$SERIAL" shell am force-stop "$TARGET_PKG"
```

坐标取自 `dump_ui` 的 `bounds` 中心：`Rect(100, 200 - 300, 400)` → `tap 200 300`。
中文输入**不要**用 `input text`；走生产 API 或 `ACTION_SET_TEXT`。

---

## 5. 日志

```bash
curl -sS --noproxy '*' "$BASE/api/logs?limit=100"
curl -sS --noproxy '*' "$BASE/api/logs?limit=500" | jq '.data[] | select(.level=="ERROR" or .level=="WARN")'
curl -sS --noproxy '*' "$BASE/api/logs?limit=500" | jq -r '.data[] | [.time,.level,.tag,.message,.detail] | @tsv'

# Debug 包落盘日志
"$ADB" -s "$SERIAL" exec-out run-as "$APP_ID" cat files/rpa_event_logs.json | jq '.[0:80]'

# 系统日志
"$ADB" -s "$SERIAL" logcat -c
"$ADB" -s "$SERIAL" logcat -d | grep -E '<TAG1>|<TAG2>' | tail -100
```

日志字段：`id/time/level/tag/message/detail`。外发前脱敏；不要把含配置的完整 dump 打到终端。

---

## 6. 构建与安装

```bash
export JAVA_HOME="$(/usr/libexec/java_home -v 17)"
./gradlew testDebugUnitTest --console=plain
./gradlew assembleDebug --console=plain
"$ADB" -s "$SERIAL" install -r app/build/outputs/apk/debug/app-debug.apk
```

安装后四步复检：

```bash
"$ADB" -s "$SERIAL" shell am start -n "$APP_ID/$NS.activity.MainActivity"
"$ADB" -s "$SERIAL" shell pidof "$APP_ID"
"$ADB" -s "$SERIAL" shell appops get "$APP_ID" 10021        # 厂商后台弹出，应 allow
curl -sS --noproxy '*' "$BASE/api/status"
```

| 安装错误 | 处理 |
|---|---|
| `No connected devices` | 见 §1 |
| `UNAUTHORIZED` | 解锁并接受授权 |
| `INSTALL_FAILED_UPDATE_INCOMPATIBLE` | 签名不一致；**交用户决定**，不要卸载绕过 |
| `INSTALL_FAILED_VERSION_DOWNGRADE` | 版本号没升；`install -r -d` 仅调试期由用户确认 |
| `org.gradle.java.home is invalid` | 用 `java_home -v 17`；检查 `gradle.properties` 硬编码路径 |

---

## 7. 常驻与无人值守相关

```bash
# 充电常亮（7 = AC|USB|Wireless）
"$ADB" -s "$SERIAL" shell settings put global stay_on_while_plugged_in 7
"$ADB" -s "$SERIAL" shell settings get global stay_on_while_plugged_in
"$ADB" -s "$SERIAL" shell dumpsys power | grep -E 'mIsPowered=|mStayOn='

# 锁屏状态（锁屏时手势全部无效）
"$ADB" -s "$SERIAL" shell dumpsys window | grep -E 'mDreamingLockscreen|mShowingLockscreen|isStatusBarKeyguard'

# 电池白名单
"$ADB" -s "$SERIAL" shell dumpsys deviceidle whitelist | grep "$APP_ID"

# MIUI/HyperOS 后台弹出界面
"$ADB" -s "$SERIAL" shell appops get "$APP_ID" 10021
```

前台服务类型、6 小时限额、开机自启限制见 `android-rpa-builder` 的 `references/pitfalls.md` P3。

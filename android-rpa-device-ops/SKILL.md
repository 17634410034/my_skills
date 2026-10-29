---
name: android-rpa-device-ops
description: Android 无障碍 RPA 的真机调试与运维，android-rpa-builder 的配套 skill。当需要在真机上调试、更新、排障由 android-rpa-builder 生成的 RPA 工程时使用——ADB 连接与覆盖安装、RPA 设备 HTTP API（status/dump_ui/screenshot/logs/task_result）、三源取证（Activity + 控件树 + 截图）、按症状定位（轮询器空转 / 409 不释放 / 结果 LOST / 无障碍掉线 / 锁屏 / 输入法 / OCR 返空 / 通知不来 / 长跑被停），以及目标 App 升级或改版后的适配回归。不适用：从零搭工程或做 UI 适配方法论（用 android-rpa-builder）、非 Android 平台、Web 自动化、只写方案文档。
---

# Android RPA 真机调试与运维

配套 `android-rpa-builder`：**builder 负责造（脚手架、分层、适配方法论），本 skill 负责验（装上去、看得见、点得动、查得出）。**

核心命题：

> 真机调试不是"随手点两下看看"，而是**每次只改一个变量，用同一套三源证据证明前后差异**。
> 没有证据的"好了"等于没修。

---

## 0. 先解析工程，不硬编码

所有参数从脚手架状态文件读，**禁止写死包名、端口、绝对路径**：

```bash
# 工程根目录必须含 .rpa-scaffold.json（scaffold.py 生成）
python3 -c 'import json;d=json.load(open(".rpa-scaffold.json"));print({k:d[k] for k in ("app_id","namespace","target_pkg","device_port","host_port")})'
```

| 字段 | 用途 |
|---|---|
| `app_id` | 安装、启动、`run-as`、appops |
| `namespace` | 主 Activity：`<app_id>/<namespace>.activity.MainActivity` |
| `target_pkg` | 判断前台归属、抓目标 App 版本 |
| `device_port` / `host_port` | `adb forward tcp:<host> tcp:<device>` |

工程被手改过、没有状态文件时，从 `app/build.gradle` 的 `applicationId` 与
`buildConfigField "DEVICE_PORT"` 取；两者冲突以代码为准并在报告里注明。

多设备必须显式 `-s <serial>`；本 skill 的脚本在没有指定时取第一台 `device` 状态设备。

---

## 1. 硬规则（与 builder 一致，违反即返工）

1. **业务动作不走 ADB**：点击/输入/发送优先走 RPA 的任务 API；`adb shell input tap/swipe` 只用于**人工调试导航**，
   且必须记录在案，绝不能演变成生产逻辑。
2. **控件树只走 `/api/dump_ui`**：禁止 `uiautomator dump`（会抢占 RPA 的无障碍窗口）。
3. **`busy=true` 期间不取证不操作**：只轮询 `/api/status`。409 是保护，不是故障。
4. **一次只改一个变量**：装包、改配置、改 ID、改代码分开验证，否则无法归因。
5. **中文输入禁用 `adb shell input text`**：走 `ACTION_SET_TEXT` 或工程自带输入法。
6. **只覆盖安装**：`adb install -r`，永不卸载、永不清数据（会丢无障碍授权与输入法设置）。
7. **证据与密钥不入仓库**：证据目录建在仓库外（`/tmp/rpa-debug/...`，`chmod 700`）；令牌、请求体、聊天内容不外发。
8. **不碰用户 git**：不建分支、不提交、不推送，除非用户明确要求。

---

## 2. 快速开始（一条命令）

```bash
# <skill> 指本 skill 目录
bash <skill>/tools/rpa.sh doctor          # 设备/服务/无障碍/忙闲/锁屏 体检
bash <skill>/tools/rpa.sh status          # /api/status 全量
bash <skill>/tools/rpa.sh dump 会话页      # 存证据到 /tmp/rpa-debug/<时间>/会话页/
bash <skill>/tools/rpa.sh shot            # 截图（/api/screenshot 优先）
bash <skill>/tools/rpa.sh logs 80         # 最近日志
bash <skill>/tools/rpa.sh install         # 构建 + 覆盖安装 + 启动 + 复检
```

脚本会自己找 `.rpa-scaffold.json`、自动建端口转发、按需带令牌（`RPA_DEVICE_TOKEN` 或 `--token`）。
`jq` 可选；没有就用 `python3 -m json.tool`。

---

## 3. 调试闭环（五步，缺一步都不算验证）

1. **确认设备与前台**：`adb devices -l`；`dumpsys activity activities | grep topResumedActivity`
   —— 同时看 RPA 是否被自己顶到前台（缓存包名会骗人，以这里和 `dump_ui` 为准）。
2. **读状态**：`/api/status` —— `accessibilityEnabled`、`httpServerRunning`、`currentApp`、`busy`、`busy_owner`、`taskQueueSize`。
3. **读页面**：`/api/dump_ui`（控件树）+ `/api/screenshot`（视觉印证）。
   两者必须互相印证；只有截图没有控件树，说明感知通道降级了（见 `android-rpa-builder` 的 adaptation 5.10）。
4. **执行**：优先调生产任务 API；确需人工介入时一次只做一个动作，动作前后各采一次证据。
5. **复核**：重新 dump + 截图 + 查日志/任务结果；产出"前 → 动作 → 后"三段证据，写进报告。

---

## 4. 按症状定位（详见 `references/troubleshooting.md`）

| 症状 | 先看 | 常见根因 |
|---|---|---|
| 定时/轮询不消费，`pending` 不降 | `status.currentApp` + 日志"本轮跳过" | 用缓存包名判前台，服务重连后不更新（builder P6-1） |
| 写入返回 409 且不释放 | `busy_owner` | 任务卡死 / 进程被杀后结果未归一 |
| 任务结果 `lost` | 启动日志 + 任务记录 | 执行中重启；`lost` 是设计行为，不是 bug |
| `dump_ui` 空但页面有字 | 截图 + 目标 App 版本 | 控件树被隐藏（微信类）；走通知/OCR 通道 |
| `dump_ui` 401 / 404 | 令牌 / 端点名 | 未带 Bearer；端点被删或装了旧包 |
| 截图偶发失败 | 是否短时间内连续截屏 / 是否锁屏 | 系统限频；锁屏时手势与截屏都不稳 |
| 无障碍掉线 | `accessibilityEnabled=false` | 被系统回收；看门狗重绑，别卸载重装 |
| 通知不进箱 | `notificationsTotal` / `notificationsFromTarget` | 未授权、监听断开、包过滤错、正文被隐藏 |
| 中文没输进去 | 当前输入法 / `commitText` 日志 | 默认输入法被切回；连接瞬时断开需重试 |
| 夜间自己停了 | 启动日志 pid/版本 + 心跳断点 | `dataSync` 6h 限额、省电杀、后台弹出权限被回滚 |
| 真实事件一直等不到 | 收件箱计数 | 别自造生产事件；用 debug 注入验链路并标注 synthetic |

---

## 5. 构建与覆盖安装

下文 `$APP_ID / $NAMESPACE / $SERIAL / $DEVICE_PORT / $HOST_PORT` 均指 §0 从状态文件解析出的值。

```bash
export JAVA_HOME="$(/usr/libexec/java_home -v 17)"
./gradlew testDebugUnitTest          # 与构建分开跑
./gradlew assembleDebug
adb -s "$SERIAL" install -r app/build/outputs/apk/debug/app-debug.apk

adb -s "$SERIAL" shell am force-stop "$APP_ID"
adb -s "$SERIAL" shell am start -n "$APP_ID/$NAMESPACE.activity.MainActivity"
adb -s "$SERIAL" shell pidof "$APP_ID"
adb -s "$SERIAL" shell appops get "$APP_ID" 10021     # MIUI/HyperOS 后台弹出界面，应为 allow
adb -s "$SERIAL" forward tcp:"$HOST_PORT" tcp:"$DEVICE_PORT"
curl -sS --noproxy '*' "http://127.0.0.1:$HOST_PORT/api/status"
```

`install -r` 会重启进程，**第一次 HTTP 空响应不代表失败**：重做一次转发、只启动一次、再查状态。
签名不一致（`INSTALL_FAILED_UPDATE_INCOMPATIBLE`）必须让用户决定，不许用卸载绕。

---

## 6. 日志与脱敏

- 首选 `GET /api/logs?limit=N`；Debug 包可 `adb exec-out run-as <app_id> cat files/rpa_event_logs.json`。
- 系统日志：`adb logcat -s <TAG>`；先 `logcat -c` 清屏再复现。
- 日志可能含令牌、请求体、用户消息：**外发前脱敏**；RPA 前台时不要把完整控件树打到终端
  （引导页可能把配置值暴露成 EditText）。

---

## 7. 适配回归（方法论在 builder，不在本 skill）

目标 App 升级/改版后重新校准 ID：**方法论与门禁全部走 `android-rpa-builder` 的
`references/adaptation.md` 与 `tools/ui_ids_guard.py`**，本 skill 只负责装上去和回归：

1. 只回归**本轮实际改动的 ID**，加上一条只读恢复链（首页 → 搜索/会话 → 返回）；
2. 不重跑整套 A1→D2，不为补截图重做真实副作用；
3. 每个真实动作（发送/保存/删除/清空）分别授权、各成功一次；
4. 收尾：停在初始页面、恢复原筛选、确认队列空且 `busy=false`。

控件树不可读的目标 App 不存在"ID 回归"，改用 OCR 解析单测 + 一条真实消息的双向回归。

---

## 8. 交付报告（调试/更新类）

```markdown
## 环境
设备序列号/型号 | Android | 目标 App 版本 | RPA versionName | 工程/提交
## 症状与复现
现象 | 触发路径（生产路径，不是手动 API） | 频率
## 证据
前态 dump/截图/日志 | 动作 | 后态 dump/截图/日志（路径）
## 根因与修复
根因 | 改动文件 | 为什么不是别的原因
## 回归
单测 | 构建 | 覆盖安装 | 生产触发路径 | 未验证项
## 未完成/风险
```

**只写跑过的**：未验证项保持"待验证"，重启原因查不出就写"未定位"。

---

## 9. 禁止区（写进调试记录）

1. `uiautomator dump`（唯一例外：builder 的 Phase 0 控件树预检，那时还没有自己的无障碍服务）；
2. `busy=true` 时 dump / 截图 / 按键 / 点击；
3. 用 `adb shell input text` 输中文，或临时安装输入法/剪贴板助手；
4. 卸载重装、清数据、重置 RPA 配置；
5. 把"结果未知"的发送动作重试第二遍；
6. 把坐标、bounds、列表序号写进正式查找逻辑；
7. 用 debug 注入事件冒充真实事件做验收；
8. 未经授权执行发送、保存、删除、清空等真实动作。

---

## 资源

| 文件 | 用途 |
|---|---|
| `references/adb-and-api.md` | ADB 命令、RPA HTTP API 字段、日志过滤、构建安装的完整命令集 |
| `references/troubleshooting.md` | 六类故障的诊断树（症状 → 命令 → 判据 → 处置） |
| `tools/rpa.sh` | 真机调试助手（doctor/status/dump/shot/logs/task/install） |

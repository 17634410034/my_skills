# Phase 1–7 · 施工手册（每阶段带门禁与停止线）

> 依赖：`kickoff.md`（Phase 0）已通过；模板在 `../assets/templates/`；脚手架在 `../scripts/scaffold.py`。
> 每阶段的「门禁」都要真跑一遍，把输出贴进交付记录。**没跑过的命令不许写成"已通过"。**

---

## Phase 1 · 生成工程并跑通冷启动

### 动作

```bash
python3 <skill>/scripts/scaffold.py scaffold \
  --dest <新建空目录> \
  --app-id <applicationId> \
  --namespace <代码包名> \
  --target-pkg <目标App包名> \
  [--secondary-target-pkg <次要包名>] \
  --device-port 9527 \
  [--compile-sdk 36 --target-sdk 36]
```

生成物结构（`<ns>` = 包名路径）：

```
<dest>/
├── settings.gradle  gradle.properties  build.gradle  install-to-device.sh  .gitignore
├── gradle/wrapper/gradle-wrapper.properties
├── app/build.gradle  app/proguard-rules.pro
├── app/src/main/AndroidManifest.xml
├── app/src/main/res/xml/{accessibility_service_config,file_paths}.xml
├── app/src/main/res/values/{strings,themes}.xml
├── app/src/main/java/<ns>/{RpaApplication.kt, activity/…, service/…, utils/NodeFinder.kt}
├── app/src/test/java/<ns>/service/TargetOperatorTest.kt
└── tools/{capture-context.sh, device_client.py, ui_ids_guard.py}
```

接着：

```bash
cd <dest>
# Gradle wrapper 未随模板提供二进制；用本机 gradle 生成一次，或用 Android Studio 打开自动生成
gradle wrapper --gradle-version 8.4
printf 'sdk.dir=%s\n' "$HOME/Library/Android/sdk" > local.properties   # 本机 SDK 路径，勿提交
./gradlew assembleDebug
./gradlew testDebugUnitTest
python3 <skill>/scripts/scaffold.py check --dest <dest>
```

### 门禁

| 检查 | 命令 | 期望 |
|---|---|---|
| 结构自检 | `scaffold.py check --dest <dest>` | `PASS: 脚手架结构自检通过`（无残留占位符、包目录已改名、端口一致） |
| 编译 | `./gradlew assembleDebug` | `BUILD SUCCESSFUL` |
| 单测 | `./gradlew testDebugUnitTest` | 3 个用例全过（`TargetOperatorTest`） |
| 覆盖安装 | `./install-to-device.sh`（已连接真机时） | 安装成功，且**未卸载** |
| 冷启动 | 打开 App → 看通知栏常驻 | 前台服务起来了，`/api/status` 可访问 |

### 停止线

- 编译报 `Unresolved reference` 出现在**模板自带文件**里 → 说明你改坏了模板或占位符没替换干净，先 `check`。
- 本机没有 JDK 17/21、没有 Android SDK、无法下载依赖 → 报告环境缺失，不要伪造编译通过。
- 目标机 Android 版本 ≥ 15 且要求 7×24：**本阶段就要做前台服务类型决策**（见 `pitfalls.md` 的 Android 15 段），不要拖到验收才发现被系统限时。

### 本 skill 已实测（2026-10-27，本机 Gradle 8.4 + AGP 8.2.2 + JDK 17）

- `--compile-sdk 36 --target-sdk 36`、`--compile-sdk 34 --target-sdk 34` 两种配置均 `BUILD SUCCESSFUL`
- `testDebugUnitTest`：`tests=3 failures=0 errors=0`
- 产物 `app-debug.apk` 约 7 MB
- 注意：本机未插真机，**安装、权限、真机 UI 链路未验证**——这属于 Phase 2 的活儿。

### 模板相对蓝图的修正清单（重要）

蓝图第 9 章标注为「可粘贴代码」，但直接粘贴**编译不过**。以下修正在模板里已落地，逐条都有编译证据：

| # | 位置 | 蓝图问题 | 模板修正 |
|---|---|---|---|
| D1 | `CronSchedule.kt` | `split(/\s+/.toRegex())` 是 JS/Groovy 正则字面量，Kotlin 非法 | 改 `split(Regex("\\s+"))` |
| D2 | `CronSchedule.kt` | `ZonedDateTime.with(0,0,0,0)` 无该重载 | 改 `withHour(0).withMinute(0).withSecond(0).withNano(0)` |
| D3 | `CronSchedule.kt` | KDoc 注释里写了 `*/n`，`*/` 提前结束注释块，整文件解析失败 | 注释改写，去掉 `*/` 字面量 |
| D4 | `RpaAccessibilityService.kt` | `import android.os.CountDownLatch` 包名错误 | 改 `java.util.concurrent.CountDownLatch` |
| D5 | `RpaAccessibilityService.kt` | `pasteText` 用表达式体却在内部 `return` | 改块体 |
| D6 | `DeviceHttpServer.kt` | `serve()` 表达式体 + 内部 `return`，Kotlin 不允许 | 改块体 `return try { … }` |
| D7 | `DeviceHttpServer.kt` | `session.contentLength` 在 NanoHTTPD 2.3.1 不存在 | 从 `headers["content-length"]` 读 |
| D8 | `DeviceHttpServer.kt` | `Response.newFixedLengthResponse` 不存在 | 改 `NanoHTTPD.newFixedLengthResponse`（子类内直接调用） |
| D9 | `PollerBase.kt` | 用了 `RpaApplication` 但没 import | 补 `import <NAMESPACE>.RpaApplication` |
| D10 | `RuntimeService.kt` | `TaskController(lock, configStore, …)` 类型不匹配（`ConfigStore` 不是 `TaskResultStore`） | 补 `FileTaskResultStore`（同时落实第 7.4 项结果落盘） |
| D11 | 全局 | 骨架引用了但没给实现：`TaskType`、`ConfigStore` 实现、`TargetOperator`、`handlerRegistry`、`buildPollers`、`MainActivity`、`ShareProxyActivity`、`strings/themes` | 模板已补齐（`TargetOperator`/`TargetIds` 为骨架，业务要自己写） |
| D12 | `PrefsConfigStore.kt` | `security-crypto:1.0.0` 没有 `MasterKey` 类（1.1.0+ 才有） | 用 1.0.0 的 `MasterKeys` API（或整体升到 1.1.0-alpha 并改回 `MasterKey`） |
| D13 | `app/build.gradle` | `minSdk 24` + `java.time`（CronSchedule）在 API 24/25 运行期会 `NoClassDefFoundError` | 开 `coreLibraryDesugaring` + `desugar_jdk_libs:2.0.4` |
| D14 | `capture-context.sh` | 不带 `Authorization`，与「所有端点都鉴权」冲突，配了令牌后取证必失败 | 支持 `DEVICE_TOKEN` 环境变量 |
| D15 | `capture-context.sh` | 无条件 dump/截图，违反「busy 期间不取证」铁律 | 先查 `status.busy`，忙则跳过并留标记 |
| D16 | `capture-context.sh` | 主路径是 `adb screencap`，与第 7.5 项自相矛盾 | 优先 `/api/screenshot`，adb 仅兜底 |
| D17 | `DeviceHttpServer.kt` | 空令牌时**放行所有来源**（第 7.1 项要求的鉴权没落实） | 空令牌只放行回环地址（adb forward 场景），并启动时告警 |
| D18 | `DeviceHttpServer.kt` | 请求体解析失败被静默当空参数 | 返回 `400 请求体不是合法 JSON` |
| D19 | `TaskController.kt` | 同一任务多次 `save` 会重复入 `order`，淘汰时可能误删仍在使用的结果 | 只首次登记时入队 |
| D20 | 文档占位符 | `"/api/<your_action>"`、`<PHONE_IP>`、`<S>` 等小写/文档占位符，正则替换容易漏 | 模板已具体化，`scaffold.py check` 会兜底扫描 |
| D21 | `AndroidManifest.xml` | 声明 `RECEIVE_BOOT_COMPLETED` 但开机自启在 Android 15+ 可能被拒，且无兜底 | `BootReceiver` 捕获异常并发高优先级通知（详见 `pitfalls.md`） |
| D22 | `RuntimeService.kt` | 未配置令牌时没有任何提示，运维不知道当前处于"仅回环"模式 | 启动时 `WARN` 日志 + 引导页状态显示 |

> 另有一处**非阻塞文档不一致**：`DeviceHttpServer` 的 CORS 白名单常量 `ALLOWED_ORIGINS` 未被使用（默认不发 CORS 头，浏览器跨源被拒）。若确实需要 Web 面板直连，按白名单补 `Access-Control-Allow-Origin`，**禁止 `*`**。

---

## Phase 2 · 权限、常驻与自愈

### 动作

1. 按引导页逐项开启：无障碍 → 悬浮窗 → 通知 → 电池优化白名单 → 厂商自启动 / 后台弹出界面。
2. 厂商私有开关跑 ADB 校验（MIUI 例）：

```bash
adb shell appops get <APP_ID> 10021            # 必须含 allow；10021 = MIUI 后台弹出界面
adb shell appops set <APP_ID> 10021 allow
adb shell settings put global stay_on_while_plugged_in 7   # 充电时常亮（第二道保险）
```

3. 屏幕保活与自愈三源验证（见下门禁）。

### 门禁（全部要在真机做）

| 检查 | 怎么做 | 期望 |
|---|---|---|
| 无障碍连接 | 引导页状态 / `GET /api/status` | `accessibilityEnabled=true` |
| HTTP 常驻 | 拔掉 USB 后用 LAN IP 访问 `/api/status` | `httpServerRunning=true`（**执行链不依赖 USB**） |
| 杀进程自愈 | `adb shell am force-stop <APP_ID>` → 打开 App | 服务重新起来；或由通知提醒 |
| 关无障碍自愈 | 系统设置里关掉无障碍 → 再打开 | 状态刷新循环能补拉起；`accessibilityEnabled` 回到 true |
| 重启自愈 | `adb reboot` → 等开机 | Android 14 及以下应自动起来；15+ 若被拒，必须看到"请手动打开"通知 |
| 屏幕保活 | 亮屏放置 30 分钟不动 | 屏幕不熄；用户按电源键息屏被尊重，重新亮屏后常亮恢复 |
| 厂商开关 | `appops get` | 两次校验都为 `allow`（MIUI 覆盖安装后会异步回滚） |

### 停止线

- 电池优化未加白名单 / 自启动未开 → 不进入无人值守测试，先解决权限。
- 目标 ROM 无法开启后台弹出界面或自启动 → 把「必须人工打开 App」写进运行手册，并降级无人值守承诺。

---

## Phase 3 · 可观测与取证工具链

### 动作

```bash
DEVICE_TOKEN=<令牌> APP_ID=<APP_ID> TARGET_PKG=<目标包名> DEVICE_PORT=9527 \
  bash tools/capture-context.sh --serial <SERIAL> --label <页面名>
```

逐个关键页面采集一次，形成 `evidence/<日期>-<页面>/`：`manifest.txt`、`device.txt`、`activity.txt`、
`status.json`、`dump-ui.txt`、`screenshot.png`、`logs.json`、`ids.txt`。

### 门禁

| 检查 | 命令 | 期望 |
|---|---|---|
| 控件树可用 | `curl --noproxy '*' -H "Authorization: Bearer $TOKEN" http://127.0.0.1:19527/api/dump_ui` | `data` 非空且含 `id=` |
| 日志可读 | `… /api/logs?limit=20` | 返回条目，`NET` 级别能看到入站请求且**已脱敏** |
| 截图端点 | `… /api/screenshot` | `success=true` 且有 base64（Android 11+） |
| 取证完整 | 脚本退出码 | `0`；若 `busy=true` 会写 `BUSY_SKIPPED.txt` 并只采必需项 |
| 脱敏自检 | `grep -i "token\|authorization" evidence/**/*.json` | 只应看到 `***REDACTED***` |

### 停止线

- `/api/dump_ui` 一直返回"设备忙" → 说明有轮询器在长期占锁，先修 Phase 4 的忙时策略。
- 日志里出现明文令牌 / 后端 key → **立即停止**，先修脱敏再继续（这是最高优先级缺陷）。

---

## Phase 4 · 编排骨架（并发、队列、总闸）

### 动作

1. 确认 `DeviceLock` 只有一把；所有碰屏幕的路径（HTTP 任务、每个轮询器）都经它。
2. HTTP 写端点异步入队、返 `task_id`；同步端点只留确定短于 2 秒的只读动作，且**非阻塞抢锁**。
3. 业务轮询器继承 `PollerBase`，四道门禁顺序固定：总闸 → 后端就绪 → 前台归属 → 抢锁；忙时**跳过**（不要混用等待）。
4. 总闸：`enabled=false` ⇒ 轮询器早退 + 在跑流程协作式中止 + 写端点 `409 {mode:"manual"}`；切人工时清待执行队列、**保留业务现场**。

### 门禁

| 检查 | 命令 | 期望 |
|---|---|---|
| 异步语义 | `POST /api/read_messages` 后立刻 `GET /api/task_result/<id>` | 首次 `data.success == null`（运行中），随后 `true/false` |
| 忙时拒绝取证 | `busy=true` 时 `GET /api/dump_ui` | `409`（不是 200） |
| 独占锁可观测 | `GET /api/status` | `busy` / `busy_owner` 反映真实归属（owner 不为空） |
| 总闸 | `POST /api/config {"enabled": false}` 后调写端点 | `409 mode=manual`；`enabled=true` 后恢复 |
| 结果落盘 | 下任务 → `am force-stop` → 重开 → `GET /api/task_result/<id>` | 得到 `status=LOST`（不是永远"运行中"） |

### 停止线

- 出现「同一手机两条 UI 流并发」的现象（两处同时 `dispatchGesture`）→ 立即停，检查是否有人绕过锁直接调无障碍原语。
- 结果落盘失败但流程仍返回成功 → 停，先修落盘（第 7.4 项会让上层误判）。

---

## Phase 5 · 目标 App 适配

动作、证据法与停止条件见 `adaptation.md`（这是工作量最大的一阶段，按周估）。

**本阶段的唯一铁律**：所有改动必须落在 ID 登记表内；用 `tools/ui_ids_guard.py` 机械化地把住边界。

---

## Phase 6 · 业务闭环（任务、后端、幂等）

### 动作

1. 每个任务实现为 `TargetOperator` 的一个方法 + `TaskType` 一个枚举 + `handlerRegistry` 一行（三处同步）。
2. 幂等：动作前先判定「是否已完成」，已完成则记成功而非重做。
3. 重试矩阵（第 4.2 节）：

| 动作类别 | 上限 | 关键规则 |
|---|---|---|
| UI 执行（打开/输入/点击） | 1 + 3 | 终态失败落 tombstone + 冷却（例 5min） |
| 单条内容发送 | 3 | 终败保留本地，不上报成功也不重复触发 |
| 结果上报后端 | 3 | **`RESULT_UNKNOWN` 永不重推** |
| 权限类失败 | — | 冷却（例 2min），不要立即重试 |

4. 后端契约集中在一个 `Endpoints` 对象；超时逐层放大（设备内 < SDK < 网关 < 调用方）并表驱动，禁止散落常量。
5. 每个出站调用写 `NET` 级日志（method/url/status/duration/req/resp，脱敏后）。

### 门禁

| 检查 | 期望 |
|---|---|
| 任务 happy path | 真机跑通并留证据包 |
| 任务失败路径 | 失败返回可读 `message` + 取证载荷，不是异常栈 |
| 幂等 | 重复下发同一条任务不会重复产生副作用 |
| 超时矩阵 | 设备侧最坏耗时 < SDK 超时 < 调用方超时，且有余量 |
| 报告未知 | 模拟上报超时，确认不会被重推 |

### 停止线

- 出现「不确定是否成功却重试了发送」→ 立即停（可能重复发给真人，见第 4.2 节铁律）。
- 设备端最坏耗时逼近 SDK 超时 → 先调超时矩阵，不要靠"加大重试"掩盖。

---

## Phase 7 · 验收与交接

见 `acceptance.md`。核心是：24 小时观测、自愈矩阵、安全自检、运行手册、已知限制四件套。

---

## 通用停止线（任何阶段都适用）

1. 需要靠猜测的 ID / 坐标 → 停。
2. 需要绕过锁、绕过鉴权、绕过脱敏才能跑通 → 停。
3. 需要卸载重装才能验证 → 停（卸载会丢授权与身份）。
4. 找不到真机、找不到目标 ROM → 只能交付到 Phase 1 并明确声明未验证范围，**不许把在纸上跑通的流程写成"已验证"**。
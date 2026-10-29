---
name: android-rpa-builder
description: Android 无障碍 RPA 项目构建器：基于 AccessibilityService 生成可远程控制、可无人值守的手机自动化 App（换目标 App、换任务都适用）。当用户要「用无障碍做一个自动操作某 App 的 RPA / 自动化脚本 / 手机群控 / 定时任务 / 远程操作手机 / 自动发消息」，或要给某个 App 做 UI 自动化适配时使用。流程：可行性三问与立项决策 → 脚手架生成可编译工程（设备内嵌 HTTP、前台服务、独占锁、任务队列、取证工具链）→ 权限与自愈门禁 → 证据驱动适配目标 App（ID 登记表 + 防回归 guard）→ 按验收矩阵交付。不适用：非 Android 平台、Web 自动化、不需要无人值守的一次性脚本、只写方案文档（那用 hdr-gate）。
---

# Android 无障碍 RPA 项目构建器

用户提出一个「用无障碍自动操作某个手机 App」的需求后，按本 skill 把这个需求变成**一个可编译、可远程驱动、可无人值守、换目标 App 还能复用**的 Android 工程，而不是一段脆弱的坐标脚本。

核心命题（来自一份生产运行的企业微信 RPA 的抽象蓝图）：

> **目标 App 的不确定性必须被隔离在一层里。** 换目标 App 只重写那一层，其余全部复用。
> 一旦 L2 及以下出现目标 App 的 resource-id / Activity 类名 / 业务文案，"换 App 只改一处"就失效了。

---

## 何时用 / 不用

| 用 | 不用 |
|---|---|
| 做长期运行、可远程控制的手机自动化（RPA / 群控 / 定时任务） | 一次性、不需要无人值守的本机脚本 |
| 要适配一个新目标 App，并希望后续换 App 能复用 | 非 Android 平台、Web 自动化（Playwright/Selenium） |
| 需要设备免线、远程下发任务、取证与回归 | 只想要一份方案文档、不写代码（用 `hdr-gate`） |
| 需要跨 App 文件/图片传递、转人工、定时调度 | 单点 bug 修复、改文案 |

判断不清时先做 Phase 0 的可行性三问（`references/kickoff.md`），**不要先写代码**。

**问 1 必须用 5 分钟预检回答，不许凭经验判断**：脚手架还没落地时，用 `adb shell uiautomator dump`
抓一次目标 App 首页即可（此时还没有自己的无障碍服务，不存在抢窗口问题；RPA 服务一上线就恢复禁令）。
抓到的节点树若"页面上满是文字、XML 里却没几个节点" → 控件树被隐藏，直接按 `adaptation.md` 5.10
的降级通道立项。真实案例：微信 8.0.52+ 就是这样，没做预检导致整架构返工。

---

## 硬规则（违反即返工）

1. **分层边界**：目标 App 的知识（resource-id、Activity、文案阶梯、导航链）只允许出现在 `TargetOperator` 与 `TargetIds` 里。L2 及以下禁止出现目标 App 任何标识。
2. **执行链免 USB**：运行期走「控制面 → 设备 HTTP → 任务队列 → 无障碍 → 目标 App」。ADB 只用于安装、配权限、取证、救砖，**任何业务动作不许走 ADB**。
3. **写操作一律异步**：HTTP 写端点只入队并返 `task_id`，结果另查。同步端点只留确定短于 2 秒的只读动作，且**非阻塞抢锁**，抢不到立即 `409 {busy_owner}`，绝不排队。
4. **一台设备一把锁**：所有碰屏幕的路径（任务、轮询器、人工动作）都经同一把 `DeviceLock`；`owner` 必填（`task:<id>:<type>` / `poller:<name>`），它是唯一可观测性来源。
5. **结果必须落盘**：任务结果内存环形 + 落盘，状态机 `pending/running/done/lost`；`lost` 必须显式存在，SDK 把 `lost` 当失败。
6. **UI 动作不可回滚**：**"不确定是否成功"的动作绝不重试**（重试一次 = 可能重复发给真人）；`RESULT_UNKNOWN` 永不重推。
7. **定位只走语义**：id / text / contentDescription；坐标**只允许作为点击的最后兜底**，禁止用坐标做导航。需要"绝不能点偏"的动作必须关闭坐标兜底。
8. **一个语义一个当前 ID**：禁止 `_PREVIOUS/_OLD/_LEGACY/_CURRENT/_LATEST/_NEW`；历史只靠 Git；ID 改动必须证据齐全且过 guard。
9. **失败是常态**：流程返回 `Boolean` 或结果 DTO（带人类可读 `message`），不抛异常；每个失败点附取证载荷（可见文本摘要 + 窗口概览）。
10. **密钥不外泄**：设备 HTTP 必须鉴权（空令牌只放行回环）；日志写入时脱敏；任何回显端点不回密钥；密钥走 `EncryptedSharedPreferences`；日志/证据不入仓库。
11. **取证三禁令**：禁止 `uiautomator dump`（**唯一例外**是 Phase 0 预检，此时还没有自己的无障碍服务）；`busy=true` 时不 dump / 不截图 / 不点击；中文输入禁用 `adb shell input text`。
12. **只覆盖安装**：永不卸载、永不清数据（卸载会丢无障碍授权与设备身份）。
13. **不动用户仓库的 git**：只在用户指定目录写文件；不 `git init`、不建分支、不提交，除非用户明确要求。
14. **前台归属只信实时信号**：判断"谁在前台"必须用实时根节点 + Activity 可见标志；`TYPE_WINDOW_STATE_CHANGED` 的包名只许进日志。服务重连后缓存可能长期停在旧值——真实项目里同一个坑让**轮询器空转**和**返回键失效**各一次。
15. **一次判定只截一次屏**：同一次页面判定里截两次屏会触发系统限频，OCR 返回空后被误判成"页面不对"。截图一次，尺寸与 OCR 共用。
16. **感知通道可降级、不可伪造**：控件树不可读时按 `adaptation.md` 5.10 走「通知读 / 截图 OCR 看 / 自建输入法写 / 手势点」。禁止伪装包名、注入事件等绕过目标 App 防护的手段。
17. **外部事件验收项一次转交**：只能由真人或外部事件触发的验收（真实来信、真实回调），交付时一次性列成清单 + 一条自检命令交给用户触发。**不得反复空等、反复催促**。

---

## 工作流

| 阶段 | 做什么 | 门禁（真跑） | 详读 |
|---|---|---|---|
| Phase 0 | 可行性三问 + 16 项立项决策 + 一次问完参数 | 三问全过；**5 分钟控件树预检跑过**；决策表填满 | `references/kickoff.md` |
| Phase 1 | 脚手架生成 + 编译 + 单测 | `scaffold.py check` PASS；`assembleDebug`；`testDebugUnitTest` | `references/build-phases.md` |
| Phase 2 | 权限、常驻、自愈三源 | 免 USB 可访问；杀进程/关无障碍/重启各测一次 | `references/build-phases.md` |
| Phase 3 | 可观测与取证工具链 | `dump_ui` 非空；`logs` 已脱敏；`capture-context.sh` 退出码 0 | `references/build-phases.md` |
| Phase 4 | 独占锁、任务队列、轮询器、总闸 | 异步语义；忙时 409；结果重启后为 LOST | `references/build-phases.md` |
| Phase 5 | **目标 App 适配**（工作量最大） | 五步证据法 + guard PASS + 定义集≡引用集 | `references/adaptation.md` |
| Phase 6 | 任务实现、后端契约、幂等重试 | 重试矩阵；幂等；超时逐层放大 | `references/build-phases.md` |
| Phase 7 | 验收与交接 | 24h 观测；安全表；README/runbook/已知限制 | `references/acceptance.md` |

**中途踩坑先查** `references/pitfalls.md`（反模式自查表 + 厂商 ROM 预算 + Android 15 前台服务限制 + 真实案例复盘 P6）。

**真机装包、看控件树、查日志、定位"为什么不触发"** → 用配套 skill `android-rpa-device-ops`（builder 造 / 它来验）。

---

## 收尾（Phase 7 之后）

两件事只能由真机与外部条件触发，**不要在会话里干等**：

- **外部事件项**（真实来信 / 真实回调）：列一张「待用户触发」清单 + 一条自检命令，一次交出去；
  之后最多复核一次。反例：真实项目里为等一条微信来信反复监听十余轮，全部空耗。
- **24h 观测**：必须跑满。中途重启要能归因——启动日志记「启动原因 + 上次心跳时间 + 版本号」；
  归因不了就写「未定位」，不许猜成「偶发、正常」。
- **验收矩阵全绿后停止扩张**：不再自行追加加固项；确有价值的先说明收益并征得同意。

---

## 开始前：一次问完，别多轮打断

把最关键的 2–4 个形态决策放进**一次** AskUserQuestion，每个问题第一个选项标「推荐」：

1. 是否需要业务后端 / 网关（决定架构量级）
2. 触发方式：人工 curl / 定时 / 后端下发
3. 项目落点（新建空目录的绝对路径）
4. 无人值守时长 + 目标 ROM（决定前台服务类型，见 Android 15 限制）

可推导的细节（端口、命名、日志级别）**写成假设继续走**，不要问。必须问清的字段清单见
`references/kickoff.md` 的「需求卡」。

---

## 脚手架

> 下文的 `<skill>` 指本 skill 所在目录（例如 `~/Desktop/MyProjects/my_skills/android-rpa-builder`）。

```bash
python3 <skill>/scripts/scaffold.py scaffold \
  --dest <新建空目录> --app-id com.example.rpa --namespace com.example.rpa \
  --target-pkg com.example.target [--secondary-target-pkg ...] \
  --device-port 9527 [--compile-sdk 36 --target-sdk 36]

cd <dest>
gradle wrapper --gradle-version 8.4      # 或 Android Studio 打开自动生成
./gradlew assembleDebug
./gradlew testDebugUnitTest
python3 <skill>/scripts/scaffold.py check --dest <dest>
```

脚手架做的事：复制模板 → 把源码包目录改名到你的 namespace → 替换 `APP_ID/NAMESPACE/TARGET_PKG/
SECONDARY_TARGET_PKG/DEVICE_PORT/HOST_PORT` 等占位符 → 写 `.rpa-scaffold.json` → 打印下一步。
它**不**跑 git、不装任何东西、不删无关文件。`check` 会兜底扫描残留占位符、包目录、端口一致性与必需文件。

### 生成物一览

| 层 | 文件 |
|---|---|
| L0/L1 | `RuntimeService.kt`（装配根 + 内嵌 HTTP 前台服务）、`ScreenAwakeController.kt`、`BootReceiver`（在 `RpaApplication.kt`） |
| L2 | `RpaAccessibilityService.kt`（动作原语）、`utils/NodeFinder.kt`（无状态门面） |
| L3 | `TargetOperator.kt`（**你要写的主体**）、`TargetIds.kt`（ID 登记表） |
| L4 | `DeviceLock.kt`、`TaskController.kt`、`FileTaskResultStore.kt`、`PollerBase.kt`、`handlers.kt`、`CronSchedule.kt` |
| L5 | `DeviceHttpServer.kt`、`PrefsConfigStore.kt`、`activity/`（引导页、权限引导、分享代理） |
| 工具链 | `tools/capture-context.sh`、`tools/ui_ids_guard.py`、`tools/device_client.py`、`install-to-device.sh` |

`TargetOperator.readMessages/sendMessage` 现在是**骨架占位**（返回"任务未实现"），必须按 Phase 5 换成真实流程。

---

## 蓝图第 9 章的代码不能直接粘

蓝图第 9 章自己标注为「可粘贴代码」，但**实测直接粘贴编译不过**：Kotlin 表达式体里的 `return`、
错误的 `CountDownLatch` 包名、NanoHTTPD 不存在的 `contentLength`/`newFixedLengthResponse`、
JS 风格正则字面量、KDoc 里的 `*/`、`minSdk 24` 配 `java.time` 缺脱糖、`TaskController` 构造参数类型不匹配、
以及一批被引用但没给实现的类。

**用脚手架，不要手工粘。** 完整修正清单（D1–D28）与实测证据在 `references/build-phases.md`
「模板相对蓝图的修正清单」。

---

## 反模式速查（详表见 `references/pitfalls.md`）

- 用 `uiautomator dump` 取控件树 → 抢占无障碍窗口，服务挂起（**仅 Phase 0 预检例外**，见上）。
- `busy=true` 时 dump / 截图 / 点击 → 被模板拦成 409，别绕过。
- 中文用 `adb shell input text` → 乱码。
- 失败立刻重试"结果未知"的发送 → 重复发给真人。
- 结果上报超时重推 → `RESULT_UNKNOWN` 永不重推。
- 卸载重装排障 → 丢授权与身份。
- 坐标导航 / 在注释里留旧 ID / 借适配顺手重构 → guard 会拦。
- 空令牌图省事 → 生产必须配令牌，否则仅回环。
- 无人值守前不跑 24h 观测 → 6h FGS 限时、appop 回滚都只在长跑里暴露。
- 未询问就建分支/提交/推送 → 禁止。
- 用事件缓存的包名判断"谁在前台" → 轮询器空转、返回键失效。
- 同一判定点截两次屏 → 系统限频，OCR 返空被误判。
- 7×24 用 `dataSync` 前台服务 → Android 15 六小时额度耗尽即停。
- 看到通知正文就当真 → 关闭「显示消息详情」后是占位文本，关键词永不命中。
- 等真实事件等十几轮 → 一次转交即可，别空耗。

---

## 资源地图

| 文件 | 读它做什么 |
|---|---|
| `references/blueprint.md` | **规范原文**（第 1–11 章：六层架构、逐子系统规格、UI 引擎、编排、适配方法论、反模式、参考实现、占位符字典）。需要细节时按章查；第 9 章的参考实现已抽成 `assets/templates/`，不要直接粘贴 |
| `references/kickoff.md` | Phase 0：需求卡、可行性三问、16 项立项决策、参数默认值 |
| `references/build-phases.md` | Phase 1–7 施工手册：动作 / 门禁命令 / 停止线 / 模板修正清单 D1–D28 |
| `references/adaptation.md` | Phase 5：A1→D2、五步证据法、ID 登记表、guard、跨 App 文件传递、**控件树不可读时的降级通道（5.10）** |
| `references/acceptance.md` | Phase 7：验收矩阵、24h 观测、生产触发路径、外部事件项、安全自检、交接物 |
| `references/pitfalls.md` | 反模式、厂商 ROM、Android 15 前台服务限制、禁止区、**真实案例复盘 P6** |
| `assets/templates/` | 可直接生成的工程骨架（Kotlin + 脚本），占位符为 `<UPPER_CASE>` |
| `scripts/scaffold.py` | 生成与自检脚手架（零第三方依赖，Python 3.9+） |

---

## 已实测结论（不要把没跑过的说成"已验证"）

- 2026-10-27，本机 Gradle 8.4 + AGP 8.2.2 + JDK 17：脚手架生成的工程在
  `compileSdk 34/36` 两种配置下均 `BUILD SUCCESSFUL`，`testDebugUnitTest` 3/3 通过，
  产物 `app-debug.apk` 约 7 MB。
- 2026-10-29，修正 D23–D28（实时前台判定、`specialUse` 前台服务、占位符误报、深链兜底、
  进程启动日志）后重跑：`BUILD SUCCESSFUL`、单测 3/3、`scaffold.py check` PASS、
  APK 6.7 MB；二次目标 App 与 `BuildConfig.TARGET_PKG` 白名单写法均验证通过。
- 真实项目证据（2026-10，微信自动回复，约 3700 行 Kotlin）：`targetSdk 36` + 微信 8.0.79 真机，
  控件树被隐藏，改走「通知 + 截图 OCR + 自建输入法」，一键验收 10/10，真实来信 14 秒自动回复。
  这只证明该路径可行，**不代表本模板已在真机验证**。
- **未验证**：真机安装、权限引导、无障碍连接、UI 链路、24h 稳定性——这些必须在 Phase 2/7 于目标真机完成。
- 蓝图的 `compileSdk/targetSdk 36` 与 `AGP 8.2.2` 组合可用（会有版本提示）；若本机未装 SDK 36，
  用 `--compile-sdk/--target-sdk` 降到已安装版本，`minSdk 24` 不要动（`dispatchGesture` 下限）。
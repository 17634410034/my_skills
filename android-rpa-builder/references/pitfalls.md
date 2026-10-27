# 反模式、厂商坑与自查表

> 依据：`blueprint.md` 第 7 章、第 2.7 节（厂商 ROM）、第 2.14 节（铁律）。
> 括号里的编号沿用蓝图编号，便于回溯原文。

---

## P1. 架构与安全反模式

| # | 问题 | 后果 | 对策（模板已落实的标 ✅） |
|---|---|---|---|
| 7.1 | 设备 HTTP 无鉴权 + 绑全网卡 + `Access-Control-Allow-Origin: *` | 同网段任何人可驱动手机发消息/改配置 | 令牌校验 + 空令牌只放行回环 ✅ + 不发 CORS 头 |
| 7.2 | 请求体原文写日志（含后端 key）；配置查询端点回显 key | 日志外传即泄密 | 写入时脱敏 ✅ + 令牌走 `EncryptedSharedPreferences` ✅ + 回显字段白名单 ✅ |
| 7.3 | 声明 `RECEIVE_BOOT_COMPLETED` 却无 `BootReceiver`；无障碍被关后无 watchdog | 重启后静默停摆 | BootReceiver ✅ + 周期自检 + 状态端点暴露"上次成功执行时间"（末项需自行补） |
| 7.4 | 任务结果只在内存环形表 | 重启后 `task_id` 永远"没结果"，上层误判仍在跑 | 结果落盘 ✅ + 状态机含 `lost` ✅ |
| 7.5 | 实时画面走 `adb exec-out screencap` | 把运行期拉回 USB 依赖 | 设备侧 `/api/screenshot` ✅，adb 仅兜底 |
| 7.6 | UI 动作里的 `Thread.sleep` 跑在 HTTP 连接线程上 | 连接被占满、误判服务卡死 | 写操作全异步 ✅；同步端点限 2s |
| 7.7 | 配置/状态/密钥混在同一 prefs；复杂结构塞成一个 JSON 字符串 | 改一处即全量覆盖 | 三类分存储 ✅（明文 prefs + EncryptedSharedPreferences + 结果文件） |
| 7.8 | 文档与代码漂移；同名配置模块互相遮蔽 | 按文档实施的人做错 | 端点表从代码生成；配置走外置；CI 校验一致性 |
| 7.9 | `namespace` 与 `applicationId` 不同且文档未标注 | 工具链命令实际不可用 | README 首屏标注"外部只认 applicationId" |
| 7.10 | 厂商 ROM 私有开关被系统安装后异步回滚 | 安装脚本 PASS 但功能不工作（"昨天还能用"） | 双重 set + 双 get 校验（`install-to-device.sh` ✅） |
| 7.11 | 用坐标 / `adb input tap` 写导航逻辑 | 分辨率或布局变更即全线崩 | 规则级禁止：坐标只作兜底；guard 拦 ID 之外的逻辑改动 ✅ |
| 7.12 | 保留"兼容旧 ID"常量与回退分支 | 定位失败被静默掩盖 | 单一当前 ID + 历史靠 Git；guard 拒历史后缀 ✅ |
| 7.13 | 构建配置硬编码开发机代理/绝对路径 | 换机器即构建失败 | 代理出仓；`local.properties` 与 keystore 进 `.gitignore` ✅ |

---

## P2. 厂商 ROM 适配预算（每一项都需要真机时间）

- **appop 回滚**：MIUI/HyperOS 覆盖安装后会异步用自己的权限库回滚"后台弹出界面"。对策：`set → 5s → set → 2s → get → 2s → get`，两次校验任一失败即退出并提示人工（脚本已实现）。
- **后台分享被丢弃**：MIUI 实测丢弃由后台无障碍服务直接 `startActivity(ACTION_SEND)` 的请求。对策：`ShareProxyActivity` + PendingIntent/ActivityOptions 后台启动许可（**模板未完成此项**，见 `adaptation.md` 5.8）。
- **软键盘 flag 删 EditText**：部分 ROM 会把 EditText 从控件树整体移除（非仅视觉隐藏）。对策：**不要设置任何隐藏软键盘/保持触摸模式的 flag**（模板已刻意不设）。
- **控件树刷新慢约 10 倍**：手势后要轮询等待（例 10 × 500ms），不要按 AOSP 节奏写等待。
- **剪贴板隐私弹窗**：部分 ROM 在读取剪贴板时弹提示，会打断流程。对策：写入优先 `ACTION_SET_TEXT`，剪贴板路径只作兜底并留容错。
- **换 ROM 不要假设清单可省**：ColorOS / OneUI / EMUI 各有一套深链与开关。

---

## P3. Android 15+ 前台服务限制（蓝图未覆盖，必须真机核实）

| 事实 | 影响 | 处理 |
|---|---|---|
| `dataSync` 型前台服务在 Android 15 有 **6 小时/24h 累计上限** | 7×24 无人值守会被系统在额度用尽后停止 | 评估改用 `specialUse`（旁加载可用；上架需在 Play 申报用途）或把 `targetSdk` 保持在 34；**上线前在目标 ROM 真机跑够 24h 观测** |
| Android 15 限制从 `BOOT_COMPLETED` 拉起部分 FGS 类型（含 `dataSync`） | 开机自启可能抛 `ForegroundServiceStartNotAllowedException` | 模板 `BootReceiver` 已捕获异常并发"请手动打开"高优先级通知 ✅；无人值守要求高时改用 `specialUse` 或加一条 WorkManager/闹钟兜底 |
| `targetSdk` 决定行为变更是否生效 | `targetSdk ≥ 35` 才会吃到上述限制 | Phase 1 就要决策 `targetSdk`，不要拖到验收 |

> 这些是**行为层面**的限制，编译期不会报错——只能靠真机 24h 观测暴露。

---

## P4. 常见误用自查表

| 症状 | 问题 |
|---|---|
| 直接把蓝图第 9 章代码粘进 IDEA 就编译 | 蓝图有多处 Kotlin 语法错误与 API 错误（见 `build-phases.md` 修正清单 D1–D22）。用脚手架，不要手工粘 |
| 用 `uiautomator dump` 取控件树 | 它会抢占无障碍窗口，导致 RPA 自己的服务挂起或误判。只用 `/api/dump_ui` |
| `busy=true` 时 dump / 截图 / 点击 | 违反铁律；`/api/dump_ui`、`/api/screenshot` 已被模板改成返回 `409` |
| 中文输入用 `adb shell input text` | 会乱码/失败。只走设备 API 的 `ACTION_SET_TEXT` |
| 同一动作失败后立刻重试 | "结果未知"的 UI 动作重试 = 可能重复发给真人。先判定是否已完成 |
| 结果上报超时后重推 | `RESULT_UNKNOWN` 永不重推（第 4.2 节铁律） |
| 卸载重装解决问题 | 卸载会丢无障碍授权、持久化状态与设备身份。只覆盖安装 |
| 坐标导航 | 分辨率/布局一变全线崩。坐标只能兜底 |
| 改 ID 顺手重构周边逻辑 | guard 会 FAIL；适配只允许改 ID 对象内的常量/注释 |
| 在注释里留旧 ID | 会造成旧值被误引用。历史只靠 Git，guard 拒历史后缀 |
| 让令牌为空图省事 | 模板已限制为"仅回环"，LAN 访问会 401。生产必须配令牌 |
| 把日志/证据包提交进仓库 | 含用户内容与令牌。`.gitignore` 已挡 logs/evidence，交付前再核一次 |
| 在无人值守前没跑 24h 观测 | 6h FGS 上限、appop 回滚、内存/队列积压都只在长跑里暴露 |
| 未询问用户就建分支/提交/推送 | 工作区可能混有多条并行工作线。只写用户指定目录，不碰 git |

---

## P5. 禁止区（写进项目 README）

1. 禁止 `uiautomator dump`；控件树只走 `/api/dump_ui`。
2. `busy=true` 期间不 dump、不截图、不按键、不点击、不 tap，只轮 `/api/status`。
3. 中文输入禁用 `adb shell input text`；禁止临时安装输入法或剪贴板工具。
4. 日志 / dump / 截图含密钥与用户内容：不外传原文、不落仓库、外发先脱敏。
5. 同一动作最多尝试两次；相同失败原因第二次出现即停该项并报告。
6. 返回键每次只按一次再验证页面，禁止连按。
7. 任何业务动作不许走 ADB（拔线即断流，ADB 只做安装/权限/取证）。
8. 只覆盖安装，永不卸载、永不清数据。
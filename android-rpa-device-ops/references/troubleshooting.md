# 故障诊断树

诊断顺序固定：**状态 → 证据 → 日志 → 单一变量验证**。每次只改一件事，改完用同一套证据复核。
下面的命令假设已按 `adb-and-api.md` §0/§2 设好 `ADB / SERIAL / BASE / APP_ID`。

---

## T1. 自动化不触发（定时 / 轮询 / 后端下发都没动静）

```bash
curl -sS --noproxy '*' "$BASE/api/status"
curl -sS --noproxy '*' "$BASE/api/task_queue_size"
curl -sS --noproxy '*' "$BASE/api/logs?limit=200" | jq -r '.data[] | [.time,.tag,.message,.detail] | @tsv' | tail -40
```

判据与分支：

| 现象 | 结论 | 处置 |
|---|---|---|
| 日志反复出现"本轮跳过 / RPA App 在前台" | 前台归属误判 | 看 `status.currentApp` 与 `dump_ui` 头部 `pkg=` 是否实时；若代码用事件缓存包名判断，按 builder P6-1 改成实时根节点 |
| 日志"总闸关闭（人工接管）" | 配置态 | 确认是否用户主动接管；不要擅自切换 |
| 日志"设备被占用 busyOwner=…" 且长期不变 | 任务卡死 | 转 T2 |
| 完全无轮询日志 | 轮询器没启动 | 查 `RuntimeService` 启动日志；确认 pollers 装配 |
| 队列有值但无执行 | 单线程执行器未消费 | 查 `TaskController` 线程与 `DeviceLock` 持有者 |

**只用手动 API 验过不算数**：必须打生产触发路径（定时/轮询/后端），手动接口只是调试通道。

---

## T2. 写端点 409 / 任务不结束 / 结果 LOST

```bash
curl -sS --noproxy '*' "$BASE/api/status" | jq '{busy,busy_owner,data:{queue:.data.taskQueueSize}}'
curl -sS --noproxy '*' "$BASE/api/task_result/<task_id>"
"$ADB" -s "$SERIAL" shell pidof "$APP_ID"
"$ADB" -s "$SERIAL" shell dumpsys activity activities | grep -E 'topResumedActivity' | head -1
```

- **409 + `busy_owner`**：409 是保护不是故障。owner 命名应能看出是谁（`task:<id>:<type>` / `poller:<name>`）。
  若 owner 对应的任务已不存在且长时间不释放 → 任务线程卡死，重启 App（覆盖安装不需要），并在报告里记一项。
- **`status=LOST`**：执行中进程重启，结果未知。**这是设计行为**，不是 bug。不要重发；按"结果未知不重试"处理。
- **`data.success=null` 且任务长时间不结束**：轮询 `task_result`，同时看目标 App 是否停在权限弹窗/锁屏；
  单个任务超时上限由 `TaskController` 与动作超时表决定，超时后应落 tombstone。
- 恢复前确认：`busy=false`、队列空、无障碍在线。恢复动作只做一次。

---

## T3. 感知异常（dump 空 / OCR 返空 / 截图失败）

```bash
curl -sS --noproxy '*' "$BASE/api/dump_ui" | head -c 500
curl -sS --noproxy '*' "$BASE/api/screenshot" > /tmp/shot.json
"$ADB" -s "$SERIAL" shell dumpsys window | grep -E 'mCurrentFocus|mFocusedApp'
```

分支：

1. **dump 空但截图正常** → 目标 App 隐藏了控件树（微信 8.0.52+ 这类）。不是故障，是架构分岔：
   确认是否已启用通知/OCR 通道（builder `adaptation.md` 5.10）。同机对照一个系统 App 的 dump 可证明是目标 App 的问题。
2. **dump 和截图都空** → 先看 `busy`：`busy=true` 时两个端点都返 409，属正常保护。
   再看 `accessibilityEnabled=false` → 无障碍掉线（T4）。
3. **OCR 突然整页空** → 90% 是**同一判定点连续截了两次屏**触发系统限频。
   检查代码是否在一次判定里既取尺寸又取 OCR；合并成一次截图。手动验证时不要连点 screenshot。
4. **截图失败但 dump 正常** → Android 11 以下不支持 `takeScreenshot`；或无障碍服务刚重连。
5. **页面判定"不在目标页"但人眼看就在** → 先 dump，再截图；两者不一致时以 dump 的 `pkg=` + `text` 为准，
   并核对等待时间（厂商 ROM 控件树刷新可能慢 10 倍）。

停止线：连续两次相同原因失败即停，收集现场后报告，不换坐标/换等待时间碰运气。

---

## T4. 无障碍掉线 / 服务假活

```bash
"$ADB" -s "$SERIAL" shell pidof "$APP_ID"
curl -sS --noproxy '*' "$BASE/api/status" | jq '.data.accessibilityEnabled'
"$ADB" -s "$SERIAL" shell settings get secure enabled_accessibility_services
"$ADB" -s "$SERIAL" shell settings get secure accessibility_enabled
```

- `accessibilityEnabled=false`：服务被系统回收或用户关闭。看门狗会在已授权时重绑；仍不恢复则
  `am force-stop` 后重新 `am start`（**不要卸载**，卸载会丢授权）。
- 服务在线但所有动作失败：可能 root 节点为空/输入法抢占。用 `dump_ui` 头部的 `pkg=` 判断真实前台。
- 自愈失败时保留日志与截图，报告"无障碍未恢复"，不要连续重启碰运气。

---

## T5. 输入与点击不生效

```bash
"$ADB" -s "$SERIAL" shell settings get secure default_input_method
curl -sS --noproxy '*' "$BASE/api/logs?limit=200" | jq -r '.data[] | select(.message|test("输入|input|Ime")) | [.time,.message,.detail] | @tsv'
```

| 现象 | 根因 | 处置 |
|---|---|---|
| 点了没反应 | 坐标来自旧 dump；页面已滚动/弹层 | 动作前重新 dump 并重新解析节点；坐标只作兜底 |
| 输入框有焦点但无文字 | 默认输入法被切回系统输入法 | 发送前 `ensureDefault()`；失败则取消发送，不硬发 |
| 偶发 `commitText` 不生效 | 输入法连接瞬时断开 | 排队 + 250ms×6 重试；仍失败记可读原因 |
| 聊天页停在"按住说话" | 语音模式没有输入框 | 先点回键盘；切换失败就取消 |
| 发送按钮点不到 | 图标与文字粘连、按钮不可见 | 用节点 bounds 最右区域；不可见时滚动一次 |

中文输入永远不走 `adb shell input text`。

---

## T6. 通知不进箱（通知触发型任务）

```bash
curl -sS --noproxy '*' "$BASE/api/status" | jq '.data | {notificationsGranted, notificationsTotal, notificationsFromTarget, notificationDetailLikelyHidden}'
"$ADB" -s "$SERIAL" shell cmd notification allow_listener | grep "$APP_ID"
"$ADB" -s "$SERIAL" shell dumpsys notification_enums | head -40   # Android 13+ 可查监听器
```

判据：

| 指标 | 含义 | 处置 |
|---|---|---|
| `notificationsGranted=false` | 未授权通知使用权 | `adb shell cmd notification allow_listener <pkg>/<service>`，或手动授权 |
| `notificationsTotal` 不涨 | 监听回调没工作 | 检查监听服务是否声明、进程是否存活；已授权时主动 `requestRebind` |
| `notificationsTotal` 涨、`notificationsFromTarget=0` | 包过滤错误 | 核对目标包名（`BuildConfig.TARGET_PKG`）与通知 `packageName` |
| 进箱但关键词不命中 | 正文是占位文本 | 目标 App 关闭了"显示消息详情"；系统应置 `notificationDetailLikelyHidden` 并告警 |
| 全对但没触发 | 白名单/开关/过期拦截 | 查策略日志：`ignored: 原因`；确认消息未超过过期阈值 |

自造通知（`adb shell cmd notification post`）只能证明"回调活着 + 包过滤生效"，**不能证明真实来信链路**。
真实事件项按"一次转交"处理：给触发方式 + 自检命令，不要反复开监听空等。

---

## T7. 长跑中断 / 夜间自己停了

```bash
curl -sS --noproxy '*' "$BASE/api/logs?limit=1500" | jq -r '.data[] | select(.message|test("进程启动|心跳")) | [.time,.message,.detail] | @tsv'
"$ADB" -s "$SERIAL" shell dumpsys deviceidle whitelist | grep "$APP_ID"
"$ADB" -s "$SERIAL" shell appops get "$APP_ID" 10021
"$ADB" -s "$SERIAL" shell dumpsys package "$APP_ID" | grep -E 'versionName|lastUpdateTime'
```

按顺序排除：

1. **前台服务类型**：`dataSync` 在 Android 15+ 有 6 小时/日上限；应为 `specialUse`（builder D25）。
2. **厂商杀后台**：电池白名单、自启动、后台弹出界面（appops 10021）被覆盖安装后回滚。
3. **内存/队列积压**：PSS 与队列长度曲线；`lost` 任务堆积说明每次执行都超时。
4. **重启归因**：启动日志必须有 pid + 版本；配合心跳断点判断是"被杀"还是"崩溃"。
   查不出就写"未定位"，不许猜成"偶发、正常"。
5. 24h 观测没跑满，不得对外说"可无人值守"。

---

## T8. 构建 / 安装失败

见 `adb-and-api.md` §6 的错误表。补充两条：

- 单测与构建**分开跑**，先看单测失败是不是本轮引入；
- `local.properties` 的 `sdk.dir`、`JAVA_HOME`、Gradle wrapper 版本三者是换机后最常见的三类失败，
  先核这三项再动代码。

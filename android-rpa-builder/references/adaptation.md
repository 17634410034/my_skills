# 目标 App 适配方法论（Phase 5 详解）

> 依据：`blueprint.md` 第 5 章（适配流程）、第 3 章（UI 自动化引擎）、第 3.7 节（ID 登记表）、第 9.13 节（guard 脚本）。
> 一句话：**把「给新目标 App 做适配」当成有向流程走，不是探索。所有定位靠证据，不靠经验。**

---

## 5.1 阶段划分（A1 → D2）

换成任何目标 App 都成立，只是含义换成该 App 的页面：

| 阶段 | 通用含义 | 以 IM 类目标 App 为例 |
|---|---|---|
| A1 | 冷启动到达主界面 | 启动、进主 tab、处理首启弹窗/登录态 |
| A2 | 列表/入口定位 | 会话列表 + 未读标记识别 |
| B1–B3 | 打开对象 → 读取内容 → 基础写 | 进会话、读消息、发消息 |
| C1–C4 | 高级/多模态能力 | 图片存取、@提及、引用回复、成员管理 |
| D1–D2 | 跨 App 边界与清理 | 系统分享面板、历史清理 |

一次只推进一个阶段，每阶段结束留**真机证据包**，再进下一阶段。

---

## 5.2 每阶段固定五步（禁止跳步）

1. **从代码生成待采清单**：对 `TargetIds` 的定义与全部引用点做 `rg`，清单来自代码，不来自人脑。
2. **基线快照**（在动任何东西之前）：

```bash
python3 tools/ui_ids_guard.py snapshot \
  --repo . \
  --target app/src/main/java/<ns>/service/TargetIds.kt \
  --object TargetIds --id-prefix "<TARGET_PKG>:id/" \
  --state ~/.rpa-baseline/$(date +%F)-session.json
```

3. **采集证据**（逐页）：

```bash
DEVICE_TOKEN=… APP_ID=… TARGET_PKG=… bash tools/capture-context.sh --serial <S> --label <页面名>
```

逐页记录：class / text / desc / visible / clickable / enabled / parent / 可点击祖先 / 命中数。

4. **只改一个当前常量**，然后最小回归（单测与 `assembleDebug` **分开跑**）。
5. **收尾校验**：

```bash
rg -n "_PREVIOUS|_OLD|_LEGACY|_CURRENT|_LATEST|_NEW" app/src/main   # 必须为空
# 定义集 ≡ 引用集（必须零差集）
rg -o '\bTargetIds\.[A-Z0-9_]+' app/src/main -rN | sed 's/^TargetIds\.//' | sort -u > /tmp/ref.txt
awk '/object TargetIds/{f=1;next} f&&/^}/{f=0} f' app/src/main/java/<ns>/service/TargetIds.kt \
  | rg -o 'const val ([A-Z0-9_]+)' -rN '$1' | sort -u > /tmp/def.txt
comm -3 /tmp/def.txt /tmp/ref.txt        # 必须为空
git diff --check
python3 tools/ui_ids_guard.py check --repo . --state ~/.rpa-baseline/<...>.json
# 覆盖安装（绝不卸载）+ 只对本轮改动做针对性回归 + 停在初始页面、恢复初始筛选
```

### 停止条件（必须原样继承）

出现以下任一情况 ⇒ 报「**需要逻辑代码修改，本次 ID 适配未处理该项**」，不许猜、不许顺手重构：

- 节点无稳定 ID；
- 需要改等待、手势、坐标、状态机或权限才能定位；
- 正确节点被现有逻辑拒绝。

### 授权门禁

真实动作（发送、保存、改备注、删除、清空）**逐项**获得用户授权；未授权只采集到该动作前一页。

### 唯一事实源

一个目标 App 只允许一份现行方法论文档；旧版要么删除，要么显著标注作废并说明冲突点。

---

## 5.3 ID 登记表制度（最重要的可维护性设计）

把目标 App 的全部 `resource-id` 集中到 `TargetIds` 一个对象里，规则：

1. **按页面分区**（主页 / 会话页 / 设置页…），每条 `const val` 尾注释写：目标 App 版本+构建号、页面、用途、测试日期、设备序列号。
2. **一个语义只留一个当前 ID**。禁止 `_PREVIOUS/_OLD/_LEGACY/_CURRENT/_LATEST/_NEW` 之类兼容常量，注释里也不许留旧值——**历史只靠 Git**。
   - 理由：回退引用会让定位失败被静默掩盖，旧 ID 与新 UI 错配比"找不到"更难查。
3. **原始 ID 允许跨语义重复**（目标 App 常复用布局）。登记表按语义组织，不做值去重。
4. **定义集 ≡ 引用集**：`rg` 扫全部引用与定义名做集合差，必须零差集。
5. 只凭证据改 ID：Activity + dump_ui + 截图 + 日志齐了才动常量。

执行必须机械化（`tools/ui_ids_guard.py` 用规则拦，不靠人守）。guard 的性质：快照之后
**只允许 ID 登记表对象内的 `const val` 字符串与注释变化**，其余任何逻辑行改动一律 FAIL，
并拒绝历史后缀常量与重名常量，同时校验常量值以目标包 ID 前缀开头。

---

## 5.4 定位健壮性四条（"偶发找不到元素"的根因都在这里）

1. **跨窗口搜索兜底**：`findById` 在活动窗口未命中时，自动遍历 `windows[*].root` 再查。PopupWindow / Dialog / 浮层列表常不在活动窗口树里，且是否在同一次调用里可见**不确定**（同一代码同一机型时好时坏就是这个原因）。
2. **节点引用会失效**：手势后节点需 `refresh()` 重取；点击前用谓词**重新解析**目标，不要复用早前引用。
3. **遍历必须有上界**：DFS/BFS 限定 `maxNodes`（例 128）与 `maxDepth`（例 20），超限即放弃并记录——未知 view-id 的试探可能把 Binder 调用卡住数分钟。
4. **不可见/禁用节点要过滤**：候选校验用 `isEnabled && isVisibleToUser`；文本命中但不可见通常是列表外缓存或回收单元格。

---

## 5.5 页面判定：三信号并用

| 信号 | 形态 | 用于 |
|---|---|---|
| (a) 元素集合存在性 | 任一/多数命中（输入框 ∥ 输入栏 ∥ 发送键 ∥ 标题+更多…） | 粗判"我在哪类页面" |
| (b) 精确标签集合 | 一组标签全部命中（成功页、编辑器页、选择器浮层） | 判定动作完成 |
| (c) Activity 类名/包名 | `currentClassName.contains(...)`、根包名校验 | 多页面共用同一 Activity、需区分浮层时的裁决 |

补充：**几何分类器**——列表区被侧栏/浮层遮挡时，"没找到条目"会被误判成"列表为空"。用 bounds
把列表区分类为 `USABLE / COVERED / INVALID` 再决定结论可信度。

`currentPackage/currentClassName` 通常只在 `TYPE_WINDOW_STATE_CHANGED` 更新，**是缓存值**；
服务重连后它可能长期停在旧值——真实项目里因此让轮询器空转、返回键失效各一次。需要实时时用
`rootInActiveWindow.packageName`（模板 `livePackageName()` / `isSelfForeground()` / `isTargetForeground()`
就是干这个的，缓存值 `lastEventPackage` 只许进日志）。

---

## 5.6 交互基元的使用规范

- **点击降级阶梯**：`isClickable` → 沿 parent 上溯第一个 clickable → 坐标兜底（可由 `allowCoordinateFallback=false` 显式关闭）。需要"绝不能点偏"的动作（从候选列表选唯一项、@提及选人）**必须关闭坐标兜底**。
- **坐标只允许作为兜底，禁止作为定位方式**。"按 dump 里的 bounds 写死坐标导航"属于越界，评审时拦掉（guard 用规则拦，不靠人）。
- **等待必须是业务参数**，禁止 `sleep(N)` 式硬等待：每个等待要有 超时 + 轮询间隔 + 失败原因字符串三件套。
- **滚动"到底"用签名收敛法**：滚动 → 计算可见内容签名 → 连续 N 次不变（例 2 次）即认为到底，比"固定滑 N 次"稳。
- **返回栈防护**：`safePressBack()` 在 RPA 自己的 App 在前台时必须拒绝；回主页面用有界 N 次返回（例 6），仍失败则 relaunch 目标 App；禁止无限连按。
- **错误不用异常体系**：流程返回 `Boolean` 或结果 DTO（带人类可读 `message`）。UI 自动化里"失败"是常态。只在最外层 catch 一次并记日志。
- **每个失败点附带取证载荷**：当前屏可见文本摘要 + 窗口概览写进日志。没有取证载荷的失败等于无法复现的失败。

---

## 5.7 可测试性：判定与执行彻底分层

验收基准（来自生产项目）：**UI 逻辑抽出 20+ 个纯判定函数、近百个单测、纯 JUnit4、零 Robolectric/零 Mockito**。

- 判定抽成 `companion object internal fun`，入参**只允许 `String/Int/Boolean/List` 等 JVM 类型，绝不传 `AccessibilityNodeInfo`**。
- 独立的纯类放纯算法：格式化器、消息组装器、内容角色分类器。
- 单测里**永不实例化 Operator**；测试命名按行为，一个规则一个用例，直接内联真实设备 bounds 数值当夹具。
- 点击/手势等设备侧逻辑零覆盖，靠真机回归。**测试预算据此分配：判定层写单测，执行层写回归清单。**
- 模板里 `TargetOperatorTest` 已给出范式；新建的判定函数照抄这个形状。

---

## 5.8 跨 App 文件与媒体传递

任何"把图片/文件交给目标 App"的任务都会撞上这组问题：

1. **URI 共享**：`FileProvider`（authority `${applicationId}.fileprovider`，`file_paths.xml` 只暴露 `cacheDir`）+ **显式 `grantUriPermission(目标包, uri, flags)`**。
2. **后台发起分享会被部分 ROM 丢弃**（MIUI 实测）。解法：无 UI 代理 Activity（`noHistory`、`excludeFromRecents`、透明主题）+ `PendingIntent.send()` 配合 `ActivityOptions` 后台启动许可 + 「静态 requestId 握手 + 轮询确认 `onCreate` 真的跑过（≈2s）」。
   - 模板 `ShareProxyActivity` **只完成了代理与握手**，后台启动许可**尚未实现**。未补之前不要用于无人值守分享；`awaitHandled()` 返回 false 时必须视为失败并提示"请开启后台弹出界面权限"。
3. **完成校验要两路并行**：分享成功页的标签集合 **且** 业务页面快照 diff。`startActivity` 返回 ≠ 页面已替换。
4. 兜底路径：`createChooser` + 在系统分享面板里按标签优先级选目标 App。
5. **入站媒体识别**：气泡常无文本可依据，只能结构化识别（id ∥ ImageView 类 ∥ desc 含"图片" + 最小尺寸 + 排除顶栏 + 位于输入栏之上）；保存动作走**标签阶梯**（不同版本用词不同），并处理"再次保存"确认弹窗。
6. **文件发现**：轮询 MediaStore + 文件系统（时间戳 ≥ 动作开始 - 3s）约 10s，拷进 `cacheDir`，并受媒体读权限门禁。
7. **系统分享面板的 id 永远不要写进 ID 登记表**（它是系统 UI，不属于目标 App，写进去会造成假适配）。

---

## 5.9 适配完成判定

- 每条任务都有真机证据包与回归记录；
- `ui_ids_guard.py check` PASS，且 `rg` 历史后缀为空、定义集 ≡ 引用集；
- 判定函数有单测，执行层有回归清单；
- 停在初始页面、恢复初始筛选状态；
- 未处理项（需要逻辑修改的）逐条列进「已知限制」。

---

## 5.10 控件树不可读时的降级通道（真实案例总结）

目标 App 主动隐藏无障碍树时（微信 8.0.52+），ID 适配整条路走不通，改用四条通道组合：
**通知读 / 截图 OCR 看 / 自建输入法写 / `dispatchGesture` 点**。这时的质量重心从「ID 登记表」
转移到「OCR 解析纯函数 + 发送前复核」。

### 硬约束

1. **解析必须纯函数**：入参只允许 `List<OcrLine>`（文本 + bounds），不传 `AccessibilityNodeInfo`；
   用真实设备 OCR 输出当夹具写单测（真实案例 16 个版面解析用例 + 5 个文本容错用例）。
2. **文本容错只做三类**：繁体/异体字、等长单字差异、全角标点。**不等长名称一律不容错**
   （张三 ≠ 张三丰）；多候选命中时取最可信并记录，不静默挑一个。
3. **一次判定只截一次屏**：同一次判定里取屏幕尺寸与做 OCR 必须复用同一张截图。连续截图会被系统限频，
   OCR 返回空 → 被误判成"页面不对" → 放弃发送。截图失败按 3 次退避（500/1000ms）。
4. **写前复核（防答非所问）**：发消息前确认三件事——顶部标题等于会话名；消息区最后 3 行仍含源消息
   （群聊兼容「发送者: 内容」前缀）；输入框草稿已清空。对不上就放弃，宁可不回。
5. **发送后复核是弱证据**：只校验"话术出现在消息区右侧"，不区分气泡归属；**复核失败绝不重发**。
6. **图标入口**：优先 `contentDescription`；没有文本时用屏幕比例锚点，且必须作为"最后兜底"登记进已知限制。
7. **通知正文可能被隐藏**：目标 App 关闭「显示消息详情」后正文是占位文本，关键词永不命中。
   必须显式告警（写 WARN + `/api/status` 置诊断位），不能静默跳过。
8. **通知链路要能证活**：暴露 `notificationsTotal` 与 `notificationsFromTarget` 计数；发一条测试通知
   验证回调在跑、包过滤生效。掉线且已授权时主动 `requestRebind`（进程被杀/权限变动后系统不会自动重连）。
9. **锁屏即停**：锁屏时手势全部无效。显式判定并返回「设备已锁屏」，不要含糊成"主界面不可达"。
10. **页面态分支**：聊天页可能停在「按住说话」语音模式，输入前先切回键盘，切换失败则取消发送。

### 验收差异

- `ui_ids_guard.py` 不适用（没有 ID 可管）——在验收报告里**写明原因**，不要静默跳过；
- 质量门禁改为：OCR 解析单测 + 真机回归（每轮验收脚本覆盖一次真实发送与一次轮询自动消费）；
- 已知限制里必须写明：耗时（8–15 秒/次）、OCR 误差、APK 体积、目标 App 升级风险。
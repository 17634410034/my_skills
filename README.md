# my_skills

个人 Agent Skill 集合。每个子目录是一个可独立安装、独立使用的 Skill，互不依赖。

| Skill | 一句话 | 什么时候用 |
|---|---|---|
| [hdr-gate](#hdr-gate--异构双审门禁heterogeneous-dual-signoff-gate) | 复杂需求先产出一份**陌生 agent 能独立执行**的开发文档 | 跨 3 个以上文件、涉及架构/数据模型、要引入新依赖，或用户明确要方案 |
| [android-rpa-builder](#android-rpa-builder--android-无障碍-rpa-项目构建器) | 把「自动操作某 App」落成可编译、可远程驱动的 Android 工程 | 无障碍 RPA、手机群控、定时任务、换目标 App 适配 |
| [android-rpa-device-ops](#android-rpa-device-ops--android-rpa-真机调试与运维) | builder 的配套 skill：装到真机、取证、按症状排障 | 工程跑不起来、不触发、控件树为空、结果 LOST、适配回归 |
| [mcp-tool-selection](#mcp-tool-selection--mcp-工具注册表设计与工具检索) | 工具多到选不准时，先补工具卡而不是先上架构 | 工具超过 50 个、schema token 膨胀、要评估工具检索 |

---

## hdr-gate — 异构双审门禁（Heterogeneous Dual-Signoff Gate）

一份**能被陌生 agent 独立执行的开发文档**是怎么产出的：不是让一个模型写完就算，而是让**不同厂商的模型背对背审**，再让**完全不了解项目的人空跑一遍**，逼出文档里所有「作者以为写清楚了」的洞。

核心命题：

> 文档的验收测试，是**另一个 agent 的执行过程**。作者和审稿人都会靠先验脑补，只有一无所知的人不会。

### 它解决什么问题

模型自己写文档、自己审，会陷入自洽偏差——它看不出的错，审的时候照样看不出，因为盲区是同一个。换模型族等于换了一套训练数据和偏好，盲区不再重合。这跟航天器用两家不同厂商的芯片做冗余是同一个道理。

### 流程（当前为 v2.7）

| 阶段 | 内容 | 谁做 |
|---|---|---|
| -2 | **运行环境体检**：密钥注入方式、`doctor` 误报识别、文档里的构建命令先在本机跑一遍 | 主持 |
| -1 | **澄清预算**：歧义写成编号假设继续走；只有会改变方案形态的决策才问 | 主持 |
| 0 | 门禁判定 + 关键形态决策，合并成一次交互 | 主持 |
| 1 | 分析项目并落 `recon.md`（行号级事实底稿）→ 写 v0.1；**默认主持亲写** | 主持 / Agent1 |
| 2 | Agent2 ∥ Agent3 **背对背并行双审**（互不可见） | GLM + DeepSeek |
| 3 | Agent4 机械归并 + 主持分级复核，产出溯源表 | Agent4 + 主持 |
| 4 | 拿合并清单**一次性**改到 v0.2 | 主持 / Agent1 |
| 5 | 分歧裁定（P0 僵局交人，不自行放行） | 主持 |
| 6 | 复审双签 → v0.3（返工上限 2 轮） | GLM + DeepSeek |
| 7 | **双冷读**：A 路异族 API 纯文档冷读 ∥ B 路宿主侧只读 subagent（可读仓库、抓事实性错误） | GLM API + 宿主 subagent |
| 8 | 只增信息补齐 → v1.0，并立刻落到用户指定路径 | 主持 / Agent1 |
| 交付 | 声明评审强度、文档路径、遗留项；**不开工写代码** | 主持 |

### 九条铁律

1. **先判定，再动手。** 不判定复杂度就直接开写，是最常见的误用。
2. **歧义不阻塞判定。** 判定只看需求规模，不依赖细节；先按显式假设判定，再一次性确认。
3. **双审必须背对背。** 串行评审会被锚定，等于只审了一遍。
4. **合并去重与分级分离。** 归并是体力活，定级是判断；主持兼任归并必须标 `MERGED-BY-HOST` 并声明。
5. **冷读者不许问，作者不许答。** 一旦口头补答，补的是对话不是文档，这次试跑就废了。
6. **交付文档即终止。** 本 skill 结束时不动一行代码。
7. **长任务不许脱手。** 慢阶段必须可观测、可续跑，并在同一回合内等到落盘；挪到后台就结束回合，等于让任务死在半路。
8. **落点与产物处置要问。** 首次在某项目使用，先读既有文档命名规则，再问「最终文档放哪 + 中间产物要不要清理」。
9. **交付路径不许空着。** 用户提前建好空占位文件时，双签一通过就写进去；不要等第 8 步。

### 实测踩出来的三条硬约束

- **长文档不要交给单次 API 生成。** 瓶颈是输出长度，不是上下文体积。上下文压到 22K、`max_tokens` 降到 14000、`timeout` 抬到 900s，qwen3.8-max 起草仍 1000s 无响应；同通道的 review/coldread 都在 77–345s 返回。draft / fix / finalize 默认由主持自己写。
- **推理模型会把配额花在思维链上。** glm-5.3 一次评审 `out=17684`，正文只有 3.8K；配额给 16000 时直接返回空正文。现默认 **agent2/agent3 = 32000、agent4 = 24000、coldread = 40000**，同时用 prompt 里的 `OUTPUT_TERSE_*` 压正文体量。空/截断产物走 `hdr.py retry`，不要手写脚本调 `llm.chat`（会绕过校验和成本记账）。
- **单路冷读抓不到事实性错误。** `hdr.py coldread` 是纯 API 调用，没有仓库读权限，只能查字面歧义；「文档写的行号/签名/命令是否真的存在」必须由宿主的只读 subagent 另跑一路。两路都跑，缺口合并后再进第 8 步。

### 目录

```
hdr-gate/
├── SKILL.md              # 主指令：九条铁律 / 11 个阶段 / 长任务协议 / 自查表
├── sop-full.md           # 完整 SOP v2.7（含版本变更记录）
├── dev-doc-template.md   # 开发文档骨架（任务卡 / 契约变更 / 假设账本 / 禁止区）
└── tools/                # 零第三方依赖，只用 Python 标准库
    ├── README.md         # 工具用法：MCP Server 与 CLI 两种驱动方式
    ├── hdr.py            # 编排 CLI
    ├── llm.py            # 多 provider 统一客户端（OpenAI 兼容协议）
    ├── prompts.py        # 各角色提示词
    ├── mcp_server.py     # MCP stdio server
    └── config.example.toml
```

`config.toml`、`.hdr-projects.json`、`.hdr-gate/` 都是运行期产物，已在 `.gitignore` 中排除。

### 快速开始

```bash
cd hdr-gate/tools
cp config.example.toml config.toml

# 密钥走环境变量，不要写进文件
export TOKENPLAN_API_KEY="sk-xxx"

# agent 的非交互 shell 不读 ~/.zshrc：密钥写在 .zshrc 里时，每条命令都要重新注入
# export TOKENPLAN_API_KEY="$(zsh -c 'source ~/.zshrc >/dev/null 2>&1; printf %s "$TOKENPLAN_API_KEY"')"

python3 hdr.py models --provider tokenplan   # 列出通道里的真实模型 id，核对 config
python3 hdr.py doctor                        # 自检：通道 / 角色绑定 / 异质性（缺密钥退出码 2）

python3 hdr.py init --name 需求名 --repo /path/to/repo --req "需求原文" \
  --assume "A1|平台=当前应用|需求原文写'这个平台'"
python3 hdr.py draft    --name 需求名
python3 hdr.py review   --name 需求名     # 并行双审
python3 hdr.py merge    --name 需求名
python3 hdr.py fix      --name 需求名
python3 hdr.py sign     --name 需求名
python3 hdr.py coldread --name 需求名     # 只跑 A 路；B 路用宿主只读 subagent
python3 hdr.py finalize --name 需求名
python3 hdr.py status   --name 需求名

# 空正文 / 被截断：重跑单个角色，成本照常入账
python3 hdr.py retry --name 需求名 --stage review-a --doc v0.1.md --max-tokens 32000
```

要求 Python 3.11+（用到内置 `tomllib`）。产物落在**执行命令时的 cwd** 下 `.hdr-gate/<需求名>/`，全部命令固定在目标仓库根执行。

### 默认模型配置

| 角色 | 模型 | 族 | 默认配额 |
|---|---|---|---|
| Agent1 作者 | qwen3.8-max | qwen | 32000 |
| Agent2 可行性审 | glm-5.3 | zhipu | 32000 |
| Agent3 正确性审 | deepseek-v4-pro | deepseek | 32000 |
| Agent4 合并去重 | qwen3.8-flash | qwen | 24000 |
| 冷读试跑者 | glm-5.2 | zhipu | 40000 |

**异质性看「模型族」，不看「通道」。** 同一个通道里不同族的模型，双审依然有效；同族同模型只是换了会话，脑补习惯一模一样。`doctor` 若因 fallback 缺 key 报「无可用通道」，先按族直查再决定是否降级。

典型成本 **13–15 次 API 调用**（含 1 轮返工与空正文重试）。

---

## android-rpa-builder — Android 无障碍 RPA 项目构建器

把一句「帮我做个能自动操作某某 App 的 RPA」，变成一个**可编译、可远程驱动、可无人值守、换目标 App 还能复用**的 Android 工程——而不是一段会在布局变更后全线崩掉的坐标脚本。

核心命题：

> 目标 App 的不确定性必须被隔离在一层里。换目标 App 只重写那一层，其余全部复用。一旦下层出现目标 App 的 resource-id / Activity 类名 / 业务文案，「换 App 只改一处」就失效了。

### 关键能力

1. **可行性判定**：动手前先做「控件树可读吗 / 动作能用点击输入滚动表达吗 / 合规与风控怎么过」三问，任一条不过就停，不写代码。控件树用 **5 分钟 `uiautomator dump` 预检**回答，不靠经验；微信 8.0.52+ 就是控件树不可读、没预检导致整架构返工的真实案例。
2. **可编译脚手架**：一条命令生成完整工程——设备内嵌 HTTP 前台服务、无障碍动作原语、独占锁、异步任务队列、结果落盘、屏幕保活、权限引导、引导页、取证脚本与防回归 guard。
3. **分层与边界**：六层模型（设备接入 / 运行时 / 动作原语 / 页面语义 / 编排 / 控制面），L3 是唯一允许出现目标 App 知识的层，落地时锁在 `TargetOperator` + `TargetIds` 两个文件里。
4. **感知降级通道**：目标 App 隐藏控件树时，按「通知读 / 截图 OCR 看 / 自建输入法写 / 手势点」重建感知层，并给出对应的验证与防误发纪律。
5. **证据驱动适配**：A1→D2 分阶段推进，每阶段五步（代码生成清单 → 基线快照 → 采证据 → 只改一个 ID → 收尾校验），配 `ui_ids_guard.py` 机械化拦截越界改动。
6. **幂等与重试纪律**：「UI 动作不可回滚」——结果未知绝不重试、`RESULT_UNKNOWN` 永不重推，避免把消息重复发给真人。
7. **安全默认值**：设备 HTTP 强制令牌鉴权（空令牌只放行回环）、日志写入时脱敏、密钥走 EncryptedSharedPreferences、开机自启失败有通知兜底。
8. **验收与交接**：24 小时观测、自愈矩阵（杀进程 / 关无障碍 / 重启）、生产触发路径必测、外部事件项一次转交、安全自检、运行手册与已知限制清单。

共 **17 条硬规则**，覆盖分层边界、免 USB 执行链、异步写端点、单设备单锁、结果落盘、实时前台判定、单次截图、取证三禁令等。

### 工作流与门禁

| 阶段 | 做什么 | 门禁（真跑） |
|---|---|---|
| Phase 0 | 可行性三问 + 16 项立项决策 + 一次问完参数 | 三问全过；5 分钟控件树预检跑过 |
| Phase 1 | 脚手架生成 + 编译 + 单测 | `scaffold.py check` PASS；`assembleDebug`；`testDebugUnitTest` |
| Phase 2 | 权限、常驻、自愈三源 | 免 USB 可访问；杀进程 / 关无障碍 / 重启各测一次 |
| Phase 3 | 可观测与取证工具链 | `dump_ui` 非空；`logs` 已脱敏；`capture-context.sh` 退出码 0 |
| Phase 4 | 独占锁、任务队列、轮询器、总闸 | 异步语义；忙时 409；结果重启后为 `LOST` |
| Phase 5 | **目标 App 适配**（工作量最大） | 五步证据法 + guard PASS + 定义集 ≡ 引用集 |
| Phase 6 | 任务实现、后端契约、幂等重试 | 重试矩阵；幂等；超时逐层放大 |
| Phase 7 | 验收与交接 | 24h 观测；安全表；README / runbook / 已知限制 |

### 它顺手做的事情：把蓝图第 9 章的代码修到能编译

规范蓝图的第 9 章自称「可粘贴代码」，实测**直接粘贴编译不过**——Kotlin 表达式体里的 `return`、错误的 `CountDownLatch` 包名、NanoHTTPD 不存在的 API、JS 风格正则字面量、KDoc 里的 `*/` 把注释切断、`minSdk 24` 配 `java.time` 缺脱糖、`TaskController` 构造参数类型不匹配，还有一批被引用却没给实现的类。这些在模板里逐条修掉并记录（D1–D28），且**真的跑过编译与单测**。

其中 D23–D28 来自一次真实交付（微信自动回复，约 3700 行 Kotlin）的事故复盘：用缓存包名判断前台导致轮询器空转、`dataSync` 前台服务被 Android 15 六小时额度停掉、同一判定点截两次屏触发限频、脚手架占位符误报 Kotlin 泛型。复盘表见 `references/pitfalls.md` P6。

### 目录

```
android-rpa-builder/
├── SKILL.md                 # 主指令：17 条硬规则 / 8 个 Phase / 收尾纪律
├── references/
│   ├── blueprint.md         # 规范原文（六层架构、逐子系统规格、参考实现）
│   ├── kickoff.md           # Phase 0：需求卡、可行性三问、16 项立项决策
│   ├── build-phases.md      # Phase 1–4、6 施工手册 + 模板修正清单 D1–D28
│   ├── adaptation.md        # Phase 5：五步证据法、ID 登记表、guard、跨 App 传递、感知降级通道
│   ├── acceptance.md        # Phase 7：验收矩阵、24h 观测、安全自检、外部事件项、交接物
│   └── pitfalls.md          # 反模式 P1–P5 + 厂商 ROM + Android 15 前台服务限制 + 真实案例 P6
├── assets/templates/        # 可生成的工程骨架（Kotlin + 取证/guard 脚本）
└── scripts/scaffold.py      # 生成与自检脚手架（零第三方依赖）
```

### 快速开始

```bash
python3 android-rpa-builder/scripts/scaffold.py scaffold \
  --dest ~/rpa-myapp --app-id com.example.rpa --namespace com.example.rpa \
  --target-pkg com.example.target --device-port 9527

cd ~/rpa-myapp
gradle wrapper --gradle-version 8.4
./gradlew assembleDebug
./gradlew testDebugUnitTest
python3 <skill>/scripts/scaffold.py check --dest .
```

可选参数：`--secondary-target-pkg`（次要被控 App）、`--compile-sdk` / `--target-sdk`（默认 36）。生成器不跑 git、不装依赖、不删无关文件。

### 与 hdr-gate 的分工

`hdr-gate` 产出一份**给别人执行的开发文档**（不动代码）；`android-rpa-builder` **直接把工程和代码做出来**。需要先审视方案再动手时，可以先用 `hdr-gate` 出文档，再用本 skill 执行。

工程生成之后，真机调试与排障交给配套的 **`android-rpa-device-ops`**。

---

## android-rpa-device-ops — Android RPA 真机调试与运维

`android-rpa-builder` 的配套 skill：**builder 负责造，它负责验**——把工程装到真机、看清页面、驱动动作、按症状定位，并把每次「修好了」变成可复核的证据。

核心命题：

> 真机调试不是随手点两下看看，而是**每次只改一个变量，用同一套三源证据（前台 Activity + 控件树 + 截图）证明前后差异**。没有证据的「好了」等于没修。

### 关键能力

1. **零硬编码接入**：包名、端口、目标 App 全部从脚手架状态文件 `.rpa-scaffold.json` 读，换工程、换设备、换人都不改脚本；多设备显式 `-s`。
2. **一条命令体检**：`rpa.sh doctor` 依次检查设备在线、端口转发、HTTP 服务、无障碍连接、忙闲、锁屏、厂商后台权限，逐项给出 PASS/FAIL（另附 WARN/INFO 提示）。
3. **三源取证**：`dump` / `shot` 自动落到仓库外的证据目录（`/tmp/rpa-debug/<时间>/`，`chmod 700`），`dump` 按页面标签分目录；`logs` 直接输出，需要留存时自行重定向。
4. **症状驱动排障**：轮询器空转、409 不释放、结果 `LOST`、控件树为空、截图限频、无障碍掉线、输入法抢焦点、通知不进箱、长跑被系统停掉，逐条给出「先查什么 → 判据 → 处置 → 停止线」。
5. **覆盖安装闭环**：单测与构建分开跑 → `install -r` → 启动 → `pidof` → appops → 端口转发 → 状态复检；签名冲突不靠卸载绕过。
6. **适配回归边界**：ID 适配方法论仍归 builder，本 skill 只做装上去后的针对性回归（只回归本轮改动的 ID + 一条只读恢复链），不重跑整套 A1→D2。
7. **生产路径优先**：业务动作走 RPA 任务 API，`adb input` 只限人工调试导航；手动 API 验证不算验收，必须打定时/轮询/后端触发路径。
8. **证据纪律**：只写跑过的；外部事件项一次转交不空等；重启原因查不出就写「未定位」。

### 目录

```
android-rpa-device-ops/
├── SKILL.md                     # 主指令：硬规则 / 五步闭环 / 症状矩阵 / 报告模板
├── references/
│   ├── adb-and-api.md           # ADB 命令、RPA HTTP API 字段、日志、构建安装
│   └── troubleshooting.md       # 八类故障的诊断树
└── tools/
    └── rpa.sh                   # 调试助手：doctor/status/queue/task/dump/shot/logs/install/start/stop/restart
```

### 快速开始

```bash
# 在 builder 生成的工程根目录（含 .rpa-scaffold.json）
export RPA_DEVICE_TOKEN="设备令牌"          # 或用 --token 传入

bash android-rpa-device-ops/tools/rpa.sh doctor   # 设备 / 服务 / 无障碍 / 忙闲 / 锁屏体检
bash android-rpa-device-ops/tools/rpa.sh status   # /api/status 全量
bash android-rpa-device-ops/tools/rpa.sh dump 会话页
bash android-rpa-device-ops/tools/rpa.sh logs 80
bash android-rpa-device-ops/tools/rpa.sh install  # 构建 + 覆盖安装 + 启动 + 复检
```

脚本会自己读 `.rpa-scaffold.json`、建端口转发并按需带令牌；多设备用 `-s <serial>` 或环境变量 `ANDROID_SERIAL` 指定。

### 与 builder 的分工

| 场景 | 用哪个 |
|---|---|
| 从零做新 RPA / 换目标 App / 写适配方法论 | `android-rpa-builder` |
| 装到真机、看控件树、查日志、定位「为什么不触发」 | `android-rpa-device-ops` |
| 目标 App 升级后重校准 ID | builder 的 `adaptation.md` + guard；本 skill 负责安装与回归 |
| 24h 观测与无人值守验收 | builder 的 `acceptance.md`；本 skill 提供体检与日志 |

---

## mcp-tool-selection — MCP 工具注册表设计与工具检索

工具多到模型选不准、schema 把 prompt 撑爆时该怎么办——**先别选架构，先补工具卡**。这套结论来自 2026-10 的一次实测：93/200/400/1000 四个规模、75 条标注任务、真实模型盲测、真实端点 TTFT 测量。

核心命题：

> 收益来自**索引文本质量**，不来自**架构复杂度**。任何「按需加载 schema」的聪明设计，都不如把工具描述写好。

### 被实测推翻的常见做法

这些是网上被广泛推荐、但在对照实验里**负收益或贡献为零**的：

| 常见做法 | 实测结果 |
|---|---|
| 两级 bundle 路由（先选包再选工具） | **负收益**。带兜底 97.8% 召回，比单级 100% 更低；去掉兜底掉到 80% |
| 三通道 RRF 融合 | **贡献为 0**。有工具卡后单通道 BM25 已达 100% |
| 分数断层自适应 K | **在 RRF 上不成立**，`1/(60+rank)` 衰减太平，阈值怎么调都不触发 |
| 包描述拼接成员描述（怕长文稀释而避开） | 担心错了，拼接反而更好，因为把工具名带进了索引 |
| 工具数涨到 1000 模型就会选乱 | **零影响**。1000 个跨域干扰下 22/24，与 200 个时完全相同 |

### 真正有效的只有三件事

1. **去重**：同一件事有两个工具都能做时必须合并。这类损失任何架构都救不回来。
2. **工具卡**：给每个工具补「1 句人话 + 5~8 条用户口语问法」写进索引。**仅这一步就让 1000 工具规模召回从 86.7% 提到 100%。**
3. **单级检索 + 固定 K=10**：K=8 是 100% 召回的实测下限，取 10 留余量。

### 三项指标的实测收益（N=1000）

| 指标 | 全量暴露 | 单级检索 | 差异 |
|---|---|---|---|
| 准确率 | 92%（22/24） | 96%（23/24） | 只差 1 条，**p=1.0 不显著**，实质是「不劣于」 |
| schema token/次 | 54,040 | 591 | **−98.9%** |
| TTFT | 1.32 s | 0.45 s | **−66%** |
| 本地检索开销 | 0 | 4~8 ms | 可忽略 |

定位是**成本优化器，不是准确率增强器**——这点在文档里被反复强调，别读成「RAG 更准」。

### 目录

```
mcp-tool-selection/
├── SKILL.md                        # 决策树 / 五步工作流 / 阈值速查
├── references/
│   ├── antipatterns.md             # 动手前必读：8 条被实测推翻的做法
│   ├── evidence.md                 # 实验数据 + 适用边界 + 与公开研究对照
│   ├── tool-card-spec.md           # 工具卡格式、好坏例子、生成提示词、维护时机
│   └── pitfalls.md                 # 9 个方法论坑（做任何方案验证都能复用）
└── scripts/                        # 零第三方依赖
    ├── gen_tool_cards.py           # split / merge / check 三子命令
    ├── eval_recall.py              # 通用召回评测（多规模 + 干扰池 + K 扫描）
    └── measure_latency.py          # TTFT 测量 + 缓冲诊断 + 固定开销拟合
```

### 快速开始

```bash
# 注入某个场景：从你自己的注册表导出成通用格式即可
cd mcp-tool-selection

python3 scripts/eval_recall.py --tools tools.json --tasks tasks.json \
  --cards tool_cards.json --k 10 --split holdout

python3 scripts/gen_tool_cards.py split --tools tools.json --parts 4 --out ./cards_work
python3 scripts/gen_tool_cards.py merge --dir ./cards_work --out tool_cards.json
python3 scripts/gen_tool_cards.py check --cards tool_cards.json --tools tools.json

NO_PROXY='*' python3 scripts/measure_latency.py \
  --base-url http://host/v1 --api-key $KEY --model qwen3.7-flash \
  --sizes 500,6000 --runs 3 --no-thinking
```

### 两个写脚本时踩到的坑

- **中文 BM25 必须同时出单字和 bigram。** 只做 bigram 会漏掉单字查询，实测召回差 2~3 个百分点（85.3% vs 89.3%），且原因极难定位。
- **qwen3.x 默认开思考模式**，会先吐几百个 reasoning token，`max_tokens` 也压不住，测出来的「TTFT 慢」全是思考耗时。必须传 `chat_template_kwargs={"enable_thinking": false}`。

### 适用边界

「1000 工具准确率 92%」成立的前提是：工具描述由人工精心撰写、候选清单渲染极简、单一模型（deepseek-v4.1-flash）、单步任务。**换成真实 MCP schema（几十个参数、嵌套 JSON）和更弱的模型，这个数字一定会掉。** `references/evidence.md` 有专门一节讲这个，就是用来防误用的。

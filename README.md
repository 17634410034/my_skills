# my_skills

个人 Skill 集合。每个子目录是一个可独立使用的 WorkBuddy / Agent Skill。

## hdr-gate — 异构双审门禁（Heterogeneous Dual-Signoff Gate）

一份**能被陌生 agent 独立执行的开发文档**是怎么产出的：不是让一个模型写完就算，而是让**不同厂商的模型背对背审**，再让一个**完全不了解项目的人空跑一遍**，逼出文档里所有"作者以为写清楚了"的洞。

核心命题：

> 文档的验收测试，是**另一个 agent 的执行过程**。作者和审稿人都会靠先验脑补，只有一无所知的人不会。

### 它解决什么问题

模型自己写文档、自己审，会陷入自洽偏差——它看不出的错，审的时候照样看不出，因为盲区是同一个。换模型族等于换了一套训练数据和偏好，盲区不再重合。这跟航天器用两家不同厂商的芯片做冗余是同一个道理。

### 流程（9 段在流程内）

| 阶段 | 内容 |
|---|---|
| -1 | 澄清预算：歧义不阻塞判定，未确认的写成编号假设 |
| 0 | 门禁判定 + 假设 + 开工选项，合并为一次交互 |
| 1 | Agent1 分析项目，产出开发文档 v0.1 |
| 2 | Agent2 ∥ Agent3 **背对背并行双审**（互不可见） |
| 3 | Agent4 机械归并 + 主持分级复核，产出溯源表 |
| 4 | Agent1 拿合并清单**一次性**改到 v0.2 |
| 5 | 分歧裁定（P0 僵局交人，不自行放行） |
| 6 | 复审双签 → v0.3（返工上限 2 轮） |
| 7 | **冷读试跑**：陌生 agent 只拿文档空跑，报缺口 |
| 8 | 补齐信息定稿 → v1.0（只增信息，不许改方案） |
| 9 | 交接给新会话执行（流程外） |

### 两个容易踩的坑，都已写进铁律

- **歧义不阻塞判定。** 判定只依赖需求规模，不依赖细节。"哪个平台"说不清，不影响判断"这事要新建对外契约、跨 3 个以上文件"。
- **降级不许静默。** 评审强度分三级：`full`（跨族双审）/ `session`（同族多会话，只能叫"多会话复核"）/ `single`（等于没审）。降到 `session` 必须在交付第一句声明。

### 目录

```
hdr-gate/
├── SKILL.md              # 主指令
├── sop-full.md           # 完整 SOP v2.5
├── dev-doc-template.md   # 开发文档骨架（任务卡 / 契约变更 / 假设账本 / 禁止区）
└── tools/                # 零第三方依赖，只用 Python 标准库
    ├── hdr.py            # 编排 CLI
    ├── llm.py            # 多 provider 统一客户端（OpenAI 兼容协议）
    ├── prompts.py        # 8 个角色的提示词
    ├── mcp_server.py     # MCP stdio server
    └── config.example.toml
```

### 快速开始

```bash
cd hdr-gate/tools
cp config.example.toml config.toml

# 密钥走环境变量，不要写进文件
export TOKENPLAN_API_KEY="sk-xxx"

python3 hdr.py doctor        # 自检：通道 / 角色绑定 / 异质性（缺密钥退出码 2）
python3 hdr.py init --name 需求名 --repo /path/to/repo --req "需求原文" \
  --assume "A1|平台=当前应用|需求原文写'这个平台'"
python3 hdr.py draft --name 需求名
python3 hdr.py review --name 需求名     # 并行双审
python3 hdr.py merge  --name 需求名
python3 hdr.py fix    --name 需求名
python3 hdr.py sign   --name 需求名
python3 hdr.py coldread --name 需求名
python3 hdr.py finalize --name 需求名
```

要求 Python 3.11+（用到内置 `tomllib`）。

### 默认模型配置

| 角色 | 模型 | 族 |
|---|---|---|
| Agent1 作者 | qwen3.8-max | qwen |
| Agent2 可行性审 | glm-5.3 | zhipu |
| Agent3 正确性审 | deepseek-v4-pro | deepseek |
| Agent4 合并去重 | qwen3.8-flash | qwen |
| 冷读试跑者 | glm-5.2 | zhipu |

**异质性看「模型族」，不看「通道」。** 同一个通道里不同族的模型，双审依然有效；同族同模型只是换了会话，脑补习惯一模一样。

---

## android-rpa-builder — Android 无障碍 RPA 项目构建器

把一句「帮我做个能自动操作某某 App 的 RPA」，变成一个**可编译、可远程驱动、可无人值守、换目标 App 还能复用**的 Android 工程——而不是一段会在布局变更后全线崩掉的坐标脚本。

核心命题：

> 目标 App 的不确定性必须被隔离在一层里。换目标 App 只重写那一层，其余全部复用。一旦下层出现目标 App 的 resource-id / Activity 类名 / 业务文案，「换 App 只改一处」就失效了。

### 七项能力

1. **可行性判定**：动手前先做「控件树可读吗 / 动作能用点击输入滚动表达吗 / 合规与风控怎么过」三问，任一条不过就停，不写代码。
2. **可编译脚手架**：一条命令生成完整工程——设备内嵌 HTTP 前台服务、无障碍动作原语、独占锁、异步任务队列、结果落盘、屏幕保活、权限引导、引导页、取证脚本与防回归 guard。
3. **分层与边界**：六层模型（设备接入 / 运行时 / 动作原语 / 页面语义 / 编排 / 控制面），把目标 App 知识锁在 `TargetOperator` + `TargetIds` 两个文件里。
4. **证据驱动适配**：A1→D2 分阶段推进，每阶段五步（代码生成清单 → 基线快照 → 采证据 → 只改一个 ID → 收尾校验），配 `ui_ids_guard.py` 机械化拦截越界改动。
5. **幂等与重试纪律**：「UI 动作不可回滚」——结果未知绝不重试、`RESULT_UNKNOWN` 永不重推，避免把消息重复发给真人。
6. **安全默认值**：设备 HTTP 强制令牌鉴权（空令牌只放行回环）、日志写入时脱敏、密钥走 EncryptedSharedPreferences、开机自启失败有通知兜底。
7. **验收与交接**：24 小时观测、自愈矩阵（杀进程 / 关无障碍 / 重启）、安全自检、运行手册与已知限制清单。

### 它顺手做的事情：把蓝图第 9 章的代码修到能编译

规范蓝图的第 9 章自称「可粘贴代码」，实测**直接粘贴编译不过**——Kotlin 表达式体里的 `return`、错误的 `CountDownLatch` 包名、NanoHTTPD 不存在的 API、JS 风格正则字面量、KDoc 里的 `*/` 把注释切断、`minSdk 24` 配 `java.time` 缺脱糖、`TaskController` 构造参数类型不匹配，还有一批被引用却没给实现的类。这些在模板里逐条修掉并记录（D1–D22），且**真的跑过编译与单测**。

### 目录

```
android-rpa-builder/
├── SKILL.md                 # 主指令：铁律 / 阶段 / 门禁 / 参数提问
├── references/
│   ├── blueprint.md         # 规范原文（六层架构、逐子系统规格、参考实现）
│   ├── kickoff.md           # Phase 0：需求卡、可行性三问、16 项立项决策
│   ├── build-phases.md      # Phase 1–7 施工手册 + 模板修正清单 D1–D22
│   ├── adaptation.md        # Phase 5：五步证据法、ID 登记表、guard、跨 App 传递
│   ├── acceptance.md        # Phase 7：验收矩阵、24h 观测、安全自检、交接物
│   └── pitfalls.md          # 13 条反模式 + 厂商 ROM + Android 15 前台服务限制
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
python3 <skill>/scripts/scaffold.py check --dest .
```

### 与 hdr-gate 的分工

`hdr-gate` 产出一份**给别人执行的开发文档**（不动代码）；`android-rpa-builder` **直接把工程和代码做出来**。需要先审视方案再动手时，可以先用 `hdr-gate` 出文档，再用本 skill 执行。

# HDR Gate Tools

把 `SKILL.md` 里的九阶段流程变成可执行的东西。**零第三方依赖**，只要 Python 3.11+。

解决的核心问题：大多数 agent 工具不让你在一个会话里编排多个模型。
这里的答案是——**别在会话层编排，在工具调用层编排**。

---

## 5 分钟上手

```bash
cd tools

# 1. 填密钥（推荐 export 到环境变量，别写进文件）
#    默认走阿里百炼 Token Plan，一个 Key 就够：
export TOKENPLAN_API_KEY="sk-xxx"
#    想用其他通道再补：NEWAPI_KEY / DASHSCOPE_API_KEY / ZHIPU_API_KEY / DEEPSEEK_API_KEY

# 2. 列出通道里的真实模型 id，核对 config.toml 有没有写错
python3 hdr.py models --provider tokenplan

# 3. 自检：看通道、角色绑定、异质性是否合格
python3 hdr.py doctor

# 4. 开一个需求（--assume 先把没确认的前提登记成假设，别憋在心里）
python3 hdr.py init --name 用户登录态改造 --repo /path/to/your/repo --req "需求原文" \
  --assume "A1|平台=当前应用内创建的 agent|需求原文写'这个平台'" \
  --assume "A2|调用方=外部 MCP 客户端|需求写'给其他 agent 调用'"
python3 hdr.py draft --name 用户登录态改造
```

---

## 两种驱动方式

| 方式 | 适合 | 特点 |
|---|---|---|
| **MCP Server** | 在 agent 工具里边聊边跑 | 主控 agent 当编排器，可中途人工裁决 |
| **CLI（hdr.py）** | 需求清楚，想一条龙 | 脱离 agent 工具，可重跑、可调试 |

### 方式一：挂成 MCP

写进 `~/.workbuddy/mcp.json`（Claude Code 用 `~/.claude.json`，格式类似）：

```json
{
  "mcpServers": {
    "hdr-gate": {
      "command": "python3",
      "args": ["/Users/boohee/Desktop/MyProjects/my_skills/hdr-gate/tools/mcp_server.py"],
      "env": {
        "HDR_CONFIG": "/Users/boohee/Desktop/MyProjects/my_skills/hdr-gate/tools/config.toml",
        "NEWAPI_KEY": "sk-xxx",
        "ZHIPU_API_KEY": "xxx",
        "DEEPSEEK_API_KEY": "sk-xxx"
      }
    }
  }
}
```

挂上之后，任何支持 MCP 的 agent 就多了三个能力：

| 工具 | 作用 |
|---|---|
| `hdr_list_roles` | 查角色绑定 + 异质性体检 |
| `hdr_ask` | 按角色问一次任意模型（可带项目上下文） |
| `hdr_ask_parallel` | **并行**问多个角色，一次拿回两份结果 |

双审就这样实现，两个模型互不可见：

```
hdr_ask_parallel(tasks=[
  {role: "agent2", prompt: "按可行性视角审这份文档…"},
  {role: "agent3", prompt: "按正确性视角审这份文档…"}
], repo="/path/to/repo")
```

### 方式二：命令行

```bash
python3 hdr.py init      --name X --repo /path --req "…"   # 建项目
python3 hdr.py draft     --name X                          # 阶段1 → v0.1
python3 hdr.py review    --name X                          # 阶段2 → 双审清单（并行）
python3 hdr.py merge     --name X                          # 阶段3 → 合并 + 溯源表
python3 hdr.py fix       --name X                          # 阶段4 → v0.2
python3 hdr.py sign      --name X                          # 阶段6 → 双签 → v0.3
python3 hdr.py coldread  --name X                          # 阶段7 → 缺口清单
python3 hdr.py retry     --name X --stage coldread --max-tokens 40000
                                                        # 空正文/被截断时重跑单个角色（成本照常入账）
                                                        # stage ∈ review-a | review-b | merge | coldread
python3 hdr.py finalize  --name X                          # 阶段8 → 交付 <需求名>.md
python3 hdr.py status    --name X                          # 看进度和花费
python3 hdr.py run       --name X --auto                   # 一条龙
```

### finalize 之后：落点确认与产物清理（默认路径只是兜底）

不带 `--out` 时 `<需求名>.md` 落在**当前目录**——在有文档规范的项目里通常是错的。收尾三步：

```bash
# 1) 先问用户：最终文档放哪（照该项目既有规则）+ 中间产物要不要清理
ls <repo>/app/service/*/openspec/specs 2>/dev/null | tail -5     # 归纳规则：日期前缀？-开发文档 后缀？
# 2) 先移动 + 校验字节数，通过后才删过程产物（顺序反了就是把交付物删了）
mv <需求名>.md <确认目录>/<确认文件名>.md && wc -c <确认目录>/<确认文件名>.md
# 3) 清理后必须改文档自引用，否则接手方打不开 degraded.md / resolution.md
grep -n "hdr-gate" <确认目录>/<确认文件名>.md     # 期望：空
rm -rf .hdr-gate/<需求名>
```

执行期记录（`t0-findings.md` / `pytest-baseline.txt` / `t8-findings.md`）**不要**写在 `.hdr-gate/`（会跟着一起删），也不要写进文档所在目录（污染 git）——写到仓库已 gitignore 的临时目录，文档里定义一次 `REC=<临时目录> && mkdir -p $REC` 之后统一引用。

阶段 5（分歧裁定）刻意没有自动化——那是判断题，要人来做。脚本会把分歧候选放在 `merged.md` 的第三节，你裁决后写进 `resolution.md`，`sign` 阶段会自动带上。

---

## 产物在哪

```
.hdr-gate/<需求名>/
├── meta.json          # 需求原文、repo、时间线、成本
├── v0.1.md            # 初稿
├── review-a.md        # Agent2 可行性清单
├── review-b.md        # Agent3 正确性清单
├── merged.md          # 合并清单 + 溯源表 + 分歧候选
├── fix-table.md       # 逐条回应
├── v0.2.md
├── sign.md            # 双签记录
├── v0.3.md            # 双签通过版
├── coldread.md        # 冷读缺口清单
└── finalize-table.md  # 补齐对照表

<工作目录>/<需求名>.md   # ← 最终交付物，交给新会话执行
```

---

## 阿里百炼 Token Plan（默认通道）

一个 Key 同时拿到 Qwen / GLM / DeepSeek 三家模型，是这套工具最省事的接入方式。

| 项 | 值 |
|---|---|
| OpenAI 协议 Base URL | `https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1` |
| Anthropic 协议 | `https://token-plan.cn-beijing.maas.aliyuncs.com/apps/anthropic`（本工具不走，只走 OpenAI 协议） |
| API Key | Token Plan 个人版专属 Key，环境变量 `TOKENPLAN_API_KEY` |

### 可用模型（只列文本生成类，其余音视频/图像本工具用不到）

| 模型 id | 能力 | 备注 |
|---|---|---|
| `qwen3.8-max` | 文本、推理、视觉理解 | 限时夜间 4 折 |
| `qwen3.8-flash` | 文本、推理、视觉理解 | 限时夜间 4 折 |
| `qwen3.7-plus` | 文本、推理、视觉理解 | |
| `qwen3.7-max` | 文本、推理 | |
| `qwen3.6-flash` | 文本、推理、视觉理解 | |
| `auto` | 自动路由到合适的文本/推理模型 | 不确定选哪个时可用 |
| `glm-5.3` | 文本、推理 | 智谱 |
| `glm-5.2` | 文本、推理 | 智谱，小一档 |
| `deepseek-v4-pro` | 文本、推理 | 最强，建议给 Agent3 |
| `deepseek-v4-pro-0813` | 文本、推理 | 限时夜间 5 折 |
| `deepseek-v4.1-flash` | 文本、推理、视觉理解 | 限时夜间 5 折 |
| `deepseek-v4.1-flash-1017` | 文本、推理、视觉理解 | 限时夜间 5 折 |
| `deepseek-v4-flash-0731` | 文本、推理 | 限时夜间 5 折 |
| `decision-model-preview` | 文本 | 限时免费 |

模型 id 以 `python3 hdr.py models --provider tokenplan` 的返回为准，手册可能滞后。

### 关键：异质性看「模型族」，不看「通道」

这是接入 Token Plan 后最容易搞错的一点。三个角色都走 `tokenplan`，`provider` 字段完全一样，但底层是三家不同公司的模型——训练数据、RLHF 偏好、架构都不同，盲区照样不重合，双审依然成立。

所以判定逻辑是看 `family`：

```
qwen3.8-max [qwen]  vs  glm-5.3 [zhipu]      → ✅ 盲区基本不重合
qwen3.8-max [qwen]  vs  deepseek-v4-pro [deepseek] → ✅
qwen3.8-max [qwen]  vs  qwen3.8-flash [qwen]  → ⚠️ 同族不同尺寸（字面化补偿）
qwen3.8-max [qwen]  vs  qwen3.8-max [qwen]    → ❌ 等于没隔离
```

`family` 可以手写，也可以省略由模型名自动推断。`doctor` 会把每个角色的族标出来。

**代价**：三家模型挤在一个通道上，通道挂了双审会同时失败。这是单点故障，`doctor` 会提示。介意的话给 `agent1` 配 `fallback`（配置里已指向 `dashscope/qwen3-max`），或把某一个审稿人换成官方 API。

---

## 配置：provider 直接切换

`config.toml` 里 `[roles.*]` 的 `provider` 字段指向 `[providers.*]`，改一行就换通道：

```toml
[roles.agent2]
provider = "zhipu"        # 改成 "newapi" 就走内网网关
model = "glm-5.3"

[roles.agent2.fallback]   # 主通道 401/404/超时 时自动切
provider = "newapi"
model = "glm-5.3"
```

所有通道都走 OpenAI 兼容的 `/chat/completions`，理论上任何兼容网关都能接。

---

## 五个容易踩的坑

**1. 模型 id 一定要核对，别照抄示例**
跑 `python3 hdr.py models --provider tokenplan` 看返回的真实 id。名字打错会 404，然后 `fallback` 悄悄顶上——日志里能看出来，但很容易漏。

**2. `doctor` 的异质性检查别忽略，也别误读**
它看的是 `family` 不是 `provider`。三个角色都走 tokenplan **不代表**同源：qwen / zhipu / deepseek 是三家的脑子，异质成立。真正危险的是同族同模型——比如冷读者配成 `qwen3.8-max`，那它有着和作者一样的脑补习惯，那份缺口清单基本是废纸。

**3. `finalize` 可能拒绝执行**
如果补齐过程中发现必须改方案，它会输出 `!! SCHEME-DEFECT` 并以退出码 3 结束。这是设计好的：改方案必须回 `fix` 重走双签，不能在「补齐」的名义下偷偷改。

**4. 产物 0–2 字节的空文件 ≠ 模型不可用**
推理型模型（`glm-5.x` / `deepseek-v4-pro` / `qwen3.x` 思考档）会把 `max_tokens` 花在思维链上，正文 `content` 返回空串、`finish_reason=length`。`llm.py` 只取 `content`，早期版本会把空产物直接落盘、让下一阶段拿垃圾当输入。现在：空正文会报错并在 `progress.log` 打出 `out= / reasoning=`，各阶段落盘前还会校验非空 + 期望标记。
**处置**：`prompts.py` 已把 `OUTPUT_TERSE_REVIEW / _SIGN / _COLD`（条数上限、字数上限、禁止复述原文）硬编进这三个角色的 system，配额默认也提到 **agent2/agent3=32000、agent4=24000、coldread=40000**。2026-10-26 实测：20000 时 glm-5.3 评审返回 2 字节、glm-5.2 冷读返回 0 字节；32000/40000 + 体量约束后两者都拿到完整正文（542s / 184s）。**只提配额不压体量，模型会在思维链里写到撞顶，照样空手而归。**
仍不够时用 `hdr.py retry`（它会走 `require_artifact` 校验并正常记账，**不要手写脚本调 `llm.chat`**）：
```bash
python3 hdr.py retry --name X --stage review-a --doc v0.1.md  --max-tokens 40000
python3 hdr.py retry --name X --stage coldread --doc v0.3.md  --max-tokens 48000
```

**4.5 冷读必须跑两路（`coldread` 只覆盖了其中一路）**
`hdr.py coldread` 是纯 API 调用，**没有仓库读权限**：它只能报「文档没告诉我 X」，报不出「文档写的 `file:行号` / 函数签名 / 命令在仓库里其实不成立」。后者是接手方损失最大的一类缺陷，且冷读清单再长也不会自己冒出来。
**第二路用宿主侧全新上下文的只读 subagent 跑**（prompt 要点见 SKILL 第 7 步），要求它单列 `## 事实性错误` 一节，格式 `文档的主张 | 仓库实况 | 证据 file:line`。`coldread` 结束时工具会打印这条提醒，别跳过。

**5. `draft` / `fix` / `finalize` 用 API 起草整份长文档，几乎必超时**
非流式调用的墙钟时间由**输出长度**决定：一次生成 1–3 万 token 的完整文档，压上下文、降 `max_tokens`、抬 `timeout` 都救不了（实测 22K 字符上下文 + 14000 上限 + 900s，仍在 1000s 后无响应）。
**正路**：`draft` 检测到 `v0.1.md` 已存在时**只做硬门禁自检后放行**（不通过直接退出），所以由主持自己写 v0.1/v0.2/定稿是被工具承认的一等路径——前提是在 `degraded.md` 标 `AUTHORED-BY-HOST` / `FIXED-BY-HOST` 并在交付时声明。API 只跑 review / merge / sign / coldread（它们输出短，实测 77–345s 全部返回）。

## 运行位置与状态

- `.hdr-gate/` 落在**执行命令时的 cwd**，`<需求名>.md` 交付物同理。请固定在**目标仓库根目录**执行所有命令。`init` 会把绝对路径登记到 `tools/.hdr-projects.json`，换目录执行时后续命令能自动找回现场（会打印一行提示）。
- agent 宿主（Qoder/Claude 类）的 shell 是**非交互式且状态不跨调用**：`TOKENPLAN_API_KEY` 每条命令都要重新注入，`cd` 也要每条带上。
- 慢阶段等待配方（单条 Bash 调用常封顶 10 分钟）：
  ```bash
  D=".hdr-gate/<需求名>"
  for i in $(seq 1 34); do
    [ -s "$D/review-a.md" ] && [ -s "$D/review-b.md" ] && break
    pgrep -f "hdr.py review" >/dev/null || { echo "PROCESS GONE"; break; }
    sleep 15
  done
  tail -5 "$D/progress.log"
  ```
  超过一轮就**在同一回合内再开一轮**，别结束回合（后台完成通知只在下一回合到达，没人收就等于任务死在半路）。

## 阶段产物与门禁

| 阶段 | 产物 | 落盘前校验 |
|---|---|---|
| draft | `v0.1.md` | 任务卡 / 验收断言 / 非目标 三项必含，缺则退出（API 起草与主持自写都跑这套） |
| review | `review-a.md` `review-b.md` | ≥120 字符且含 `[P0-/P1-/P2-` |
| merge | `merged.md` | 含「溯源 / 归入」 |
| sign | `sign.md` `sign-roundN.md` `v0.3.md` `v0.3.roundN.md` | 两位都必须含 `SIGNOFF`；`meta.json.sign_round` 累计，第 3 轮不过会提示交用户裁决 |
| coldread | `coldread.md` | ≥120 字符且含 `GAP-n`；**默认纯净模式**（不附带任何上下文与 recon.md），要带才加 `--with-context` |

---

## 成本

脚本会在 `meta.json` 累积估算花费（按 `[pricing]` 里的单价），`status` 能看到。

一次典型流程约 **13 次模型调用**，其中两次是最贵的全文档读写（draft、fix），两次双审较长但输出短。想省钱，优先砍 `sign` 阶段——它只需核对上一轮的 P0，不必重读全文。

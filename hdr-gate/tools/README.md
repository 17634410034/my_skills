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
python3 hdr.py finalize  --name X                          # 阶段8 → 交付 <需求名>.md
python3 hdr.py status    --name X                          # 看进度和花费
python3 hdr.py run       --name X --auto                   # 一条龙
```

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

## 三个容易踩的坑

**1. 模型 id 一定要核对，别照抄示例**
跑 `python3 hdr.py models --provider tokenplan` 看返回的真实 id。名字打错会 404，然后 `fallback` 悄悄顶上——日志里能看出来，但很容易漏。

**2. `doctor` 的异质性检查别忽略，也别误读**
它看的是 `family` 不是 `provider`。三个角色都走 tokenplan **不代表**同源：qwen / zhipu / deepseek 是三家的脑子，异质成立。真正危险的是同族同模型——比如冷读者配成 `qwen3.8-max`，那它有着和作者一样的脑补习惯，那份缺口清单基本是废纸。

**3. `finalize` 可能拒绝执行**
如果补齐过程中发现必须改方案，它会输出 `!! SCHEME-DEFECT` 并以退出码 3 结束。这是设计好的：改方案必须回 `fix` 重走双签，不能在「补齐」的名义下偷偷改。

---

## 成本

脚本会在 `meta.json` 累积估算花费（按 `[pricing]` 里的单价），`status` 能看到。

一次典型流程约 **13 次模型调用**，其中两次是最贵的全文档读写（draft、fix），两次双审较长但输出短。想省钱，优先砍 `sign` 阶段——它只需核对上一轮的 P0，不必重读全文。

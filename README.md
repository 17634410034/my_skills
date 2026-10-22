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

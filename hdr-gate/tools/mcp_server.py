#!/usr/bin/env python3
"""HDR Gate MCP Server —— 把「切换模型」变成「调用工具」。

零第三方依赖，实现 MCP stdio transport（每行一个 JSON-RPC 消息）。

注册方式（以 WorkBuddy / Claude Code 的 mcp.json 为例）：

    "hdr-gate": {
      "command": "python3",
      "args": ["/Users/boohee/Desktop/MyProjects/my_skills/hdr-gate/tools/mcp_server.py"],
      "env": {
        "HDR_CONFIG": "/Users/boohee/Desktop/MyProjects/my_skills/hdr-gate/tools/config.toml",
        "NEWAPI_KEY": "sk-xxx",
        "ZHIPU_API_KEY": "xxx",
        "DEEPSEEK_API_KEY": "sk-xxx",
        "DASHSCOPE_API_KEY": "sk-xxx"
      }
    }

暴露的工具：
    hdr_list_roles   —— 查看角色与模型绑定、异质性检查
    hdr_ask          —— 按角色问一次任意模型
    hdr_ask_parallel —— 一次并行问多个角色（双审的核心）
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import llm  # noqa: E402

SERVER_INFO = {"name": "hdr-gate", "version": "1.0.0"}
PROTOCOL = "2024-11-05"


def _cfg():
    return llm.load_config(os.environ.get("HDR_CONFIG"))


def _build_context(repo: str | None, paths: list[str] | None, max_chars: int = 300_000) -> str:
    if not repo and not paths:
        return ""
    return llm.collect_context(paths or [], repo=repo, max_chars=max_chars)


TOOLS = [
    {
        "name": "hdr_list_roles",
        "description": "列出 HDR Gate 的角色与模型绑定，并检查异质性约束（双审是否异厂、冷读者是否与作者同模型）。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "hdr_ask",
        "description": "按角色调用指定模型一次。角色决定模型，如 agent1/agent2/agent3/agent4/coldread。可附带项目上下文。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "role": {
                    "type": "string",
                    "description": "角色名：agent1(作者) / agent2(可行性审) / agent3(正确性审) / agent4(合并去重) / coldread(冷读)",
                },
                "prompt": {"type": "string", "description": "用户消息内容"},
                "system": {"type": "string", "description": "可选的 system 提示词"},
                "repo": {"type": "string", "description": "项目根目录，会附带文件树"},
                "context_paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "需要读取的文件或目录列表",
                },
                "max_context_chars": {"type": "integer", "description": "上下文字符上限，默认 300000"},
            },
            "required": ["role", "prompt"],
        },
    },
    {
        "name": "hdr_ask_parallel",
        "description": "并行调用多个角色，一次返回全部结果。用于背对背双审：同时问 agent2 与 agent3，两者互不可见。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "tasks": {
                    "type": "array",
                    "description": "任务列表",
                    "items": {
                        "type": "object",
                        "properties": {
                            "role": {"type": "string"},
                            "prompt": {"type": "string"},
                            "system": {"type": "string"},
                        },
                        "required": ["role", "prompt"],
                    },
                },
                "repo": {"type": "string"},
                "context_paths": {"type": "array", "items": {"type": "string"}},
                "max_context_chars": {"type": "integer"},
            },
            "required": ["tasks"],
        },
    },
]


def call_list_roles(_args: dict) -> str:
    cfg = _cfg()
    lines = ["# 角色绑定", ""]
    for role in cfg["roles"]:
        chain = llm.resolve_chain(cfg, role)
        desc = " → ".join(f"{c['provider']}/{c['model']} [{c['family']}]" for c in chain)
        lines.append(f"- `{role}`: {desc}")
    lines += ["", "# 异质性检查（看模型族，不看通道）", ""]
    try:
        for role_b, label in (
            ("agent2", "Agent2 可行性审"),
            ("agent3", "Agent3 正确性审"),
            ("coldread", "冷读试跑者"),
        ):
            mark, why = llm.heterogeneity(cfg, "agent1", role_b)
            lines.append(f"- {label} vs Agent1: {mark} {why}")
    except Exception as e:
        lines.append(f"- 检查失败: {e}")
    return "\n".join(lines)


def call_ask(args: dict) -> str:
    cfg = _cfg()
    role = args["role"]
    ctx = _build_context(args.get("repo"), args.get("context_paths"), args.get("max_context_chars", 300_000))
    user = args["prompt"] + (f"\n\n## 项目上下文\n{ctx}" if ctx else "")
    messages = []
    if args.get("system"):
        messages.append({"role": "system", "content": args["system"]})
    messages.append({"role": "user", "content": user})
    result = llm.chat(cfg, role, messages)
    usage = result.get("usage", {})
    return (
        f"{result['text']}\n\n---\n"
        f"_[{result['provider']}/{result['model']} · {result['elapsed']:.1f}s · "
        f"in {usage.get('prompt_tokens', '?')} / out {usage.get('completion_tokens', '?')}]_"
    )


def call_ask_parallel(args: dict) -> str:
    cfg = _cfg()
    ctx = _build_context(args.get("repo"), args.get("context_paths"), args.get("max_context_chars", 300_000))
    jobs = []
    for t in args["tasks"]:
        user = t["prompt"] + (f"\n\n## 项目上下文\n{ctx}" if ctx else "")
        messages = []
        if t.get("system"):
            messages.append({"role": "system", "content": t["system"]})
        messages.append({"role": "user", "content": user})
        jobs.append({"role": t["role"], "messages": messages})

    results = llm.chat_parallel(cfg, jobs)
    parts = []
    for task, res in zip(args["tasks"], results):
        usage = res.get("usage", {})
        parts.append(
            f"## [{task['role']}] {res['provider']}/{res['model']} "
            f"· {res['elapsed']:.1f}s · in {usage.get('prompt_tokens', '?')} / "
            f"out {usage.get('completion_tokens', '?')}\n\n{res['text']}"
        )
    return "\n\n---\n\n".join(parts)


HANDLERS = {
    "hdr_list_roles": call_list_roles,
    "hdr_ask": call_ask,
    "hdr_ask_parallel": call_ask_parallel,
}


def send(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def make_result(msg_id, result):
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def make_error(msg_id, code: int, message: str):
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def handle(msg: dict) -> dict | None:
    method = msg.get("method")
    msg_id = msg.get("id")

    if method == "initialize":
        return make_result(
            msg_id,
            {
                "protocolVersion": PROTOCOL,
                "capabilities": {"tools": {}},
                "serverInfo": SERVER_INFO,
            },
        )
    if method in ("notifications/initialized", "initialized"):
        return None
    if method == "ping":
        return make_result(msg_id, {})
    if method == "tools/list":
        return make_result(msg_id, {"tools": TOOLS})
    if method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        handler = HANDLERS.get(name)
        if not handler:
            return make_error(msg_id, -32601, f"未知工具: {name}")
        try:
            text = handler(args)
            return make_result(msg_id, {"content": [{"type": "text", "text": text}]})
        except Exception as e:
            return make_result(
                msg_id,
                {"content": [{"type": "text", "text": f"执行失败: {e}"}], "isError": True},
            )
    if msg_id is not None:
        return make_error(msg_id, -32601, f"未知方法: {method}")
    return None


def main() -> None:
    llm.log("[hdr-gate mcp] 已启动，等待请求")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            send(make_error(None, -32700, "JSON 解析失败"))
            continue
        try:
            response = handle(msg)
        except Exception as e:
            response = make_error(msg.get("id"), -32603, str(e))
        if response is not None:
            send(response)


if __name__ == "__main__":
    main()

"""HDR Gate 多模型客户端 —— 零第三方依赖。

设计约束：
1. 只走 OpenAI 兼容协议（/chat/completions），靠 base_url 区分厂商。
2. 所有日志走 stderr，stdout 留给调用方（MCP 协议占用了 stdout）。
3. 角色 → 模型 的映射集中在 config.toml，代码里不出现模型名。
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import tomllib
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_CONFIG = HERE / "config.toml"

# 收集项目上下文时跳过的目录
SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv",
    "dist", "build", ".next", ".nuxt", ".idea", ".vscode", ".pytest_cache",
    ".mypy_cache", "target", "vendor", ".DS_Store", "coverage", ".turbo",
}
SOURCE_EXT = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".vue", ".go", ".java", ".kt",
    ".rs", ".rb", ".php", ".cs", ".sql", ".proto", ".yaml", ".yml",
    ".toml", ".json", ".md", ".sh", ".dockerfile", ".env.example",
}


class ConfigError(Exception):
    pass


class LLMError(Exception):
    pass


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


# --------------------------------------------------------------------------
# 配置
# --------------------------------------------------------------------------

_ENV_RE = re.compile(r"\$\{(\w+)\}")


def _expand(value):
    """递归展开 ${ENV_VAR}。"""
    if isinstance(value, str):
        return _ENV_RE.sub(lambda m: os.environ.get(m.group(1), ""), value)
    if isinstance(value, dict):
        return {k: _expand(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand(v) for v in value]
    return value


def load_config(path: str | Path | None = None) -> dict:
    cfg_path = Path(path) if path else DEFAULT_CONFIG
    if not cfg_path.exists():
        raise ConfigError(
            f"配置文件不存在: {cfg_path}\n"
            f"先执行: cp config.example.toml config.toml"
        )
    with cfg_path.open("rb") as f:
        cfg = tomllib.load(f)
    cfg = _expand(cfg)
    if not cfg.get("roles"):
        raise ConfigError("配置缺少 [roles.*] 段")
    if not cfg.get("providers"):
        raise ConfigError("配置缺少 [providers.*] 段")
    cfg["_path"] = str(cfg_path)
    return cfg


def resolve_chain(cfg: dict, role: str) -> list[dict]:
    """返回该角色的 provider 尝试链：主通道 + fallback。"""
    roles = cfg.get("roles", {})
    if role not in roles:
        raise ConfigError(f"未定义角色: {role}，已定义: {', '.join(roles)}")
    spec = roles[role]
    chain = [_bind(cfg, spec)]
    fb = spec.get("fallback")
    if fb:
        merged = {k: v for k, v in spec.items() if k != "fallback"}
        merged.update(fb)
        # fallback 换了模型却没指定 family 时，清掉继承来的 family 让它重新推断，
        # 否则会拿主模型族的标签去判一个不同族的备份模型。
        if "family" not in fb:
            merged.pop("family", None)
        chain.append(_bind(cfg, merged))
    return chain


# 模型族推断：异质性判定看族，不看通道。
# 同一个网关里可以同时跑 Qwen / GLM / DeepSeek，通道相同但族不同，盲区照样不重合。
FAMILY_HINTS: list[tuple[str, str]] = [
    ("qwen", "qwen"),
    ("glm", "zhipu"),
    ("chatglm", "zhipu"),
    ("deepseek", "deepseek"),
    ("claude", "anthropic"),
    ("gpt", "openai"),
    ("o1", "openai"),
    ("kimi", "moonshot"),
    ("moonshot", "moonshot"),
    ("hunyuan", "tencent"),
    ("ernie", "baidu"),
    ("doubao", "bytedance"),
    ("minimax", "minimax"),
    ("happyhorse", "happyhorse"),
    ("decision-model", "domain"),
]


def infer_family(model: str) -> str:
    m = model.lower()
    for prefix, family in FAMILY_HINTS:
        if m.startswith(prefix) or f"/{prefix}" in m or f"-{prefix}" in m:
            return family
    return m.split("-")[0].split(":")[0]


def _bind(cfg: dict, spec: dict) -> dict:
    pname = spec.get("provider") or cfg.get("default_provider")
    providers = cfg.get("providers", {})
    if pname not in providers:
        raise ConfigError(f"未定义 provider: {pname}")
    prov = providers[pname]
    return {
        "provider": pname,
        "base_url": prov["base_url"].rstrip("/"),
        "api_key": prov.get("api_key", ""),
        "timeout": prov.get("timeout", 600),
        "model": spec["model"],
        "family": spec.get("family") or infer_family(spec["model"]),
        "temperature": spec.get("temperature", 0.3),
        "max_tokens": spec.get("max_tokens", 8192),
    }


# --------------------------------------------------------------------------
# 调用
# --------------------------------------------------------------------------

def _post(bound: dict, messages: list[dict]) -> dict:
    url = f"{bound['base_url']}/chat/completions"
    body = {
        "model": bound["model"],
        "messages": messages,
        "temperature": bound["temperature"],
        "max_tokens": bound["max_tokens"],
    }
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {bound['api_key']}",
    }
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=bound["timeout"]) as resp:
        return json.loads(resp.read().decode("utf-8"))


def chat(
    cfg: dict,
    role: str,
    messages: list[dict],
    *,
    retries: int = 2,
    silent: bool = False,
    on_event=None,
) -> dict:
    """按角色调用模型，自动走 fallback 链。返回 {text, usage, model, provider, elapsed}。

    on_event: 可选回调，接收进度文本（重试/切通道），供上层写进 progress.log。
    """
    chain = resolve_chain(cfg, role)
    last_err: Exception | None = None

    def emit(msg: str) -> None:
        log(msg)
        if on_event:
            try:
                on_event(msg.strip())
            except Exception:
                pass

    for idx, bound in enumerate(chain):
        for attempt in range(retries + 1):
            started = time.time()
            if attempt:
                emit(f"  [{role}] 第 {attempt + 1} 次尝试 {bound['provider']}/{bound['model']}")
            try:
                payload = _post(bound, messages)
                elapsed = time.time() - started
                choice = payload["choices"][0]["message"]["content"] or ""
                usage = payload.get("usage", {})
                if not silent:
                    log(
                        f"  [{role}] {bound['provider']}/{bound['model']} "
                        f"{elapsed:.1f}s in={usage.get('prompt_tokens', '?')} "
                        f"out={usage.get('completion_tokens', '?')}"
                    )
                return {
                    "text": choice,
                    "usage": usage,
                    "model": bound["model"],
                    "provider": bound["provider"],
                    "elapsed": elapsed,
                }
            except urllib.error.HTTPError as e:
                detail = ""
                try:
                    detail = e.read().decode("utf-8", "replace")[:400]
                except Exception:
                    pass
                last_err = LLMError(f"{bound['provider']} HTTP {e.code}: {detail}")
                # 4xx（除 429）不可重试，直接换下一个 provider
                if e.code in (400, 401, 403, 404) or (400 <= e.code < 500 and e.code != 429):
                    emit(f"  [{role}] {bound['provider']} 返回 {e.code}，切换下一个通道")
                    break
                emit(f"  [{role}] {bound['provider']} 返回 {e.code}，{1.5 * (attempt + 1):.1f}s 后重试")
                if attempt < retries:
                    time.sleep(1.5 * (attempt + 1))
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError) as e:
                last_err = LLMError(f"{bound['provider']} 调用失败: {e}")
                emit(f"  [{role}] {bound['provider']} 调用异常: {e}")
                if attempt < retries:
                    time.sleep(1.5 * (attempt + 1))
        if idx + 1 < len(chain):
            emit(f"  [{role}] 主通道失败，尝试 fallback: {chain[idx + 1]['provider']}/{chain[idx + 1]['model']}")

    raise LLMError(f"角色 {role} 所有通道均失败: {last_err}")


def chat_parallel(cfg: dict, jobs: list[dict]) -> list[dict]:
    """并行调用多个角色。jobs: [{role, messages, key?}]，结果按输入顺序返回。"""
    if not jobs:
        return []
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        futures = [
            pool.submit(chat, cfg, j["role"], j["messages"], silent=j.get("silent", False))
            for j in jobs
        ]
        results = []
        for f in futures:
            results.append(f.result())
    return results


# --------------------------------------------------------------------------
# 项目上下文收集
# --------------------------------------------------------------------------

def file_tree(root: str | Path, max_lines: int = 400) -> str:
    root = Path(root).resolve()
    lines: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
        rel_dir = Path(dirpath).relative_to(root)
        depth = len(rel_dir.parts)
        if depth > 6:
            dirnames[:] = []
            continue
        indent = "  " * depth
        if rel_dir != Path("."):
            lines.append(f"{indent}{rel_dir.name}/")
        for fn in sorted(filenames):
            if fn.startswith("."):
                continue
            lines.append(f"{indent}  {fn}")
        if len(lines) > max_lines:
            lines.append(f"... （文件树已截断，超过 {max_lines} 行）")
            break
    return "\n".join(lines)


def collect_context(
    paths: list[str],
    *,
    repo: str | None = None,
    max_chars: int = 300_000,
) -> str:
    """把指定文件/目录的源码拼成一段上下文。超限时按文件截断并明确标注。"""
    chunks: list[str] = []
    total = 0
    truncated: list[str] = []

    files: list[Path] = []
    for p in paths:
        path = Path(p).expanduser()
        if path.is_dir():
            for dirpath, dirnames, filenames in os.walk(path):
                dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
                for fn in sorted(filenames):
                    fp = Path(dirpath) / fn
                    if fp.suffix.lower() in SOURCE_EXT or fn in ("Dockerfile", "Makefile"):
                        files.append(fp)
        elif path.is_file():
            files.append(path)
        else:
            chunks.append(f"!! 路径不存在: {p}")

    for fp in files:
        try:
            text = fp.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            chunks.append(f"!! 读取失败 {fp}: {e}")
            continue
        label = str(fp)
        if repo:
            try:
                label = str(fp.resolve().relative_to(Path(repo).resolve()))
            except ValueError:
                label = str(fp)
        block = f"\n===== FILE: {label} =====\n{text}\n"
        if total + len(block) > max_chars:
            remain = max_chars - total
            if remain > 2000:
                chunk = block[:remain]
                chunks.append(chunk + "\n... （此文件已截断）\n")
                truncated.append(label)
                total = max_chars
            else:
                truncated.append(label)
            continue
        chunks.append(block)
        total += len(block)

    if repo:
        tree = file_tree(repo)
        header = f"===== PROJECT TREE: {repo} =====\n{tree}\n"
        chunks.insert(0, header)

    if truncated:
        chunks.append(
            "\n!! 以下文件因超出上下文预算未完整提供，涉及它们的判断必须标注为「未验证」：\n"
            + "\n".join(f"  - {t}" for t in truncated[:50])
        )
    return "".join(chunks)


def heterogeneity(cfg: dict, role_a: str, role_b: str) -> tuple[str, str]:
    """评估两个角色的模型异质程度。返回 (标记, 说明)。

    异质性的本质是「盲区是否重合」，判据是**模型族**而不是 API 通道：
      不同族（跨厂）  → 训练数据与偏好不同，盲区基本不重合（最强）
      同族不同尺寸    → 大模型靠先验补全，小模型严格照字面，能补偿一部分（中等）
      同族同模型      → 换会话只解决了记忆污染，盲区完全重合（等于没隔离）

    通道相同不代表模型同源：阿里百炼 Token Plan 一个通道里就有 Qwen / GLM /
    DeepSeek 三家模型，族不同，双审照样成立。
    """
    a = resolve_chain(cfg, role_a)[0]
    b = resolve_chain(cfg, role_b)[0]
    same_channel = "（同通道）" if a["provider"] == b["provider"] else ""
    if a["family"] != b["family"]:
        return "✅", f"不同模型族 {a['family']} vs {b['family']}{same_channel}，盲区基本不重合"
    if a["model"] != b["model"]:
        return "⚠️", f"同族不同尺寸 {a['model']} vs {b['model']}，字面化可补偿部分盲区，但非最强异质"
    return "❌", f"同族同模型 {a['model']}，只隔离了记忆，盲区完全重合"


def channel_risk(cfg: dict, roles: list[str]) -> str | None:
    """检查是不是所有角色都挤在同一个通道上——那是单点故障。"""
    used = set()
    for r in roles:
        if r in cfg.get("roles", {}):
            used.add(resolve_chain(cfg, r)[0]["provider"])
    if len(used) == 1:
        only = next(iter(used))
        return (
            f"全部角色都走同一通道 `{only}`：该通道故障会导致整个流程同时失败。"
            f"建议给 agent1 配 fallback，或把某一角色指向官方 API。"
        )
    return None


def list_models(cfg: dict, provider: str, timeout: int = 30) -> list[str]:
    """调 GET /models 列出通道可用模型 id，用来核对 config 里的名字。"""
    providers = cfg.get("providers", {})
    if provider not in providers:
        raise ConfigError(f"未定义 provider: {provider}")
    prov = providers[provider]
    url = prov["base_url"].rstrip("/") + "/models"
    req = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {prov.get('api_key', '')}"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise LLMError(f"{provider} 拉取模型列表失败 HTTP {e.code}: {e.read()[:200]}")
    except Exception as e:
        raise LLMError(f"{provider} 拉取模型列表失败: {e}")
    data = payload.get("data") or payload.get("models") or []
    ids = [item.get("id") or item.get("name") for item in data if isinstance(item, dict)]
    return sorted(x for x in ids if x)


def estimate_cost(cfg: dict, usage_list: list[dict], models: list[str]) -> float:
    pricing = cfg.get("pricing", {})
    total = 0.0
    for usage, model in zip(usage_list, models):
        price = pricing.get(model, 0)
        pt = usage.get("prompt_tokens", 0) or 0
        ct = usage.get("completion_tokens", 0) or 0
        total += (pt + ct) / 1_000_000 * price
    return total

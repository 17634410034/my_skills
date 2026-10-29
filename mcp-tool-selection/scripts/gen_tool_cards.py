#!/usr/bin/env python3
"""生成 / 合并 / 校验工具卡。

工具卡是给检索用的索引文本，与 schema 分开维护。规范见 references/tool-card-spec.md。

用法
----
  # 1. 拆分提示词（按 20~30 个一批，防止输出被截断）
  python3 gen_tool_cards.py split --tools tools.json --parts 4 --out ./cards_work

  # 2. 对每个 prompt_partN.txt 跑一次模型，产出 cards_partN.json
  #    放回 ./cards_work 目录

  # 3. 合并
  python3 gen_tool_cards.py merge --dir ./cards_work --out tool_cards.json

  # 4. 校验：卡片数必须等于工具数，话术条数够
  python3 gen_tool_cards.py check --cards tool_cards.json --tools tools.json

输入 tools.json 格式
-------------------
[{ "name": "food_search", "ns": "food", "desc": "...", "params": ["keyword"] }, ...]

params 可以是字符串列表，也可以是 JSON Schema 的 properties 对象。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

MIN_QUERIES = 5

PROMPT_HEAD = """你在为一个 MCP 工具库做「工具卡增强」，目的是让工具检索能听懂用户的自然语言。

下面每个工具给出 name / 所属领域 / 现有描述 / 参数。很多描述是空转的（比如
「xxx 平台的 yyy 操作」），完全看不出该在什么时候用它。请为每个工具产出：
  1. zh：一句中文说明「这个工具到底做什么、什么场景该调用它」，要能与同类工具区分开；
  2. queries：{n} 条用户可能的口语化说法，要有真实语气（例如「帮我把这个删了」），
     不要出现工具名里的英文单词，不要写成「调用 xx 工具」这种说明腔。

严格输出一个 JSON 对象，键是工具 name，值是 {{"zh": "...", "queries": ["...", ...]}}。
不要 markdown 代码块，不要注释，不要多余文字。

=== 工具清单 ===
"""


def load_tools(path: str) -> list[dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict):  # 兼容 {"tools": [...]}
        data = data.get("tools", [])
    return data


def fmt_params(params) -> str:
    if not params:
        return "无"
    if isinstance(params, dict):
        return ", ".join(params.keys())
    return ", ".join(str(p) for p in params)


def build_prompt(tools: list[dict], n_queries: int) -> str:
    lines = [PROMPT_HEAD.format(n=n_queries)]
    for t in tools:
        lines.append(f"\n[{t['name']}] 领域={t.get('ns', '-')}  参数={fmt_params(t.get('params'))}")
        lines.append(f"  现有描述: {t.get('desc') or '(无)'}")
        if t.get("aliases"):
            lines.append(f"  现有别名: {' / '.join(t['aliases'])}")
    return "\n".join(lines)


def cmd_split(a: argparse.Namespace) -> None:
    tools = load_tools(a.tools)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    if a.parts > len(tools):
        raise SystemExit(f"parts={a.parts} 超过工具数 {len(tools)}")

    # 按索引取模分配，保证每批的工具在命名空间上尽量分散
    total_chars = 0
    for i in range(a.parts):
        chunk = [t for j, t in enumerate(tools) if j % a.parts == i]
        text = build_prompt(chunk, a.queries)
        total_chars += len(text)
        f = out / f"prompt_part{i}.txt"
        f.write_text(text, encoding="utf-8")
        print(f"  {f}  {len(text):>7,} 字符  {len(chunk):>3} 个工具  ({chunk[0]['name']} ... {chunk[-1]['name']})")

    print(f"\n共 {len(tools)} 个工具，{a.parts} 批，{total_chars:,} 字符")
    print("下一步：对每个 prompt_partN.txt 跑一次模型，把输出存为同目录下的 cards_partN.json")
    print("\n⚠️ 只把这一个文件喂给模型。禁止同时提供任何评测任务文件，否则是数据泄漏。")


def cmd_merge(a: argparse.Namespace) -> None:
    d = Path(a.dir)
    merged: dict[str, dict] = {}
    files = sorted(d.glob("cards_part*.json"))
    if not files:
        raise SystemExit(f"{d} 下没有 cards_part*.json")
    for f in files:
        part = json.loads(f.read_text(encoding="utf-8"))
        dup = set(part) & set(merged)
        if dup:
            print(f"  ⚠️ {f.name} 与已有卡片重复 {len(dup)} 个: {list(dup)[:3]}...")
        merged.update(part)
        print(f"  {f.name:<20} +{len(part):>4}  累计 {len(merged):>4}")
    Path(a.out).write_text(json.dumps(merged, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n已写出 {a.out}，共 {len(merged)} 张卡片")


def cmd_check(a: argparse.Namespace) -> None:
    cards = json.loads(Path(a.cards).read_text(encoding="utf-8"))
    tools = load_tools(a.tools)
    names = {t["name"] for t in tools}

    missing = sorted(names - set(cards))
    extra = sorted(set(cards) - names)
    thin = sorted(k for k, v in cards.items() if len(v.get("queries") or []) < MIN_QUERIES)
    empty_zh = sorted(k for k, v in cards.items() if not (v.get("zh") or "").strip())
    # 话术里混入工具名英文词，会让卡片只对英文查询有效
    leaked = sorted(
        k for k, v in cards.items()
        if any(w.lower() in q.lower() for q in (v.get("queries") or []) for w in k.split("_") if len(w) > 3)
    )

    print(f"工具 {len(names)} 个，卡片 {len(cards)} 张\n")
    ok = True
    for label, items, fatal in (
        ("缺少卡片（会变成召回漏洞）", missing, True),
        ("多出卡片（工具已删除？）", extra, False),
        (f"话术不足 {MIN_QUERIES} 条", thin, False),
        ("zh 为空", empty_zh, True),
        ("话术里混入了工具名英文词", leaked, False),
    ):
        if items:
            mark = "✗" if fatal else "⚠"
            print(f"  {mark} {label}: {len(items)} 个")
            for x in items[:8]:
                print(f"      {x}")
            if len(items) > 8:
                print(f"      ... 还有 {len(items)-8} 个")
            ok = ok and not fatal

    if ok and not (thin or leaked or extra):
        print("  ✓ 全部通过")
    print()
    raise SystemExit(0 if (ok and not thin) else 1)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("split", help="拆分生成提示词")
    s.add_argument("--tools", required=True)
    s.add_argument("--parts", type=int, default=4)
    s.add_argument("--queries", type=int, default=6, help="每个工具要求几条话术")
    s.add_argument("--out", default="./cards_work")
    s.set_defaults(func=cmd_split)

    m = sub.add_parser("merge", help="合并分批结果")
    m.add_argument("--dir", default="./cards_work")
    m.add_argument("--out", default="tool_cards.json")
    m.set_defaults(func=cmd_merge)

    c = sub.add_parser("check", help="校验覆盖与质量")
    c.add_argument("--cards", required=True)
    c.add_argument("--tools", required=True)
    c.set_defaults(func=cmd_check)

    a = ap.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()

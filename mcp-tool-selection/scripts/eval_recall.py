#!/usr/bin/env python3
"""工具选择召回评测：给你自己的工具库算 recall@K、schema token，并按失败环节归因。

只测**召回**（正确工具在不在候选集里），不调模型。这是整套评测里最便宜、信息量最大的一层：
召回率就是端到端准确率的**天花板**，低于 95% 时优化模型侧是白费力气。

用法
----
  # 只有原始描述（基线）
  python3 eval_recall.py --tools tools.json --tasks tasks.json --k 10

  # 加了工具卡
  python3 eval_recall.py --tools tools.json --tasks tasks.json --cards tool_cards.json --k 10

  # 扫 K 曲线，找最小可用 K
  python3 eval_recall.py --tools tools.json --tasks tasks.json --cards tool_cards.json --k-sweep 4,6,8,10,16,24

  # 只用 hold-out 验收（切分比例固定，避免调参污染）
  python3 eval_recall.py ... --split holdout --dev-ratio 0.4

  # 漏召明细（看是哪个环节掉的）
  python3 eval_recall.py ... --show-miss

输入格式
--------
tools.json : [{"name","ns","desc","params"?,"aliases"?,"schema"?}, ...]
tasks.json : [{"id","query","gold","level"?}, ...]   level ∈ easy/medium/hard

输出
----
  召回率（分难度）、平均候选数、schema token/次、节省比例、漏召明细
"""

from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter
from pathlib import Path

# ---------------------------------------------------------------- tokenizer

_ASCII = re.compile(r"[a-z0-9_]+")
_CJK_RANGE = ("\u4e00", "\u9fff")

# 这些字在中文查询里几乎不携带领域信号，留着只会稀释 BM25 权重
_STOP = set("的 了 我 你 他 她 它 是 在 有 和 就 都 也 还 吧 吗 呢 啊 这 那 下 上 给 帮 把 到 要 会 能 请 一 个 么 什 怎".split())


def tokenize(text: str) -> list[str]:
    """ASCII 按词切（下划线再拆一次），CJK 同时出单字和 bigram。

    单字 + bigram 两条都要：bigram 负责真正的词（"体重"），单字负责单字词（"卡""油"）。
    只做 bigram 会漏掉单字查询，只做单字会丢掉词序信息。实测差异可达 2~3 个百分点。
    """
    text = (text or "").lower()
    out: list[str] = []
    for w in _ASCII.findall(text):
        out.append(w)
        if "_" in w:
            out.extend(w.split("_"))

    cjk = [c for c in text if _CJK_RANGE[0] <= c <= _CJK_RANGE[1]]
    out.extend(c for c in cjk if c not in _STOP)
    out.extend(cjk[i] + cjk[i + 1] for i in range(len(cjk) - 1))
    return out


class BM25:
    def __init__(self, docs: list[list[str]], k1: float = 1.2, b: float = 0.6):
        self.docs = docs
        self.k1, self.b = k1, b
        self.n = len(docs)
        self.avgdl = sum(len(d) for d in docs) / max(self.n, 1)
        self.df: Counter[str] = Counter()
        for d in docs:
            self.df.update(set(d))
        self.tf = [Counter(d) for d in docs]

    def idf(self, term: str) -> float:
        df = self.df.get(term, 0)
        return math.log(1 + (self.n - df + 0.5) / (df + 0.5))

    def scores(self, q: list[str]) -> list[float]:
        out = []
        for i in range(self.n):
            tf, dl = self.tf[i], len(self.docs[i]) or 1
            s = 0.0
            for t in q:
                f = tf.get(t, 0)
                if f:
                    s += self.idf(t) * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * dl / self.avgdl))
            out.append(s)
        return out


# ---------------------------------------------------------------- 索引文本

def index_text(tool: dict, card: dict | None) -> str:
    """拼出该工具的索引文档。

    字段加权用「重复次数」实现（BM25 下重复 = 加权），这是不依赖检索后端的最省事做法。
    权重依据实测：用户话术 > 语义说明 ≈ 工具名 > 别名 > 原始描述。

    原始描述权重最低是有意的——如果它写得好，就不需要工具卡；写得差（"xxx 平台的 yyy 操作"）
    也不该让它污染索引。
    """
    name = tool["name"].replace("_", " ") + " " + tool["name"]
    parts = [name] * 4
    if card:
        if card.get("zh"):
            parts += [card["zh"]] * 2
        if card.get("queries"):
            parts += [" ".join(card["queries"])] * 2
    if tool.get("aliases"):
        parts += [" ".join(tool["aliases"])] * 2
    if tool.get("desc"):
        parts.append(tool["desc"])
    if tool.get("ns"):
        parts.append(tool["ns"])
    return " ".join(p for p in parts if p)


def schema_tokens(tool: dict) -> int:
    """序列化一份工具定义进 prompt 大致占多少 token。ASCII 约 4 字符/token，CJK 约 1.5 字符/token。"""
    blob = json.dumps(tool.get("schema") or tool, ensure_ascii=False)
    ascii_n = sum(1 for c in blob if ord(c) < 128)
    return int(ascii_n / 4 + (len(blob) - ascii_n) / 1.5)


# ---------------------------------------------------------------- 主流程

def load(path: str | None):
    if not path:
        return None
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tools", required=True)
    ap.add_argument("--tasks", required=True)
    ap.add_argument("--cards", help="工具卡 JSON；不给则只有原始描述")
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--k-sweep", help="逗号分隔的 K 列表，扫曲线用")
    ap.add_argument("--split", choices=["all", "dev", "holdout"], default="all")
    ap.add_argument("--dev-ratio", type=float, default=0.4)
    ap.add_argument("--show-miss", action="store_true", help="打印漏召样本")
    a = ap.parse_args()

    tools = load(a.tools)
    tasks = load(a.tasks)
    cards = load(a.cards) or {}

    # 稳定切分：按 id 排序后取前 dev_ratio 为 dev
    if a.split != "all":
        ordered = sorted(tasks, key=lambda t: t["id"])
        cut = int(len(ordered) * a.dev_ratio)
        dev, hold = ordered[:cut], ordered[cut:]
        tasks = dev if a.split == "dev" else hold

    tmap = {t["name"]: t for t in tools}
    names = [t["name"] for t in tools]
    missing_card = [n for n in names if n not in cards]

    idx = BM25([tokenize(index_text(t, cards.get(t["name"]))) for t in tools])
    total_schema = sum(schema_tokens(t) for t in tools)

    print(f"工具 {len(tools)} 个   任务 {len(tasks)} 条 ({a.split})   工具卡 {len(cards)} 张")
    if not cards:
        print("  ⚠️ 未提供工具卡，这是基线（无卡）结果")
    elif missing_card:
        print(f"  ⚠️ {len(missing_card)} 个工具没有卡片，会成为召回漏洞: {missing_card[:5]}")
    print(f"全量暴露 = {total_schema:,} token/次\n")

    def run(k: int, quiet: bool = False) -> None:
        hits = 0
        tok = 0
        ncand = 0
        by_level: dict[str, list[int]] = {}
        misses = []
        for t in tasks:
            sc = idx.scores(tokenize(t["query"]))
            order = sorted(range(len(names)), key=lambda i: -sc[i])[:k]
            cands = [names[i] for i in order]
            hit = t["gold"] in cands
            hits += hit
            ncand += len(cands)
            tok += sum(schema_tokens(tmap[c]) for c in cands)
            by_level.setdefault(t.get("level", "-"), []).append(int(hit))
            if not hit and not quiet:
                rank = next((i + 1 for i, n in enumerate(names) if n == t["gold"]), 999)
                misses.append((t, rank))

        n = len(tasks)
        print(f"K={k:<3} 召回 {hits/n:6.1%}   候选 {ncand/n:5.1f}   schema {tok/n:6.0f} token   省 {1-(tok/n)/total_schema:5.1%}")
        lv = "  ".join(f"{x} {sum(v)/len(v):.0%}({sum(v)}/{len(v)})" for x, v in sorted(by_level.items()))
        print(f"     分难度: {lv}")

        if misses and not quiet:
            print(f"\n     漏召 {len(misses)} 条：")
            for t, rank in misses:
                stage = "检索排太低（排名 > K）" if rank <= len(names) else "不在注册表里"
                print(f"       [{t.get('level','-')}] {t['query'][:34]}")
                print(f"             gold={t['gold']}  排名={rank if rank<=len(names) else '未命中'}  {stage}")
        print()

    if a.k_sweep:
        print("— K 曲线 —")
        for k in [int(x) for x in a.k_sweep.split(",")]:
            run(k, quiet=True)
        print()
    run(a.k)


if __name__ == "__main__":
    main()

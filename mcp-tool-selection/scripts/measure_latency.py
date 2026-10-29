#!/usr/bin/env python3
"""测量 TTFT（首 token 时间）随 prompt 长度的变化，用于判断「塞更多工具 schema 到底慢多少」。

只测 prefill 阶段（首 token），因为 decode 阶段两个方案一样长，不影响对比。

用法
----
  # 用填充文本测纯长度效应
  python3 measure_latency.py --base-url http://host:9021/v1 --api-key sk-xxx \
      --model qwen3.7-flash --sizes 500,3000,6000,10000,24000

  # 用真实工具清单测（更准，但要自己准备文件）
  python3 measure_latency.py ... --prompt-dir ./prompts

  # 关掉思考模式（qwen 系必须）
  python3 measure_latency.py ... --no-thinking

  # 测网关固定开销
  python3 measure_latency.py ... --sizes 20 --runs 3

输出
----
  每个规模的 TTFT 中位数、固定开销估计、边际吞吐（token/s）、每 1000 工具的成本

⚠️ 两个前提，测之前先确认
------------------------
1. **流式是否被网关缓冲**。发一个极短 prompt，如果 TTFT 接近总耗时，说明被缓冲了，
   这个数不是 TTFT。脚本会自动警告。
2. **思考模式是否关闭**。qwen3.x 默认开思考，会先吐几百 reasoning token，max_tokens 压不住。
   不开 --no-thinking 测出来的全是思考时间。
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
import urllib.error
import urllib.request
from pathlib import Path

FILLER = "以下是工具定义说明，用于测试上下文长度对推理延迟的影响。这些内容不代表真实业务，仅用于量测。"


def build_filler(target_chars: int) -> str:
    head = "请阅读下面的工具清单，然后只回复一个词：OK\n\n"
    body = FILLER * (target_chars // len(FILLER) + 1)
    return head + body[:target_chars]


class Client:
    def __init__(self, base_url: str, api_key: str, model: str, no_thinking: bool, max_tokens: int):
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.key = api_key
        self.model = model
        self.no_thinking = no_thinking
        self.max_tokens = max_tokens
        # 关键：绕过 HTTP_PROXY，否则本地/内网端点会被代理劫持成 502
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def once(self, prompt: str) -> tuple[float | None, float, dict, str]:
        body = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": self.max_tokens,
            "temperature": 0,
            "stream": True,
        }
        if self.no_thinking:
            body["chat_template_kwargs"] = {"enable_thinking": False}

        req = urllib.request.Request(
            self.url,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.key}"},
        )
        t0 = time.perf_counter()
        ttft = None
        usage: dict = {}
        txt = ""
        with self.opener.open(req, timeout=600) as resp:
            for raw in resp:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    d = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                if d.get("usage"):
                    usage = d["usage"]
                ch = d.get("choices") or []
                delta = (ch[0].get("delta") or {}) if ch else {}
                if delta.get("content"):
                    if ttft is None:
                        ttft = time.perf_counter() - t0
                    txt += delta["content"]
        return ttft, time.perf_counter() - t0, usage, txt.strip()[:24]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-url", required=True, help="如 http://host:9021/v1")
    ap.add_argument("--api-key", default="dummy")
    ap.add_argument("--model", required=True)
    ap.add_argument("--sizes", default="500,3000,6000,10000,24000", help="目标字符数，逗号分隔")
    ap.add_argument("--prompt-dir", help="改用目录下的 .txt 作为各规模 prompt（优先于 --sizes）")
    ap.add_argument("--runs", type=int, default=5, help="每档测几次，取中位数（第 1 次丢弃，预热）")
    ap.add_argument("--max-tokens", type=int, default=16)
    ap.add_argument("--no-thinking", action="store_true", help="关闭思考模式（qwen 系必须）")
    a = ap.parse_args()

    c = Client(a.base_url, a.api_key, a.model, a.no_thinking, a.max_tokens)

    cases: list[tuple[str, int, str]] = []
    if a.prompt_dir:
        for f in sorted(Path(a.prompt_dir).glob("*.txt")):
            txt = f.read_text(encoding="utf-8")
            cases.append((f.stem, len(txt), txt))
    else:
        for s in [int(x) for x in a.sizes.split(",")]:
            cases.append((f"{s}字符", s, build_filler(s)))

    print(f"模型 {a.model}   端点 {a.base_url}   每档 {a.runs} 次   思考模式 {'关' if a.no_thinking else '开'}\n")

    rows = []
    for label, nchars, prompt in cases:
        ts, totals, usage, sample = [], [], {}, ""
        try:
            for i in range(a.runs):
                ttft, total, u, txt = c.once(prompt)
                if i == 0:  # 丢弃预热
                    continue
                if ttft is None:
                    continue
                ts.append(ttft)
                totals.append(total)
                usage, sample = u, txt
        except urllib.error.HTTPError as e:
            print(f"  {label:<12} HTTP {e.code}: {e.read().decode()[:160]}")
            continue
        except Exception as e:
            print(f"  {label:<12} {type(e).__name__}: {str(e)[:120]}")
            continue

        if not ts:
            print(f"  {label:<12} 没有有效样本")
            continue
        mt, ma = statistics.median(ts), statistics.median(totals)
        ptok = usage.get("prompt_tokens")
        rows.append((label, ptok, mt, ma))
        print(f"  {label:<12} TTFT {mt:6.2f}s   总 {ma:6.2f}s   "
              f"in={ptok if ptok else '?':>7}   out={usage.get('completion_tokens', '?'):>3}   «{sample}»")

        # 缓冲诊断：TTFT 接近总耗时说明流式没生效
        if ma > 0 and mt / ma > 0.9 and ma > 1.0:
            print(f"       ⚠️ TTFT/总耗时 = {mt/ma:.0%}，流式可能被网关缓冲，这个数不是真的 TTFT")

    valid = [(p, t) for _, p, t, _ in rows if p]
    if len(valid) >= 2:
        (p1, t1), (p2, t2) = valid[0], valid[-1]
        d_tok, d_t = p2 - p1, t2 - t1
        print(f"\n— 拟合 —")
        print(f"  固定开销 ≈ {t1 - (d_t / d_tok) * p1:.2f} s（网络+网关+调度）")
        if d_t > 0:
            print(f"  边际吞吐 ≈ {d_tok / d_t:,.0f} token/s")
            print(f"  每多 1,000 token 多付 {1000 * d_t / d_tok * 1000:.0f} ms")
    else:
        print("\n  样本不足，无法拟合。至少需要两档带 prompt_tokens 的数据。")


if __name__ == "__main__":
    main()

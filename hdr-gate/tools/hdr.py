#!/usr/bin/env python3
"""HDR Gate 编排器 —— 用一条命令串起 9 个阶段。

用法：
    python3 hdr.py init      --name 用户登录态改造 --repo /path/to/repo --req "需求原文"
    python3 hdr.py draft     --name 用户登录态改造
    python3 hdr.py review    --name 用户登录态改造
    python3 hdr.py merge     --name 用户登录态改造
    python3 hdr.py fix       --name 用户登录态改造
    python3 hdr.py sign      --name 用户登录态改造
    python3 hdr.py coldread  --name 用户登录态改造
    python3 hdr.py finalize  --name 用户登录态改造
    python3 hdr.py status    --name 用户登录态改造
    python3 hdr.py doctor

    # 一条龙（遇到需要人工判断的地方会停）
    python3 hdr.py run --name 用户登录态改造 --auto
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

import llm
import prompts

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE.parent / "dev-doc-template.md"
WORKROOT = Path(".hdr-gate")


# --------------------------------------------------------------------------
# 项目管理
# --------------------------------------------------------------------------

class Project:
    def __init__(self, name: str, root: Path | None = None):
        self.name = name
        self.dir = (root or WORKROOT) / name
        self.meta_path = self.dir / "meta.json"

    @property
    def exists(self) -> bool:
        return self.meta_path.exists()

    def meta(self) -> dict:
        if not self.exists:
            raise SystemExit(f"项目不存在: {self.dir}，先执行 init")
        return json.loads(self.meta_path.read_text(encoding="utf-8"))

    def save_meta(self, meta: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self.meta_path.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def path(self, filename: str) -> Path:
        return self.dir / filename

    def read(self, filename: str) -> str:
        p = self.path(filename)
        if not p.exists():
            raise SystemExit(f"缺少文件 {p}，请先完成前置阶段")
        return p.read_text(encoding="utf-8")

    def write(self, filename: str, content: str) -> Path:
        p = self.path(filename)
        p.write_text(content, encoding="utf-8")
        return p

    def add_cost(self, meta: dict, result: dict, step: str) -> None:
        cost = llm.estimate_cost(
            llm.load_config(meta.get("config")), [result["usage"]], [result["model"]]
        )
        meta.setdefault("cost", 0.0)
        meta["cost"] = round(meta["cost"] + cost, 4)
        meta.setdefault("history", []).append(
            {
                "step": step,
                "role_model": f"{result['provider']}/{result['model']}",
                "elapsed": round(result["elapsed"], 1),
                "tokens": result.get("usage", {}),
                "at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
        )


def extract_doc(text: str) -> str:
    for marker in ("===文档===", "===DOC==="):
        if marker in text:
            return text.split(marker, 1)[1].strip()
    return text.strip()


def extract_table(text: str) -> str:
    if "===对照表===" in text:
        head = text.split("===对照表===", 1)[1]
        for end in ("===文档===", "===DOC==="):
            if end in head:
                head = head.split(end, 1)[0]
                break
        return head.strip()
    return ""


def parse_signoff(text: str) -> tuple[bool, str]:
    m = re.search(r"SIGNOFF:\s*(可交接|不可交接)\s*(?:——\s*(.+))?", text)
    if not m:
        return False, "未找到 SIGNOFF 结论"
    ok = m.group(1) == "可交接"
    return ok, (m.group(2) or "").strip()


def count_gaps(text: str) -> int:
    return len(re.findall(r"\[GAP-\d+\]", text))


def load_ctx(meta: dict, extra: list[str] | None = None) -> str:
    repo = meta.get("repo")
    paths = list(meta.get("context", [])) + (extra or [])
    if not paths and not repo:
        return "（未提供项目上下文；本次判断必须标注为基于文档自身，未验证代码）"
    return llm.collect_context(paths, repo=repo, max_chars=meta.get("max_context_chars", 300_000))


def cfg_of(meta: dict) -> dict:
    return llm.load_config(meta.get("config"))


# --------------------------------------------------------------------------
# 各阶段
# --------------------------------------------------------------------------

def cmd_init(args) -> None:
    proj = Project(args.name)
    if proj.exists and not args.force:
        raise SystemExit(f"{proj.dir} 已存在，加 --force 覆盖")
    req = args.req
    if args.req_file:
        req = Path(args.req_file).read_text(encoding="utf-8").strip()
    if not req:
        raise SystemExit("必须提供 --req 或 --req-file")
    proj.dir.mkdir(parents=True, exist_ok=True)
    proj.save_meta(
        {
            "name": args.name,
            "requirement": req,
            "repo": str(Path(args.repo).resolve()) if args.repo else None,
            "context": [str(Path(p).resolve()) for p in (args.context or [])],
            "config": str(Path(args.config).resolve()) if args.config else None,
            "max_context_chars": args.max_context_chars,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "stage": "init",
            "cost": 0.0,
            "history": [],
        }
    )
    print(f"已创建: {proj.dir}")
    if getattr(args, "assume", None):
        lines = ["# 假设账本", "", "> 未确认的前提逐条编号。每条必须有依据；阶段 2 的 Agent2 会逐条到仓库里打假。", ""]
        for raw in args.assume:
            parts = [p.strip() for p in raw.split("|")]
            aid = parts[0] or f"A{len(lines) - 2}"
            claim = parts[1] if len(parts) > 1 else ""
            basis = parts[2] if len(parts) > 2 else "（未写依据 → 审稿人无法核实）"
            lines.append(f"- **{aid}** · {claim}\n  - 依据：{basis}\n  - 状态：待确认")
        proj.write("assumptions.md", "\n".join(lines) + "\n")
        print(f"假设账本: {proj.dir / 'assumptions.md'}（{len(args.assume)} 条）")
    print(f"下一步: python3 hdr.py draft --name {args.name}")


def cmd_draft(args) -> None:
    proj = Project(args.name)
    meta = proj.meta()
    cfg = cfg_of(meta)
    template = TEMPLATE.read_text(encoding="utf-8")
    ctx = load_ctx(meta, args.context)
    assume_file = proj.dir / "assumptions.md"
    if assume_file.exists():
        ctx += "\n\n## 已登记的假设（必须原样写进文档 §0.1，并补全「若不成立的影响」）\n" + assume_file.read_text(
            encoding="utf-8"
        )

    print(f"[1/8] Agent1 正在分析项目并撰写 v0.1（上下文 {len(ctx)} 字符）…")
    result = llm.chat(cfg, "agent1", prompts.draft_messages(meta["requirement"], ctx, template))
    proj.write("v0.1.md", extract_doc(result["text"]))
    proj.add_cost(meta, result, "draft")
    meta["stage"] = "draft"
    proj.save_meta(meta)

    doc = proj.read("v0.1.md")
    checks = {
        "任务卡(T1…)": bool(re.search(r"###\s*T1\b|##\s*5\.\s*任务卡", doc)),
        "验收断言(命令)": "```" in doc,
        "非目标清单": "非目标" in doc,
    }
    print(f"  → {proj.path('v0.1.md')}")
    for k, ok in checks.items():
        print(f"  {'✅' if ok else '⚠️ '} {k}")
    if not all(checks.values()):
        print("  ⚠️  门禁未通过：缺章节按 SOP 应打回重写，建议加 --context 后重跑 draft")
    print(f"下一步: python3 hdr.py review --name {args.name}")


def cmd_review(args) -> None:
    proj = Project(args.name)
    meta = proj.meta()
    cfg = cfg_of(meta)
    doc = proj.read(args.doc)
    ctx = load_ctx(meta)

    print("[2/8] Agent2(GLM) 与 Agent3(DeepSeek) 背对背并行评审…")
    jobs = [
        {"role": "agent2", "messages": prompts.review_feasibility_messages(doc, ctx)},
        {"role": "agent3", "messages": prompts.review_correctness_messages(doc, ctx)},
    ]
    res = llm.chat_parallel(cfg, jobs)
    proj.write("review-a.md", res[0]["text"])
    proj.write("review-b.md", res[1]["text"])
    for r in res:
        proj.add_cost(meta, r, "review")
    meta["stage"] = "review"
    proj.save_meta(meta)

    p0a = len(re.findall(r"\[P0-", res[0]["text"]))
    p0b = len(re.findall(r"\[P0-", res[1]["text"]))
    print(f"  → review-a.md（可行性）P0×{p0a}   review-b.md（正确性）P0×{p0b}")
    print(f"下一步: python3 hdr.py merge --name {args.name}")


def cmd_merge(args) -> None:
    proj = Project(args.name)
    meta = proj.meta()
    cfg = cfg_of(meta)
    a = proj.read("review-a.md")
    b = proj.read("review-b.md")

    print("[3/8] Agent4(flash) 机械归并…")
    result = llm.chat(cfg, "agent4", prompts.merge_messages(a, b))
    proj.write("merged.md", result["text"])
    proj.add_cost(meta, result, "merge")
    meta["stage"] = "merge"
    proj.save_meta(meta)

    print(f"  → {proj.path('merged.md')}")
    print("  ⚠️  主持需人工复核分级（Agent4 不定级）：检查溯源表有无错并/漏并")
    print(f"下一步: python3 hdr.py fix --name {args.name}")


def cmd_fix(args) -> None:
    proj = Project(args.name)
    meta = proj.meta()
    cfg = cfg_of(meta)
    doc = proj.read(args.doc)
    merged = proj.read(args.merged)

    print("[4/8] Agent1 一次性修复…")
    result = llm.chat(cfg, "agent1", prompts.fix_messages(doc, merged))
    table = extract_table(result["text"])
    if table:
        proj.write("fix-table.md", table)
    proj.write(args.out, extract_doc(result["text"]))
    proj.add_cost(meta, result, "fix")
    meta["stage"] = "fix"
    proj.save_meta(meta)

    print(f"  → {proj.path(args.out)}" + (f"（对照表: fix-table.md）" if table else ""))
    print(f"下一步: python3 hdr.py sign --name {args.name}")


def cmd_sign(args) -> None:
    proj = Project(args.name)
    meta = proj.meta()
    cfg = cfg_of(meta)
    doc = proj.read(args.doc)
    resolution = ""
    if proj.path("resolution.md").exists():
        resolution = proj.read("resolution.md")

    print("[6/8] 复审双签（并行，只看自己上一轮的 P0）…")
    jobs = [
        {
            "role": "agent2",
            "messages": prompts.sign_messages(doc, proj.read("review-a.md"), resolution, "feasibility"),
        },
        {
            "role": "agent3",
            "messages": prompts.sign_messages(doc, proj.read("review-b.md"), resolution, "correctness"),
        },
    ]
    res = llm.chat_parallel(cfg, jobs)
    ok_a, why_a = parse_signoff(res[0]["text"])
    ok_b, why_b = parse_signoff(res[1]["text"])

    report = [
        "# 复审双签记录",
        "",
        f"- Agent2（可行性）: {'✅ 可交接' if ok_a else '❌ 不可交接'} {why_a}",
        f"- Agent3（正确性）: {'✅ 可交接' if ok_b else '❌ 不可交接'} {why_b}",
        "",
        "---",
        "",
        "## Agent2 复审全文",
        res[0]["text"],
        "",
        "## Agent3 复审全文",
        res[1]["text"],
    ]
    proj.write("sign.md", "\n".join(report))
    for r in res:
        proj.add_cost(meta, r, "sign")

    if ok_a and ok_b:
        proj.write("v0.3.md", doc)
        meta["stage"] = "signoff"
        proj.save_meta(meta)
        print(f"  ✅ 双签通过 → v0.3.md")
        print(f"下一步: python3 hdr.py coldread --name {args.name}")
    else:
        meta["stage"] = "signoff-failed"
        proj.save_meta(meta)
        print(f"  ❌ 双签未通过：{why_a or why_b}")
        print(f"  详见 {proj.path('sign.md')}；修复后重跑 fix → sign（返工上限 2 轮）")
        sys.exit(2)


def cmd_coldread(args) -> None:
    proj = Project(args.name)
    meta = proj.meta()
    cfg = cfg_of(meta)
    doc = proj.read(args.doc)
    ctx = load_ctx(meta)

    role = "coldread"
    if meta.get("repo") is None and args.context:
        ctx = llm.collect_context(args.context, repo=None, max_chars=meta.get("max_context_chars", 300_000))

    print("[7/8] 冷读试跑：陌生视角，只给文档 + 代码，不许提问…")
    print(f"  模型: {cfg['roles'][role]['provider']}/{cfg['roles'][role]['model']}")
    result = llm.chat(cfg, role, prompts.coldread_messages(doc, ctx, meta.get("repo")))
    proj.write("coldread.md", result["text"])
    proj.add_cost(meta, result, "coldread")
    meta["stage"] = "coldread"
    proj.save_meta(meta)

    gaps = count_gaps(result["text"])
    print(f"  → {proj.path('coldread.md')}  缺口 GAP×{gaps}")
    if gaps:
        print("  ⚠️  有缺口：属信息缺失 → finalize 补齐；属方案缺陷 → 回 fix 改方案后重走 sign/coldread")
    else:
        print("  ✅ 无缺口")
    print(f"下一步: python3 hdr.py finalize --name {args.name}")


def cmd_finalize(args) -> None:
    proj = Project(args.name)
    meta = proj.meta()
    cfg = cfg_of(meta)
    doc = proj.read(args.doc)
    gaps = proj.read("coldread.md")

    print("[8/8] Agent1 按缺口补齐（只增信息，不改方案）…")
    result = llm.chat(cfg, "agent1", prompts.finalize_messages(doc, gaps))
    table = extract_table(result["text"])
    if table:
        proj.write("finalize-table.md", table)
    body = extract_doc(result["text"])

    if "SCHEME-DEFECT" in result["text"][:2000]:
        proj.write("v0.3.rejected.md", body)
        print("  ❌ 检测到 !! SCHEME-DEFECT：补齐过程需要改方案，流程应回退到 fix 阶段")
        print(f"  详见 {proj.path('v0.3.rejected.md')}")
        sys.exit(3)

    out = Path(args.out) if args.out else Path.cwd() / f"{proj.name}.md"
    out.write_text(body, encoding="utf-8")
    proj.add_cost(meta, result, "finalize")
    meta["stage"] = "delivered"
    meta["delivered_to"] = str(out)
    proj.save_meta(meta)

    print(f"  ✅ 交付: {out}")
    print(f"  本次累计估算成本: ¥{meta['cost']:.4f}")
    print("  下一步（人工）：开一个**新会话**，把这份文档交给它执行。不要在当前会话直接写代码。")


def cmd_status(args) -> None:
    proj = Project(args.name)
    meta = proj.meta()
    stages = [
        ("init", "初始化", None),
        ("draft", "v0.1 初稿", "v0.1.md"),
        ("review", "双审清单", "review-a.md"),
        ("merge", "合并清单", "merged.md"),
        ("fix", "v0.2 修复版", "v0.2.md"),
        ("signoff", "v0.3 双签版", "v0.3.md"),
        ("coldread", "冷读缺口", "coldread.md"),
        ("delivered", "交付", None),
    ]
    print(f"项目: {meta['name']}")
    print(f"仓库: {meta.get('repo') or '（未指定）'}")
    print(f"阶段: {meta.get('stage')}")
    print(f"累计成本: ¥{meta.get('cost', 0):.4f}")
    print("\n产物:")
    for _, label, fname in stages:
        if fname:
            p = proj.path(fname)
            mark = "✅" if p.exists() else "—"
            size = f"{p.stat().st_size} B" if p.exists() else ""
            print(f"  {mark} {label:<12} {fname:<14} {size}")
    if meta.get("history"):
        print("\n调用记录:")
        for h in meta["history"]:
            print(f"  {h['at']}  {h['step']:<10} {h['role_model']:<28} {h['elapsed']}s")


def cmd_doctor(args) -> None:
    print("== HDR Gate 自检 ==")
    print(f"Python: {sys.version.split()[0]}")
    try:
        cfg = llm.load_config(args.config)
        print(f"配置: {cfg['_path']} ✅")
    except llm.ConfigError as e:
        print(f"配置: ❌ {e}")
        sys.exit(1)

    print("\n通道:")
    unconfigured = []
    for name, prov in cfg["providers"].items():
        key = prov.get("api_key", "")
        state = "✅ 已配置" if key else "⚠️  缺密钥"
        if not key:
            unconfigured.append(name)
        masked = (key[:6] + "…" + key[-4:]) if len(key) > 12 else ("(空)" if not key else "***")
        print(f"  {name:<10} {prov['base_url']:<52} {state} {masked}")

    print("\n角色绑定:")
    roles_ok = {}
    for role in cfg["roles"]:
        chain = llm.resolve_chain(cfg, role)
        desc = " → ".join(f"{c['provider']}/{c['model']} [{c['family']}]" for c in chain)
        roles_ok[role] = all(c["provider"] not in unconfigured for c in chain)
        flag = "" if roles_ok[role] else "   ← 该角色无可用通道"
        print(f"  {role:<10} {desc}{flag}")

    print("\n异质性检查（看模型族，不看通道）:")
    try:
        checks = (
            ("agent2", "Agent2 可行性审  vs Agent1"),
            ("agent3", "Agent3 正确性审  vs Agent1"),
            ("coldread", "冷读试跑者     vs Agent1"),
        )
        for role_b, label in checks:
            if not roles_ok.get("agent1") or not roles_ok.get(role_b):
                print(f"  {label}: ⏸ 未验证（缺密钥，配置再漂亮也跑不起来）")
                continue
            mark, why = llm.heterogeneity(cfg, "agent1", role_b)
            print(f"  {label}: {mark} {why}")
        risk = llm.channel_risk(cfg, list(cfg["roles"]))
        if risk:
            print(f"\n  ⚠️  {risk}")
    except Exception as e:
        print(f"  ❌ 检查失败: {e}")

    print("\n模板:")
    print(f"  {TEMPLATE} {'✅' if TEMPLATE.exists() else '❌ 缺失'}")

    if unconfigured:
        dead_roles = [r for r, ok in roles_ok.items() if not ok]
        print("\n❌ 自检未通过：以下通道缺密钥 →", ", ".join(unconfigured))
        print("   受影响角色:", ", ".join(dead_roles) or "无")
        print("   → 此时双审**不可能**跨族，最多做「同族多会话」降级。")
        print("   → 补密钥: export TOKENPLAN_API_KEY=...  然后重跑本命令")
        print("   → 明知故犯可加 --allow-missing（退出码 0），但降级必须在交付时声明。")
        if not getattr(args, "allow_missing", False):
            sys.exit(2)


def cmd_models(args) -> None:
    """列出通道里的真实模型 id —— 用来核对 config.toml 里有没有写错名字。"""
    cfg = llm.load_config(args.config)
    targets = [args.provider] if args.provider else list(cfg["providers"])
    for pname in targets:
        prov = cfg["providers"][pname]
        key = prov.get("api_key", "")
        print(f"\n[{pname}] {prov['base_url']}")
        if not key:
            print("  跳过：未配置 api_key（先 export 对应环境变量）")
            continue
        try:
            ids = llm.list_models(cfg, pname)
        except llm.LLMError as e:
            print(f"  ❌ {e}")
            continue
        if not ids:
            print("  （返回空列表）")
        for mid in ids:
            print(f"  {mid:<34} family={llm.infer_family(mid)}")
        print(f"  共 {len(ids)} 个")


def cmd_run(args) -> None:
    proj = Project(args.name)
    steps = [
        ("draft", lambda: cmd_draft(args)),
        ("review", lambda: cmd_review(args)),
        ("merge", lambda: cmd_merge(args)),
        ("fix", lambda: cmd_fix(argparse.Namespace(name=args.name, doc="v0.1.md", merged="merged.md", out="v0.2.md"))),
        ("sign", lambda: cmd_sign(argparse.Namespace(name=args.name, doc="v0.2.md"))),
        ("coldread", lambda: cmd_coldread(argparse.Namespace(name=args.name, doc="v0.3.md", context=None))),
        ("finalize", lambda: cmd_finalize(argparse.Namespace(name=args.name, doc="v0.3.md", out=args.out))),
    ]
    for label, fn in steps:
        print(f"\n{'=' * 60}\n[{label}]")
        if not args.auto and label in ("merge", "sign", "finalize"):
            ans = input(f"继续执行 {label}？[y/N] ").strip().lower()
            if ans != "y":
                print("已停止")
                return
        try:
            fn()
        except SystemExit as e:
            print(f"\n流程在 [{label}] 阶段停止（code={e.code}），请处理后再继续。")
            raise


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="HDR Gate 编排器")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, func, help_text):
        sp = sub.add_parser(name, help=help_text)
        sp.set_defaults(func=func)
        return sp

    sp = add("init", cmd_init, "创建项目")
    sp.add_argument("--name", required=True)
    sp.add_argument("--repo", help="项目根目录")
    sp.add_argument("--req", help="需求原文")
    sp.add_argument("--req-file", help="需求文件路径")
    sp.add_argument("--context", nargs="*", help="额外上下文文件或目录")
    sp.add_argument("--config", help="配置文件路径")
    sp.add_argument("--max-context-chars", type=int, default=300_000)
    sp.add_argument("--force", action="store_true")
    sp.add_argument(
        "--assume",
        action="append",
        metavar="ID|假设|依据",
        help="登记一条假设，可重复。写入 assumptions.md 并进入 draft 上下文",
    )

    sp = add("draft", cmd_draft, "阶段1：分析 + v0.1")
    sp.add_argument("--name", required=True)
    sp.add_argument("--context", nargs="*")

    sp = add("review", cmd_review, "阶段2：双审并行")
    sp.add_argument("--name", required=True)
    sp.add_argument("--doc", default="v0.1.md")

    sp = add("merge", cmd_merge, "阶段3：合并去重")
    sp.add_argument("--name", required=True)

    sp = add("fix", cmd_fix, "阶段4：一次性修复")
    sp.add_argument("--name", required=True)
    sp.add_argument("--doc", default="v0.1.md")
    sp.add_argument("--merged", default="merged.md")
    sp.add_argument("--out", default="v0.2.md")

    sp = add("sign", cmd_sign, "阶段6：复审双签")
    sp.add_argument("--name", required=True)
    sp.add_argument("--doc", default="v0.2.md")

    sp = add("coldread", cmd_coldread, "阶段7：冷读试跑")
    sp.add_argument("--name", required=True)
    sp.add_argument("--doc", default="v0.3.md")
    sp.add_argument("--context", nargs="*")

    sp = add("finalize", cmd_finalize, "阶段8：补齐定稿")
    sp.add_argument("--name", required=True)
    sp.add_argument("--doc", default="v0.3.md")
    sp.add_argument("--out", help="交付路径，默认 ./<需求名>.md")

    sp = add("status", cmd_status, "查看进度")
    sp.add_argument("--name", required=True)

    sp = add("doctor", cmd_doctor, "配置自检（缺密钥退出码 2）")
    sp.add_argument("--config")
    sp.add_argument("--allow-missing", action="store_true", help="缺密钥也返回 0，仅用于明知故犯的降级")

    sp = add("models", cmd_models, "列出通道可用模型 id")
    sp.add_argument("--provider", help="指定通道，默认全部")
    sp.add_argument("--config")

    sp = add("run", cmd_run, "一条龙")
    sp.add_argument("--name", required=True)
    sp.add_argument("--auto", action="store_true", help="不询问，直接跑")
    sp.add_argument("--out")

    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.func(args)
    except llm.ConfigError as e:
        print(f"配置错误: {e}", file=sys.stderr)
        sys.exit(1)
    except llm.LLMError as e:
        print(f"调用失败: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n已中断", file=sys.stderr)
        sys.exit(130)


if __name__ == "__main__":
    main()

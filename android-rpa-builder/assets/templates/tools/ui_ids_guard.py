#!/usr/bin/env python3
"""ID 适配变更边界校验。

snapshot: 记录适配前的完整工作区（含目标文件内容基线）。
check   : 比对基线，断言
          1) 除目标文件外无任何文件变化；
          2) 目标文件内只有 ID 登记表对象内的 const 字符串/注释行变化；
          3) 常量值都以 --id-prefix 开头（确实是目标 App 的 resource-id）；
          4) 无历史兼容后缀常量、无重名常量。
基线路径必须在仓库之外，避免证据与提交混淆。
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import difflib
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

CONST_LINE = re.compile(r'^\s*const\s+val\s+([A-Z][A-Z0-9_]*)\s*=\s*"([^"\r\n]*)"\s*(?://.*)?$')
HISTORICAL_SUFFIXES = ("_PREVIOUS", "_OLD", "_LEGACY", "_CURRENT", "_LATEST", "_NEW")


class GuardError(RuntimeError):
    pass


def run_git(repo: Path, *args: str) -> bytes:
    r = subprocess.run(["git", "-C", str(repo), *args], stdout=-1, stderr=-1, check=False)
    if r.returncode != 0:
        raise GuardError(r.stderr.decode(errors="replace").strip())
    return r.stdout


def ensure_repo(repo: Path) -> Path:
    repo = repo.resolve()
    top = Path(run_git(repo, "rev-parse", "--show-toplevel").decode().strip()).resolve()
    if top != repo:
        raise GuardError(f"--repo 必须是仓库根: {top}")
    return repo


def workspace_paths(repo: Path) -> list[str]:
    raw = run_git(repo, "ls-files", "--cached", "--others", "--exclude-standard", "-z")
    return sorted(p.decode(errors="surrogateescape") for p in raw.split(b"\0") if p)


def file_record(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"missing": True}
    st = path.stat()
    return {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "mode": oct(st.st_mode & 0o777), "size": st.st_size}


def records(repo: Path) -> dict[str, dict[str, Any]]:
    return {rel: file_record(repo / rel) for rel in workspace_paths(repo)}


def object_span(lines: list[str], object_name: str) -> tuple[int, int]:
    """返回 ID 登记表对象的 (起始行, 结束行) 下标；靠花括号配平找右括号。"""
    opener = re.compile(r"\bobject\s+" + re.escape(object_name) + r"\s*\{")
    start = next((i for i, l in enumerate(lines) if opener.search(l)), None)
    if start is None:
        raise GuardError(f"未找到 object {object_name}")
    depth, opened = 0, False
    for i in range(start, len(lines)):
        o, c = lines[i].count("{"), lines[i].count("}")
        if o:
            opened = True
        depth += o - c
        if opened and depth == 0:
            return start, i
    raise GuardError(f"object {object_name} 花括号未配平")


def allowed_changed_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped or stripped.startswith(("//", "/*", "*", "*/")):
        return True
    return CONST_LINE.fullmatch(line.rstrip("\r\n")) is not None


def changed_line_errors(before: str, after: str, object_name: str, id_prefix: str):
    b, a = before.splitlines(), after.splitlines()
    bs, be = object_span(b, object_name)
    as_, ae = object_span(a, object_name)
    errors, changes = [], []
    for tag, o1, o2, n1, n2 in difflib.SequenceMatcher(a=b, b=a, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        for i in range(o1, o2):
            line = b[i]
            changes.append(f"- {i + 1}: {line}")
            if not (bs < i < be):
                errors.append(f"改动落在 {object_name} 之外: {i + 1}: {line}")
            elif not allowed_changed_line(line):
                errors.append(f"删除/改动了逻辑行: {i + 1}: {line}")
        for i in range(n1, n2):
            line = a[i]
            changes.append(f"+ {i + 1}: {line}")
            if not (as_ < i < ae):
                errors.append(f"新增落在 {object_name} 之外: {i + 1}: {line}")
            elif not allowed_changed_line(line):
                errors.append(f"新增/改动了逻辑行: {i + 1}: {line}")
            else:
                m = CONST_LINE.fullmatch(line.rstrip("\r\n"))
                if m and id_prefix and not m.group(2).startswith(id_prefix):
                    errors.append(f"常量值不是目标 App resource-id: {i + 1}: {line}")
    names: dict[str, int] = {}
    for i in range(as_ + 1, ae):
        m = CONST_LINE.fullmatch(a[i].rstrip("\r\n"))
        if not m:
            continue
        name = m.group(1)
        if name.endswith(HISTORICAL_SUFFIXES):
            errors.append(f"禁止历史兼容常量: {name} (行 {i + 1})")
        if name in names:
            errors.append(f"常量重名: {name} (行 {names[name]} 与 {i + 1})")
        names[name] = i + 1
    return errors, changes


def snapshot(args) -> int:
    repo = ensure_repo(Path(args.repo))
    state = Path(args.state).expanduser().resolve()
    if str(state).startswith(str(repo)):
        raise GuardError("基线文件必须放在仓库之外")
    if state.exists() and not args.force:
        raise GuardError(f"基线已存在: {state}（--force 覆盖）")
    target = repo / args.target
    if not target.is_file():
        raise GuardError(f"目标文件不存在: {target}")
    text = target.read_text()
    object_span(text.splitlines(), args.object)
    payload = {
        "schema": 1,
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "repo": str(repo), "target": args.target, "object": args.object, "id_prefix": args.id_prefix,
        "git_head": run_git(repo, "rev-parse", "HEAD").decode().strip(),
        "git_branch": run_git(repo, "branch", "--show-current").decode().strip(),
        "files": records(repo),
        "target_content_base64": base64.b64encode(text.encode()).decode(),
    }
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    os.chmod(state, 0o600)
    print(f"PASS: 基线已写入 {state}（含 {len(payload['files'])} 个文件指纹）")
    return 0


def check(args) -> int:
    repo = ensure_repo(Path(args.repo))
    state_path = Path(args.state).expanduser().resolve()
    if not state_path.is_file():
        raise GuardError(f"基线不存在: {state_path}")
    state = json.loads(state_path.read_text())
    if Path(state["repo"]).resolve() != repo:
        raise GuardError("基线属于另一个仓库")
    target, obj, id_prefix = state["target"], state["object"], state.get("id_prefix", "")
    before = base64.b64decode(state["target_content_base64"]).decode()
    changed = [rel for rel in sorted(set(state["files"]) | set(records(repo)))
               if state["files"].get(rel) != records(repo).get(rel)]
    errors = [f"目标文件之外发生变化: {rel}" for rel in changed if rel != target]
    changes: list[str] = []
    if not (repo / target).is_file():
        errors.append(f"目标文件被删除: {target}")
    else:
        line_errors, changes = changed_line_errors(before, (repo / target).read_text(), obj, id_prefix)
        errors.extend(line_errors)
    if errors:
        print(f"FAIL: 适配越界（{len(errors)} 项）", file=sys.stderr)
        for e in errors:
            print(f"- {e}", file=sys.stderr)
        for c in changes[:60]:
            print(c, file=sys.stderr)
        return 1
    print("PASS: 基线之后的全部改动都限定在 ID 登记表常量/注释内。")
    for c in changes:
        print(c)
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    s = sub.add_parser("snapshot")
    s.add_argument("--repo", required=True)
    s.add_argument("--state", required=True)
    s.add_argument("--target", required=True, help="ID 登记表所在文件的仓库相对路径")
    s.add_argument("--object", required=True, help="ID 登记表对象名，如 TargetIds")
    s.add_argument("--id-prefix", default="", help="资源 ID 前缀，如 com.tencent.wework:id/")
    s.add_argument("--force", action="store_true")
    s.set_defaults(handler=snapshot)
    c = sub.add_parser("check")
    c.add_argument("--repo", required=True)
    c.add_argument("--state", required=True)
    c.set_defaults(handler=check)
    args = p.parse_args()
    try:
        return args.handler(args)
    except (GuardError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

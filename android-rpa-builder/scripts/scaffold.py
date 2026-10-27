#!/usr/bin/env python3
"""Scaffold an Android accessibility RPA project from the templates in this skill.

Commands:
  scaffold  Copy templates into --dest, rename the source package directory,
            replace placeholders, and write .rpa-scaffold.json.
  check     Verify a scaffolded project: no leftover placeholders, required files
            present, package directory renamed, ports/config consistent.

The script never runs git, never installs packages, and never deletes unrelated
files. --force only overwrites files that share a name with a template.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_ROOT = SKILL_ROOT / "assets" / "templates"
ANDROID_TEMPLATE = TEMPLATE_ROOT / "android"
TOOLS_TEMPLATE = TEMPLATE_ROOT / "tools"
STATE_FILE = ".rpa-scaffold.json"
DEFAULT_PKG_PATH = Path("app/src/main/java/com/rpalib/app")
PLACEHOLDER_RE = re.compile(r"<([A-Z][A-Z0-9_]*)>")
PKG_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
TEXT_SUFFIXES = {".kt", ".gradle", ".xml", ".properties", ".pro", ".sh", ".py", ".md", ".txt", ".json"}

REQUIRED_FILES = [
    "settings.gradle",
    "build.gradle",
    "gradle.properties",
    "gradle/wrapper/gradle-wrapper.properties",
    "install-to-device.sh",
    ".gitignore",
    "app/build.gradle",
    "app/proguard-rules.pro",
    "app/src/main/AndroidManifest.xml",
    "app/src/main/res/xml/accessibility_service_config.xml",
    "app/src/main/res/xml/file_paths.xml",
    "app/src/main/res/values/strings.xml",
    "app/src/main/res/values/themes.xml",
    "tools/capture-context.sh",
    "tools/device_client.py",
    "tools/ui_ids_guard.py",
]

REQUIRED_SOURCES = [
    "RpaApplication.kt",
    "activity/MainActivity.kt",
    "activity/PermissionGuide.kt",
    "activity/ShareProxyActivity.kt",
    "service/RpaAccessibilityService.kt",
    "service/EventLogger.kt",
    "service/DeviceLock.kt",
    "service/TaskController.kt",
    "service/FileTaskResultStore.kt",
    "service/PrefsConfigStore.kt",
    "service/PollerBase.kt",
    "service/RuntimeService.kt",
    "service/DeviceHttpServer.kt",
    "service/ScreenAwakeController.kt",
    "service/CronSchedule.kt",
    "service/TaskType.kt",
    "service/TargetIds.kt",
    "service/TargetOperator.kt",
    "service/handlers.kt",
    "utils/NodeFinder.kt",
]


class ScaffoldError(RuntimeError):
    pass


def is_text_file(path: Path) -> bool:
    return path.suffix in TEXT_SUFFIXES or path.name in {"gitignore", ".gitignore"}


def write_atomic(path: Path, text: str) -> None:
    mode = None
    if path.exists():
        mode = stat.S_IMODE(path.stat().st_mode)
    tmp = path.with_name(path.name + ".scaffold-tmp")
    tmp.write_text(text, encoding="utf-8")
    if mode is not None:
        os.chmod(tmp, mode)
    os.replace(tmp, path)


def copy_tree(src: Path, dst: Path) -> int:
    count = 0
    for item in sorted(src.rglob("*")):
        rel = item.relative_to(src)
        target = dst / rel
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, target)
        count += 1
    return count


def prune_empty_dirs(base: Path) -> None:
    if not base.exists():
        return
    for current, dirs, files in os.walk(base, topdown=False):
        path = Path(current)
        if path == base:
            continue
        if not any(path.iterdir()):
            path.rmdir()


def move_package_dir(base: Path, namespace: str) -> None:
    old = base / DEFAULT_PKG_PATH.relative_to("app/src/main/java")
    if not old.is_dir():
        return
    new = base / Path(*namespace.split("."))
    if new.resolve() == old.resolve():
        return
    if new.exists():
        raise ScaffoldError(f"目标包目录已存在，拒绝覆盖: {new}")
    new.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(old), str(new))
    prune_empty_dirs(base)


def validate_args(args: argparse.Namespace) -> None:
    errors: list[str] = []
    for label, value in (
        ("--app-id", args.app_id),
        ("--namespace", args.namespace),
        ("--target-pkg", args.target_pkg),
    ):
        if not PKG_RE.match(value):
            errors.append(f"{label} 不是合法 Android 包名: {value}")
    if args.secondary_target_pkg and not PKG_RE.match(args.secondary_target_pkg):
        errors.append(f"--secondary-target-pkg 不是合法 Android 包名: {args.secondary_target_pkg}")
    if not 1024 <= args.device_port <= 9999:
        errors.append("--device-port 必须在 1024..9999（host 端口按 1<port> 派生，再大会超过 65535）")
    if not 24 <= args.compile_sdk <= 40:
        errors.append("--compile-sdk 必须在 24..40")
    if not 24 <= args.target_sdk <= args.compile_sdk:
        errors.append("--target-sdk 必须在 24..compile-sdk 之间")
    if not ANDROID_TEMPLATE.is_dir() or not TOOLS_TEMPLATE.is_dir():
        errors.append(f"模板目录缺失: {TEMPLATE_ROOT}")
    if errors:
        raise ScaffoldError("\n".join(errors))


def mapping_for(args: argparse.Namespace) -> dict[str, str]:
    return {
        "APP_ID": args.app_id,
        "NAMESPACE": args.namespace,
        "TARGET_PKG": args.target_pkg,
        "DEVICE_PORT": str(args.device_port),
        "HOST_PORT": f"1{args.device_port}",
        "PHONE_IP": "192.168.1.10",
        "S": "SERIAL",
    }


def replace_in_file(
    path: Path,
    mapping: dict[str, str],
    secondary_pkg: str | None,
    compile_sdk: int,
    target_sdk: int,
) -> int:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return 0

    if secondary_pkg:
        text = text.replace("<SECONDARY_TARGET_PKG>", secondary_pkg)
    else:
        text = "\n".join(line for line in text.splitlines() if "<SECONDARY_TARGET_PKG>" not in line) + "\n"

    if path.name == "build.gradle" and path.parent.name == "app":
        text = re.sub(r"compileSdk\s+\d+", f"compileSdk {compile_sdk}", text)
        text = re.sub(r"targetSdk\s+\d+", f"targetSdk {target_sdk}", text)

    replaced = 0

    def sub(match: re.Match[str]) -> str:
        nonlocal replaced
        key = match.group(1)
        if key in mapping:
            replaced += 1
            return mapping[key]
        return match.group(0)

    updated = PLACEHOLDER_RE.sub(sub, text)
    write_atomic(path, updated)
    return replaced


def ensure_executable(path: Path) -> None:
    if path.suffix in {".sh", ".py"} and path.is_file():
        os.chmod(path, path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def scaffold(args: argparse.Namespace) -> int:
    validate_args(args)
    dest = Path(args.dest).expanduser().resolve()

    if dest.exists() and not dest.is_dir():
        raise ScaffoldError(f"--dest 不是目录: {dest}")
    existing = []
    if dest.exists():
        existing = [p for p in dest.iterdir() if p.name != ".DS_Store"]
    if existing and not args.force:
        raise ScaffoldError(
            f"目标目录非空: {dest}\n"
            "为避免覆盖你的文件，请换一个空目录；确认只覆盖同名模板文件时加 --force。"
        )

    old_state = None
    state_path = dest / STATE_FILE
    if state_path.is_file():
        try:
            old_state = json.loads(state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            raise ScaffoldError(f"已有状态文件损坏: {state_path}")
        if old_state.get("namespace") != args.namespace:
            if (dest / DEFAULT_PKG_PATH).exists():
                raise ScaffoldError(
                    "该目录已用另一个 namespace 生成过脚手架；请新建目录，"
                    "或手动确认旧源码目录后再处理。"
                )

    dest.mkdir(parents=True, exist_ok=True)

    if args.dry_run:
        print(f"DRY-RUN: 将复制模板到 {dest}")
        print(f"  namespace={args.namespace} appId={args.app_id} target={args.target_pkg}")
        print(f"  devicePort={args.device_port} hostPort=1{args.device_port}")
        return 0

    copied = copy_tree(ANDROID_TEMPLATE, dest)
    copied += copy_tree(TOOLS_TEMPLATE, dest / "tools")

    gitignore = dest / "gitignore"
    if gitignore.is_file():
        os.replace(gitignore, dest / ".gitignore")

    move_package_dir(dest / "app/src/main/java", args.namespace)
    move_package_dir(dest / "app/src/test/java", args.namespace)

    mapping = mapping_for(args)
    replaced_total = 0
    processed = 0
    for path in sorted(dest.rglob("*")):
        if not path.is_file() or path.name == STATE_FILE:
            continue
        if not is_text_file(path):
            continue
        replaced_total += replace_in_file(
            path, mapping, args.secondary_target_pkg, args.compile_sdk, args.target_sdk
        )
        processed += 1
        ensure_executable(path)

    state = {
        "schema": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "app_id": args.app_id,
        "namespace": args.namespace,
        "target_pkg": args.target_pkg,
        "secondary_target_pkg": args.secondary_target_pkg or "",
        "device_port": args.device_port,
        "host_port": f"1{args.device_port}",
        "compile_sdk": args.compile_sdk,
        "target_sdk": args.target_sdk,
        "template": "android-rpa-builder",
    }
    write_atomic(state_path, json.dumps(state, ensure_ascii=False, indent=2) + "\n")
    os.chmod(state_path, 0o600)

    print(f"PASS: 已生成 {dest}")
    print(f"  applicationId : {args.app_id}")
    print(f"  namespace     : {args.namespace}")
    print(f"  目标 App       : {args.target_pkg}")
    print(f"  端口           : {args.device_port}（adb forward 后 http://127.0.0.1:1{args.device_port}）")
    print(f"  复制文件       : {copied}，替换占位符 {replaced_total} 处")
    print("下一步:")
    print(f"  1) cd {dest}")
    print("  2) gradle wrapper --gradle-version 8.4   # 或 Android Studio 打开项目自动生成 wrapper")
    print("  3) ./gradlew assembleDebug")
    print(f"  4) python3 {Path(__file__).resolve()} check --dest {dest}")
    print("注意: 未执行 git init / 提交 / 安装；这些动作需你确认后再做。")
    return 0


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def check(args: argparse.Namespace) -> int:
    dest = Path(args.dest).expanduser().resolve()
    state_path = dest / STATE_FILE
    if not state_path.is_file():
        raise ScaffoldError(f"不是 scaffold 生成的项目（缺 {STATE_FILE}）: {dest}")
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ScaffoldError(f"状态文件不是合法 JSON: {exc}")

    errors: list[str] = []
    namespace = state.get("namespace", "")
    if not namespace:
        errors.append("状态文件缺 namespace")

    for rel in REQUIRED_FILES:
        if not (dest / rel).is_file():
            errors.append(f"缺文件: {rel}")

    ns_path = Path(*namespace.split(".")) if namespace else Path("__missing__")
    for base in (Path("app/src/main/java"), Path("app/src/test/java")):
        if not (dest / base / ns_path).is_dir():
            errors.append(f"包目录不存在: {base / ns_path}")
    for rel in REQUIRED_SOURCES:
        if not (dest / "app/src/main/java" / ns_path / rel).is_file():
            errors.append(f"缺源码: {rel}")

    for path in sorted(dest.rglob("*")):
        if not path.is_file() or path.name == STATE_FILE or not is_text_file(path):
            continue
        text = read_text(path)
        for match in PLACEHOLDER_RE.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            errors.append(f"未替换占位符 {match.group(0)} @ {path.relative_to(dest)}:{line}")

    build = read_text(dest / "app/build.gradle") if (dest / "app/build.gradle").is_file() else ""
    manifest = read_text(dest / "app/src/main/AndroidManifest.xml") if (dest / "app/src/main/AndroidManifest.xml").is_file() else ""
    svc_path = dest / "app/src/main/java" / ns_path / "service/RpaAccessibilityService.kt"
    svc = read_text(svc_path) if svc_path.is_file() else ""

    if f'applicationId "{state.get("app_id", "")}"' not in build:
        errors.append("app/build.gradle 的 applicationId 与状态文件不一致")
    if f"namespace '{namespace}'" not in build:
        errors.append("app/build.gradle 的 namespace 与状态文件不一致")
    if f'"DEVICE_PORT", "{state.get("device_port", "")}"' not in build:
        errors.append("app/build.gradle 的 BuildConfig.DEVICE_PORT 与状态文件不一致")
    if not re.search(rf"compileSdk\s+{state.get('compile_sdk')}\b", build):
        errors.append("app/build.gradle 的 compileSdk 与状态文件不一致")
    if not re.search(rf"targetSdk\s+{state.get('target_sdk')}\b", build):
        errors.append("app/build.gradle 的 targetSdk 与状态文件不一致")
    if state.get("target_pkg") and state["target_pkg"] not in manifest:
        errors.append("AndroidManifest.xml 的 <queries> 未包含目标 App 包名")
    if state.get("target_pkg") and state["target_pkg"] not in svc:
        errors.append("无障碍窗口白名单未包含目标 App 包名")
    if state.get("secondary_target_pkg") and state["secondary_target_pkg"] not in svc:
        errors.append("无障碍窗口白名单未包含次要目标 App 包名")
    if not state.get("secondary_target_pkg") and "<SECONDARY_TARGET_PKG>" in svc:
        errors.append("未提供次要目标 App，但白名单里仍残留占位行")
    if state.get("host_port") != f"1{state.get('device_port')}":
        errors.append("host_port 与 device_port 派生规则不一致")
    if not (dest / ".gitignore").is_file():
        errors.append("缺 .gitignore（日志/密钥/构建产物可能被误提交）")

    if errors:
        print(f"FAIL: {len(errors)} 项问题", file=sys.stderr)
        for item in errors[:60]:
            print(f"- {item}", file=sys.stderr)
        if len(errors) > 60:
            print(f"... 其余 {len(errors) - 60} 项省略", file=sys.stderr)
        return 1

    print(f"PASS: 脚手架结构自检通过 {dest}")
    print(f"  namespace={namespace}  appId={state.get('app_id')}  target={state.get('target_pkg')}")
    print(f"  端口 {state.get('device_port')} / forward {state.get('host_port')}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command")

    s = sub.add_parser("scaffold", help="从模板生成项目")
    s.add_argument("--dest", required=True, help="目标目录（建议新建空目录）")
    s.add_argument("--app-id", required=True, help="applicationId，如 com.example.rpa")
    s.add_argument("--namespace", required=True, help="代码包名，如 com.example.rpa")
    s.add_argument("--target-pkg", required=True, help="被控目标 App 包名")
    s.add_argument("--secondary-target-pkg", default="", help="次要被控 App 包名（可选）")
    s.add_argument("--device-port", type=int, default=9527, help="设备内嵌 HTTP 端口（1024..9999）")
    s.add_argument("--compile-sdk", type=int, default=36)
    s.add_argument("--target-sdk", type=int, default=None, help="默认与 compile-sdk 相同")
    s.add_argument("--force", action="store_true", help="目标目录非空时仍写入（只覆盖同名模板文件）")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(handler=scaffold)

    c = sub.add_parser("check", help="校验 scaffold 产物")
    c.add_argument("--dest", required=True)
    c.set_defaults(handler=check)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return 2
    if args.command == "scaffold" and args.target_sdk is None:
        args.target_sdk = args.compile_sdk
    try:
        return args.handler(args)
    except ScaffoldError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""repo-context — 扫描仓库，生成只含事实的 AGENTS.md / CLAUDE.md 草稿。

零依赖，只用 Python 3 标准库（TOML 解析依赖 3.11+ 的 tomllib，缺失时自动跳过）。

用法:
    python repo_context.py <仓库目录> [--write AGENTS.md] [--force] [--json] [--max-tree 15]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

try:
    import tomllib
except ImportError:  # Python < 3.11
    tomllib = None

IGNORE_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "dist", "build", "out", "coverage",
    ".next", ".nuxt", ".venv", "venv", "env", "__pycache__", ".idea", ".vscode",
    "vendor", "target", ".cache",
}
SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf",
                 ".zip", ".gz", ".tar", ".7z", ".exe", ".dll", ".so", ".dylib",
                 ".woff", ".woff2", ".ttf", ".mp4", ".mp3"}
LANGUAGE_BY_EXT = {
    ".py": "Python", ".js": "JavaScript", ".ts": "TypeScript", ".tsx": "TypeScript",
    ".jsx": "JavaScript", ".go": "Go", ".rs": "Rust", ".java": "Java",
    ".rb": "Ruby", ".php": "PHP", ".c": "C", ".h": "C", ".cpp": "C++",
    ".cs": "C#", ".swift": "Swift", ".kt": "Kotlin", ".sh": "Shell",
    ".ps1": "PowerShell", ".md": "Markdown", ".sql": "SQL", ".html": "HTML",
    ".css": "CSS", ".scss": "SCSS", ".vue": "Vue", ".svelte": "Svelte",
}
TEST_DIRS = {"test", "tests", "__tests__", "spec", "specs", "e2e"}
TEST_FILE_RE = re.compile(r"(?:^test_.*\.py$|.*_test\.py$|.*\.(?:test|spec)\.[jt]sx?$)")


def collect_files(root: Path):
    files = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in IGNORE_DIRS for part in path.parts):
            continue
        if path.suffix.lower() in SKIP_SUFFIXES:
            continue
        try:
            if path.stat().st_size > 1024 * 1024:
                continue
        except OSError:
            continue
        files.append(path.relative_to(root))
    return files


def detect_stack(root: Path):
    stack = []

    package_json = root / "package.json"
    if package_json.is_file():
        try:
            data = json.loads(package_json.read_text(encoding="utf-8", errors="replace"))
        except json.JSONDecodeError:
            data = {}
        deps = len(data.get("dependencies") or {}) + len(data.get("devDependencies") or {})
        if (root / "pnpm-lock.yaml").is_file():
            manager = "pnpm"
        elif (root / "yarn.lock").is_file():
            manager = "yarn"
        else:
            manager = "npm"
        stack.append({
            "ecosystem": "Node.js",
            "name": data.get("name"),
            "manifest": "package.json",
            "dependencies": deps,
            "package_manager": manager,
            "scripts": data.get("scripts") or {},
        })

    pyproject = root / "pyproject.toml"
    if pyproject.is_file() and tomllib:
        try:
            data = tomllib.loads(pyproject.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            data = {}
        project = data.get("project") or {}
        stack.append({
            "ecosystem": "Python",
            "name": project.get("name"),
            "manifest": "pyproject.toml",
            "dependencies": len(project.get("dependencies") or []),
            "package_manager": "uv" if (root / "uv.lock").is_file() else "pip",
            "scripts": {},
        })
    elif (root / "requirements.txt").is_file():
        lines = (root / "requirements.txt").read_text(encoding="utf-8", errors="replace").splitlines()
        packages = [line.strip() for line in lines
                    if line.strip() and not line.strip().startswith("#")]
        stack.append({
            "ecosystem": "Python",
            "name": None,
            "manifest": "requirements.txt",
            "dependencies": len(packages),
            "package_manager": "pip",
            "scripts": {},
        })

    go_mod = root / "go.mod"
    if go_mod.is_file():
        text = go_mod.read_text(encoding="utf-8", errors="replace")
        module = re.search(r"^module\s+(\S+)", text, re.M)
        go_version = re.search(r"^go\s+(\S+)", text, re.M)
        stack.append({
            "ecosystem": "Go",
            "name": module.group(1) if module else None,
            "manifest": "go.mod",
            "dependencies": None,
            "package_manager": "go",
            "version": go_version.group(1) if go_version else None,
            "scripts": {},
        })

    cargo = root / "Cargo.toml"
    if cargo.is_file() and tomllib:
        try:
            data = tomllib.loads(cargo.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            data = {}
        package = data.get("package") or {}
        stack.append({
            "ecosystem": "Rust",
            "name": package.get("name"),
            "manifest": "Cargo.toml",
            "dependencies": len(data.get("dependencies") or {}),
            "package_manager": "cargo",
            "scripts": {},
        })

    return stack


def build_commands(stack, has_tests: bool):
    commands = []
    for entry in stack:
        scripts = entry.get("scripts") or {}
        manager = entry.get("package_manager")
        if entry["ecosystem"] == "Node.js":
            for name in scripts:
                commands.append({
                    "command": f"{manager} run {name}",
                    "source": "package.json scripts",
                    "verified": False,
                })
        elif entry["ecosystem"] == "Python" and has_tests:
            commands.append({"command": "python -m pytest", "source": "检测到测试文件", "verified": False})
        elif entry["ecosystem"] == "Go" and has_tests:
            commands.append({"command": "go test ./...", "source": "检测到测试文件", "verified": False})
        elif entry["ecosystem"] == "Rust" and has_tests:
            commands.append({"command": "cargo test", "source": "检测到测试文件", "verified": False})
    return commands


def detect_tests(files):
    hits = []
    for rel in files:
        if any(part in TEST_DIRS for part in rel.parts) or TEST_FILE_RE.match(rel.name):
            hits.append(rel.as_posix())
    return hits


def build_tree(files, max_entries: int):
    counts = {}
    for rel in files:
        if len(rel.parts) > 1:
            key, is_dir = rel.parts[0] + "/", True
        else:
            key, is_dir = rel.name, False
        entry = counts.setdefault(key, {"count": 0, "dir": is_dir})
        entry["count"] += 1
    ordered = sorted(counts.items(), key=lambda item: (-item[1]["count"], item[0]))
    lines = []
    for name, entry in ordered[:max_entries]:
        if entry["dir"]:
            lines.append(f"- {name}（{entry['count']} 个文件）")
        else:
            lines.append(f"- {name}")
    if len(ordered) > max_entries:
        lines.append(f"- …另有 {len(ordered) - max_entries} 项未展示")
    return lines


def language_stats(files):
    counts = {}
    for rel in files:
        language = LANGUAGE_BY_EXT.get(rel.suffix.lower())
        if language:
            counts[language] = counts.get(language, 0) + 1
    return sorted(counts.items(), key=lambda item: -item[1])


def render(root: Path, files, stack, commands, tests, tree, languages):
    name = root.name
    lines = [
        "# AGENTS.md（草稿）",
        "",
        "> 由 repo-context 扫描生成，只包含可验证事实；标注「待补充」的部分需要人工填写。",
        "",
        "## 项目概览",
        "",
        f"- 名称：{name}",
        f"- 文件数：{len(files)}",
    ]
    if languages:
        summary = "、".join(f"{lang} {count}" for lang, count in languages[:5])
        lines.append(f"- 主要文件类型：{summary}")
    lines.append("")

    lines.append("## 技术栈")
    lines.append("")
    if stack:
        for entry in stack:
            detail = f"- {entry['ecosystem']}（{entry['manifest']}"
            if entry.get("name"):
                detail += f"，名称 {entry['name']}"
            if entry.get("dependencies") is not None:
                detail += f"，依赖 {entry['dependencies']} 个"
            if entry.get("package_manager"):
                detail += f"，包管理器 {entry['package_manager']}"
            lines.append(detail + "）")
    else:
        lines.append("- 未识别到常见 manifest（待补充）")
    lines.append("")

    lines.append("## 目录结构")
    lines.append("")
    lines.append("```text")
    lines.extend(tree)
    lines.append("```")
    lines.append("")

    lines.append("## 常用命令")
    lines.append("")
    if commands:
        lines.append("> 命令来源见括号，均未执行验证。")
        lines.append("")
        for item in commands:
            lines.append(f"- `{item['command']}`（{item['source']}）")
    else:
        lines.append("- 未识别到脚本（待补充）")
    lines.append("")

    lines.append("## 测试")
    lines.append("")
    if tests:
        shown = "、".join(tests[:8])
        suffix = " 等" if len(tests) > 8 else ""
        lines.append(f"- 检测到 {len(tests)} 个测试相关文件：{shown}{suffix}")
    else:
        lines.append("- 未检测到测试目录或测试文件（待补充）")
    lines.append("")

    lines.append("## 约定（待补充）")
    lines.append("")
    lines.append("- 代码风格：")
    lines.append("- 提交规范：")
    lines.append("- 禁止事项：")
    lines.append("")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="扫描仓库生成 AGENTS.md 草稿")
    parser.add_argument("path", nargs="?", default=".", help="仓库目录，默认当前目录")
    parser.add_argument("--write", metavar="FILE", help="写入指定文件，默认只打印")
    parser.add_argument("--force", action="store_true", help="允许覆盖已存在的文件")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--max-tree", type=int, default=15, help="目录结构最多展示多少项，默认 15")
    args = parser.parse_args(argv)

    root = Path(args.path).expanduser().resolve()
    if not root.is_dir():
        print(f"目录不存在: {root}", file=sys.stderr)
        return 2

    files = collect_files(root)
    stack = detect_stack(root)
    tests = detect_tests(files)
    commands = build_commands(stack, bool(tests))
    tree = build_tree(files, args.max_tree)
    languages = language_stats(files)
    markdown = render(root, files, stack, commands, tests, tree, languages)

    if args.json:
        print(json.dumps({
            "root": str(root),
            "stats": {
                "files": len(files),
                "languages": dict(languages),
                "tests": len(tests),
            },
            "stack": stack,
            "commands": commands,
            "markdown": markdown,
        }, ensure_ascii=False, indent=2))
    else:
        print(markdown)

    if args.write:
        target = Path(args.write)
        if target.exists() and not args.force:
            print(f"目标已存在，使用 --force 覆盖: {target}", file=sys.stderr)
            return 2
        target.write_text(markdown, encoding="utf-8")
        if not args.json:
            print(f"[已写入] {target}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    raise SystemExit(main())

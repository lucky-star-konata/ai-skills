#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""skill-audit — 审计 SKILL.md / CLAUDE.md / AGENTS.md 的质量与上下文预算。

零依赖，只用 Python 3 标准库。

用法:
    python audit_skills.py <目录> [--max-tokens 5000] [--json] [--strict] [--quiet]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

TARGET_NAMES = {"skill.md", "claude.md", "agents.md"}
IGNORE_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "dist", "build", "out",
    ".venv", "venv", "env", "__pycache__", ".next", ".nuxt", "vendor",
}
NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
TRIGGER_CUES = (
    "use when", "use this", "when ", "whenever", "trigger",
    "用于", "当用户", "触发", "适用", "需要时",
)
REF_RE = re.compile(r"`([^`\n]+?\.(?:py|sh|ps1|js|ts|mjs|json|md|yaml|yml|toml|txt))`")
HEADING_RE = re.compile(r"^#{1,6}\s+\S", re.M)
RESOURCE_PREFIXES = ("scripts/", "references/", "assets/", "templates/")

LEVEL_ORDER = {"error": 0, "warn": 1, "info": 2}
LEVEL_LABEL = {"error": "ERROR", "warn": "WARN ", "info": "INFO "}


def estimate_tokens(text: str) -> int:
    """保守估算：CJK 字符 1 token，其余 4 字符 1 token。"""
    cjk = len(CJK_RE.findall(text))
    return int(cjk + (len(text) - cjk) / 4)


def parse_frontmatter(text: str):
    """返回 (data, body)。没有 frontmatter 时 data 为 None。"""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None, text

    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() in ("---", "..."):
            end = i
            break
    if end is None:
        return None, text

    data: dict = {}
    i = 1
    while i < end:
        matched = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", lines[i])
        if not matched:
            i += 1
            continue
        key, value = matched.group(1), matched.group(2).strip()
        i += 1
        if value in ("|", ">", "|-", ">-"):
            block = []
            while i < end and (not lines[i].strip() or lines[i][:1] in (" ", "\t")):
                block.append(lines[i].strip())
                i += 1
            joiner = " " if value.startswith(">") else "\n"
            data[key] = joiner.join(part for part in block if part).strip()
        elif value == "":
            items = []
            while i < end and re.match(r"^\s+-\s+", lines[i]):
                items.append(re.sub(r"^\s+-\s+", "", lines[i]).strip().strip("'\""))
                i += 1
            data[key] = items
        else:
            data[key] = value.strip("'\"")
    return data, "\n".join(lines[end + 1:])


def word_shingles(text: str) -> set:
    return set(re.findall(r"[a-z]{3,}", text.lower())) | set(
        re.findall(r"[\u4e00-\u9fff]{2}", text)
    )


def jaccard(a: str, b: str) -> float:
    sa, sb = word_shingles(a), word_shingles(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def collect_files(root: Path):
    found = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in IGNORE_DIRS for part in path.parts):
            continue
        if path.name.lower() in TARGET_NAMES:
            found.append(path)
    return sorted(found)


def audit_file(path: Path, root: Path, max_tokens: int):
    issues = []

    def add(level: str, field: str, message: str):
        issues.append({"level": level, "field": field, "message": message})

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        add("error", "file", f"无法读取文件: {exc}")
        return {"file": path, "name": None, "tokens": 0, "issues": issues, "data": {}}

    data, body = parse_frontmatter(text)
    is_skill = path.name.lower() == "skill.md"

    if data is None:
        add("error" if is_skill else "info", "frontmatter", "缺少 YAML frontmatter（--- 包裹的元数据块）")
        data = {}

    name = data.get("name")
    description = data.get("description")
    if isinstance(name, list):
        name = " ".join(name)
    if isinstance(description, list):
        description = "\n".join(str(x) for x in description)

    # --- name ---
    if is_skill:
        if not name:
            add("error", "name", "缺少 name 字段")
        else:
            if len(name) > 64:
                add("error", "name", f"name 超过 64 字符（当前 {len(name)}）")
            if not NAME_RE.match(name):
                add("error", "name", f'name 必须是 lower-kebab-case，当前为 "{name}"')
            if path.parent.name and name != path.parent.name:
                add("warn", "name", f'name "{name}" 与目录名 "{path.parent.name}" 不一致')
    elif name and not NAME_RE.match(str(name)):
        add("warn", "name", f'name 建议使用 lower-kebab-case，当前为 "{name}"')

    # --- description ---
    if is_skill:
        if not description:
            add("error", "description", "缺少 description 字段，Agent 无法判断何时触发")
        else:
            desc = str(description)
            if len(desc) < 40:
                add("warn", "description", f"description 仅 {len(desc)} 字符，建议写清触发场景")
            if len(desc) > 1024:
                add("error", "description", f"description 超过 1024 字符（当前 {len(desc)}），会被截断")
            lowered = desc.lower()
            if not any(cue in lowered for cue in TRIGGER_CUES):
                add("warn", "description", "description 缺少触发线索（如 use when / 当用户 / 用于）")

    # --- body ---
    tokens = estimate_tokens(body)
    if not body.strip():
        add("error", "body", "正文为空")
    else:
        if not HEADING_RE.search(body):
            add("warn", "body", "正文没有 Markdown 标题，结构不清晰")
        if tokens > max_tokens:
            add(
                "warn",
                "body",
                f"正文约 {tokens} tokens，超过上限 {max_tokens}；"
                "建议把长清单移到 references/ 并按需读取",
            )
        if re.search(r"(?im)^\s*(?:[-*]\s*)?(?:TODO|TBD)\s*[:：]", body):
            add("info", "body", "正文仍包含 TODO / TBD 占位")

    # --- resource references ---
    missing = []
    for ref in sorted(set(REF_RE.findall(body))):
        if ref.startswith(("http://", "https://", "/", "~")):
            continue
        if any(ch in ref for ch in "<>$*{}\\") or "..." in ref:
            continue
        normalized = ref[2:] if ref.startswith("./") else ref
        if not normalized.startswith(RESOURCE_PREFIXES):
            continue
        if not (path.parent / normalized).exists():
            missing.append(ref)
    if missing:
        add("warn", "body", "引用的本地文件不存在: " + ", ".join(missing))

    if is_skill or name:
        return {"file": path, "name": str(name) if name else None, "tokens": tokens, "issues": issues, "data": data}
    return {"file": path, "name": None, "tokens": tokens, "issues": issues, "data": data}


def run_audit(root: Path, max_tokens: int):
    files = collect_files(root)
    results = [audit_file(path, root, max_tokens) for path in files]

    # --- 跨文件: 重名 ---
    seen = {}
    for item in results:
        if not item["name"]:
            continue
        seen.setdefault(item["name"], []).append(item)
    for name, items in seen.items():
        if len(items) > 1:
            locations = ", ".join(rel(item["file"], root) for item in items)
            for item in items:
                item["issues"].append({
                    "level": "error",
                    "field": "name",
                    "message": f'name "{name}" 重复出现在: {locations}',
                })

    # --- 跨文件: description 相似度 ---
    skill_items = [item for item in results if str(item["file"].name).lower() == "skill.md"]
    for i in range(len(skill_items)):
        for j in range(i + 1, len(skill_items)):
            a = skill_items[i]["data"].get("description")
            b = skill_items[j]["data"].get("description")
            if not a or not b or isinstance(a, list) or isinstance(b, list):
                continue
            if len(str(a)) < 80 or len(str(b)) < 80:
                continue
            score = jaccard(str(a), str(b))
            if score > 0.72:
                msg = (
                    f'description 与 {rel(skill_items[j]["file"], root)} 相似度 {score:.2f}，'
                    "触发词可能互相抢占"
                )
                skill_items[i]["issues"].append({"level": "warn", "field": "description", "message": msg})
                msg2 = (
                    f'description 与 {rel(skill_items[i]["file"], root)} 相似度 {score:.2f}，'
                    "触发词可能互相抢占"
                )
                skill_items[j]["issues"].append({"level": "warn", "field": "description", "message": msg2})

    for item in results:
        item["issues"].sort(key=lambda issue: LEVEL_ORDER.get(issue["level"], 9))

    counts = {"error": 0, "warn": 0, "info": 0}
    for item in results:
        for issue in item["issues"]:
            counts[issue["level"]] = counts.get(issue["level"], 0) + 1
    summary = {
        "files": len(results),
        "skills": len(skill_items),
        "errors": counts["error"],
        "warnings": counts["warn"],
        "infos": counts["info"],
        "tokens_total": sum(item["tokens"] for item in results),
        "tokens_max": max((item["tokens"] for item in results), default=0),
    }
    return results, summary


def rel(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="审计 Agent Skill 文件的质量与上下文预算")
    parser.add_argument("path", nargs="?", default=".", help="技能目录（默认当前目录）")
    parser.add_argument("--max-tokens", type=int, default=5000, help="单文件正文 token 上限，默认 5000")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--strict", action="store_true", help="存在警告也返回非零退出码")
    parser.add_argument("--quiet", action="store_true", help="只输出汇总")
    args = parser.parse_args(argv)

    target = Path(args.path).expanduser().resolve()
    if target.is_file():
        root = target.parent
    elif target.is_dir():
        root = target
    else:
        print(f"路径不存在: {target}", file=sys.stderr)
        return 2

    results, summary = run_audit(root, args.max_tokens)

    if args.json:
        payload = {
            "root": str(root),
            "files": [
                {
                    "file": rel(item["file"], root),
                    "name": item["name"],
                    "tokens": item["tokens"],
                    "issues": item["issues"],
                }
                for item in results
            ],
            "summary": summary,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"skill-audit 1.0 · 目录 {root}")
        if not args.quiet:
            for item in results:
                if not item["issues"]:
                    continue
                print(f"\n{rel(item['file'], root)}")
                for issue in item["issues"]:
                    print(f"  {LEVEL_LABEL[issue['level']]} {issue['field']}: {issue['message']}")
        print("\n" + "─" * 64)
        print(
            f"文件 {summary['files']} 个（skill {summary['skills']} 个）  "
            f"错误 {summary['errors']}  警告 {summary['warnings']}  提示 {summary['infos']}"
        )
        print(
            f"上下文估算: 合计约 {summary['tokens_total']:,} tokens，"
            f"单文件最大 {summary['tokens_max']:,} tokens"
        )
        if summary["errors"]:
            print("结果: 存在错误，发布前必须修复")
        elif summary["warnings"]:
            print("结果: 无错误，建议处理警告项")
        else:
            print("结果: 通过")

    if summary["errors"]:
        return 1
    if args.strict and summary["warnings"]:
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    raise SystemExit(main())

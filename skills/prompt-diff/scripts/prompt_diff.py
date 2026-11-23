#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""prompt-diff — 对比两个提示词版本，找出丢失的约束、变更的限制和新增内容。

零依赖，只用 Python 3 标准库。

用法:
    python prompt_diff.py <旧版> <新版> [--json] [--fail-level high] [--threshold 0.62]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path

RISK_ORDER = {"none": 0, "low": 1, "medium": 2, "high": 3}

CLASSIFIERS = [
    ("guardrail", re.compile(
        r"(拒绝|不得泄露|不透露|安全|隐私|敏感|违规|harmful|refuse|safety|privacy|guardrail)",
        re.I)),
    ("constraint", re.compile(
        r"(必须|禁止|只能|不要|不得|务必|不可以|不允许|"
        r"\bmust\b|\bnever\b|\bonly\b|should not|\bdo not\b|\brequired?\b|\bprohibited\b)",
        re.I)),
    ("format", re.compile(
        r"(输出格式|返回格式|格式要求|结构如下|字段|\bjson\b|\bmarkdown\b|\bschema\b|"
        r"\bformat\b|输出为)",
        re.I)),
    ("example", re.compile(
        r"(示例|例子|例如|比如|\bexample\b|e\.g\.|输入[:：]|输出[:：])",
        re.I)),
    ("role", re.compile(
        r"(你是|你的角色|你的身份|\brole\b|\bpersona\b|扮演)",
        re.I)),
]

STRIP_RE = re.compile(r"^[\s>*#\-+•]+|^\d+[.、)]\s*")
NUMBER_RE = re.compile(r"\d+(?:\.\d+)?%?")
HEADING_RE = re.compile(r"^#{1,6}\s+")
ROOT = Path(__file__).resolve().parents[3]
VERSION_FILE = ROOT / "VERSION"


def get_version() -> str:
    try:
        return VERSION_FILE.read_text(encoding="utf-8").strip() or "0.0.0"
    except OSError:
        return "0.0.0"


def normalize(text: str) -> str:
    return re.sub(r"[\W_]+", "", text.lower())


def classify(line: str) -> str:
    for kind, pattern in CLASSIFIERS:
        if pattern.search(line):
            return kind
    return "other"


def extract_items(text: str):
    items = []
    for number, raw in enumerate(text.splitlines(), 1):
        stripped = raw.strip()
        if not stripped:
            continue
        is_heading = bool(HEADING_RE.match(stripped))
        content = STRIP_RE.sub("", stripped).strip()
        if len(content) < 4 and not is_heading:
            continue
        items.append({
            "line": number,
            "text": content if content else stripped,
            "raw": stripped,
            "kind": "section" if is_heading else classify(content),
        })
    return items


def similarity(a: str, b: str) -> float:
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    if min(len(na), len(nb)) >= 12 and (na in nb or nb in na):
        return 0.98
    return SequenceMatcher(None, na, nb).ratio()


def item_risk(kind: str) -> str:
    if kind in ("guardrail", "constraint", "format"):
        return "high"
    if kind == "example":
        return "medium"
    if kind == "section":
        return "low"
    return "low"


def digits(text: str):
    return set(NUMBER_RE.findall(text))


def diff(old_items, new_items, threshold: float):
    matched = {}
    used_new = set()

    for old_index, old in enumerate(old_items):
        best_index, best_score = None, 0.0
        for index, new in enumerate(new_items):
            if index in used_new:
                continue
            score = similarity(old["text"], new["text"])
            if score > best_score:
                best_index, best_score = index, score
        if best_index is not None and best_score >= threshold:
            matched[old_index] = (best_index, best_score)
            used_new.add(best_index)

    removed = [
        {**old, "risk": item_risk(old["kind"])}
        for index, old in enumerate(old_items) if index not in matched
    ]

    changed = []
    for old_index, (new_index, score) in matched.items():
        old, new = old_items[old_index], new_items[new_index]
        if normalize(old["text"]) == normalize(new["text"]):
            continue
        number_changed = digits(old["text"]) != digits(new["text"])
        changed.append({
            "old_line": old["line"],
            "new_line": new["line"],
            "old_text": old["text"],
            "new_text": new["text"],
            "kind": new["kind"],
            "similarity": round(score, 3),
            "risk": "medium" if number_changed else "low",
        })

    added = [
        {**new, "risk": "low"}
        for index, new in enumerate(new_items) if index not in used_new
    ]

    stats = {
        "old_items": len(old_items),
        "new_items": len(new_items),
        "removed": len(removed),
        "added": len(added),
        "changed": len(changed),
        "unchanged": len(matched) - len(changed),
    }
    risks = [item["risk"] for item in removed + added + changed]
    overall = "none"
    for risk in risks:
        if RISK_ORDER[risk] > RISK_ORDER[overall]:
            overall = risk
    return removed, added, changed, stats, overall


def print_report(old_path, new_path, old_items, new_items, removed, added, changed, stats, overall):
    print(f"prompt-diff {get_version()}")
    print(f"旧版: {old_path}（{len(old_items)} 条）")
    print(f"新版: {new_path}（{len(new_items)} 条）\n")

    if removed:
        print(f"## 丢失 {len(removed)} 条")
        for item in sorted(removed, key=lambda x: -RISK_ORDER[x["risk"]]):
            tag = {"high": "高", "medium": "中", "low": "低"}[item["risk"]]
            print(f"  [{tag}] 旧 L{item['line']}（{item['kind']}）: {item['text']}")
        print()

    if changed:
        print(f"## 变更 {len(changed)} 条")
        for item in changed:
            tag = {"medium": "中", "low": "低"}.get(item["risk"], "低")
            print(f"  [{tag}] 旧 L{item['old_line']} → 新 L{item['new_line']}（相似度 {item['similarity']}）")
            print(f"    - {item['old_text']}")
            print(f"    + {item['new_text']}")
        print()

    if added:
        print(f"## 新增 {len(added)} 条")
        for item in added:
            print(f"  [+] 新 L{item['line']}（{item['kind']}）: {item['text']}")
        print()

    print("─" * 64)
    print(
        f"丢失 {stats['removed']} / 新增 {stats['added']} / 变更 {stats['changed']} / "
        f"未变 {stats['unchanged']}   （未匹配条目按行数估算）"
    )
    label = {"none": "无", "low": "低", "medium": "中", "high": "高"}[overall]
    print(f"整体风险: {label}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="对比两个提示词版本，检查约束丢失与限制变更")
    parser.add_argument("old", help="旧版提示词文件")
    parser.add_argument("new", help="新版提示词文件")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--threshold", type=float, default=0.62, help="相似度匹配阈值，默认 0.62")
    parser.add_argument(
        "--fail-level", choices=["none", "low", "medium", "high"], default="none",
        help="达到该风险等级时返回非零退出码",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {get_version()}")
    args = parser.parse_args(argv)

    old_path, new_path = Path(args.old), Path(args.new)
    for path in (old_path, new_path):
        if not path.is_file():
            print(f"文件不存在: {path}", file=sys.stderr)
            return 2

    old_text = old_path.read_text(encoding="utf-8", errors="replace")
    new_text = new_path.read_text(encoding="utf-8", errors="replace")
    old_items, new_items = extract_items(old_text), extract_items(new_text)

    removed, added, changed, stats, overall = diff(old_items, new_items, args.threshold)

    if args.json:
        payload = {
            "old": str(old_path),
            "new": str(new_path),
            "removed": removed,
            "added": added,
            "changed": changed,
            "stats": stats,
            "risk": overall,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print_report(old_path, new_path, old_items, new_items,
                     removed, added, changed, stats, overall)

    if args.fail_level != "none" and RISK_ORDER[overall] >= RISK_ORDER[args.fail_level]:
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    raise SystemExit(main())

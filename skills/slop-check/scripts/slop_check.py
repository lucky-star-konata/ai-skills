#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""slop-check — 检测前端代码里的「AI 味」设计套路。

零依赖，只用 Python 3 标准库。

用法:
    python slop_check.py <文件或目录> [--json] [--threshold 70] [--include-js]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

DEFAULT_EXTS = {".html", ".htm", ".css", ".scss", ".sass", ".less",
                ".jsx", ".tsx", ".vue", ".svelte", ".astro"}
EXTRA_EXTS = {".js", ".ts", ".mjs"}
IGNORE_DIRS = {
    ".git", "node_modules", "dist", "build", "out", "coverage",
    ".next", ".nuxt", ".venv", "venv", "vendor", "__pycache__",
}

RULES = [
    {
        "id": "AI-001",
        "name": "紫蓝渐变",
        "weight": 18,
        "patterns": [
            r"(?:linear|radial|conic)-gradient\([^)\n]{0,200}?(?:#(?:6366f1|8b5cf6|a855f7|7c3aed|4f46e5|3b82f6|06b6d4)|rgba?\(\s*(?:99|139|168)\s*,)",
            r"\bfrom-(?:purple|violet|indigo)-\d{3}\b",
            r"\bto-(?:pink|fuchsia|cyan|sky)-\d{3}\b",
        ],
        "mode": "any",
        "advice": "换成中性底色 + 一个品牌强调色，把颜色收进 CSS 变量；紫→粉是最典型的模板配色。",
    },
    {
        "id": "AI-002",
        "name": "居中大标题英雄区",
        "weight": 10,
        "patterns": [
            r"text-align:\s*center",
            r"font-size:\s*(?:[4-9]\d|[1-9]\d{2})px",
        ],
        "mode": "all",
        "advice": "试试左对齐的编辑式版式：标题、正文、CTA 沿同一基线对齐，信息密度更高也更有品牌感。",
    },
    {
        "id": "AI-003",
        "name": "三卡片网格",
        "weight": 12,
        "patterns": [
            r"repeat\(\s*3\s*,",
            r"grid-cols-3\b",
            r"md:grid-cols-3\b",
            r"lg:grid-cols-3\b",
        ],
        "mode": "any",
        "advice": "三个功能卡是最省事也最雷同的排法；改成 1 大 + 2 小、瀑布流或一条横向流程带。",
    },
    {
        "id": "AI-004",
        "name": "Emoji 当图标",
        "weight": 14,
        "patterns": [
            r"<h[1-6][^>]*>[^<]{0,80}[\U0001F300-\U0001FAFF\u2600-\u27BF]",
            r"^#{1,6}\s.*[\U0001F300-\U0001FAFF\u2600-\u27BF]",
        ],
        "mode": "any",
        "advice": "标题里的 emoji 换成真实图标或直接删掉；正文里最多保留一处作为语气点缀。",
    },
    {
        "id": "AI-005",
        "name": "空话文案",
        "weight": 15,
        "patterns": [
            r"赋能", r"一站式", r"无缝(?:衔接|集成)?", r"极致", r"重新定义",
            r"未来已来", r"释放潜[能力]", r"开启新篇章", r"颠覆(?:式|传统)?", r"引领未来",
            r"\bempower\w*", r"\bseamless(?:ly)?\b", r"\bunleash\w*", r"\brevolutioniz\w*",
            r"\bcutting[- ]edge\b", r"\belevate your\b", r"\bnext[- ]generation\b",
            r"\bstate[- ]of[- ]the[- ]art\b", r"\bgame[- ]chang\w*", r"\bsupercharg\w*",
        ],
        "mode": "any",
        "advice": "换成一个具体事实：谁在什么场景下省了多少时间、多少钱，或一句可验证的能力描述。",
    },
    {
        "id": "AI-006",
        "name": "胶囊渐变按钮",
        "weight": 10,
        "patterns": [
            r"border-radius:\s*9999px|rounded-full",
            r"linear-gradient|bg-gradient-to|from-[a-z]+-\d{3}",
        ],
        "mode": "all",
        "advice": "按钮圆角降到 6–10px，渐变改纯色；主按钮靠对比度和尺寸突出，不靠发光。",
    },
    {
        "id": "AI-007",
        "name": "玻璃拟态",
        "weight": 8,
        "patterns": [r"backdrop-filter:\s*blur|backdrop-blur"],
        "mode": "any",
        "advice": "玻璃拟态只在有真实背景层次时成立；否则改半透明纯色或加 1px 边框更干净。",
    },
    {
        "id": "AI-008",
        "name": "全大写细字距标签",
        "weight": 6,
        "patterns": [
            r"text-transform:\s*uppercase|\buppercase\b",
            r"letter-spacing:\s*0?\.\d+em|tracking-(?:wide|wider|widest)",
        ],
        "mode": "all",
        "advice": "每个区块都顶一个 uppercase 小标签会显得机械；保留一处做节奏，其余直接删。",
    },
    {
        "id": "AI-009",
        "name": "没有出处的漂亮数字",
        "weight": 8,
        "patterns": [r"\b99\.9\s*%", r"\b10x\b", r"\b100\+", r"\b10,000\+"],
        "mode": "any",
        "advice": "数字要能追溯到数据源；否则改成定性描述，或补上统计口径和日期。",
    },
    {
        "id": "AI-010",
        "name": "占位文案残留",
        "weight": 20,
        "patterns": [r"lorem ipsum"],
        "mode": "any",
        "advice": "上线前必须清掉占位文案，用真实产品文案替换。",
    },
    {
        "id": "AI-011",
        "name": "大圆角 + 长阴影",
        "weight": 6,
        "patterns": [
            r"border-radius:\s*(?:1[2-9]|[2-9]\d)px|rounded-(?:2xl|3xl)",
            r"box-shadow:[^;]{0,80}\b(?:2[0-9]|3[0-9])px|shadow-(?:xl|2xl)",
        ],
        "mode": "all",
        "advice": "圆角收到 8–12px，阴影改成 1px 描边或极短投影，整体会更利落。",
    },
    {
        "id": "AI-012",
        "name": "装饰性光斑",
        "weight": 6,
        "patterns": [
            r"blur-3xl|blur-\[(?:80|100|120)px\]",
            r"radial-gradient\([^)]{0,80}(?:rgba|#)",
        ],
        "mode": "any",
        "advice": "背景光斑和内容没关系就删；需要氛围感时用纸张纹理、网格或真实图片。",
    },
    {
        "id": "AI-013",
        "name": "渐变文字",
        "weight": 10,
        "patterns": [r"background-clip:\s*text|bg-clip-text|text-transparent"],
        "mode": "all",
        "advice": "渐变文字可读性差且极易撞款；改用单色 + 字重对比。",
    },
]


def compile_rule(rule):
    flags = re.IGNORECASE | re.MULTILINE
    rule["_compiled"] = [re.compile(p, flags) for p in rule["patterns"]]
    return rule


for _rule in RULES:
    compile_rule(_rule)


def collect_files(target: Path, include_js: bool):
    exts = DEFAULT_EXTS | (EXTRA_EXTS if include_js else set())
    if target.is_file():
        return [target] if target.suffix.lower() in exts else []
    files = []
    for path in target.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in exts:
            continue
        if any(part in IGNORE_DIRS for part in path.parts):
            continue
        files.append(path)
    return sorted(files)


def scan_file(path: Path, root: Path):
    try:
        if path.stat().st_size > 2 * 1024 * 1024:
            return [], 0
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return [], 0

    lines = text.splitlines()
    findings = []
    for rule in RULES:
        compiled = rule["_compiled"]
        hits = []
        if rule["mode"] == "all":
            matched_all = True
            for pattern in compiled:
                found = None
                for idx, line in enumerate(lines, 1):
                    if pattern.search(line):
                        found = (idx, line.strip())
                        break
                if not found:
                    matched_all = False
                    break
                hits.append(found)
            if not matched_all:
                continue
            first = min(hits, key=lambda item: item[0])
            findings.append(_finding(rule, path, root, first[0], first[1], 1))
        else:
            seen_lines = set()
            for pattern in compiled:
                for idx, line in enumerate(lines, 1):
                    if idx in seen_lines:
                        continue
                    if pattern.search(line):
                        seen_lines.add(idx)
                        hits.append((idx, line.strip()))
                if len(hits) >= 5:
                    break
            for idx, line in hits[:5]:
                findings.append(_finding(rule, path, root, idx, line, len(hits)))
    return findings, len(lines)


def _finding(rule, path, root, line_no, evidence, occurrences):
    try:
        file_label = path.relative_to(root).as_posix()
    except ValueError:
        file_label = str(path)
    return {
        "id": rule["id"],
        "rule": rule["name"],
        "weight": rule["weight"],
        "file": file_label,
        "line": line_no,
        "evidence": evidence[:160],
        "occurrences": occurrences,
        "advice": rule["advice"],
    }


def grade(score: int) -> str:
    if score >= 85:
        return "A"
    if score >= 70:
        return "B"
    if score >= 55:
        return "C"
    if score >= 40:
        return "D"
    return "F"


def evaluate(findings):
    by_rule = {}
    for item in findings:
        bucket = by_rule.setdefault(item["id"], {
            "id": item["id"],
            "rule": item["rule"],
            "weight": item["weight"],
            "advice": item["advice"],
            "files": set(),
            "count": 0,
        })
        bucket["files"].add(item["file"])
        bucket["count"] += 1

    penalty = sum(bucket["weight"] for bucket in by_rule.values())
    score = max(0, 100 - penalty)
    ordered = sorted(by_rule.values(), key=lambda b: (-b["weight"], b["id"]))
    return score, ordered


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="检测前端代码里的 AI 味设计套路")
    parser.add_argument("path", nargs="?", default=".", help="要扫描的文件或目录（默认当前目录）")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--threshold", type=int, default=None, help="分数低于该值时返回非零退出码")
    parser.add_argument("--include-js", action="store_true", help="把 .js/.ts/.mjs 也纳入扫描")
    parser.add_argument("--limit", type=int, default=40, help="最多展示多少条明细，默认 40")
    args = parser.parse_args(argv)

    target = Path(args.path).expanduser().resolve()
    if not target.exists():
        print(f"路径不存在: {target}", file=sys.stderr)
        return 2

    root = target.parent if target.is_file() else target
    files = collect_files(target, args.include_js)
    if not files:
        print("没有找到可扫描的文件（支持 .html/.css/.jsx/.tsx/.vue/.svelte 等）", file=sys.stderr)
        return 2

    findings = []
    total_lines = 0
    for path in files:
        file_findings, line_count = scan_file(path, root)
        findings.extend(file_findings)
        total_lines += line_count

    score, by_rule = evaluate(findings)
    rank = grade(score)

    if args.json:
        payload = {
            "files": len(files),
            "lines": total_lines,
            "score": score,
            "grade": rank,
            "findings": findings,
            "by_rule": [
                {
                    "id": bucket["id"],
                    "rule": bucket["rule"],
                    "weight": bucket["weight"],
                    "count": bucket["count"],
                    "files": sorted(bucket["files"]),
                }
                for bucket in by_rule
            ],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"slop-check 1.0 · 扫描 {len(files)} 个文件 / {total_lines:,} 行")
        print(f"评分: {score}/100  等级 {rank}")
        if not by_rule:
            print("未命中任何规则，页面没有明显的模板化痕迹。")
        else:
            print(f"命中 {len(by_rule)} 条规则\n")
            shown = 0
            for bucket in by_rule:
                print(f"[{bucket['id']}] {bucket['rule']}  -{bucket['weight']}  ({bucket['count']} 处)")
                rule_findings = [f for f in findings if f["id"] == bucket["id"]]
                for item in rule_findings[:3]:
                    print(f"  {item['file']}:{item['line']}")
                    print(f"    {item['evidence']}")
                    shown += 1
                    if shown >= args.limit:
                        break
                print(f"  修复: {bucket['advice']}\n")
                if shown >= args.limit:
                    print("（已达到展示上限，用 --json 查看全部）")
                    break

    if args.threshold is not None and score < args.threshold:
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    raise SystemExit(main())

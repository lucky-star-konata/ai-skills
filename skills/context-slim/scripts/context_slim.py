#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""context-slim — 把长会话 / 长文档压成有预算的结构化摘要。

零依赖，只用 Python 3 标准库。

用法:
    python context_slim.py <文件|-> [--budget 1500] [--json] [--keep-code] [--no-dropped]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
HEADING_RE = re.compile(r"^#{1,6}\s+(.+)$", re.M)
CODE_RE = re.compile(r"```[^\n]*\n(.*?)```", re.S)
ERROR_HINT = re.compile(r"(error|exception|traceback|failed|报错|失败)", re.I)

DECISION = re.compile(
    r"(决定|确定|采用|选用|统一|最终方案|必须|禁止|不允许|改用|"
    r"\bdecided\b|\bdecision\b|\bwe will\b|\bmust\b|\bnever\b|\bavoid\b|\brequired?\b)",
    re.I,
)
TODO = re.compile(
    r"(?:\bTODO\b|\bFIXME\b|待办|下一步|接下来|还需要|待确认|需要补|"
    r"\bpending\b|\bnext step\b|follow[- ]?up|跟进)",
    re.I,
)
ERROR = re.compile(
    r"(?:\berror\b|\bexception\b|\btraceback\b|\bfailed?\b|\bblocker\b|"
    r"报错|失败|踩坑|卡住|崩溃)",
    re.I,
)
FACT = re.compile(
    r"(?:https?://|(?:[\w.-]+/)+[\w.-]+\.\w{1,6}\b|v?\d+\.\d+(?:\.\d+)?\b|"
    r"端口\s*\d+|\bport\s*\d+|\d{2,5}\s*端口)",
    re.I,
)

KINDS = [
    ("decision", "决策与约束", DECISION, 4.0),
    ("todo", "待办", TODO, 3.0),
    ("error", "问题与报错", ERROR, 3.0),
    ("fact", "关键事实", FACT, 2.0),
]
SECTION_ORDER = ["决策与约束", "待办", "问题与报错", "关键事实", "其他要点"]
BOLD_LABEL = re.compile(r"^\*\*[^*]{1,40}\*\*[:：]?\s*")
ROOT = Path(__file__).resolve().parents[3]
VERSION_FILE = ROOT / "VERSION"


def get_version() -> str:
    try:
        return VERSION_FILE.read_text(encoding="utf-8").strip() or "0.0.0"
    except OSError:
        return "0.0.0"


def estimate_tokens(text: str) -> int:
    cjk = len(CJK_RE.findall(text))
    return int(cjk + (len(text) - cjk) / 4)


def content_to_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                text = part.get("text") or part.get("content")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    return ""


def flatten_messages(messages) -> str:
    chunks = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = message.get("role") or message.get("from") or "msg"
        text = content_to_text(message.get("content", message.get("value", ""))).strip()
        if text:
            chunks.append(f"**{role}**: {text}")
    return "\n\n".join(chunks)


def parse_structured(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        return text

    # 整个文件是 JSON
    if stripped[0] in "[{":
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, list) and payload and all(isinstance(x, dict) for x in payload):
            return flatten_messages(payload)
        if isinstance(payload, dict) and isinstance(payload.get("messages"), list):
            return flatten_messages(payload["messages"])

    # JSONL
    lines = [line for line in stripped.splitlines() if line.strip()]
    if lines and all(line.lstrip().startswith("{") for line in lines):
        messages = []
        for line in lines:
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                return text
            if not isinstance(obj, dict):
                return text
            messages.append(obj)
        if messages:
            return flatten_messages(messages)
    return text


def shrink_code(text: str, keep_code: bool) -> str:
    def replace(match):
        inner = match.group(1)
        lines = inner.splitlines()
        if keep_code:
            return "```\n" + inner.strip() + "\n```"
        if ERROR_HINT.search(inner) and lines:
            kept = "\n".join(lines[:8])
            return f"```\n{kept}\n...（代码块共 {len(lines)} 行）\n```"
        return f"[代码块 {len(lines)} 行，已折叠]"

    return CODE_RE.sub(replace, text)


def split_blocks(text: str):
    headings = list(HEADING_RE.finditer(text))
    blocks = []
    if len(headings) >= 2:
        if headings[0].start() > 0 and text[:headings[0].start()].strip():
            blocks.append({"title": "（开头）", "text": text[:headings[0].start()].strip()})
        for i, match in enumerate(headings):
            end = headings[i + 1].start() if i + 1 < len(headings) else len(text)
            body = text[match.end():end].strip()
            if body:
                blocks.append({"title": match.group(1).strip(), "text": body})
    else:
        for chunk in re.split(r"\n\s*\n", text):
            chunk = chunk.strip()
            if chunk:
                blocks.append({"title": chunk.lstrip("# ").splitlines()[0][:40], "text": chunk})
    for index, block in enumerate(blocks):
        block["index"] = index
        block["tokens"] = estimate_tokens(block["text"])
    return blocks


def split_sentences(text: str):
    parts = re.split(r"(?<=[。！？!?；;])\s*|(?<=[.!?])\s+|\n+", text)
    sentences = []
    for part in parts:
        cleaned = part.strip(" \t>-#•")
        cleaned = BOLD_LABEL.sub("", cleaned)
        cleaned = re.sub(r"^\*{1,2}\s+", "", cleaned).strip()
        if len(cleaned) >= 8:
            sentences.append(cleaned if len(cleaned) <= 320 else cleaned[:317] + "…")
    return sentences


def normalize(text: str) -> str:
    return re.sub(r"[\W_]+", "", text.lower())


def classify(sentence: str):
    for kind, _label, pattern, _weight in KINDS:
        if pattern.search(sentence):
            return kind
    return "other"


def build_candidates(blocks):
    candidates = []
    total = max(len(blocks), 1)
    for block in blocks:
        recency = 2.5 * (block["index"] / max(total - 1, 1))
        for sentence in split_sentences(block["text"]):
            kind = classify(sentence)
            weight = next((w for k, _l, _p, w in KINDS if k == kind), 1.0)
            score = weight + recency + min(len(sentence) / 200, 1.0)
            candidates.append({
                "kind": kind,
                "text": sentence,
                "score": round(score, 3),
                "block": block["index"],
                "block_title": block["title"],
            })
    return candidates


def dedupe(candidates):
    seen = set()
    kept = []
    for item in sorted(candidates, key=lambda c: (-c["score"], c["block"])):
        key = normalize(item["text"])
        if not key or key in seen:
            continue
        seen.add(key)
        kept.append(item)
    return kept


def select(candidates, budget: int):
    chosen = []
    used_blocks = set()
    remaining = max(budget - 90, 0) if budget else 10 ** 9
    other_cap = max(int(remaining * 0.3), 120) if budget else 10 ** 9
    other_used = 0

    for kind in ["decision", "todo", "error", "fact", "other"]:
        items = [c for c in candidates if c["kind"] == kind]
        for item in items:
            cost = estimate_tokens(item["text"]) + 6
            if budget and cost > remaining:
                continue
            if kind == "other" and other_used + cost > other_cap:
                continue
            chosen.append(item)
            used_blocks.add(item["block"])
            remaining -= cost
            if kind == "other":
                other_used += cost
    return chosen, used_blocks


def render(source: str, blocks, chosen, used_blocks, input_tokens,
           show_dropped: bool, title: str):
    grouped = {label: [] for label in SECTION_ORDER}
    for item in chosen:
        if item["kind"] == "decision":
            grouped["决策与约束"].append(item["text"])
        elif item["kind"] == "todo":
            grouped["待办"].append(item["text"])
        elif item["kind"] == "error":
            grouped["问题与报错"].append(item["text"])
        elif item["kind"] == "fact":
            grouped["关键事实"].append(item["text"])
        else:
            grouped["其他要点"].append(item["text"])

    lines = [f"# {title}", "", f"> 来源: {source} · 原始约 {input_tokens:,} tokens"]
    lines.append("> 由 context-slim 规则抽取，未调用模型")
    lines.append("")

    if not chosen:
        lines.append("（未识别到可抽取的信息，尝试提高 --budget 或加 --keep-code）")
        lines.append("")

    for label in SECTION_ORDER:
        items = grouped[label]
        if not items:
            continue
        lines.append(f"## {label}")
        for item in items:
            prefix = "- [ ] " if label == "待办" else "- "
            lines.append(prefix + item)
        lines.append("")

    dropped = [b for b in blocks if b["index"] not in used_blocks]
    dropped_tokens = sum(b["tokens"] for b in dropped)

    if show_dropped and dropped:
        lines.append("<details>")
        lines.append(f"<summary>已丢弃 {len(dropped)} 段 / 约 {dropped_tokens:,} tokens</summary>")
        lines.append("")
        for block in sorted(dropped, key=lambda b: -b["tokens"])[:12]:
            lines.append(f"- {block['title']}（约 {block['tokens']:,} tokens）")
        lines.append("")
        lines.append("</details>")
        lines.append("")

    output_tokens = estimate_tokens("\n".join(lines))
    if input_tokens and output_tokens < input_tokens:
        ratio = (1 - output_tokens / input_tokens) * 100
        header = f"> 输出约 {output_tokens:,} tokens · 压缩率 {ratio:.0f}%"
    elif input_tokens:
        header = f"> 输出约 {output_tokens:,} tokens（原文较短，未做压缩）"
    else:
        header = f"> 输出约 {output_tokens:,} tokens"
    lines.insert(2, header)
    return "\n".join(lines), output_tokens, dropped


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="把长会话压成有预算的结构化摘要")
    parser.add_argument("path", nargs="?", default="-", help="输入文件，- 表示 stdin")
    parser.add_argument("--budget", type=int, default=1500, help="目标 token 预算，0 表示不限，默认 1500")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--keep-code", action="store_true", help="保留完整代码块")
    parser.add_argument("--no-dropped", action="store_true", help="不输出被丢弃段落清单")
    parser.add_argument("--title", default="上下文摘要", help="摘要标题")
    parser.add_argument("--version", action="version", version=f"%(prog)s {get_version()}")
    args = parser.parse_args(argv)

    if args.path == "-":
        raw = sys.stdin.read()
        source = "stdin"
    else:
        path = Path(args.path).expanduser()
        if not path.is_file():
            print(f"文件不存在: {path}", file=sys.stderr)
            return 2
        raw = path.read_text(encoding="utf-8", errors="replace")
        source = path.name

    if not raw.strip():
        print("输入为空", file=sys.stderr)
        return 2

    transcript = parse_structured(raw)
    transcript = shrink_code(transcript, args.keep_code)
    blocks = split_blocks(transcript)
    if not blocks:
        print("没有可压缩的内容", file=sys.stderr)
        return 2

    candidates = dedupe(build_candidates(blocks))
    chosen, used_blocks = select(candidates, args.budget)
    markdown, output_tokens, dropped = render(
        source, blocks, chosen, used_blocks,
        estimate_tokens(raw), not args.no_dropped, args.title,
    )

    if args.json:
        payload = {
            "source": source,
            "input_tokens": estimate_tokens(raw),
            "output_tokens": output_tokens,
            "blocks": len(blocks),
            "selected": len(chosen),
            "sections": {},
            "dropped": [
                {"title": b["title"], "tokens": b["tokens"]} for b in dropped
            ],
        }
        for label in SECTION_ORDER:
            payload["sections"][label] = [
                item["text"] for item in chosen
                if {"决策与约束": "decision", "待办": "todo", "问题与报错": "error",
                    "关键事实": "fact", "其他要点": "other"}[label] == item["kind"]
            ]
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(markdown)
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    raise SystemExit(main())

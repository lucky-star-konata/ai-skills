#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mcp-lint — 静态检查 MCP server 配置文件。

零依赖，只用 Python 3 标准库。

用法:
    python mcp_lint.py <文件或目录> [--json] [--strict]
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path
from urllib.parse import urlparse

CONFIG_NAMES = {"mcp.json", ".mcp.json", "mcp_config.json", "claude_desktop_config.json"}
IGNORE_DIRS = {
    ".git", "node_modules", "dist", "build", "out", "coverage",
    ".next", ".nuxt", ".venv", "venv", "vendor", "__pycache__",
}
SECRET_KEY_RE = re.compile(r"(token|api[_-]?key|secret|password|passwd|credential)", re.I)
SECRET_VALUE_RE = re.compile(r"(sk-[A-Za-z0-9]{8,}|ghp_[A-Za-z0-9]{8,}|Bearer\s+\S{8,}|token\s*=\s*\S{8,})", re.I)
PLACEHOLDER_HINTS = ("${", "$", "%", "{{")
ROOT = Path(__file__).resolve().parents[3]
VERSION_FILE = ROOT / "VERSION"

LEVEL_ORDER = {"error": 0, "warn": 1, "info": 2}
LEVEL_LABEL = {"error": "ERROR", "warn": "WARN ", "info": "INFO "}


def get_version() -> str:
    try:
        return VERSION_FILE.read_text(encoding="utf-8").strip() or "0.0.0"
    except OSError:
        return "0.0.0"


class DuplicateKeyError(ValueError):
    pass


def reject_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateKeyError(f"重复键: {key}")
        result[key] = value
    return result


def find_configs(target: Path):
    if target.is_file():
        return [target]
    found = []
    for path in target.rglob("*"):
        if not path.is_file() or path.name not in CONFIG_NAMES:
            continue
        if any(part in IGNORE_DIRS for part in path.parts):
            continue
        found.append(path)
    return sorted(found)


def check_server(name, entry, config_dir: Path):
    issues = []

    def add(level, message):
        issues.append({"level": level, "server": name, "message": message})

    if not isinstance(entry, dict):
        add("error", "server 配置必须是对象")
        return issues

    command = entry.get("command")
    url = entry.get("url")
    transport = entry.get("type") or ("stdio" if command else "http")

    if not command and not url:
        add("error", "既没有 command 也没有 url，无法启动")
    if command and url:
        add("warn", "同时配置了 command 与 url，多数客户端只会取其一")
    if command is not None and not isinstance(command, str):
        add("error", "command 必须是字符串")

    if isinstance(command, str) and command:
        if any(sep in command for sep in ("/", "\\")) or command.startswith("."):
            candidate = Path(command)
            if not candidate.is_absolute():
                candidate = config_dir / candidate
            if not candidate.exists():
                add("warn", f"命令路径不存在: {command}")
            elif not candidate.is_file():
                add("warn", f"命令路径不是文件: {command}")
        elif shutil.which(command) is None:
            add("warn", f'命令 "{command}" 不在当前 PATH，客户端可能无法启动')

    if isinstance(url, str) and url:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            add("warn", f"URL 协议不常见: {url}")
        elif parsed.scheme == "http":
            host = (parsed.hostname or "").lower()
            if host not in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
                add("warn", f"非本机地址使用明文 http: {url}")

    env = entry.get("env")
    if isinstance(env, dict):
        for key, value in env.items():
            if not isinstance(value, str) or not SECRET_KEY_RE.search(str(key)):
                continue
            if any(hint in value for hint in PLACEHOLDER_HINTS):
                continue
            if len(value) >= 8:
                add("warn", f"env.{key} 疑似明文凭据，建议改用环境变量占位符")
    elif env is not None:
        add("warn", "env 必须是对象")

    args = entry.get("args")
    if isinstance(args, list):
        for arg in args:
            if isinstance(arg, str) and SECRET_VALUE_RE.search(arg):
                add("warn", f"args 中出现疑似凭据片段: {arg[:40]}")
                break

    if entry.get("disabled") is True:
        add("info", "server 已标记为 disabled")
    if transport == "stdio" and not entry.get("timeout") and not entry.get("startupTimeout"):
        add("info", "stdio server 未设置超时，启动卡住时难以及时发现")

    return issues


def check_config(path: Path, root: Path):
    try:
        display = path.relative_to(root).as_posix()
    except ValueError:
        display = str(path)

    result = {"file": display, "servers": 0, "issues": []}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        result["issues"].append({"level": "error", "server": None, "message": f"无法读取: {exc}"})
        return result

    try:
        data = json.loads(text, object_pairs_hook=reject_duplicates)
    except DuplicateKeyError as exc:
        result["issues"].append({"level": "error", "server": None, "message": str(exc)})
        return result
    except json.JSONDecodeError as exc:
        result["issues"].append({
            "level": "error", "server": None,
            "message": f"JSON 解析失败: 第 {exc.lineno} 行第 {exc.colno} 列，{exc.msg}",
        })
        return result

    if not isinstance(data, dict):
        result["issues"].append({"level": "error", "server": None, "message": "根节点必须是对象"})
        return result

    servers = data.get("mcpServers")
    if servers is None:
        servers = data.get("servers")
    if servers is None:
        result["issues"].append({
            "level": "error", "server": None,
            "message": "未找到 mcpServers 或 servers 字段",
        })
        return result
    if not isinstance(servers, dict):
        result["issues"].append({
            "level": "error", "server": None,
            "message": "mcpServers / servers 必须是对象",
        })
        return result

    result["servers"] = len(servers)
    for name, entry in servers.items():
        result["issues"].extend(check_server(name, entry, path.parent))
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="静态检查 MCP server 配置")
    parser.add_argument("path", nargs="?", default=".", help="配置文件或目录，默认当前目录")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--strict", action="store_true", help="存在警告也返回非零退出码")
    parser.add_argument("--version", action="version", version=f"%(prog)s {get_version()}")
    args = parser.parse_args(argv)

    target = Path(args.path).expanduser().resolve()
    if not target.exists():
        print(f"路径不存在: {target}", file=sys.stderr)
        return 2

    root = target.parent if target.is_file() else target
    configs = find_configs(target)
    if not configs:
        print("没有找到 MCP 配置文件（mcp.json / .mcp.json / claude_desktop_config.json）", file=sys.stderr)
        return 2

    results = [check_config(path, root) for path in configs]

    counts = {"error": 0, "warn": 0, "info": 0}
    for result in results:
        result["issues"].sort(key=lambda issue: LEVEL_ORDER.get(issue["level"], 9))
        for issue in result["issues"]:
            counts[issue["level"]] += 1

    summary = {
        "configs": len(results),
        "servers": sum(result["servers"] for result in results),
        "errors": counts["error"],
        "warnings": counts["warn"],
        "infos": counts["info"],
    }

    if args.json:
        print(json.dumps({"results": results, "summary": summary}, ensure_ascii=False, indent=2))
    else:
        print(f"mcp-lint {get_version()} · 检查 {summary['configs']} 个配置文件\n")
        for result in results:
            print(f"{result['file']}（{result['servers']} 个 server）")
            if not result["issues"]:
                print("  无问题")
            for issue in result["issues"]:
                prefix = f'[{issue["server"]}] ' if issue["server"] else ""
                print(f"  {LEVEL_LABEL[issue['level']]} {prefix}{issue['message']}")
            print()
        print("─" * 64)
        print(
            f"配置 {summary['configs']} / server {summary['servers']} / "
            f"错误 {summary['errors']} / 警告 {summary['warnings']} / 提示 {summary['infos']}"
        )
        if summary["errors"]:
            print("结果: 存在错误，配置可能无法加载")
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

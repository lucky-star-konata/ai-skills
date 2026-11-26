#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""alert-triage — 安全告警研判：真攻击 / 误报、攻击类型、溯源方向与加固清单。

零依赖，只用 Python 3 标准库。

用法:
    python alert_triage.py --text "src_ip=1.2.3.4 url='/?id=1 union select 1'"
    python alert_triage.py --input access.log
    python alert_triage.py --input alerts.csv --format json
    python alert_triage.py --input alerts.jsonl --fail-on attack
    cat alert.txt | python alert_triage.py --text -
"""

from __future__ import annotations

import argparse
import csv
import html
import io
import ipaddress
import json
import re
import sys
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
VERSION_FILE = ROOT / "VERSION"
DEFAULT_LIMIT = 1000
SNIPPET_MAX = 90


def get_version() -> str:
    try:
        return VERSION_FILE.read_text(encoding="utf-8").strip() or "0.0.0"
    except OSError:
        return "0.0.0"



VERDICT_ATTACK = "真实攻击"
VERDICT_SUSPICIOUS = "可疑"
VERDICT_FALSE = "误报"

# ---------------------------------------------------------------------------
# 字段别名：解析 CSV / JSON / key=value 时统一到内部字段
# ---------------------------------------------------------------------------
FIELD_ALIASES = {
    "time": ["time", "timestamp", "@timestamp", "event_time", "datetime", "date",
             "occur_time", "日志时间", "时间", "事件时间"],
    "src_ip": ["src_ip", "source_ip", "remote_addr", "client_ip", "ip", "src",
               "source", "攻击源ip", "源ip", "来源ip", "客户端ip"],
    "method": ["method", "http_method", "request_method", "请求方法"],
    "url": ["url", "uri", "request_uri", "path", "request", "request_url",
            "url路径", "攻击url", "请求路径", "访问路径"],
    "status": ["status", "status_code", "http_status", "code", "响应码", "状态码"],
    "ua": ["ua", "user_agent", "http_user_agent", "useragent", "user-agent",
           "浏览器指纹"],
    "rule": ["rule", "rule_name", "alert", "alert_name", "signature", "name",
             "message", "msg", "事件名称", "规则名称", "告警名称", "告警内容"],
    "payload": ["payload", "body", "request_body", "args", "query", "params",
                "post_data", "攻击payload", "请求体", "请求参数"],
    "host": ["host", "hostname", "server", "dst_host", "domain", "目标", "域名"],
    "dst_ip": ["dst_ip", "destination_ip", "server_ip", "remote_ip", "目标ip"],
    "user": ["user", "username", "account", "用户名", "账号"],
    "cmdline": ["cmdline", "command", "process", "process_name", "命令行", "进程"],
    "count": ["count", "hits", "times", "次数", "请求次数"],
}


def norm_key(value: str) -> str:
    return re.sub(r"[\s_\-]+", "", str(value).strip().lower())


ALIAS_INDEX = {}
for _field, _aliases in FIELD_ALIASES.items():
    for _alias in [_field] + _aliases:
        ALIAS_INDEX[norm_key(_alias)] = _field


def new_alert(raw: str = "") -> dict:
    alert = {"raw": raw}
    for field in ("time", "src_ip", "method", "url", "status", "ua", "rule",
                  "payload", "host", "dst_ip", "user", "cmdline", "count"):
        alert[field] = ""
    return alert


# ---------------------------------------------------------------------------
# 输入解析
# ---------------------------------------------------------------------------
ACCESS_LOG_RE = re.compile(
    r'^(?P<src_ip>\S+)\s+\S+\s+\S+\s+\[(?P<time>[^\]]+)\]\s+'
    r'"(?P<method>[A-Z]+)\s+(?P<url>[^"\s]*)(?:\s+[^"]*)?"\s+'
    r'(?P<status>\d{3})\s+(?P<size>\S+)'
    r'(?:\s+"[^"]*"\s+"(?P<ua>[^"]*)")?',
    re.I,
)
REQUEST_RE = re.compile(
    r'"(?P<method>GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS|TRACE|CONNECT)\s+'
    r'(?P<url>\S+)(?:\s+HTTP/[\d.]+)?"',
    re.I,
)
KV_PAIR_RE = re.compile(
    r'([A-Za-z0-9_\u4e00-\u9fff][A-Za-z0-9_\u4e00-\u9fff\-]{1,24})'
    r'\s*[:=：]\s*'
    r'("([^"]*)"|\'([^\']*)\'|[^\s,;]+)'
)
KV_LINE_RE = re.compile(r'^\s*([^:=：]{2,24})\s*[:=：]\s*(.+)$')
TIME_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?|"
    r"\d{2}/\w{3}/\d{4}:\d{2}:\d{2}:\d{2}"
)


def flatten(data, depth: int = 0):
    """把嵌套 dict 展平为 (key, value)，避免 WAF 导出里的嵌套字段被丢掉。"""
    if depth > 4:
        return
    for key, value in data.items():
        if isinstance(value, dict):
            yield from flatten(value, depth + 1)
        elif isinstance(value, list):
            for item in value[:5]:
                if isinstance(item, dict):
                    yield from flatten(item, depth + 1)
                elif item not in (None, ""):
                    yield key, item
        elif value is not None:
            yield key, value


def alert_from_mapping(data: dict, raw: str | None = None) -> dict:
    if raw is None:
        raw = json.dumps(data, ensure_ascii=False)
    alert = new_alert(raw)
    for key, value in flatten(data):
        field = ALIAS_INDEX.get(norm_key(key))
        if field and alert[field] == "" and str(value).strip():
            alert[field] = str(value).strip()
    return alert


def kv_alert(text: str) -> dict:
    """从一条多行告警文本里抽取 key: value 字段，原文整体保留作为证据。"""
    alert = new_alert(text.strip())
    if "\n" in text.strip():
        for line in text.splitlines():
            matched = KV_LINE_RE.match(line)
            if not matched:
                continue
            key, value = matched.group(1).strip(), matched.group(2).strip()
            field = ALIAS_INDEX.get(norm_key(key))
            if field and alert[field] == "" and value:
                alert[field] = value
    for matched in KV_PAIR_RE.finditer(text):
        key = matched.group(1)
        value = matched.group(2).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        field = ALIAS_INDEX.get(norm_key(key))
        if field and alert[field] == "" and value:
            alert[field] = value
    if not alert["time"]:
        matched = TIME_RE.search(text)
        if matched:
            alert["time"] = matched.group(0)
    return alert


def parse_log_line(line: str) -> dict:
    line = line.strip()
    if not line:
        return new_alert("")
    if line.startswith("{"):
        try:
            data = json.loads(line)
            if isinstance(data, dict):
                return alert_from_mapping(data, raw=line)
        except json.JSONDecodeError:
            pass
    alert = kv_alert(line)
    matched = ACCESS_LOG_RE.match(line)
    if matched:
        for field in ("src_ip", "time", "method", "url", "status", "ua"):
            value = matched.group(field)
            if value:
                alert[field] = value.strip()
    else:
        matched = REQUEST_RE.search(line)
        if matched:
            alert["method"] = matched.group("method").upper()
            alert["url"] = matched.group("url")
        status = re.search(r'\bstatus(?:_code)?[=:\s]+"?(\d{3})\b', line, re.I)
        if status and not alert["status"]:
            alert["status"] = status.group(1)
    return alert


def alerts_from_json(data) -> list:
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = None
        for key in ("alerts", "data", "events", "results", "items", "records"):
            if isinstance(data.get(key), list):
                items = data[key]
                break
        if items is None:
            items = [data]
    else:
        items = [{"raw": str(data)}]
    alerts = []
    for item in items:
        if isinstance(item, dict):
            alerts.append(alert_from_mapping(item))
        else:
            alerts.append(new_alert(str(item)))
    return alerts


def parse_csv(text: str) -> list:
    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.reader(io.StringIO(text), dialect))
    rows = [row for row in rows if any(cell.strip() for cell in row)]
    if not rows:
        return []
    header = [cell.strip() for cell in rows[0]]
    mapped = [ALIAS_INDEX.get(norm_key(cell)) for cell in header]
    alerts = []
    if sum(1 for item in mapped if item) >= 2:
        for row in rows[1:]:
            pairs = []
            for index, cell in enumerate(row[:len(header)]):
                pairs.append("%s=%s" % (header[index], cell))
            alerts.append(alert_from_mapping(
                {header[index]: cell for index, cell in enumerate(row[:len(header)])},
                raw=", ".join(pairs),
            ))
        return alerts
    return [new_alert(", ".join(row)) for row in rows]


def looks_like_csv(text: str) -> bool:
    first = text.splitlines()[0] if text.splitlines() else ""
    if "," not in first and ";" not in first and "\t" not in first:
        return False
    cells = re.split(r"[,;\t]", first)
    hits = sum(1 for cell in cells if ALIAS_INDEX.get(norm_key(cell)))
    return hits >= 2


def parse_text_block(text: str) -> list:
    text = text.strip().lstrip("\ufeff")
    if not text:
        return []
    try:
        return alerts_from_json(json.loads(text))
    except json.JSONDecodeError:
        pass

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return []

    json_lines = 0
    for line in lines:
        if line.startswith("{"):
            try:
                json.loads(line)
                json_lines += 1
            except json.JSONDecodeError:
                pass
    if len(lines) > 1 and json_lines >= max(1, len(lines) // 2):
        return [parse_log_line(line) for line in lines]

    access_lines = sum(1 for line in lines if ACCESS_LOG_RE.match(line))
    if len(lines) > 1 and access_lines >= max(1, len(lines) // 2):
        return [parse_log_line(line) for line in lines]

    if len(lines) == 1:
        return [parse_log_line(lines[0])]

    kv_lines = sum(1 for line in lines if KV_LINE_RE.match(line))
    if kv_lines >= max(1, len(lines) // 2):
        return [kv_alert(text)]

    return [parse_log_line(line) for line in lines]


def read_text(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def parse_file(path: Path) -> list:
    text = read_text(path)
    suffix = path.suffix.lower()
    if suffix in (".json", ".jsonl", ".ndjson", ".csv"):
        if suffix == ".csv":
            return parse_csv(text)
        try:
            data = json.loads(text)
            return alerts_from_json(data)
        except json.JSONDecodeError:
            return parse_text_block(text)
    if looks_like_csv(text):
        return parse_csv(text)
    return parse_text_block(text)


# ---------------------------------------------------------------------------
# 攻击特征库
# ---------------------------------------------------------------------------
SIGNATURES = [
    # SQL 注入
    {"sid": "SQLI-UNION", "category": "SQLI", "weight": 45,
     "regex": r"\bunion\b\s+(?:all\s+)?select\b", "note": "联合查询注入特征"},
    {"sid": "SQLI-TIME", "category": "SQLI", "weight": 45,
     "regex": r"\b(?:sleep|benchmark|pg_sleep|waitfor\s+delay)\s*\(", "note": "时间盲注函数"},
    {"sid": "SQLI-META", "category": "SQLI", "weight": 45,
     "regex": r"\binformation_schema\b|\bload_file\s*\(|\bextractvalue\s*\(|\bupdatexml\s*\(|\binto\s+(?:out|dump)file\b",
     "note": "读取元数据/文件写入特征"},
    {"sid": "SQLI-BOOL", "category": "SQLI", "weight": 25,
     "regex": r"['\")]\s*(?:or|and)\s+(?:'[^']*'|\d+)\s*=\s*(?:'[^']*'|\d+)", "note": "布尔条件注入"},
    {"sid": "SQLI-STACK", "category": "SQLI", "weight": 25,
     "regex": r";\s*(?:select|insert|update|delete|drop)\b", "note": "堆叠查询特征"},
    {"sid": "SQLI-CMDSHELL", "category": "SQLI", "weight": 45,
     "regex": r"\bxp_cmdshell\b", "note": "SQL Server 命令执行"},
    # XSS
    {"sid": "XSS-SCRIPT", "category": "XSS", "weight": 45,
     "regex": r"<script\b|%3cscript|&#x3c;script", "note": "script 标签注入"},
    {"sid": "XSS-EVENT", "category": "XSS", "weight": 45,
     "regex": r"\bon(?:error|load|click|mouseover|focus|submit)\s*=", "note": "事件处理器注入"},
    {"sid": "XSS-JS", "category": "XSS", "weight": 25,
     "regex": r"javascript\s*:|document\.cookie|<svg[^>]*onload", "note": "JS 伪协议/Cookie 窃取"},
    {"sid": "XSS-ALERT", "category": "XSS", "weight": 25,
     "regex": r"\balert\s*\(\s*(?:document|1|'|\")", "note": "弹窗探测特征"},
    # 命令执行 / 代码执行
    {"sid": "CMDI-SEP", "category": "RCE", "weight": 45,
     "regex": r"(?:;|&&|\|\|?)\s*(?:cat|ls|whoami|id|uname|curl|wget|nc|bash|sh|powershell|cmd)\b",
     "note": "命令拼接执行特征"},
    {"sid": "CMDI-PHP", "category": "RCE", "weight": 45,
     "regex": r"\b(?:system|exec|shell_exec|passthru|popen|proc_open)\s*\(", "note": "PHP 命令执行函数"},
    {"sid": "CMDI-PS", "category": "RCE", "weight": 45,
     "regex": r"powershell(?:\.exe)?\s+(?:-\w+\s+)*(?:-enc|-e\b|-nop|-w\s+hidden)", "note": "PowerShell 编码/隐藏执行"},
    {"sid": "CMDI-SHELL", "category": "RCE", "weight": 25,
     "regex": r"/bin/(?:ba)?sh\b|cmd(?:\.exe)?\s+/c\b", "note": "Shell 解释器调用"},
    {"sid": "CODE-JNDI", "category": "LOG4J", "weight": 45,
     "regex": r"\$\{jndi:|\$\{(?:lower|upper|env|sys|java):", "note": "Log4j2 JNDI 注入"},
    {"sid": "CODE-EXPR", "category": "CODEINJ", "weight": 25,
     "regex": r"\bT\s*\(\s*java\.lang\.|%\{\s*\w+", "note": "表达式/OGNL 注入"},
    # 路径穿越 / 任意文件读取
    {"sid": "PATH-DOTDOT", "category": "PATH", "weight": 25,
     "regex": r"(?:\.\./|\.\.\\){1,}|%2e%2e%2f|%252e%252e", "note": "目录穿越序列"},
    {"sid": "PATH-SENSITIVE", "category": "PATH", "weight": 45,
     "regex": r"/etc/passwd|/etc/shadow|/proc/self/(?:environ|cmdline)|(?:win|boot)\.ini\b", "note": "读取系统敏感文件"},
    {"sid": "PATH-FILTER", "category": "PATH", "weight": 25,
     "regex": r"php://(?:filter|input)|phar://|zip://|file://", "note": "危险文件协议"},
    # 上传 / WebShell
    {"sid": "SHELL-EVAL", "category": "UPLOAD", "weight": 45,
     "regex": r"\b(?:eval|assert)\s*\(\s*(?:\$_(?:POST|GET|REQUEST|COOKIE)|base64_decode|str_rot13)",
     "note": "一句话木马特征"},
    {"sid": "SHELL-MARK", "category": "UPLOAD", "weight": 45,
     "regex": r"\b(?:behinder|godzilla)\b|冰蝎|哥斯拉", "note": "常见 WebShell 管理端特征"},
    {"sid": "UPLOAD-EXT", "category": "UPLOAD", "weight": 25,
     "regex": r"(?:file|upload|filename|name)=[^&\s\"']*\.(?:php\d?|jspx?|asp|aspx|phtml|war)\b",
     "note": "脚本文件上传"},
    # SSRF
    {"sid": "SSRF-META", "category": "SSRF", "weight": 45,
     "regex": r"\b169\.254\.169\.254\b|metadata\.google\.internal|\b100\.100\.100\.200\b",
     "note": "云元数据地址探测"},
    {"sid": "SSRF-INNER", "category": "SSRF", "weight": 25,
     "regex": r"(?:url|target|dest|redirect|image|fetch|proxy|src)\s*=\s*[\"']?(?:https?://)?"
              r"(?:127\.0\.0\.1|localhost|0\.0\.0\.0|\[::1\])",
     "note": "回环地址访问"},
    {"sid": "SSRF-SCHEME", "category": "SSRF", "weight": 25,
     "regex": r"gopher://|dict://", "note": "非常规协议探测"},
    # XXE
    {"sid": "XXE-DOCTYPE", "category": "XXE", "weight": 45,
     "regex": r"<!doctype[^>]*\[|<!entity\b[^>]*system", "note": "外部实体声明"},
    # 反序列化
    {"sid": "DESER-JAVA", "category": "DESER", "weight": 45,
     "regex": r"\brO0AB|\baced0005|\bjava\.io\.objectinputstream\b|\breadobject\s*\(",
     "note": "Java 反序列化特征"},
    {"sid": "DESER-PY", "category": "DESER", "weight": 25,
     "regex": r"\b(?:pickle|yaml)\.loads?\s*\(|__reduce__\b", "note": "Python 不安全反序列化"},
    # 扫描探测
    {"sid": "SCAN-UA", "category": "SCAN", "weight": 40,
     "regex": r"\b(?:sqlmap|nikto|nmap|masscan|acunetix|awvs|appscan|xray|w3af|zgrab|"
              r"nuclei|dirsearch|gobuster|feroxbuster|ffuf|wfuzz|wpscan|joomscan|dirb)\b",
     "note": "自动化扫描器 User-Agent"},
    {"sid": "SCAN-UA-COM", "category": "SCAN", "weight": 25,
     "regex": r"\b(?:nessus|openvas|qualys|burp|zaproxy|whatweb|aquatone)\b", "note": "商业/代理扫描工具特征"},
    {"sid": "SCAN-PATH", "category": "SCAN", "weight": 25,
     "regex": r"/(?:\.git|\.svn|\.hg)/|/\.env\b|/\.ds_store\b|/(?:wp-admin|wp-login\.php|xmlrpc\.php)\b|"
              r"/phpmyadmin\b|/adminer\.php\b|/actuator(?:/|$)|/druid/|/nacos/|/eureka/|"
              r"/server-status\b|/manager/html\b|/solr/|/swagger-ui|/v2/api-docs\b|/backup\b|"
              r"\.sql\b|\.bak\b",
     "note": "敏感路径/备份文件探测"},
]

RULE_KEYWORDS = {
    "SQLI": ["sql注入", "sql 注入", "sql injection", "sqli"],
    "XSS": ["xss", "跨站脚本", "跨站"],
    "RCE": ["命令执行", "命令注入", "代码执行", "rce", "code injection", "command injection"],
    "LOG4J": ["log4j", "jndi"],
    "CODEINJ": ["表达式注入", "ognl", "spel"],
    "PATH": ["目录穿越", "路径穿越", "路径遍历", "任意文件读取", "文件包含", "lfi",
             "path traversal", "directory traversal"],
    "UPLOAD": ["webshell", "文件上传", "恶意上传", "后门文件"],
    "SSRF": ["ssrf", "服务端请求伪造"],
    "XXE": ["xxe", "外部实体"],
    "DESER": ["反序列化", "deserialization"],
    "SCAN": ["扫描", "scan", "探测", "recon", "probe"],
    "BRUTE": ["爆破", "brute force", "bruteforce", "暴力破解", "撞库",
              "credential stuffing", "登录失败"],
    "DOS": ["cc攻击", "cc 攻击", "ddos", "flood", "拒绝服务", "高频请求", "访问频率"],
}

RULE_WEIGHT = {"SCAN": 15, "BRUTE": 25, "DOS": 45}
DEFAULT_RULE_WEIGHT = 20

BENIGN_MONITOR = re.compile(
    r"(?:kube-probe|kubelet|elb-healthchecker|zabbix|nagios|prometheus|blackbox_exporter|"
    r"uptimerobot|healthcheck|health-check|googlehc|azure traffic manager|cloudmonitor|"
    r"aliyun.*monitor|tencent.*monitor)",
    re.I,
)
BENIGN_HEALTH_PATH = re.compile(
    r"^/?(?:health|healthz|health-check|ping|status|metrics|readyz|livez)(?:[/?]|$)", re.I
)
BENIGN_BOT = re.compile(
    r"(?:googlebot|bingbot|baiduspider|yandexbot|sogou web spider|360spider|bytespider)", re.I
)

CATEGORY_INFO = {
    "SQLI": {
        "name": "SQL 注入", "mitre": "T1190",
        "trace": [
            "检索数据库审计/慢查询日志，确认同源请求是否产生异常查询或大量数据返回",
            "检查该接口近期返回 200 且带 union/select 的请求，判断是否已回显数据",
            "核对数据库账号权限，确认是否存在 UDF、文件读写或 xp_cmdshell 调用",
        ],
        "harden": [
            "全部 SQL 改为参数化查询/预编译，禁止字符串拼接",
            "数据库账号最小权限，禁用 FILE、LOAD_FILE、xp_cmdshell",
            "关闭数据库报错回显，统一异常页面；WAF 增加 SQLi 规则",
        ],
    },
    "XSS": {
        "name": "跨站脚本", "mitre": "T1059.007",
        "trace": [
            "检查该入口是反射型还是存储型，确认是否有其他用户已加载恶意脚本",
            "查看 CSP 报告与访问日志中携带脚本参数的请求",
            "排查后台审核、富文本展示等二次触发点",
        ],
        "harden": [
            "按 HTML/JS/URL 上下文做输出编码，避免直接拼接",
            "配置 CSP、Cookie HttpOnly、SameSite",
            "富文本输入使用白名单过滤，禁止事件属性与 javascript: 协议",
        ],
    },
    "RCE": {
        "name": "命令/代码执行", "mitre": "T1059",
        "trace": [
            "用 EDR 查 Web 进程子进程与命令行，重点看 sh/bash/curl/wget/powershell",
            "检查临时目录新增文件、计划任务与异常外联",
            "定位应用中的命令拼接点，确认参数是否被用户控制",
        ],
        "harden": [
            "禁用 system/exec 等危险函数，必须调用时改为参数化+白名单",
            "服务降权运行，容器只读挂载、禁止出网",
            "及时修补框架/组件 RCE 漏洞，WAF 阻断命令拼接特征",
        ],
    },
    "LOG4J": {
        "name": "Log4j2 JNDI 注入", "mitre": "T1190",
        "trace": [
            "查 DNS/HTTP 回连日志，确认是否有 JNDI 外带请求",
            "核对 Log4j 版本与是否加载过远程类",
            "排查应用日志中是否出现 ${jndi: 原文",
        ],
        "harden": [
            "升级 Log4j2 到安全版本，禁用 lookup 与 JNDI",
            "限制服务器出网，保留 DNS 日志用于回溯",
            "WAF 增加 ${jndi: 规则并开启阻断",
        ],
    },
    "CODEINJ": {
        "name": "表达式/代码注入", "mitre": "T1059",
        "trace": [
            "定位表达式解析入口，确认是否执行了用户可控表达式",
            "检查应用日志与异常堆栈中的类加载/反射调用",
            "排查同源请求是否尝试读取系统属性或执行命令",
        ],
        "harden": [
            "禁止对用户输入求值，必须使用白名单表达式",
            "升级 OGNL/SpEL 等组件，关闭危险方法调用",
            "输入校验与 WAF 规则双管齐下",
        ],
    },
    "PATH": {
        "name": "路径穿越/文件读取", "mitre": "T1005",
        "trace": [
            "查同接口是否成功读取敏感文件（200 且响应长度异常）",
            "检查下载接口、日志文件、配置文件是否可被直接访问",
            "核对 Web 根目录外文件是否暴露",
        ],
        "harden": [
            "对路径做规范化校验并限制在允许目录内",
            "禁止用户控制文件路径，下载走固定 ID 映射或对象存储",
            "Web 容器禁止目录遍历，敏感文件移出站点目录",
        ],
    },
    "UPLOAD": {
        "name": "WebShell 上传", "mitre": "T1505.003",
        "trace": [
            "检查上传目录新增文件、修改时间与访问记录",
            "用 EDR 查该目录是否产生脚本子进程与异常网络连接",
            "比对文件哈希/内容，确认是否为已知木马",
        ],
        "harden": [
            "扩展名、MIME、文件头三重校验，禁止脚本后缀",
            "上传目录关闭脚本执行，独立域名或对象存储承载",
            "随机文件名、限制大小与频率，上传后重命名",
        ],
    },
    "SSRF": {
        "name": "SSRF", "mitre": "T1190",
        "trace": [
            "查服务器出站访问内网段与 169.254.169.254 的记录",
            "确认云元数据是否被读取、临时凭证是否泄露",
            "排查 URL 解析、图片抓取、Webhook 等外部请求入口",
        ],
        "harden": [
            "出站请求走白名单，禁止内网地址、回环与元数据地址",
            "云主机启用 IMDSv2 并限制访问",
            "解析 URL 后校验协议与目标 IP，禁止 302 跳转绕过",
        ],
    },
    "XXE": {
        "name": "XXE 外部实体", "mitre": "T1190",
        "trace": [
            "查 XML 接口是否出现异常外连或文件读取",
            "核对 XML 解析器是否允许 DTD 与外部实体",
            "检查同源请求是否尝试读取本地文件",
        ],
        "harden": [
            "禁用外部实体与 DTD，使用安全解析器配置",
            "升级 XML 解析库，限制请求体大小",
            "对 XML 入口增加 WAF 规则",
        ],
    },
    "DESER": {
        "name": "反序列化", "mitre": "T1190",
        "trace": [
            "定位反序列化入口，确认数据是否来自不可信来源",
            "排查异常进程、外联与文件落地，判断是否已执行",
            "核对组件版本与已知利用链",
        ],
        "harden": [
            "禁止反序列化不可信数据，使用 JSON 等安全格式",
            "启用类白名单与签名校验，升级存在利用链的组件",
            "WAF 拦截 rO0AB/aced0005 等序列化魔数",
        ],
    },
    "SCAN": {
        "name": "扫描探测", "mitre": "T1595",
        "trace": [
            "汇总同源探测路径与响应码，评估真实暴露面",
            "确认是否为授权扫描/资产测绘，避免误封业务探针",
            "检查敏感路径是否返回 200，判断是否有信息泄露",
        ],
        "harden": [
            "下线非必要接口、管理后台与备份文件",
            "WAF 封禁扫描器 UA 与高频 404 源 IP",
            "收敛指纹：统一错误页、隐藏 Server/X-Powered-By 头",
        ],
    },
    "BRUTE": {
        "name": "暴力破解", "mitre": "T1110",
        "trace": [
            "统计同源失败与成功登录，排查是否已有账号被攻破",
            "检查账号锁定、异地登录、MFA 变更与权限调整",
            "关联同一 IP/UA 的其他账号尝试，判断是否撞库",
        ],
        "harden": [
            "启用 MFA、失败锁定、验证码与登录限速",
            "禁用默认/弱口令，统一账号体系，防用户名枚举",
            "对高风险登录做风险评分与二次验证",
        ],
    },
    "DOS": {
        "name": "CC/拒绝服务", "mitre": "T1499",
        "trace": [
            "确认流量峰值、来源分布与业务可用性影响",
            "区分真实攻击与活动/爬虫流量，避免误封",
            "关联 CDN/WAF 报表定位攻击特征与集中源",
        ],
        "harden": [
            "接入 CDN/WAF 清洗与限速，配置频控与挑战",
            "弹性扩容与降级预案，关键接口做缓存",
            "封禁恶意 IP 段并持续观察攻击特征变化",
        ],
    },
}

TRACE_GENERIC = [
    "保全原始证据：导出告警前后 30 分钟的完整请求/响应、源 IP、UA、账号与主机日志，记录 SHA-256 后只读保存",
    "确认真实源 IP：核对 X-Forwarded-For / X-Real-IP 与 CDN、负载均衡回源日志，逐跳排查伪造头",
    "同源扩线：查该 IP/UA/会话指纹的全部历史请求，重点找返回 200 的攻击请求与后续异常访问",
    "主机侧排查：Web 目录新增/变更文件、可疑进程与计划任务、异常外联；涉及账号时查登录与权限变更",
]

HARDEN_GENERIC = [
    "修补对应组件与框架漏洞，关闭不必要的对外暴露面与调试接口",
    "更新 WAF/IPS 规则，先在观察模式验证误报再切换阻断",
    "日志留存不少于 6 个月，告警与工单联动，定期复盘规则命中率",
]


# ---------------------------------------------------------------------------
# 研判
# ---------------------------------------------------------------------------
def decode_layers(text: str) -> str:
    """做多层 URL/HTML 解码，覆盖双重编码绕过。"""
    if not text:
        return ""
    out = html.unescape(text)
    for _ in range(2):
        decoded = urllib.parse.unquote(out)
        if decoded == out:
            break
        out = decoded
    return out


def build_fields(alert: dict) -> dict:
    return {
        "payload": decode_layers(alert.get("payload", "")),
        "url": decode_layers(alert.get("url", "")),
        "cmdline": decode_layers(alert.get("cmdline", "")),
        "ua": alert.get("ua", ""),
        "raw": decode_layers(alert.get("raw", "")),
    }


def snippet(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text[:SNIPPET_MAX] + ("..." if len(text) > SNIPPET_MAX else "")


def match_signatures(alert: dict, fields: dict) -> list:
    evidence = []
    for signature in SIGNATURES:
        pattern = re.compile(signature["regex"], re.I)
        for field in ("payload", "url", "cmdline", "ua", "raw"):
            value = fields.get(field, "")
            if not value:
                continue
            matched = pattern.search(value)
            if matched:
                evidence.append({
                    "sid": signature["sid"],
                    "weight": signature["weight"],
                    "field": field,
                    "snippet": snippet(matched.group(0)),
                    "note": signature["note"],
                })
                break

    rule_text = alert.get("rule", "").lower()
    for category, keywords in RULE_KEYWORDS.items():
        for keyword in keywords:
            if keyword in rule_text:
                evidence.append({
                    "sid": "RULE-%s" % category,
                    "weight": RULE_WEIGHT.get(category, DEFAULT_RULE_WEIGHT),
                    "field": "rule",
                    "snippet": snippet(alert.get("rule", "")),
                    "note": "告警名称指向「%s」" % CATEGORY_INFO[category]["name"],
                })
                break
    return evidence


def match_benign(alert: dict, fields: dict, positive: list) -> tuple:
    evidence, notes = [], []
    ua = alert.get("ua", "")
    url = fields.get("url", "")

    if BENIGN_MONITOR.search(ua):
        evidence.append({
            "sid": "BENIGN-MONITOR", "weight": -35, "field": "ua",
            "snippet": snippet(ua), "note": "监控/健康检查 User-Agent",
        })
    if BENIGN_HEALTH_PATH.search(url):
        evidence.append({
            "sid": "BENIGN-HEALTH", "weight": -20, "field": "url",
            "snippet": snippet(url), "note": "健康检查路径",
        })
    src_ip = alert.get("src_ip", "").strip()
    has_strong = any(item["weight"] >= 45 for item in positive)
    if src_ip and not has_strong:
        candidate = src_ip.split(",")[0].strip()
        candidate = re.sub(r":\d+$", "", candidate).strip("[]")
        try:
            if ipaddress.ip_address(candidate).is_private or ipaddress.ip_address(candidate).is_loopback:
                evidence.append({
                    "sid": "BENIGN-PRIVATE", "weight": -15, "field": "src_ip",
                    "snippet": candidate, "note": "内网/回环源 IP，可能是内部扫描器、堡垒机或监控",
                })
        except ValueError:
            notes.append("源 IP %s 不是合法 IP，可能为代理头或解析异常" % snippet(src_ip))
    if BENIGN_BOT.search(ua):
        evidence.append({
            "sid": "BENIGN-BOT", "weight": -10, "field": "ua",
            "snippet": snippet(ua), "note": "自称搜索引擎爬虫，离线无法验证 rDNS，可能为伪装",
        })
    return evidence, notes


def status_notes(alert: dict, float_score: int) -> list:
    status = str(alert.get("status", "")).strip()
    notes = []
    if not status:
        return notes
    if status == "200" and float_score >= 60:
        notes.append("响应 200，需确认攻击是否已成功，优先核对响应体与数据库/主机侧痕迹")
    elif status in ("401", "403", "406", "429", "501"):
        notes.append("请求已被拦截/拒绝（状态 %s），仍需封禁源 IP 并排查绕过尝试" % status)
    elif status.startswith("5"):
        notes.append("响应 %s，可能存在注入/执行异常，建议优先复核" % status)
    return notes


def finalize(result: dict) -> None:
    positive = [item for item in result["evidence"] if item["weight"] > 0]
    total = sum(item["weight"] for item in result["evidence"])
    max_weight = max((item["weight"] for item in positive), default=0)
    if max_weight < 25:
        total = min(total, 20)
    total = max(0, min(100, total))

    if total >= 60:
        verdict, key = VERDICT_ATTACK, "attack"
    elif total >= 25:
        verdict, key = VERDICT_SUSPICIOUS, "suspicious"
    else:
        verdict, key = VERDICT_FALSE, "false_positive"

    categories = {}
    for item in positive:
        sid = item["sid"]
        if sid.startswith("RULE-"):
            category = sid[5:]
        elif sid.startswith("CORR-"):
            continue
        else:
            category = sid.split("-")[0]
            category = {
                "SQLI": "SQLI", "XSS": "XSS", "CMDI": "RCE", "CODE": "CODEINJ",
                "PATH": "PATH", "SHELL": "UPLOAD", "UPLOAD": "UPLOAD",
                "SSRF": "SSRF", "XXE": "XXE", "DESER": "DESER", "SCAN": "SCAN",
                "BRUTE": "BRUTE", "DOS": "DOS",
            }.get(category, category)
            if sid == "CODE-JNDI":
                category = "LOG4J"
        if category in CATEGORY_INFO:
            categories[category] = categories.get(category, 0) + item["weight"]

    ordered = sorted(categories.items(), key=lambda pair: (-pair[1], pair[0]))
    attack_types = [
        {"category": category, "name": CATEGORY_INFO[category]["name"],
         "mitre": CATEGORY_INFO[category]["mitre"]}
        for category, _ in ordered
    ]
    if key == "false_positive":
        attack_types = []

    if key == "attack":
        severity, priority = ("高", "P1") if total >= 85 else ("中", "P2")
        disposition = (
            "立即封禁源 IP（确认非业务来源后），核对请求是否成功并排查同源活动与主机痕迹"
            if total >= 85 else
            "当班处置：封禁/限速源 IP，核对同源请求与响应，必要时下线接口修复"
        )
    elif key == "suspicious":
        severity, priority = "低", "P3"
        disposition = "人工复核请求原文与响应，确认是业务行为后加白名单，否则升级规则"
    else:
        severity, priority = "无", "P4"
        disposition = "无需处置；若为内部扫描器/监控，建议登记白名单并记录原因"

    trace, harden = [], []
    if key != "false_positive":
        for category, _ in ordered[:2]:
            trace.extend(CATEGORY_INFO[category]["trace"])
            harden.extend(CATEGORY_INFO[category]["harden"])
        trace.extend(TRACE_GENERIC)
        harden.extend(HARDEN_GENERIC)
    else:
        trace.append("判定为误报，无需溯源；保留原始日志便于后续规则复盘")
        harden.append("如为内部扫描/监控，登记白名单并注明来源，避免重复告警")

    notes = result.get("notes", [])
    for note in status_notes(result, total):
        if note not in notes:
            notes.append(note)

    result.update({
        "score": total,
        "verdict": verdict,
        "verdict_key": key,
        "severity": severity,
        "priority": priority,
        "attack_types": attack_types,
        "traceback": trace,
        "hardening": harden,
        "disposition": disposition,
        "notes": notes,
    })


def is_login_alert(result: dict) -> bool:
    text = " ".join([result.get("url", ""), result.get("rule", "")]).lower()
    return bool(re.search(r"login|signin|sign-in|auth|oauth|token|登录|验证码|密码", text))


def correlate(results: list) -> None:
    by_ip = {}
    for result in results:
        if result.get("src_ip"):
            by_ip.setdefault(result["src_ip"], []).append(result)

    for ip, items in by_ip.items():
        brute = [
            item for item in items
            if any(entry["sid"] == "RULE-BRUTE" for entry in item["evidence"])
            or (str(item.get("status", "")) in ("401", "403") and is_login_alert(item))
        ]
        if len(brute) >= 5:
            for item in brute:
                item["evidence"].append({
                    "sid": "CORR-BRUTE", "weight": 35, "field": "source",
                    "snippet": ip,
                    "note": "同一源 IP 登录失败/爆破告警 %d 条，疑似自动化爆破" % len(brute),
                })
                item["notes"].append("同源登录失败 %d 条，建议合并为同一事件处置" % len(brute))

        scan = [
            item for item in items
            if any(entry["sid"].startswith("SCAN-") for entry in item["evidence"])
        ]
        if len(scan) >= 5:
            for item in scan:
                item["evidence"].append({
                    "sid": "CORR-SCAN", "weight": 20, "field": "source",
                    "snippet": ip,
                    "note": "同一源 IP 扫描类告警 %d 条，疑似系统性资产探测" % len(scan),
                })

    for item in results:
        finalize(item)


def analyze(alert: dict) -> dict:
    fields = build_fields(alert)
    evidence = match_signatures(alert, fields)
    positive = [item for item in evidence if item["weight"] > 0]
    benign, notes = match_benign(alert, fields, positive)
    evidence.extend(benign)

    result = {
        "index": alert.get("_index", 0),
        "time": alert.get("time", ""),
        "src_ip": alert.get("src_ip", ""),
        "method": alert.get("method", ""),
        "url": alert.get("url", ""),
        "status": alert.get("status", ""),
        "ua": alert.get("ua", ""),
        "rule": alert.get("rule", ""),
        "host": alert.get("host", ""),
        "raw": alert.get("raw", ""),
        "evidence": evidence,
        "notes": notes,
    }
    finalize(result)
    return result


# ---------------------------------------------------------------------------
# 输出
# ---------------------------------------------------------------------------
def format_request(result: dict, limit: int | None = None) -> str:
    method = result.get("method", "")
    url = result.get("url", "")
    if not url:
        return result.get("raw", "")[:limit] if limit else result.get("raw", "")
    text = ("%s %s" % (method, url)).strip()
    if limit and len(text) > limit:
        text = text[:limit] + "..."
    return text


def summary_payload(results: list, total_input: int, truncated: int, source: str) -> dict:
    counts = {"attack": 0, "suspicious": 0, "false_positive": 0}
    types = {}
    priorities = {}
    for result in results:
        counts[result["verdict_key"]] += 1
        priorities[result["priority"]] = priorities.get(result["priority"], 0) + 1
        for attack in result["attack_types"]:
            types[attack["name"]] = types.get(attack["name"], 0) + 1
    riskiest = max(results, key=lambda item: item["score"], default=None)
    return {
        "source": source,
        "total_input": total_input,
        "analyzed": len(results),
        "truncated": truncated,
        "attack": counts["attack"],
        "suspicious": counts["suspicious"],
        "false_positive": counts["false_positive"],
        "attack_types": types,
        "priorities": priorities,
        "highest": None if riskiest is None else {
            "index": riskiest["index"], "score": riskiest["score"],
            "verdict": riskiest["verdict"],
            "type": riskiest["attack_types"][0]["name"] if riskiest["attack_types"] else "",
        },
    }


def render_text(results: list, summary: dict, detail: bool) -> str:
    lines = [
        "alert-triage %s · 输入 %d 条告警%s" % (
            get_version(), summary["total_input"],
            "（仅分析前 %d 条）" % summary["analyzed"] if summary["truncated"] else "",
        ),
        "",
    ]
    if not results:
        lines.append("没有可分析的告警：请检查输入格式或 --text 内容。")
        return "\n".join(lines)

    for order, result in enumerate(results, 1):
        header = "[%d] #%d %s · %d 分 · %s" % (
            order, result["index"], result["verdict"], result["score"],
            result["priority"] + " " + result["severity"],
        )
        lines.append(header)
        meta = []
        if result.get("time"):
            meta.append("时间: %s" % result["time"])
        if result.get("src_ip"):
            meta.append("源IP: %s" % result["src_ip"])
        if result.get("status"):
            meta.append("状态: %s" % result["status"])
        if meta:
            lines.append("  " + " · ".join(meta))
        request = format_request(result, None if detail else 120)
        if request:
            lines.append("  请求: %s" % request)
        if result.get("rule"):
            lines.append("  告警: %s" % result["rule"])

        if result["attack_types"]:
            type_text = "、".join(
                "%s（%s）" % (item["name"], item["mitre"]) for item in result["attack_types"]
            )
            lines.append("  类型: %s" % type_text)

        lines.append("  证据:")
        for entry in result["evidence"]:
            sign = "+" if entry["weight"] >= 0 else "-"
            lines.append(
                "    %s %s(%+d) %s · %s: \"%s\"" % (
                    sign, entry["sid"], entry["weight"], entry["note"],
                    entry["field"], entry["snippet"],
                )
            )
        if not result["evidence"]:
            lines.append("    未命中任何攻击特征或白名单特征")

        for note in result.get("notes", []):
            lines.append("  提示: %s" % note)

        limit = None if detail else 2
        trace = result["traceback"][:limit] if limit else result["traceback"]
        harden = result["hardening"][:limit] if limit else result["hardening"]
        if trace:
            lines.append("  溯源:")
            for item in trace:
                lines.append("    - %s" % item)
        if harden:
            lines.append("  加固:")
            for item in harden:
                lines.append("    - %s" % item)
        lines.append("  处置: %s" % result["disposition"])
        lines.append("")

    lines.append("─" * 64)
    lines.append(
        "汇总: 真实攻击 %d · 可疑 %d · 误报 %d（共 %d 条）" % (
            summary["attack"], summary["suspicious"],
            summary["false_positive"], summary["analyzed"],
        )
    )
    if summary["attack_types"]:
        lines.append("类型: " + " · ".join(
            "%s ×%d" % (name, count) for name, count in summary["attack_types"].items()
        ))
    if summary["priorities"]:
        lines.append("优先级: " + " · ".join(
            "%s %d 条" % (key, summary["priorities"][key])
            for key in sorted(summary["priorities"])
        ))
    if summary["highest"] and summary["highest"]["verdict"] != VERDICT_FALSE:
        highest = summary["highest"]
        lines.append(
            "最高风险: #%d（%s，%d 分%s）" % (
                highest["index"], highest["verdict"], highest["score"],
                "，" + highest["type"] if highest["type"] else "",
            )
        )
    if summary["attack"]:
        lines.append("下一步: 先处置 P1/P2，再对可疑项人工复核；误报项加白名单并记录原因")
    elif summary["suspicious"]:
        lines.append("下一步: 对可疑项人工复核请求原文与响应，确认后再调整规则")
    else:
        lines.append("下一步: 本批无需处置，建议复查规则是否过于敏感")
    return "\n".join(lines)


def render_json(results: list, summary: dict) -> str:
    payload = {
        "tool": "alert-triage",
        "version": get_version(),
        "summary": summary,
        "alerts": results,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="安全告警研判：真攻击/误报、攻击类型、溯源与加固建议",
    )
    parser.add_argument("--text", help="单条告警文本；传 - 从标准输入读取")
    parser.add_argument("--input", help="批量输入文件：日志 / CSV / JSON / JSONL")
    parser.add_argument("--format", choices=("text", "json"), default="text", help="输出格式")
    parser.add_argument("--fail-on", choices=("none", "attack", "suspicious"),
                        default="none", help="命中该级别时返回退出码 1")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                        help="最多分析多少条（默认 %d）" % DEFAULT_LIMIT)
    parser.add_argument("--version", action="version", version=f"%(prog)s {get_version()}")
    args = parser.parse_args(argv)

    if args.text is not None and args.input:
        parser.error("--text 与 --input 只能二选一")

    source = ""
    if args.text is not None:
        if args.text == "-":
            text, source = sys.stdin.read(), "stdin"
        else:
            text, source = args.text, "text"
        alerts = parse_text_block(text)
    elif args.input:
        path = Path(args.input).expanduser()
        if not path.is_file():
            print("输入文件不存在: %s" % path, file=sys.stderr)
            return 2
        try:
            alerts = parse_file(path)
        except OSError as exc:
            print("读取失败: %s" % exc, file=sys.stderr)
            return 2
        source = str(path)
    elif not sys.stdin.isatty():
        text, source = sys.stdin.read(), "stdin"
        alerts = parse_text_block(text)
    else:
        parser.error("请用 --text 或 --input 指定告警内容")

    if not alerts:
        print("输入为空或无法解析：请提供告警文本，或检查文件编码与格式", file=sys.stderr)
        return 2

    total_input = len(alerts)
    truncated = 0
    if args.limit > 0 and total_input > args.limit:
        truncated = total_input - args.limit
        alerts = alerts[:args.limit]

    results = []
    for index, alert in enumerate(alerts, 1):
        alert["_index"] = index
        results.append(analyze(alert))
    correlate(results)

    summary = summary_payload(results, total_input, truncated, source)
    detail = len(results) == 1

    if args.format == "json":
        print(render_json(results, summary))
    else:
        print(render_text(results, summary, detail))

    if args.fail_on == "attack" and summary["attack"] > 0:
        return 1
    if args.fail_on == "suspicious" and (summary["attack"] + summary["suspicious"]) > 0:
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    raise SystemExit(main())

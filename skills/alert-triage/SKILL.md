---
name: alert-triage
description: Triage security alerts from WAF, IDS, EDR or web logs and decide whether an alert is a real attack or a false positive, which attack type it is, how to trace the source, and how to harden. Use when analyzing monitoring alerts, suspicious requests, SOC tickets, or noisy security events.
---

# alert-triage

把一条告警或一批日志，整理成可以直接派单的研判结论：**真攻击还是误报、属于什么攻击、溯源怎么查、加固怎么补**。

规则驱动、离线运行、同样输入永远得到同样输出，适合值班分流和 CI 门禁。

## 何时使用

- WAF / IDS / IPS / EDR / Web 访问日志出现告警，需要快速判断真假
- 安全监控值班，要把告警分成 P1/P2/P3 并给出处置动作
- 应急响应初期，需要逐条列出溯源方向与加固清单
- 规则调优后做回归，确认新规则不会把业务请求误判

## 运行

```bash
# 单条告警（key=value、key: value 或直接粘贴告警详情）
python scripts/alert_triage.py --text 'src_ip=1.2.3.4 url="/?id=1 union select 1" ua="sqlmap/1.7"'

# 批量文件：日志 / CSV / JSON / JSONL 自动识别
python scripts/alert_triage.py --input access.log
python scripts/alert_triage.py --input alerts.csv --format json
python scripts/alert_triage.py --input alerts.jsonl --fail-on attack

# 管道输入
cat alert.txt | python scripts/alert_triage.py --text -
```

支持的输入字段（可混用）：时间、源 IP、方法、URL、状态码、UA、告警名称、payload、目标、账号、命令行、次数。CSV 需带表头，JSON 支持数组、`{"alerts": [...]}` 和 JSONL。

## 研判口径

- 分数 0–100：**≥ 60 真实攻击**，**25–59 可疑需复核**，**< 25 误报/噪声**
- 双向证据：加分项是命中的攻击特征，减分项是监控 UA、健康检查路径、内网源 IP 等误报抑制
- 只命中弱特征（单个 `select` 词、普通 `alert(` 文本）不会判为攻击
- 同源关联：同一 IP 出现多条登录失败或扫描告警时，自动合并升级
- 攻击类型带 ATT&CK 编号，覆盖 SQL 注入、XSS、RCE、Log4j、文件读取、WebShell、SSRF、XXE、反序列化、扫描探测、爆破、CC

## 输出内容

每条告警给出：结论与分数、攻击类型、逐条证据（规则 ID、权重、命中字段、原文片段）、处置优先级、溯源步骤、加固清单。

- `--format json`：结构化结果，可直接进工单或 SIEM
- `--fail-on attack`：存在真实攻击时退出码为 1，可做 CI / 值班门禁
- `--limit N`：大文件只分析前 N 条，默认 1000

## 坑点

- 这是启发式研判，不是定性结论；高危告警仍需人工核对请求原文与响应体
- 源 IP 可能是 CDN / 代理回源地址，溯源前先核对 X-Forwarded-For 与回源日志
- 内网源不天然安全，可能是失陷主机或未登记的扫描器，需结合资产台账确认
- 授权测试期间扫描类告警属于预期行为，确认后加白名单，不要直接封禁
- 规则库是显式列表，新型攻击可能漏报；应结合业务日志定期补充特征

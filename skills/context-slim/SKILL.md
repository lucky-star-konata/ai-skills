---
name: context-slim
description: Compress long chat transcripts or documents into a budgeted structured summary. Use when context is too long, before handing a session to another agent, when compacting history, or when you need decisions, TODOs, errors, and key facts kept under a token budget.
---

# context-slim

把长会话压成一份「交接文档」：决策、待办、报错、关键事实优先保留，其余按 token 预算丢弃。

## 何时使用

- 会话太长，快撞上下文上限，需要压缩后再继续
- 要把当前任务交接给另一个 Agent / 另一个人
- 会议记录、issue 讨论、调试日志太长，只想看结论
- 需要固定预算的上下文（例如给摘要留 1500 tokens）

## 运行

```bash
python scripts/context_slim.py chat.md
python scripts/context_slim.py logs/session.jsonl --budget 1500
python scripts/context_slim.py notes.md --budget 800 --json
cat chat.txt | python scripts/context_slim.py - --budget 1200
```

支持纯文本、Markdown，以及 JSON / JSONL 格式的消息记录（自动识别 `role` + `content`）。

常用参数：`--budget`（目标 token，默认 1500，0 表示不限）、`--json`、`--keep-code`、`--no-dropped`。

## 抽取规则

| 分类 | 识别信号 |
| --- | --- |
| 决策与约束 | 决定 / 采用 / 统一 / 必须 / 禁止 / decided / must / never |
| 待办 | TODO / FIXME / 待办 / 下一步 / pending / next step |
| 问题与报错 | Error / Exception / Traceback / 失败 / 报错 / blocker |
| 关键事实 | URL、文件路径、版本号、端口 |
| 其他要点 | 其余段落中评分最高的首句 |

评分 = 分类权重 + 时间新近度 + 长度惩罚；短句、近期内容优先。

## 输出约定

- 固定小节：决策与约束 / 待办 / 问题与报错 / 关键事实 / 其他要点
- 头部给出原始与输出 token 估算、压缩率
- 末尾用 `<details>` 折叠被丢弃的段落清单（可用 `--no-dropped` 关闭）
- `--json` 输出 `sections` 与 `dropped` 结构化结果

## 坑点

- 纯规则抽取，不做语义理解：改写过的同义句不会被合并
- 代码块默认折叠为「代码块 N 行」；含报错关键字的会保留前几行，`--keep-code` 可全量保留
- JSONL 里 `content` 为多段数组时只取 text 段，图片和附件会被忽略
- 预算是估算值（CJK 1 字符 ≈ 1 token，其余 4 字符 ≈ 1 token），不是精确计费

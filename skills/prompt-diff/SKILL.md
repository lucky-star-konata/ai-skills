---
name: prompt-diff
description: Compare two prompt versions and flag lost constraints, changed limits, and removed output-format rules. Use when reviewing prompt changes, before shipping a system prompt update, or when a model starts ignoring rules that used to work.
---

# prompt-diff

提示词改版后的回归检查：哪些约束被删了、哪些数字变了、哪些输出格式要求丢了。

## 何时使用

- 修改 system prompt / 预设 / rubric 之后，提交前对比新旧版本
- 模型突然不遵守某条规则，怀疑是改版时丢了约束
- 多人协作维护提示词，评审时想看清实际改动
- 想把提示词变更纳入 CI

## 运行

```bash
python scripts/prompt_diff.py old.md new.md
python scripts/prompt_diff.py v1.txt v2.txt --json
python scripts/prompt_diff.py v1.md v2.md --fail-level high
```

`--fail-level` 可选 `none`（默认）/ `low` / `medium` / `high`，达到该风险等级时退出码为 1。

## 判定逻辑

1. 逐行抽取有效内容，按关键词分类：护栏、约束、输出格式、示例、角色
2. 用归一化文本做相似度匹配（SequenceMatcher，阈值 0.62）
3. 旧版有、新版找不到对应项的记为「丢失」；反之为「新增」
4. 相似但不完全一致的记为「变更」，并检查数字/上限是否变化

风险等级：

| 等级 | 情形 |
| --- | --- |
| 高 | 丢失护栏、约束或输出格式要求 |
| 中 | 丢失示例；限制值（数字）发生变化 |
| 低 | 其他丢失或新增内容 |

## 输出约定

- 分组展示：丢失 / 变更 / 新增，每条带旧版与新版的原始行号和内容
- 末尾统计丢失、新增、变更、未变的条数，并给出整体风险
- `--json` 输出 `removed[]`、`added[]`、`changed[]`、`stats`、`risk`

## 坑点

- 纯文本相似度匹配，整段重写会被判成「丢失 + 新增」，需人工合并判断
- 换行方式影响匹配：长句被拆行时建议先整理格式
- 中文同义改写不会识别为同一条，报告需要人复核

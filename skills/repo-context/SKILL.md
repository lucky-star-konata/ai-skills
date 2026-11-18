---
name: repo-context
description: Generate a factual AGENTS.md or CLAUDE.md draft from a repository. Use when onboarding a coding agent to a repo, writing project context files, or documenting stack, commands, and directory layout before starting work.
---

# repo-context

扫描仓库，生成一份只含可验证事实的上下文文件草稿，避免手写 AGENTS.md 时编造命令和目录。

## 何时使用

- 新仓库要接入 Claude Code / Codex / TRAE，需要一份 AGENTS.md
- 接手别人的项目，先快速摸清技术栈、命令和目录
- 团队里每个人的上下文文件各不相同，想先统一一个基线

## 运行

```bash
python scripts/repo_context.py .
python scripts/repo_context.py . --write AGENTS.md
python scripts/repo_context.py . --write AGENTS.md --force   # 覆盖已有文件
python scripts/repo_context.py . --json
```

## 生成内容

| 小节 | 来源 |
| --- | --- |
| 项目概览 | 目录名、文件数、扩展名统计 |
| 技术栈 | package.json / pyproject.toml / requirements.txt / go.mod / Cargo.toml |
| 目录结构 | 扫描结果的顶层聚合，按文件数排序 |
| 常用命令 | package.json scripts，自动识别 npm / pnpm / yarn |
| 测试 | tests 目录、test_*.py、*.test.*、*.spec.* |
| 约定 | 固定留空，标注「待补充」 |

## 输出约定

- 只写扫描到的事实；命令注明来源，并明确「未执行验证」
- 「约定」「禁止事项」不猜测，留给人工补充
- `--write` 默认拒绝覆盖已有文件，需显式 `--force`
- `--json` 输出 `stack`、`commands`、`tests`、`stats`、`markdown`

## 坑点

- 只读取 K 文件清单和几个常见 manifest，不解析源码，推断能力有限
- monorepo 只按根目录聚合，子包信息需要单独对子包运行
- 扫描跳过 node_modules、dist、.venv 等目录；如需包含，手动调整脚本里的忽略列表

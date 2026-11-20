# ai-skills

[![CI](https://github.com/lucky-star-konata/ai-skills/actions/workflows/ci.yml/badge.svg)](https://github.com/lucky-star-konata/ai-skills/actions/workflows/ci.yml)

一组面向编码 Agent（Claude Code / Codex / Cursor / TRAE）的原创 Agent Skills。
每个 skill 都是**可直接运行的本地工具**，零第三方依赖，只用 Python 3 标准库。

## 包含的 Skill

| Skill | 解决的问题 | 入口 |
| --- | --- | --- |
| [skill-audit](skills/skill-audit/SKILL.md) | 发布前质检 SKILL.md / CLAUDE.md / AGENTS.md：元数据规范、触发词冲突、上下文预算、失效引用 | `python skills/skill-audit/scripts/audit_skills.py .` |
| [slop-check](skills/slop-check/SKILL.md) | 检测前端页面里的「AI 味」套路（紫蓝渐变、三卡片、空话文案…）并给出替换建议 | `python skills/slop-check/scripts/slop_check.py src/` |
| [context-slim](skills/context-slim/SKILL.md) | 把长会话 / 长文档压成结构化摘要，按 token 预算裁剪，保留决策、待办、报错和关键事实 | `python skills/context-slim/scripts/context_slim.py chat.md --budget 1500` |
| [prompt-diff](skills/prompt-diff/SKILL.md) | 对比提示词新旧版本，标出丢失的约束、变更的限制值和新增内容 | `python skills/prompt-diff/scripts/prompt_diff.py old.md new.md` |
| [repo-context](skills/repo-context/SKILL.md) | 扫描仓库生成 AGENTS.md 草稿：技术栈、目录结构、命令、测试 | `python skills/repo-context/scripts/repo_context.py . --write AGENTS.md` |
| [mcp-lint](skills/mcp-lint/SKILL.md) | 检查 MCP 配置：命令是否存在、明文凭据、明文 http、重名 server | `python skills/mcp-lint/scripts/mcp_lint.py .` |

六个工具都不联网、不调用模型，结果可复现，适合放进 CI 或 pre-commit。

## 安装

把 skill 目录复制到对应 Agent 的 skills 目录即可。

```bash
# Claude Code（macOS / Linux）
cp -r skills/* ~/.claude/skills/

# 项目内使用（TRAE / Cursor / Codex）
mkdir -p .trae/skills && cp -r skills/* .trae/skills/
```

```powershell
# Windows
Copy-Item -Recurse skills\* "$env:USERPROFILE\.claude\skills\"
```

## 使用示例

```bash
# 1. 审计仓库里所有 skill 文件
python skills/skill-audit/scripts/audit_skills.py . --json

# 2. 检查前端页面的 AI 味，低于 70 分时返回非零退出码
python skills/slop-check/scripts/slop_check.py src/ --threshold 70

# 3. 压缩一段长对话，目标 1500 tokens
python skills/context-slim/scripts/context_slim.py logs/session.md --budget 1500 > summary.md

# 4. 提示词改版回归，高风险丢失时返回非零
python skills/prompt-diff/scripts/prompt_diff.py prompt-v1.md prompt-v2.md --fail-level high

# 5. 生成仓库上下文草稿
python skills/repo-context/scripts/repo_context.py . --write AGENTS.md

# 6. 检查 MCP 配置
python skills/mcp-lint/scripts/mcp_lint.py .trae/mcp.json --strict
```

每个脚本都支持 `--help`。JSON 输出便于接入 CI。

## 设计原则

- **确定性**：规则与启发式驱动，同样输入永远得到同样输出，不依赖模型发挥。
- **零依赖**：只用标准库，拷进任何环境都能跑。
- **可解释**：每条结论都带文件、行号、命中的原文和修复建议。
- **有退出码**：`--strict` / `--threshold` 模式下可直接当 CI 门禁。

## License

[MIT](LICENSE)

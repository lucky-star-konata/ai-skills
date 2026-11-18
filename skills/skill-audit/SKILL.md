---
name: skill-audit
description: Audit agent skill files before publishing. Use when reviewing or validating SKILL.md, CLAUDE.md, or AGENTS.md quality, YAML frontmatter, trigger-word conflicts, dead file references, or context token budget in a skills repository.
---

# skill-audit

发布 skill 之前先跑一遍，避免「自己写的 skill 永远不触发」和「一个 skill 吃掉半格上下文」。

## 何时使用

- 新增或修改 `SKILL.md` / `CLAUDE.md` / `AGENTS.md` 后，提交前自检
- skill 明明存在但 Agent 不触发，怀疑 name / description 有问题
- 一个仓库里堆了很多 skill，担心触发词互相抢
- 想知道每个 skill 实际占用多少上下文

## 运行

```bash
python scripts/audit_skills.py <技能目录>
python scripts/audit_skills.py . --json > audit.json
python scripts/audit_skills.py . --strict   # 有 warn 也返回非零，适合 CI
```

常用参数：`--max-tokens`（单文件正文上限，默认 5000）、`--json`、`--strict`、`--quiet`。

## 检查项

| 类别 | 规则 | 级别 |
| --- | --- | --- |
| frontmatter | 缺失、无法解析 | error |
| name | 缺失、非 `lower-kebab-case`、超过 64 字符、与目录名不一致 | error / warn |
| description | 缺失、短于 40 字符、超过 1024 字符、没有任何触发线索 | error / warn |
| 正文 | 为空、没有标题、超过 token 预算 | error / warn |
| 引用 | `scripts/`、`references/`、`assets/`、`templates/` 下引用的文件不存在 | warn |
| 冲突 | 跨文件重名、description 高度相似（Jaccard > 0.72） | error / warn |

## 输出约定

- 每条问题包含 `级别 / 文件 / 字段 / 说明`，按严重程度排序
- 末尾给出文件数、问题计数、总 token 估算
- `--json` 输出结构化结果，字段为 `files[].issues[]` 与 `summary`
- 退出码：有 error 返回 1；`--strict` 下存在 warn 也返回 1

## 判断口径

- token 估算：CJK 字符按 1 token，其余按 4 字符 1 token，属于保守上限
- description 相似度只看词面重合，不做语义判断；命中后由人工确认是否真的冲突
- 资源引用只检查约定目录，正文里的示例路径（如 `src/app.ts`）不算失效

## 坑点

- Windows 下脚本输出中文需要 UTF-8，脚本已强制设置 stdout 编码
- `CLAUDE.md` / `AGENTS.md` 通常没有 frontmatter，缺失只报 info，不算错误
- 名称必须与安装后的目录名一致，改名后记得同步目录

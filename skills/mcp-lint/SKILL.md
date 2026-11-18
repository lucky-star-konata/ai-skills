---
name: mcp-lint
description: Lint MCP server configuration files. Use when validating mcp.json or claude_desktop_config.json, checking tool server commands, hardcoded secrets, insecure http endpoints, or duplicate server names before starting an agent.
---

# mcp-lint

启动 Agent 之前先扫一遍 MCP 配置：命令能不能找到、有没有明文密钥、URL 是不是裸 http、有没有重名 server。

## 何时使用

- 新装了 MCP server，配置写完不确定对不对
- 排查「工具没加载」「连接超时」类问题
- 仓库要公开，检查配置文件里有没有硬编码 token
- 团队统一 MCP 配置规范，想加一道 CI 检查

## 运行

```bash
python scripts/mcp_lint.py .
python scripts/mcp_lint.py .trae/mcp.json
python scripts/mcp_lint.py . --json
python scripts/mcp_lint.py . --strict   # 有警告也返回非零
```

会自动查找 `mcp.json`、`.mcp.json`、`mcp_config.json`、`claude_desktop_config.json`。

## 检查项

| 类别 | 规则 | 级别 |
| --- | --- | --- |
| 语法 | JSON 解析失败、同一对象里重复键 | error |
| 结构 | 缺少 `mcpServers` / `servers`、server 条目不是对象 | error |
| 启动 | 既没有 `command` 也没有 `url`；`command` 为空 | error |
| 命令 | 裸命令不在 PATH；相对路径文件不存在 | warn |
| 传输 | 非本机地址使用 `http://` 明文 | warn |
| 凭据 | `env` 中 TOKEN / KEY / SECRET / PASSWORD 为字面量；`args` 里出现 `sk-`、`ghp_`、`Bearer` | warn |
| 冗余 | 同时配置 `command` 与 `url`；`disabled: true` | warn / info |

## 输出约定

- 按配置文件分组，每条问题带 server 名和原因
- 末尾汇总配置数、server 数、错误/警告/提示
- `--json` 输出 `configs[]` 与 `summary`
- 退出码：有 error 为 1；`--strict` 下有 warn 也为 1

## 坑点

- `shutil.which` 判断只反映当前 shell 的 PATH，GUI 启动的客户端可能有不同环境
- 不解析 `env` 的实际取值，只按变量名和「是否含 `$` / `%` 占位」判断
- 不校验远端 server 是否真的可连，离线静态检查，不代表服务可用

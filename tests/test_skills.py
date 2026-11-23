import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable


def run(args, cwd=None):
    return subprocess.run(
        [PY, *args], cwd=cwd or ROOT, capture_output=True,
        text=True, encoding="utf-8", errors="replace",
    )


class SkillSmokeTests(unittest.TestCase):
    def test_all_scripts_expose_version(self):
        for script in (
            "skills/skill-audit/scripts/audit_skills.py",
            "skills/slop-check/scripts/slop_check.py",
            "skills/context-slim/scripts/context_slim.py",
            "skills/prompt-diff/scripts/prompt_diff.py",
            "skills/repo-context/scripts/repo_context.py",
            "skills/mcp-lint/scripts/mcp_lint.py",
        ):
            result = run([script, "--version"])
            self.assertEqual(result.returncode, 0, f"{script}: {result.stderr}")
            self.assertRegex(result.stdout, r"\d+\.\d+\.\d+")

    def test_skill_audit_passes_on_repo(self):
        result = run(["skills/skill-audit/scripts/audit_skills.py", "."])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("结果: 通过", result.stdout)
        self.assertNotIn("ERROR", result.stdout)

    def test_slop_check_detects_cliche(self):
        html = (
            '<section style="text-align:center;font-size:64px">'
            "<h1>\U0001F680 重新定义团队协作</h1>"
            '<button class="rounded-full bg-gradient-to-r from-purple-500 to-pink-500">开始</button>'
            "</section>"
        )
        with tempfile.TemporaryDirectory() as tmp:
            page = Path(tmp) / "page.html"
            page.write_text(html, encoding="utf-8")
            result = run(["skills/slop-check/scripts/slop_check.py", str(page)])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("AI-001", result.stdout)
        self.assertIn("评分", result.stdout)

    def test_context_slim_keeps_decisions_and_todos(self):
        content = (
            "# 会话\n\n## 决策\n决定采用 Docker Compose 部署，禁止写入本地文件。\n\n"
            "## 待办\n- TODO: 补一个健康检查接口\n\n## 闲聊\n一些无关内容。\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "chat.md"
            path.write_text(content, encoding="utf-8")
            result = run([
                "skills/context-slim/scripts/context_slim.py", str(path), "--budget", "800",
            ])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("决策与约束", result.stdout)
        self.assertIn("待办", result.stdout)

    def test_prompt_diff_flags_removed_constraint(self):
        old = "禁止泄露用户手机号和住址。\n最多返回 5 条结果。\n"
        new = "最多返回 10 条结果。\n"
        with tempfile.TemporaryDirectory() as tmp:
            old_path = Path(tmp) / "old.txt"
            new_path = Path(tmp) / "new.txt"
            old_path.write_text(old, encoding="utf-8")
            new_path.write_text(new, encoding="utf-8")
            result = run([
                "skills/prompt-diff/scripts/prompt_diff.py", str(old_path), str(new_path),
            ])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("丢失", result.stdout)
        self.assertIn("变更", result.stdout)

    def test_repo_context_detects_node(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "package.json").write_text(
                json.dumps({"name": "demo", "scripts": {"build": "vite build"}}),
                encoding="utf-8",
            )
            result = run(["skills/repo-context/scripts/repo_context.py", tmp])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Node.js", result.stdout)
        self.assertIn("npm run build", result.stdout)

    def test_mcp_lint_flags_broken_server(self):
        data = {"mcpServers": {"broken": {"args": []}}}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mcp.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            result = run(["skills/mcp-lint/scripts/mcp_lint.py", str(path)])
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("ERROR", result.stdout)
        self.assertIn("broken", result.stdout)


if __name__ == "__main__":
    unittest.main()

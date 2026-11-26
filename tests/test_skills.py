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

    def test_alert_triage_flags_sqli_with_advice(self):
        text = (
            "src_ip=45.33.12.9 method=GET "
            "url=\"/item?id=1 union select username,password from users\" "
            "status=200 ua=\"sqlmap/1.7\" rule=\"SQL注入\""
        )
        result = run(["skills/alert-triage/scripts/alert_triage.py", "--text", text])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("真实攻击", result.stdout)
        self.assertIn("SQL 注入", result.stdout)
        self.assertIn("溯源", result.stdout)
        self.assertIn("加固", result.stdout)

    def test_alert_triage_marks_monitor_false_positive(self):
        text = (
            "src_ip=10.0.3.21 method=GET url=/healthz status=200 "
            "ua=\"kube-probe/1.29\" rule=\"异常探测\""
        )
        result = run(["skills/alert-triage/scripts/alert_triage.py", "--text", text])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("误报", result.stdout)
        self.assertIn("内网", result.stdout)

    def test_alert_triage_batch_json_fail_on_attack(self):
        content = (
            "time,src_ip,method,url,status,ua,rule\n"
            '2026-11-26 10:00:01,45.33.12.9,GET,"/item?id=1 union select 1",200,'
            '"sqlmap/1.7","SQL注入"\n'
            '2026-11-26 10:00:02,10.0.3.21,GET,/index.html,200,"Mozilla/5.0","正常访问"\n'
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "alerts.csv"
            path.write_text(content, encoding="utf-8")
            result = run([
                "skills/alert-triage/scripts/alert_triage.py", "--input", str(path),
                "--format", "json", "--fail-on", "attack",
            ])
        self.assertEqual(result.returncode, 1, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["summary"]["attack"], 1)
        self.assertEqual(payload["summary"]["false_positive"], 1)
        self.assertEqual(len(payload["alerts"]), 2)


if __name__ == "__main__":
    unittest.main()

"""验证 PR 范围选择、真实 Git 差异与汇总拒绝结果。"""

import copy
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.ci.gate import CHECKS, check_results, select_checks


class CIGateTests(unittest.TestCase):
    """选择保持保守，失败或缺失证据不能成为绿色门禁。"""

    def test_domain_selection(self):
        """各域改动保留完整相关检查，依赖和未知范围扩大验证。"""
        cases = [
            (["docs/intro/index.md"], {"trust", "docs"}),
            (["frontend/src/pages/Workspace.vue"], {"trust", "frontend"}),
            (
                ["backend/yuxi/modules/agents/runtime.py"],
                {"trust", "backend", "ruff", "service", "system"},
            ),
            (
                ["backend/test/unit/test_example.py"],
                {"trust", "backend", "ruff", "service"},
            ),
            (["frontend/pnpm-lock.yaml"], {"trust", "frontend", "dependencies"}),
            (
                ["backend/uv.lock"],
                {"trust", "backend", "ruff", "service", "system", "dependencies"},
            ),
            (
                ["docs/package.json", "frontend/src/index.js"],
                {"trust", "docs", "frontend", "dependencies"},
            ),
        ]
        for paths, expected in cases:
            with self.subTest(paths=paths):
                self.assertEqual(
                    {key for key, value in select_checks(paths).items() if value},
                    expected,
                )
        for path in (
            "AGENTS.md",
            "ARCHITECTURE.md",
            "Makefile",
            "scripts/ci/gate.py",
            ".github/workflows/test.yml",
            "docker-compose.yml",
            "new-domain/code.py",
        ):
            with self.subTest(path=path):
                self.assertTrue(all(select_checks([path]).values()))
        self.assertTrue(all(select_checks([]).values()))

    def test_required_result_rejects_failures_and_missing_evidence(self):
        """真实调度结果必须与已成功产生的选择匹配。"""
        selected = select_checks(["docs/intro/index.md"])
        needs = {
            key: {"result": "success" if enabled else "skipped"}
            for key, enabled in selected.items()
        }
        needs["changes"] = {
            "result": "success",
            "outputs": {"selected": json.dumps(selected)},
        }
        self.assertEqual(check_results(needs), [])
        for name in ("trust", "docs", "changes"):
            for status in ("failure", "cancelled", "skipped", ""):
                with self.subTest(name=name, status=status):
                    broken = copy.deepcopy(needs)
                    broken[name]["result"] = status
                    self.assertTrue(check_results(broken))
        for name in (*CHECKS, "changes"):
            with self.subTest(missing=name):
                broken = copy.deepcopy(needs)
                del broken[name]
                self.assertTrue(check_results(broken))
        self.assertTrue(
            check_results({name: {"result": "success"} for name in CHECKS}),
            "全部子任务成功也不能替代缺失的选择证据",
        )
        for output in (
            "",
            "null",
            "[]",
            "{}",
            json.dumps(selected | {"docs": "true"}),
            json.dumps(selected | {"trust": False}),
        ):
            with self.subTest(output=output):
                broken = copy.deepcopy(needs)
                broken["changes"]["outputs"]["selected"] = output
                self.assertTrue(check_results(broken))
        broken = copy.deepcopy(needs)
        broken["backend"]["result"] = "failure"
        self.assertTrue(
            check_results(broken), "未选择但实际执行失败的 job 也不能被隐藏"
        )

    def test_reusable_workflow_rejects_skipped_children(self):
        """多 job 工作流不能用另一个成功子 job 掩盖跳过或取消。"""
        for status in ("failure", "cancelled", "skipped", None):
            with self.subTest(status=status):
                self.assertTrue(
                    check_results(
                        {"first": {"result": "success"}, "second": {"result": status}},
                        require_selection=False,
                    )
                )
        self.assertEqual(
            check_results(
                {"first": {"result": "success"}, "second": {"result": "success"}},
                require_selection=False,
            ),
            [],
        )
        self.assertTrue(check_results({}, require_selection=False))

    def test_cli_reads_renamed_and_deleted_paths_and_returns_failure(self):
        """临时 Git 仓库验证真实差异输入与进程退出码。"""
        script = str(Path(__file__).with_name("gate.py").resolve())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def git(*args):
                """仅在测试临时仓库执行 Git。"""
                return (
                    subprocess.check_output(
                        ["git", *args], cwd=root, stderr=subprocess.DEVNULL
                    )
                    .decode()
                    .strip()
                )

            git("init")
            git("config", "user.name", "CI test")
            git("config", "user.email", "ci@example.com")
            (root / "backend").mkdir()
            (root / "docs").mkdir()
            (root / "backend/source.py").write_text("original\n")
            git("add", ".")
            git("commit", "-m", "base")
            base = git("rev-parse", "HEAD")
            (root / "backend/source.py").rename(root / "docs/moved.md")
            git("add", "-A")
            git("commit", "-m", "move")
            output = root / "output"
            env = os.environ | {
                "BASE_SHA": base,
                "HEAD_SHA": git("rev-parse", "HEAD"),
                "GITHUB_OUTPUT": str(output),
            }
            result = subprocess.run(
                ["python3", script, "select"],
                cwd=root,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            selected = json.loads(output.read_text().split("=", 1)[1])
            self.assertTrue(selected["backend"])
            self.assertTrue(selected["system"])
            self.assertTrue(selected["docs"])
            for mode, data, code in (
                ("check-jobs", {"test": {"result": "cancelled"}}, 1),
                ("check-jobs", {"test": {"result": "skipped"}}, 1),
                ("check-jobs", {"test": {"result": "success"}}, 0),
                ("check", {name: {"result": "success"} for name in CHECKS}, 1),
                ("check", {}, 1),
            ):
                result = subprocess.run(
                    ["python3", script, mode],
                    env=os.environ | {"NEEDS_JSON": json.dumps(data)},
                    check=False,
                    capture_output=True,
                )
                self.assertEqual(result.returncode, code)


if __name__ == "__main__":
    unittest.main()

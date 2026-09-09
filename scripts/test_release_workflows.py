"""发布事件与应用、CLI 发布边界的回归检查。"""

import re
import unittest
from pathlib import Path


WORKFLOWS = Path(__file__).resolve().parents[1] / ".github/workflows"


class ReleaseWorkflowTests(unittest.TestCase):
    """阻止候选检查缺失和应用 Release 误触发 CLI 上传。"""

    def assert_release_events(self, workflows: dict[str, str]) -> None:
        """检查各发布门禁的真实事件声明。"""
        for name in (
            "trust",
            "test",
            "web",
            "ruff",
            "system-tests",
            "dependency-audit",
            "deploy",
        ):
            events = workflows[name].split("\non:\n", 1)[1].split("\n\n", 1)[0]
            push = events.split("  push:\n", 1)[1]
            self.assertRegex(
                push, r"(?m)^    tags: \['v\[0-9\]\*'\]$", f"{name}: 缺少版本 tag 触发"
            )
        cli_events = (
            workflows["publish-yuxi-cli"].split("\non:\n", 1)[1].split("\n\n", 1)[0]
        )
        self.assertEqual(
            re.findall(r"^  (\w+):", cli_events, re.MULTILINE),
            ["workflow_dispatch"],
            "CLI 必须独立手动发布",
        )

    def test_repository_release_events(self) -> None:
        """当前配置覆盖候选与正式 tag，CLI 仅手动触发。"""
        self.assert_release_events(
            {path.stem: path.read_text() for path in WORKFLOWS.glob("*.yml")}
        )

    def test_missing_tag_trigger_is_rejected(self) -> None:
        """恢复仅监听分支的缺陷时对应门禁必须失败。"""
        workflows = {path.stem: path.read_text() for path in WORKFLOWS.glob("*.yml")}
        for name in (
            "trust",
            "test",
            "web",
            "ruff",
            "system-tests",
            "dependency-audit",
            "deploy",
        ):
            with (
                self.subTest(workflow=name),
                self.assertRaisesRegex(AssertionError, f"{name}: 缺少版本 tag 触发"),
            ):
                self.assert_release_events(
                    workflows
                    | {name: workflows[name].replace("    tags: ['v[0-9]*']\n", "")}
                )

    def test_application_release_cannot_publish_cli(self) -> None:
        """恢复应用 Release 触发时禁止重复上传独立 CLI 包。"""
        workflows = {path.stem: path.read_text() for path in WORKFLOWS.glob("*.yml")}
        workflows["publish-yuxi-cli"] = workflows["publish-yuxi-cli"].replace(
            "on:\n", "on:\n  release:\n    types: [published]\n", 1
        )
        with self.assertRaisesRegex(AssertionError, "CLI 必须独立手动发布"):
            self.assert_release_events(workflows)


if __name__ == "__main__":
    unittest.main()

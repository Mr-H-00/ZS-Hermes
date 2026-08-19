"""依赖更新与审计降噪策略的负向测试。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from textwrap import dedent


ROOT = Path(__file__).resolve().parents[1]


def verify_policy(root: Path) -> list[str]:
    """验证依赖更新分组和审计触发边界。"""
    errors: list[str] = []
    dependabot = (root / ".github/dependabot.yml").read_text(encoding="utf-8")
    workflow = (root / ".github/workflows/dependency-audit.yml").read_text(
        encoding="utf-8"
    )

    application_policies = (
        ("uv", "/backend", "backend-dependencies", 2),
        ("uv", "/packages/yuxi-cli", "cli-dependencies", 1),
        ("npm", "/web", "web-dependencies", 2),
        ("npm", "/docs", "docs-dependencies", 1),
    )
    for ecosystem, directory, group, limit in application_policies:
        section = _dependabot_section(dependabot, ecosystem, directory)
        required = (
            f"open-pull-requests-limit: {limit}",
            "default-days: 7",
            f"{group}:",
            "applies-to: version-updates",
            'patterns:\n          - "*"',
            "update-types:\n          - patch",
        )
        for fragment in required:
            if fragment not in section:
                errors.append(f"{directory} 的 Dependabot 策略缺少：{fragment}")
        if "version-update:semver-major" in section or "ignore:" in section:
            errors.append(f"{directory} 不能用 ignore 屏蔽跨 major 安全更新")
        group_section = section.split(f"{group}:\n", 1)[-1].split("    allow:\n", 1)[0]
        if "applies-to: security-updates" in group_section:
            errors.append(f"{directory} 的应用聚合组只能作用于常规版本更新")
        if "          - minor\n" in group_section:
            errors.append(f"{directory} 的 minor 更新不能进入聚合组")
        allow_section = section.split("    allow:\n", 1)[-1]
        expected_allow = (
            '      - dependency-name: "*"\n'
            "        update-types:\n"
            "          - version-update:semver-patch\n"
            "          - version-update:semver-minor\n"
        )
        if expected_allow not in allow_section:
            errors.append(f"{directory} 必须通过同一通配 allow 接收 patch/minor")

    for ecosystem in ("docker", "docker-compose"):
        section = _dependabot_section(dependabot, ecosystem)
        if "open-pull-requests-limit: 0" not in section:
            errors.append(f"{ecosystem} 必须关闭常规版本 PR")

    actions = _dependabot_section(dependabot, "github-actions")
    for fragment in (
        "open-pull-requests-limit: 1",
        "default-days: 7",
        "github-actions:",
        'patterns:\n          - "*"',
    ):
        if fragment not in actions:
            errors.append(f"GitHub Actions 更新策略缺少：{fragment}")

    required_paths = (
        '".github/workflows/dependency-audit.yml"',
        '"Makefile"',
        '"backend/pyproject.toml"',
        '"backend/package/pyproject.toml"',
        '"backend/uv.lock"',
        '"packages/yuxi-cli/pyproject.toml"',
        '"packages/yuxi-cli/uv.lock"',
        '"web/package.json"',
        '"web/pnpm-lock.yaml"',
        '"docs/package.json"',
        '"docs/pnpm-lock.yaml"',
        '"scripts/dependency-audit-fixtures/**"',
    )
    pull_request_paths = workflow.split("  pull_request:\n", 1)[-1].split(
        "  push:\n", 1
    )[0]
    push_paths = workflow.split("  push:\n", 1)[-1].split(
        "  workflow_dispatch:\n", 1
    )[0]
    for path in required_paths:
        if pull_request_paths.count(path) != 1:
            errors.append(f"dependency audit 的 PR paths 缺少：{path}")
        if push_paths.count(path) != 1:
            errors.append(f"dependency audit 的 push paths 缺少：{path}")

    expected_concurrency = dedent(
        """\
        concurrency:
          group: dependency-audit-${{ github.workflow }}-${{ github.event.pull_request.number || github.ref }}
          cancel-in-progress: true
        """
    )
    if expected_concurrency not in workflow:
        errors.append("dependency audit 必须取消同一分支的过期运行")

    return errors


def _dependabot_section(content: str, ecosystem: str, directory: str | None = None) -> str:
    marker = f"  - package-ecosystem: {ecosystem}\n"
    for section in content.split(marker)[1:]:
        section = marker + section.split("\n  - package-ecosystem:", 1)[0]
        if directory is None or f"    directory: {directory}\n" in section:
            return section
    return ""


class DependencyUpdatePolicyTest(unittest.TestCase):
    """证明策略缺失时 gate 会在正确原因上失败。"""

    def test_repository_policy_is_valid(self) -> None:
        self.assertEqual(verify_policy(ROOT), [])

    def test_docker_version_pr_limit_is_required(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            self._copy_policy_files(root)
            path = root / ".github/dependabot.yml"
            content = path.read_text(encoding="utf-8")
            path.write_text(
                content.replace("open-pull-requests-limit: 0", "open-pull-requests-limit: 5", 1),
                encoding="utf-8",
            )

            self.assertIn("docker 必须关闭常规版本 PR", verify_policy(root))

    def test_dependency_audit_paths_are_required(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            self._copy_policy_files(root)
            path = root / ".github/workflows/dependency-audit.yml"
            content = path.read_text(encoding="utf-8")
            path.write_text(
                content.replace('      - "web/pnpm-lock.yaml"\n', "", 1),
                encoding="utf-8",
            )

            self.assertIn(
                'dependency audit 的 PR paths 缺少："web/pnpm-lock.yaml"',
                verify_policy(root),
            )

    def test_security_updates_must_not_be_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            self._copy_policy_files(root)
            path = root / ".github/dependabot.yml"
            content = path.read_text(encoding="utf-8")
            path.write_text(
                content.replace("    allow:\n", "    ignore:\n", 1),
                encoding="utf-8",
            )

            self.assertIn(
                "/backend 不能用 ignore 屏蔽跨 major 安全更新",
                verify_policy(root),
            )

    def test_minor_updates_must_not_be_grouped(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            self._copy_policy_files(root)
            path = root / ".github/dependabot.yml"
            content = path.read_text(encoding="utf-8")
            path.write_text(
                content.replace(
                    "        update-types:\n          - patch\n",
                    "        update-types:\n          - patch\n          - minor\n",
                    1,
                ),
                encoding="utf-8",
            )

            self.assertIn(
                "/backend 的 minor 更新不能进入聚合组",
                verify_policy(root),
            )

    def test_application_groups_must_not_group_security_updates(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            self._copy_policy_files(root)
            path = root / ".github/dependabot.yml"
            content = path.read_text(encoding="utf-8")
            path.write_text(
                content.replace(
                    "        applies-to: version-updates\n",
                    "        applies-to: security-updates\n",
                    1,
                ),
                encoding="utf-8",
            )

            self.assertIn(
                "/backend 的应用聚合组只能作用于常规版本更新",
                verify_policy(root),
            )

    def test_minor_allow_must_apply_to_all_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            self._copy_policy_files(root)
            path = root / ".github/dependabot.yml"
            content = path.read_text(encoding="utf-8")
            path.write_text(
                content.replace(
                    "          - version-update:semver-minor\n",
                    "",
                    1,
                ),
                encoding="utf-8",
            )

            self.assertIn(
                "/backend 必须通过同一通配 allow 接收 patch/minor",
                verify_policy(root),
            )

    @staticmethod
    def _copy_policy_files(root: Path) -> None:
        for relative in (
            ".github/dependabot.yml",
            ".github/workflows/dependency-audit.yml",
        ):
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text((ROOT / relative).read_text(encoding="utf-8"), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()

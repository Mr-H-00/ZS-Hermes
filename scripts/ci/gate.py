"""选择 PR 检查并验证 GitHub job 的实际结果。"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

CHECKS = (
    "trust",
    "backend",
    "ruff",
    "frontend",
    "service",
    "system",
    "docs",
    "dependencies",
)


def select_checks(paths: list[str]) -> dict[str, bool]:
    """按改动域选择检查；共享入口与未知路径执行全部检查。"""
    selected = dict.fromkeys(CHECKS, False)
    selected["trust"] = True
    if not paths:
        return dict.fromkeys(CHECKS, True)

    for path in paths:
        if path.startswith("backend/"):
            for check in ("backend", "ruff", "service"):
                selected[check] = True
            if not path.startswith("backend/test/unit/"):
                selected["system"] = True
        elif path.startswith("frontend/"):
            selected["frontend"] = True
        elif path.startswith("docs/") or (
            "/" not in path
            and path.endswith(".md")
            and path not in {"AGENTS.md", "ARCHITECTURE.md"}
        ):
            selected["docs"] = True
        else:
            return dict.fromkeys(CHECKS, True)

        if Path(path).name in {
            "pyproject.toml",
            "uv.lock",
            "package.json",
            "package-lock.json",
            "pnpm-lock.yaml",
            "pnpm-workspace.yaml",
        }:
            selected["dependencies"] = True
    return selected


def check_results(needs: dict, *, require_selection: bool = True) -> list[str]:
    """只接受必需 job 成功，明确未选择的检查才允许跳过。"""
    if not isinstance(needs, dict) or not needs:
        return ["缺少 job 结果"]
    if require_selection:
        changes = needs.get("changes")
        if not isinstance(changes, dict) or changes.get("result") != "success":
            return ["检查范围选择未成功"]
        try:
            selected = json.loads(changes["outputs"]["selected"])
        except (KeyError, TypeError, ValueError):
            return ["检查范围输出无效"]
        if (
            not isinstance(selected, dict)
            or set(selected) != set(CHECKS)
            or any(type(value) is not bool for value in selected.values())
            or selected["trust"] is not True
        ):
            return ["检查范围必须明确声明全部检查，且包含工程契约"]
        if set(needs) != set(CHECKS) | {"changes"}:
            return ["汇总依赖与检查范围不一致"]
    else:
        # 被调用工作流只汇总自身必需 job，不进行第二次路径选择。
        selected = dict.fromkeys(needs, True)

    errors = []
    for name, required in selected.items():
        result = (
            needs.get(name, {}).get("result")
            if isinstance(needs.get(name), dict)
            else None
        )
        allowed = {"success"} if required else {"success", "skipped"}
        if result not in allowed:
            errors.append(
                f"{name}: {result or 'missing'}（{'必需' if required else '未选择'}）"
            )
    return errors


def main() -> int:
    """从 Git 差异产生选择结果，或校验 Actions 的 needs 数据。"""
    if sys.argv[1:] == ["select"]:
        base, head = os.environ["BASE_SHA"], os.environ["HEAD_SHA"]
        if not all(re.fullmatch(r"[0-9a-fA-F]{40}", sha) for sha in (base, head)):
            raise ValueError("PR base/head 必须是完整 commit SHA")
        changed = subprocess.check_output(
            [
                "git",
                "diff",
                "--name-only",
                "--no-renames",
                "-z",
                f"{base}...{head}",
                "--",
            ]
        )
        paths = [
            path.decode("utf-8", errors="surrogateescape")
            for path in changed.split(b"\0")
            if path
        ]
        selected = select_checks(paths)
        with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
            output.write("selected=" + json.dumps(selected) + "\n")
        print(
            "Selected checks: "
            + ", ".join(name for name, enabled in selected.items() if enabled)
        )
        return 0

    if sys.argv[1:] in (["check"], ["check-jobs"]):
        errors = check_results(
            json.loads(os.environ["NEEDS_JSON"]),
            require_selection=sys.argv[1] == "check",
        )
        if errors:
            print("\n".join(errors), file=sys.stderr)
            return 1
        print("All required checks succeeded.")
        return 0

    print("Usage: gate.py select|check|check-jobs", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())

"""Preview Owner 的静态依赖方向。"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parents[3] / "zs"


@pytest.mark.parametrize(
    ("relative_path", "forbidden"),
    [
        (
            "shared/files.py",
            ("zs.infrastructure", "zs.modules", "zs.storage", "fastapi", "starlette", "subprocess", "tempfile"),
        ),
        (
            "infrastructure/file_preview.py",
            (
                "zs.modules",
                "zs.storage",
                "fastapi",
                "starlette",
                "subprocess",
                "tempfile",
                "zs.infrastructure.office_conversion",
            ),
        ),
        (
            "infrastructure/office_conversion.py",
            (
                "zs.modules",
                "zs.storage",
                "fastapi",
                "starlette",
                "zs.infrastructure.file_preview",
                "zs.infrastructure.minio",
            ),
        ),
    ],
)
def test_file_boundaries_reject_forbidden_imports(relative_path, forbidden) -> None:
    imports = _imports(relative_path)

    _assert_no_forbidden_imports(imports, forbidden)
    for module in forbidden:
        with pytest.raises(AssertionError, match=module):
            _assert_no_forbidden_imports(imports | {module}, forbidden)


def test_knowledge_and_artifact_do_not_import_workspace_preview() -> None:
    for relative_path in (
        "modules/knowledge/base.py",
        "modules/knowledge/preview.py",
        "modules/agents/services/artifacts.py",
    ):
        assert "zs.modules.workspace.preview" not in _imports(relative_path)


def _imports(relative_path: str) -> set[str]:
    """读取模块的静态导入依赖。"""
    tree = ast.parse((PACKAGE_ROOT / relative_path).read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _assert_no_forbidden_imports(imports: set[str], forbidden: tuple[str, ...]) -> None:
    """拒绝当前职责边界禁止的依赖。"""
    for module in imports:
        assert not module.startswith(forbidden), f"forbidden import: {module}"

"""验证测试入口的报告参数和失败传播。"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


class TestCIReporting(unittest.TestCase):
    """验证报告启用前后的入口契约。"""

    def setUp(self):
        """准备隔离的命令替身和调用记录。"""
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        fake = self.path / "fake"
        fake.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, sys\n"
            'with open(os.environ["CALL_LOG"], "a") as f: f.write(json.dumps(sys.argv[1:]) + "\\n")\n'
            'if "pytest" in sys.argv: sys.exit(int(os.environ.get("FAKE_EXIT", "0")))\n'
        )
        fake.chmod(0o755)
        (self.path / "docker").symlink_to(fake)
        (self.path / "uv").symlink_to(fake)
        self.env = os.environ | {
            "PATH": f"{self.path}:{os.environ['PATH']}",
            "CALL_LOG": str(self.path / "calls"),
            "POSTGRES_URL": "isolated",
            "REDIS_URL": "isolated",
            "YUXI_SKILL_DATA_DIR": str(self.path),
        }
        self.env.pop("CI_TEST_REPORT_DIR", None)

    def run_entry(self, script, *args, report=False, exit_code=0):
        """运行入口并读取真实传给命令的参数。"""
        log = self.path / "calls"
        log.unlink(missing_ok=True)
        env = self.env | {"FAKE_EXIT": str(exit_code)}
        if report:
            env["CI_TEST_REPORT_DIR"] = "test/ci-reports"
        result = subprocess.run(
            ["bash", str(ROOT / script), *args],
            cwd=ROOT,
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )
        calls = (
            [json.loads(line) for line in log.read_text().splitlines()]
            if log.exists()
            else []
        )
        return result, [call for call in calls if "pytest" in call]

    def test_selectors_and_exit_status(self):
        """报告参数不改变选择器或掩盖失败。"""
        for script, args, names in [
            (
                "backend/test/run_tests.sh",
                ("all",),
                ["unit", "integration-api", "integration-other", "e2e"],
            ),
            ("scripts/ci/run_service_integration.sh", (), ["service-integration"]),
        ]:
            with self.subTest(script=script):
                plain, original = self.run_entry(script, *args)
                reported, calls = self.run_entry(script, *args, report=True)
                self.assertEqual(
                    (plain.returncode, reported.returncode), (0, 0), reported.stderr
                )
                self.assertEqual(len(calls), len(names))
                if script.startswith("backend/"):
                    self.assertEqual(original[0][-3:], ["test/unit", "-m", "not slow"])
                    self.assertEqual(original[1][-1], "test/integration/api")
                    self.assertEqual(
                        original[2][-3:],
                        [
                            "test/integration",
                            "--ignore=test/integration/api",
                            "--ignore=test/integration/services/test_background_job_worker_path.py",
                        ],
                    )
                    self.assertEqual(original[3][-2:], ["-m", "e2e"])
                for base, call, name in zip(original, calls, names):
                    self.assertEqual(call[:-3], base)
                    self.assertEqual(
                        call[-3:],
                        [
                            f"--junitxml=test/ci-reports/{name}.xml",
                            "--durations=20",
                            "--durations-min=1.0",
                        ],
                    )
                failed, calls = self.run_entry(script, *args, report=True, exit_code=7)
                self.assertEqual(failed.returncode, 7)
                self.assertEqual(len(calls), 1)

    def test_pytest_xml_distinguishes_skipped_and_failure(self):
        """真实 pytest 报告区分通过、失败和跳过。"""
        sample = self.path / "test_sample.py"
        sample.write_text(
            "import pytest\ndef test_pass(): pass\ndef test_fail(): assert False\n"
            '@pytest.mark.skip(reason="optional")\ndef test_skip(): pass\n'
        )
        report = self.path / "nested/result.xml"
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                str(sample),
                f"--junitxml={report}",
                "--durations=20",
                "--durations-min=1.0",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        suite = ET.parse(report).getroot().find("testsuite")
        self.assertEqual(
            (suite.get("tests"), suite.get("failures"), suite.get("skipped")),
            ("3", "1", "1"),
        )
        cases = {case.get("name"): case for case in suite.findall("testcase")}
        self.assertIsNotNone(cases["test_fail"].find("failure"))
        self.assertIsNotNone(cases["test_skip"].find("skipped"))
        self.assertIsNone(cases["test_pass"].find("failure"))
        self.assertIsNone(cases["test_pass"].find("skipped"))

    def test_container_reports_are_readable_by_host_uploader(self):
        """真实私有报告先拒绝宿主读取，再由 workflow 归属步骤交给上传者。"""
        workflow = yaml.safe_load(
            (ROOT / ".github/workflows/system-tests.yml").read_text()
        )
        checked = 0
        for name, job in workflow["jobs"].items():
            steps = job.get("steps", [])
            uploads = [
                step
                for step in steps
                if step.get("uses", "").startswith("actions/upload-artifact@")
            ]
            if not uploads:
                continue
            with self.subTest(job=name):
                directory = self.path / name
                report = directory / "backend/test/ci-reports/result.xml"
                report.parent.mkdir(parents=True)
                subprocess.run(
                    [
                        "sudo",
                        "-n",
                        "sh",
                        str(ROOT / "docker/api-entrypoint.sh"),
                        "sh",
                        "-c",
                        "printf '%s\\n' '<testsuite tests=\"1\"/>' > backend/test/ci-reports/result.xml",
                    ],
                    cwd=directory,
                    check=True,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(report.stat().st_mode & 0o777, 0o600)
                with self.assertRaises(PermissionError):
                    report.read_bytes()
                normalize = next(
                    (
                        step
                        for step in steps
                        if step.get("name") == "Make container test reports readable"
                    ),
                    None,
                )
                self.assertIsNotNone(normalize, "容器私有报告缺少上传前归属步骤")
                self.assertLess(steps.index(normalize), steps.index(uploads[0]))
                self.assertEqual(normalize.get("if"), "always()")
                subprocess.run(
                    ["bash", "-ec", normalize["run"]],
                    cwd=directory,
                    check=True,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(ET.parse(report).getroot().get("tests"), "1")
                self.assertEqual(report.stat().st_uid, os.getuid())
                self.assertEqual(report.stat().st_mode & 0o777, 0o600)
                checked += 1
        self.assertGreater(checked, 0)


if __name__ == "__main__":
    unittest.main()

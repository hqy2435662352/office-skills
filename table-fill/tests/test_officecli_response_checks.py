"""Ticket B — officecli get 响应 fail-fast 校验 (OFFICECLI_GET_FAILED / OFFICECLI_RESPONSE_INVALID).

走查修复 (2026-08-27): `flatten_table.officecli_get()` 曾做裸
`json.loads(result.stdout)`, 无 returncode / schema 检查。错 sheet 或 `--sheets`
分隔符语法错误时, officecli 返回 `success:false` (缺 `data` 键), 下游
`discover_dimensions` 里 `data["data"]["results"]` 直接 `KeyError: 'data'`,
把真因 (分隔符语法) 掩盖成一个无关的 Python 异常。修复后
`_checked_officecli_json` 三层 fail-fast: rc!=0 → OFFICECLI_GET_FAILED;
stdout 非 JSON / 缺 `data` 键 → OFFICECLI_RESPONSE_INVALID, 一律走套件
`fail()` 契约 (stderr 结构化 defect + exit 3)。

测试风格与 test_filename_evidence.py 一致: 直接调用函数 (repo 惯例), 真实
fixture + monkeypatch 覆盖三条失败分支与成功路径。
"""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

import flatten_table  # noqa: E402

FIXTURE = (
    SKILL_ROOT / "tests" / "_fixtures" / "task_orchestration"
    / "e2e" / "sources" / "parameter_book.xlsx"
)


def _officecli_available() -> bool:
    """officecli 不可用则跳过真实集成测试 (镜像 repo 惯例)。"""
    try:
        r = subprocess.run(
            ["officecli", "--version"], capture_output=True, text=True, timeout=30,
        )
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _determine_real_sheet() -> str:
    """拿 fixture 的真实 sheet 名做 smoke; 不可用时返回 None 由调用方 skip。"""
    from _officecli import officecli
    r = officecli("view", str(FIXTURE), "outline", "--json", timeout=30)
    if r.returncode != 0:
        return None
    try:
        data = json.loads(r.stdout)
        names = [s.get("name") for s in data.get("data", {}).get("sheets", [])]
        return names[0] if names else None
    except (TypeError, ValueError):
        return None


def _completed(returncode=0, stdout="", stderr=""):
    cp = subprocess.CompletedProcess(args=["officecli"], returncode=returncode,
                                     stdout=stdout, stderr=stderr)
    return lambda *a, **k: cp


class OfficecliResponseCheckTests(unittest.TestCase):
    # ── 1. 真实 officecli 集成 — 根因类别 (错 sheet 不再裸 KeyError) ─────
    def test_real_missing_sheet_fails_fast_not_keyerror(self):
        import io
        from unittest import mock

        if not _officecli_available():
            self.skipTest("officecli 不可用")
        buf = io.StringIO()
        with mock.patch.object(sys, "stderr", buf):
            with self.assertRaises(SystemExit) as ctx:
                flatten_table.officecli_get(str(FIXTURE), "不存在的sheetXYZ", "A1")
        self.assertEqual(ctx.exception.code, 3)
        # fail() 写结构化 JSON 到 stderr; 必须是 defect 而不是裸 KeyError
        defect = json.loads(buf.getvalue())
        self.assertIn(defect["code"],
                      {"OFFICECLI_GET_FAILED", "OFFICECLI_RESPONSE_INVALID"})
        self.assertIn("不存在的sheetXYZ", defect["message"])
        self.assertNotIn("KeyError", defect["message"])
        self.assertIn("corrective_action", defect)

    # ── 2. rc != 0 → OFFICECLI_GET_FAILED ────────────────────────────────
    def test_nonzero_returncode_emits_get_failed(self):
        import io
        from unittest import mock

        buf = io.StringIO()
        with mock.patch.object(flatten_table, "officecli",
                               side_effect=_completed(1, "", "boom")), \
             mock.patch.object(sys, "stderr", buf):
            with self.assertRaises(SystemExit) as ctx:
                flatten_table.officecli_get(str(FIXTURE), "S", "A1")
        self.assertEqual(ctx.exception.code, 3)
        defect = json.loads(buf.getvalue())
        self.assertEqual(defect["code"], "OFFICECLI_GET_FAILED")
        self.assertIn("boom", defect["message"])

    # ── 3. 非 JSON stdout → OFFICECLI_RESPONSE_INVALID ───────────────────
    def test_bad_json_emits_response_invalid(self):
        import io
        from unittest import mock

        buf = io.StringIO()
        with mock.patch.object(flatten_table, "officecli",
                               side_effect=_completed(0, "not-json")), \
             mock.patch.object(sys, "stderr", buf):
            with self.assertRaises(SystemExit) as ctx:
                flatten_table.officecli_get(str(FIXTURE), "S", "A1")
        self.assertEqual(ctx.exception.code, 3)
        defect = json.loads(buf.getvalue())
        self.assertEqual(defect["code"], "OFFICECLI_RESPONSE_INVALID")

    # ── 4. JSON 但缺 data 键 (错 sheet schema) → OFFICECLI_RESPONSE_INVALID ─
    def test_missing_data_key_emits_response_invalid_with_root_cause(self):
        import io
        from unittest import mock

        buf = io.StringIO()
        body = json.dumps({"success": False, "error": "sheet not found"})
        with mock.patch.object(flatten_table, "officecli",
                               side_effect=_completed(0, body)), \
             mock.patch.object(sys, "stderr", buf):
            with self.assertRaises(SystemExit) as ctx:
                flatten_table.officecli_get(str(FIXTURE), "不存在的sheetXYZ", "A1")
        self.assertEqual(ctx.exception.code, 3)
        defect = json.loads(buf.getvalue())
        self.assertEqual(defect["code"], "OFFICECLI_RESPONSE_INVALID")
        self.assertIn("不存在的sheetXYZ", defect["message"])
        self.assertIn("sheet 名", defect["corrective_action"])
        self.assertIn("分隔符", defect["corrective_action"])
        self.assertNotIn("KeyError", defect["message"])

    # ── 5. 成功路径: 返回 dict, 不 exit ──────────────────────────────────
    def test_success_returns_dict_no_exit(self):
        from unittest import mock

        body = json.dumps({"success": True,
                           "data": {"matches": 1, "results": [{"children": []}]}})
        with mock.patch.object(flatten_table, "officecli",
                               side_effect=_completed(0, body)):
            result = flatten_table.officecli_get(str(FIXTURE), "S", "A1")
        self.assertIsInstance(result, dict)
        self.assertIn("data", result)

    def test_silent_helper_returns_empty_dict_on_failure(self):
        result = flatten_table._checked_officecli_json(
            subprocess.CompletedProcess(args=["officecli"], returncode=1,
                                        stdout="", stderr="x"),
            context="ctx", fail_on_error=False)
        self.assertEqual(result, {})

    # ── 集成 smoke: discover_dimensions 真实 sheet 不 raise ──────────────
    def test_discover_dimensions_real_sheet_no_keyerror(self):
        if not _officecli_available():
            self.skipTest("officecli 不可用")
        sheet = _determine_real_sheet()
        if sheet is None:
            self.skipTest("无法解析 fixture 真实 sheet 名")
        cells, num_cols, num_rows, pivot_cols = flatten_table.discover_dimensions(
            str(FIXTURE), sheet)
        self.assertGreater(len(cells), 0)
        self.assertGreaterEqual(num_rows, 1)


if __name__ == "__main__":
    unittest.main()

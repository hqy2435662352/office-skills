"""tests/_fixtures/run_driver.py — canonical table-fill pipeline driver for tests.

Q8 migration (ADR 0018/0020): e2e tests previously drove preparation via the
retired `prepare_run.py --outline/--flatten --target` two-stage CLI. The
canonical path is now:

    workspace_init --init (role-neutral, selective sheet scope, no --target)
        → workspace_manifest.json
    materialize_run --sources a,b --target t   (or --task task.yaml for multi-run)
        → prepare_manifest.json (run-local compiler view, same schema)

Tests swap ONLY the fixture driver — domain assertions stay untouched.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


def run_py(workdir: Path, script: str, *args) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-X", "utf8", str(SCRIPTS / script), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(workdir))


def workspace_init(workdir: Path, files: str, sheets: str, task: str = "") -> dict:
    """Job-level role-neutral init (S0). Returns the stdout JSON (PASS record)."""
    proc = run_py(workdir, "workspace_init.py", "--workdir", ".",
                  "--init", "--files", files, "--sheets", sheets,
                  "--task", task)
    if proc.returncode != 0:
        raise AssertionError(
            f"workspace_init failed (exit {proc.returncode}): "
            f"{(proc.stderr or proc.stdout)[-1200:]}")
    return json.loads(proc.stdout)


def materialize(workdir: Path, sources: str, target: str,
                run_id: str = "run") -> dict:
    """Single-run materialization (S1 lowering). Returns the stdout JSON."""
    proc = run_py(workdir, "materialize_run.py", "--workdir", ".",
                  "--sources", sources, "--target", target,
                  "--run-id", run_id)
    if proc.returncode != 0:
        raise AssertionError(
            f"materialize_run failed (exit {proc.returncode}): "
            f"{(proc.stderr or proc.stdout)[-1200:]}")
    return json.loads(proc.stdout)


def prepare_single(workdir: Path, files: str, sheets: str,
                   sources: str, target: str, task: str = "") -> dict:
    """Canonical single-run prepare: init + materialize.

    Returns the run-local prepare_manifest.json (same schema/keys tests
    already assert against: fingerprints.source_structure / .target_structure
    / flattened / target / row_gaps / style_granularity).
    """
    workspace_init(workdir, files, sheets, task=task)
    materialize(workdir, sources, target)
    manifest = json.loads(
        (workdir / "prepare_manifest.json").read_text(encoding="utf-8"))
    return manifest


def review_and_confirm(workdir: Path, spec: str = "fill_spec.yaml") -> dict:
    """S6: Spec Review on the compile-clean FillSpec + confirm (hash binding).

    Required before execute in the new control plane (ADR 0019 — the Execute
    gate rejects missing/stale review_confirm). Returns review_confirm.json.
    """
    proc = run_py(workdir, "spec_review.py", "--workdir", ".",
                  "--spec", spec)
    if proc.returncode != 0:
        raise AssertionError(
            f"spec_review (present) failed (exit {proc.returncode}): "
            f"{(proc.stderr or proc.stdout)[-1200:]}")
    proc = run_py(workdir, "spec_review.py", "--workdir", ".",
                  "--spec", spec, "--confirm")
    if proc.returncode != 0:
        raise AssertionError(
            f"spec_review --confirm failed (exit {proc.returncode}): "
            f"{(proc.stderr or proc.stdout)[-1200:]}")
    return json.loads((workdir / "review_confirm.json").read_text(encoding="utf-8"))


def target_entry_name(file_stem: str, sheet: str) -> str:
    """Entry naming convention — 与 workspace_init 展平条目命名一致:
    <Path(staged).stem>_<ascii_slug(sheet)> (传入可带扩展名的文件名)."""
    import re
    from pathlib import Path
    stem = Path(file_stem).stem
    slug = re.sub(r"[^A-Za-z0-9_-]", "", sheet) or "sheet"
    return f"{stem}_{slug}"
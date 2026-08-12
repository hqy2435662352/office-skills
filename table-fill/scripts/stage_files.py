#!/usr/bin/env python3
"""
scripts/stage_files.py — Layer 0: stage input files into an ASCII workdir.

Standard action (NOT conditional): every table-fill run copies all input files
into an ASCII-only workdir with English names BEFORE Layer 1. This eliminates
the three failure classes observed on Windows with Chinese paths:
  1. NON_ASCII_PATH — officecli batch/set fails with Access denied on Chinese
     paths ('get' may work, 'set'/'batch' will not).
  2. Read-only source files — copy2 preserves the read-only attribute, so the
     copy is unwritable by officecli. This script forces write permission on
     every staged copy (and repairs stale read-only copies).
  3. Fragile names — spaces/parens/special chars in Chinese filenames break
     shell quoting; English names avoid the class entirely.

Idempotent: if the staged copy already exists with identical size+mtime, the
copy is skipped (safe for repeated runs of the same task).

Exit codes:
  0 — Pass, all files staged (copied or already present)
  1 — Fatal (file missing, copy failed)

Usage:
  python scripts/stage_files.py --workdir <ascii_dir> --files "src1|name1,src2|name2"

  --files entries: "<absolute source path>|<english filename>" per entry,
  comma-separated. The filename must be ASCII and unique within the workdir.
"""

import os, sys, json, argparse, shutil, stat
from pathlib import Path

FATAL_PREFIX = "[STAGE_FILES] "


def _is_ascii(s: str) -> bool:
    try:
        s.encode("ascii")
        return True
    except UnicodeEncodeError:
        return False


def _force_writable(path: Path) -> None:
    """Remove read-only attribute (copy2 preserves it from read-only sources)."""
    try:
        current = stat.S_IMODE(os.stat(path).st_mode)
        os.chmod(path, current | stat.S_IWRITE)
    except OSError:
        pass  # best-effort; copy failures below will surface the real problem


def _same_file(src: Path, dst: Path) -> bool:
    """Cheap idempotence check: size + mtime. Not a content hash — good enough
    for 'same task re-run' skipping, and re-copy is always safe."""
    try:
        s1, s2 = os.stat(src), os.stat(dst)
        return s1.st_size == s2.st_size and s1.st_mtime == s2.st_mtime
    except OSError:
        return False


def stage_files(workdir: Path, entries: list[tuple[str, str]]) -> list[dict]:
    """Copy each (src, name) entry into workdir. Returns per-file records."""
    records = []
    for src_raw, name in entries:
        src = Path(src_raw)
        if not name or not _is_ascii(name):
            records.append({
                "src": src_raw, "dst": "", "status": "ERROR",
                "message": f"staged name must be ASCII and non-empty, got: {name!r}",
            })
            continue
        if not src.exists():
            records.append({
                "src": src_raw, "dst": "", "status": "ERROR",
                "message": "source file not found",
            })
            continue
        dst = workdir / name
        try:
            if dst.exists() and _same_file(src, dst):
                _force_writable(dst)  # repair stale read-only copy
                records.append({
                    "src": src_raw, "dst": str(dst), "status": "SKIPPED",
                    "message": "existing copy is current (size+mtime match)",
                })
                continue
            shutil.copy2(src, dst)
            _force_writable(dst)
            records.append({
                "src": src_raw, "dst": str(dst), "status": "COPIED",
                "message": "",
            })
        except OSError as e:
            records.append({
                "src": src_raw, "dst": str(dst), "status": "ERROR",
                "message": f"copy failed: {e}",
            })
    return records


def main():
    parser = argparse.ArgumentParser(description="Layer 0: stage input files into ASCII workdir")
    parser.add_argument("--workdir", type=Path, required=True, help="ASCII-only work directory")
    parser.add_argument("--files", required=True,
                        help='Comma-separated "src|name" entries, e.g. '
                             '"C:\\data\\毛利表.xlsx|source_maoli.xlsx,C:\\data\\报价.xlsx|target_baojia.xlsx"')
    args = parser.parse_args()

    workdir = args.workdir
    try:
        workdir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print(json.dumps({"status": "FATAL", "message": f"cannot create workdir: {e}"},
                         ensure_ascii=False), file=sys.stderr)
        sys.exit(1)

    entries = []
    for chunk in args.files.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "|" in chunk:
            src, _, name = chunk.partition("|")
            entries.append((src.strip(), name.strip()))
        else:
            entries.append((chunk, Path(chunk).name))  # fallback: keep basename

    records = stage_files(workdir, entries)
    errors = [r for r in records if r["status"] == "ERROR"]

    report = {
        "status": "FATAL" if errors else "PASS",
        "code": "STAGE_FAILED" if errors else "STAGE_PASS",
        "workdir": str(workdir),
        "file_count": len(records),
        "files": records,
        "corrective_action": "Fix the failing source paths / ASCII staged names, "
                             "then re-run prepare_run",
    }
    print(json.dumps(report, ensure_ascii=False, indent=2), file=sys.stderr)

    if errors:
        sys.exit(1)
    print(f"[STAGE_FILES] PASS — {len(records)} file(s) staged in {workdir}")
    sys.exit(0)


if __name__ == "__main__":
    main()

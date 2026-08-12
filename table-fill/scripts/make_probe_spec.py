#!/usr/bin/env python3
"""scripts/make_probe_spec.py — scaffold a probe-ready fill_spec.yaml.

One command from a prepared workdir: fingerprints and inputs are auto-filled
from prepare_manifest.json, so you can drop in the fragment you are unsure
about and run `compile_fill.py --probe` without hand-copying boilerplate.

The scaffold is a SKELETON, not a working spec: clone_roles/columns/rows are
example shapes from the base fixture — edit them to your target's real
structure (digest is the source of truth) before probing. The probe answers
are about what you fill in.

Usage:
  python scripts/make_probe_spec.py --workdir <dir> [--out <path>]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> None:
    for _st in (sys.stdout, sys.stderr):
        try:
            _st.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(
        description="Scaffold a probe-ready fill_spec.yaml from a prepared workdir")
    parser.add_argument("--workdir", type=Path, required=True,
                        help="prepared workdir with prepare_manifest.json")
    parser.add_argument("--out", type=Path, default=None,
                        help="output path (default <workdir>/fill_spec.yaml)")
    args = parser.parse_args()

    import _probe_fixtures as pf

    manifest_path = args.workdir / "prepare_manifest.json"
    if not manifest_path.is_file():
        print(json.dumps({
            "status": "ERROR", "code": "MANIFEST_NOT_FOUND",
            "message": f"prepare_manifest.json missing in {args.workdir}",
            "corrective_action": "Run prepare_run.py first (outline + flatten stages)",
        }, ensure_ascii=False, indent=2))
        sys.exit(3)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    spec = pf.base_probe_spec()
    spec["fingerprints"] = {
        "source_structure": manifest["fingerprints"]["source_structure"],
        "target_structure": manifest["fingerprints"]["target_structure"],
    }
    target = manifest["target"]
    spec["inputs"]["target"] = target["file"]
    spec["inputs"]["target_sheet"] = target["sheet"]
    spec["inputs"]["sources"] = [f["staged"] for f in manifest["files"]
                                 if f["staged"] != target["file"]]
    spec["inputs"]["source_sheets"] = [
        {"source": e["file"], "sheets": [e["sheet"]]}
        for e in manifest["flattened"] if e["name"] != target["name"]
    ]

    out = args.out or (args.workdir / "fill_spec.yaml")
    out.write_text(
        "# probe scaffold — SKELETON, edit clone_roles/columns/rows to your "
        "target's real structure (digest is the source of truth), then:\n"
        "#   python scripts/compile_fill.py --probe --spec fill_spec.yaml --workdir <dir>\n"
        + json.dumps(spec, ensure_ascii=False, indent=2),
        encoding="utf-8")

    print(json.dumps({
        "status": "SUCCESS", "code": "PROBE_SCAFFOLD_WRITTEN",
        "path": str(out),
        "note": "skeleton spec with fingerprints/inputs auto-filled — edit then --probe",
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

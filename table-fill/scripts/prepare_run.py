#!/usr/bin/env python3
"""
scripts/prepare_run.py — retired runtime CLI; pure-function host (ADR 0020).

The two-stage --outline/--flatten runtime CLI is RETIRED. Workspace Init
(workspace_init.py --init, role-neutral) and Run Materialization
(materialize_run.py) are the canonical paths; this module survives ONLY as a
host for pure functions other scripts reuse:

  - ascii_slug / _entry_for / merge_flattened          entry naming/shape
  - structure_facts / facts_sha256                     structure fingerprints
  - collect_style_granularity                          style decision facts
  - flatten_pptx_table                                 PPTX flatten helper
  - workdir_pollution_defect / POST_PREPARE_TRIGGERS   run-lifetime guard

No CLI, no stage orchestration, no lifecycle behavior may grow back here
(target stage: delete this module once the pure functions are relocated —
not in this round, ADR 0020: no helper migration now).
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import sys
from pathlib import Path

from _officecli import (  # noqa: E402
    ensure_utf8_stdio as _utf8_stdio, fail,
    sha256_file,
)

sys.path.insert(0, str(Path(__file__).resolve().parent))

MANIFEST_NAME = "prepare_manifest.json"

# Business Reasoning Barrier (ticket 02): flattened xlsx entries carry
# premod evidence only; full digest is deferred until MOD unlock.
DIGEST_DEFERRED = "deferred"

# ── Run Isolation / Task Artifact Boundary (ticket 08, spec D8) ──────────

POST_PREPARE_TRIGGERS = (
    "execution_plan.json",     # compile 输出
    "mapping.md",              # compile 输出（人读报告）
    "source_trace.json",       # compile 输出（lineage 聚合）
    "draft_receipt.json",      # execute 输出
    "final_receipt.json",      # deliver 输出
)


def workdir_pollution_defect(workdir, *, strict: bool = True) -> dict | None:
    """WORKDIR_POLLUTED 污染机械检查（纯函数 seam，materialize_run 复用）。

    workdir 已含本 run 的 post-prepare 生命周期产物 → 返回结构化 defect
    {code/message/corrective_action/at}；否则返回 None。

    strict=True（prepare 入口：outline 阶段）把 fill_spec.yaml 也算作触发器；
    strict=False（flatten 续跑 / materialize 幂等重物化）豁免 fill_spec.yaml
    （spec 撰写先于或后于 materialize 均合法, plan/receipt 永不进入）。
    """
    wd = Path(workdir)
    if not wd.is_dir():
        return None
    found = [name for name in POST_PREPARE_TRIGGERS if (wd / name).is_file()]
    if strict and (wd / "fill_spec.yaml").is_file():
        found.append("fill_spec.yaml")
    if any(wd.glob("validated_draft.*")):
        found.append("validated_draft.*")
    if not found:
        return None
    return {
        "code": "WORKDIR_POLLUTED",
        "message": "workdir 已含本 run 生命周期产物: "
                   + ", ".join(sorted(set(found)))
                   + " — 禁止在已推进的 run 上原位重跑 prepare/materialize",
        "corrective_action": "使用全新 run root，或在显式 run-id 目录"
                             "（如 runs/<id>）下开始新的 run；旧 run 产物"
                             "完整保留用于追溯",
        "at": str(wd),
    }


def structure_facts(meta: dict) -> dict:
    """Deterministic structure facts for fingerprinting (subset of meta)."""
    dims = meta.get("dimensions", {})
    return {
        "sheet": meta.get("sheet"),
        "dimensions": {k: dims.get(k) for k in ("rows", "cols", "data_rows")},
        "header_band": meta.get("header_band"),
        "merged_ranges": sorted(meta.get("merged_ranges") or []),
        "blocks": meta.get("blocks"),
        "columns": [
            {"col": c.get("col"), "nonempty": c.get("nonempty"),
             "numeric_ratio": c.get("numeric_ratio")}
            for c in meta.get("columns", [])
        ],
        "formulas": sorted((meta.get("formulas") or {}).items()),
        "column_numfmt": sorted((meta.get("column_numfmt") or {}).items()),
        "merge_anchors": meta.get("merge_anchors"),
    }


def facts_sha256(facts_list: list[dict]) -> str:
    payload = json.dumps(facts_list, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ── Workspace fact-space integrity (trust boundary) ─────────────────────
#
# workspace_manifest.json 是唯一 canonical 事实空间; 任何消费它的入口
# (materialize_run.load_workspace / workspace_init --verify) 必须先证明
# 「声明事实 == 物理文件」, 否则事实空间被篡改而投影照常通过。
# 不变量: 文件哈希一致 ⇒ facts 一致 (structure_facts 是 meta 内容的确定性
# 函数) — 因此校验只比较文件级 sha256, 不重算指纹 (materialize 的 run 级
# 指纹本来就是从物理 meta 重算的; 入口校验保证物理 == 声明)。

# canonical fact-space schema version: workspace_init 写入, 各消费入口
# 校验 — 单一来源, 避免 writer/validator 各自维护版本号。
WORKSPACE_SCHEMA_VERSION = 4

# 展平条目的 workdir-resident 产物键 (digest 例外: xlsx 为 'deferred'
# 标记而非文件, pptx 才是真实文件 — 见 verify_workspace_facts)。
_FLATTEN_PROD_KEYS = ("csv", "meta", "evidence", "candidates")


def _path_inside(workdir: Path, value: str) -> bool:
    """workdir 内相对路径判定 (路径逃逸防御: 绝对路径 / '..' 越界一律拒绝)。"""
    p = Path(value)
    if p.is_absolute():
        return False
    try:
        return (workdir / p).resolve().is_relative_to(workdir.resolve())
    except OSError:
        return False


def validate_workspace_manifest_shape(m, *, expected_version: int = WORKSPACE_SCHEMA_VERSION) -> list:
    """manifest 最小形状断言 (消费前 fail-closed; 返回 problem 字符串列表)。

    检查 schema_version 严格相等、inputs[].staged / flattened[].name|file|
    csv|meta 必需、条目名唯一、outlines/derived 形状。纯内存检查:
    不做 IO、不验哈希 (那是 verify_workspace_facts 的职责)。"""
    problems = []
    if not isinstance(m, dict):
        return ["manifest top-level must be an object"]
    ver = m.get("schema_version")
    if ver != expected_version:
        problems.append(
            f"schema_version={ver!r} != {expected_version} — 旧版本 fact space "
            "不可消费, 请重新 workspace_init --init")
    if not isinstance(m.get("inputs"), list):
        problems.append("inputs must be a list")
    else:
        seen_staged = set()
        for i, it in enumerate(m["inputs"]):
            if (not isinstance(it, dict)
                    or not isinstance(it.get("staged"), str)
                    or not it["staged"]):
                problems.append(f"inputs[{i}] must carry non-empty staged")
                continue
            if it["staged"] in seen_staged:
                problems.append(f"inputs[{i}]: duplicate staged {it['staged']!r}")
            seen_staged.add(it["staged"])
    if not isinstance(m.get("flattened"), list):
        problems.append("flattened must be a list")
    else:
        seen_names = set()
        for i, e in enumerate(m["flattened"]):
            if not isinstance(e, dict):
                problems.append(f"flattened[{i}] must be an object")
                continue
            for key in ("name", "file", "csv", "meta"):
                if not isinstance(e.get(key), str) or not e[key]:
                    problems.append(f"flattened[{i}] must carry non-empty {key}")
            nm = e.get("name")
            if nm and nm in seen_names:
                problems.append(f"flattened[{i}]: duplicate entry name {nm!r}")
            if nm:
                seen_names.add(nm)
    if not isinstance(m.get("outlines"), dict):
        problems.append("outlines must be an object")
    if "derived" in m and not isinstance(m.get("derived"), list):
        problems.append("derived must be a list")
    return problems


def verify_workspace_facts(workdir: Path, manifest: dict) -> list:
    """manifest 声明事实 vs 物理文件逐条核对 (trust boundary)。

    三类核对, 返回 drift 清单 [{kind, path, expected, actual, message}]:
      1. input      inputs[].staged 存在且 sha256 一致
      2. derived    derived[].path 存在且 sha256 一致 (覆盖 outline +
                   csv/meta/evidence/candidates 全部派生产物)
      3. reference  flattened 条目产物值 (csv/meta/evidence/candidates/
                   digest) 均为 workdir 内相对路径且 (digest 'deferred'
                   除外) 文件存在; entry.file ∈ inputs[].staged;
                   outlines 值在 workdir 内

    inputs[].source 是用户原始路径, 按设计在 workdir 外 — 不做 containment。
    sha256 记录为 None 的条目 (旧/异常 manifest) 跳过哈希比对, 只查引用。
    空清单 = 物理文件与 manifest 声明完全一致。
    """
    root = workdir.resolve()
    drift = []
    inputs = manifest.get("inputs") or []
    staged_names = {(it.get("staged") or "") for it in inputs if isinstance(it, dict)}

    for it in inputs:
        staged = it.get("staged") or ""
        if not staged:
            drift.append({"kind": "input", "path": str(it),
                          "expected": "", "actual": "",
                          "message": "input entry missing staged name"})
            continue
        p = workdir / staged
        if not _path_inside(workdir, staged):
            drift.append({"kind": "input", "path": staged, "expected": "",
                          "actual": "outside-workdir",
                          "message": f"staged 路径越界: {staged!r}"})
            continue
        expected = it.get("sha256")
        if expected is None:
            continue
        actual = sha256_file(p) if p.is_file() else "<missing>"
        if actual != expected:
            drift.append({"kind": "input", "path": staged,
                          "expected": str(expected)[:16], "actual": str(actual)[:16],
                          "message": f"staged 输入 {staged} 哈希漂移"
                                     f" (expected {str(expected)[:16]}…, "
                                     f"actual {str(actual)[:16]}…)"})

    for d in manifest.get("derived") or []:
        rel = d.get("path") or ""
        if not isinstance(rel, str) or not rel:
            drift.append({"kind": "derived", "path": str(d), "expected": "",
                          "actual": "", "message": "derived entry missing path"})
            continue
        p = workdir / rel
        if not _path_inside(workdir, rel):
            drift.append({"kind": "derived", "path": rel, "expected": "",
                          "actual": "outside-workdir",
                          "message": f"derived 路径越界: {rel!r}"})
            continue
        expected = d.get("sha256")
        if expected is None:
            continue
        actual = sha256_file(p) if p.is_file() else "<missing>"
        if actual != expected:
            drift.append({"kind": "derived", "path": rel,
                          "expected": str(expected)[:16], "actual": str(actual)[:16],
                          "message": f"派生产物 {rel} 哈希漂移"
                                     f" (expected {str(expected)[:16]}…, "
                                     f"actual {str(actual)[:16]}…)"})

    for e in manifest.get("flattened") or []:
        if not isinstance(e, dict):
            continue
        fname = e.get("file") or ""
        if fname and fname not in staged_names:
            drift.append({"kind": "reference", "path": fname, "expected": "",
                          "actual": "not-an-input",
                          "message": f"flattened entry {e.get('name')!r} 引用的 "
                                     f"staged 文件 {fname!r} 不在 inputs 中"})
        for key in _FLATTEN_PROD_KEYS:
            val = e.get(key)
            if not isinstance(val, str) or not val:
                continue
            if not _path_inside(workdir, val):
                drift.append({"kind": "reference", "path": val, "expected": "",
                              "actual": "outside-workdir",
                              "message": f"flattened {e.get('name')!r} 的 {key} "
                                         f"路径越界: {val!r}"})
                continue
            if not (workdir / val).is_file():
                drift.append({"kind": "reference", "path": val, "expected": "",
                              "actual": "<missing>",
                              "message": f"flattened {e.get('name')!r} 的 {key} "
                                         f"文件缺失: {val}"})
        dig = e.get("digest")
        if isinstance(dig, str) and dig and dig != DIGEST_DEFERRED:
            if not _path_inside(workdir, dig):
                drift.append({"kind": "reference", "path": dig, "expected": "",
                              "actual": "outside-workdir",
                              "message": f"flattened {e.get('name')!r} 的 digest "
                                         f"路径越界: {dig!r}"})
            elif not (workdir / dig).is_file():
                drift.append({"kind": "reference", "path": dig, "expected": "",
                              "actual": "<missing>",
                              "message": f"flattened {e.get('name')!r} 的 digest "
                                         f"文件缺失: {dig}"})

    for staged, outline_name in (manifest.get("outlines") or {}).items():
        if not isinstance(outline_name, str) or not outline_name:
            continue
        if not _path_inside(workdir, outline_name):
            drift.append({"kind": "reference", "path": outline_name,
                          "expected": "", "actual": "outside-workdir",
                          "message": f"outline 路径越界: {outline_name!r}"})
    return drift


def ascii_slug(text: str) -> str:
    """ASCII-safe slug for artifact naming: keep [A-Za-z0-9_-], drop the rest."""
    slug = re.sub(r"[^A-Za-z0-9_-]", "", text)
    return slug or "sheet"


def _entry_for(fname: str, s: str, n: str, *, xlsx: bool = True) -> dict:
    """展平条目形态 (workspace_init / materialize_run 复用).

    xlsx=True (主路径, Business Reasoning Barrier/ticket 02):
      - "evidence" → {n}_premod_evidence.md (Pre-MOD 视图);
      - "digest"   → DIGEST_DEFERRED ("deferred"), 完整 digest 延后到 MOD
        Resolution 解锁后补生成 —— 这是标记, **不是文件路径**。

    xlsx=False (pptx 路径): 真实 {n}_digest.md, 无 evidence 字段 (pptx
    flatten 写自带 minimal digest)。"""
    if xlsx:
        return {
            "file": fname,
            "sheet": s,
            "name": n,
            "csv": f"{n}_flat.csv",
            "meta": f"{n}_meta.json",
            "evidence": f"{n}_premod_evidence.md",
            "digest": DIGEST_DEFERRED,
            "candidates": f"{n}_candidates.yaml",
        }
    return {
        "file": fname,
        "sheet": s,
        "name": n,
        "csv": f"{n}_flat.csv",
        "meta": f"{n}_meta.json",
        "digest": f"{n}_digest.md",
        "candidates": f"{n}_candidates.yaml",
    }


def merge_flattened(existing: list, new_entries: list) -> list:
    """Incrementally merge flatten results by entry name (new overwrites old).

    Multiple flatten invocations must not clobber each other's manifest
    entries — earlier sheets stay discoverable by the compiler."""
    merged = {e["name"]: e for e in existing}
    for e in new_entries:
        merged[e["name"]] = e
    return list(merged.values())


def collect_style_granularity(metas_by_name: dict) -> dict:
    """从已加载的 flatten meta 收集样式粒度决策事实 (按条目名).

    决策事实, 不入指纹 — 指纹只覆盖 structure_facts 选取的结构键, 该事实
    的加入不影响旧 spec 重编译。"""
    return {name: m["style_granularity"]
            for name, m in metas_by_name.items()
            if m.get("style_granularity")}


def flatten_pptx_table(staged: Path, table_id: str, name: str, workdir: Path) -> None:
    """Flatten one PPTX table into CSV + meta (structure facts for the compiler).

    Reads the table once with officecli get --depth 2; merged cells are not
    propagated (PPTX fills are per-cell value writes)."""
    from _officecli import clean_residents, officecli
    clean_residents()
    proc = officecli("get", str(staged), f"/{table_id}", "--depth", "2", "--json",
                     timeout=300)
    if proc.returncode != 0:
        fail("PPTX_FLATTEN_FAILED",
             f"officecli get {table_id} failed: {proc.stderr[-400:]}",
             "Confirm the table id from the outline")
    try:
        data = json.loads(proc.stdout)
    except ValueError as e:
        fail("PPTX_FLATTEN_INVALID", f"bad officecli JSON: {e}",
             "Re-run the flatten stage")
    result = data.get("data", {}).get("results", [{}])[0]
    children = result.get("children", []) or []
    rows = []
    max_cells = 0
    for tr in children:
        cells = [(c.get("text") or "").strip() for c in (tr.get("children", []) or [])]
        max_cells = max(max_cells, len(cells))
        rows.append(cells)
    if not rows:
        fail("PPTX_TABLE_EMPTY", f"table {table_id} has no rows",
             "Check the table id")
    with open(workdir / f"{name}_flat.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        for i, cells in enumerate(rows, start=1):
            w.writerow(cells + [str(i)])
    meta = {
        "file": str(staged),
        "sheet": table_id,
        "dimensions": {"rows": len(rows), "cols": max_cells, "data_rows": len(rows)},
        "merged_ranges": [],
        "merge_anchors": [],
        "header_band": None,
        "blocks": [],
        "columns": [
            {"col": chr(65 + ci), "nonempty": sum(1 for r in rows if ci < len(r) and r[ci])}
            for ci in range(max_cells)
        ],
        "formulas": {},
        "column_numfmt": {},
    }
    with open(workdir / f"{name}_meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    # digest: minimal structural view (digest consumers only read it for facts)
    with open(workdir / f"{name}_digest.md", "w", encoding="utf-8") as f:
        f.write(f"# {table_id} — 结构摘要 (PPTX)\n")
        f.write(f"- 文件: {staged.name} | table: {table_id} | "
                f"{len(rows)}行 × {max_cells}列\n")
        f.write("- 非空列画像 (|列|非空|):\n")
        for ci in range(max_cells):
            f.write(f"  - {chr(65 + ci)} | {sum(1 for r in rows if ci < len(r) and r[ci])}\n")
    with open(workdir / f"{name}_candidates.yaml", "w", encoding="utf-8") as f:
        f.write("column_classifications: []\n")  # PPTX 无确定性分类, LLM 依业务判定


if __name__ == "__main__":
    # Retired CLI (ADR 0020): invoke via workspace_init/materialize_run instead.
    _utf8_stdio()
    fail("CLI_RETIRED",
         "prepare_run.py 的 outline/flatten CLI 已退役 (ADR 0020)",
         "用 workspace_init.py --init (角色中立事实空间) + "
         "materialize_run.py (run 投影) 替代")
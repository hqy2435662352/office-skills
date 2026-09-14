#!/usr/bin/env python3
"""
scripts/compile_fill.py — the Compiler (v2.5): FillSpec → execution_plan.json.

The single deterministic compiler that replaces build_batch.py,
render_mapping.py, validate_batch.py, handwritten checks, and handwritten
batch JSON. It:
  1. Loads fill_spec.yaml and checks its fingerprints against
     prepare_manifest.json (stale spec → loud failure).
  2. Loads the flattened source CSVs and target structure facts.
  3. Materializes row values from rules (selectors + column mappings +
     lookups + transforms + constants) — the spec stores rules, not data.
  4. Computes the target layout (base_last_row + clone_roles).
  5. Generates the globally-ordered operation list
     (clear → add → remove → merge-clears → merge-sets → fills).
  6. Runs static validation (Section 9 of the v2.5 plan): unique target
     writes, clone source not a merge anchor, null residue policy,
     formula ranges inside the data block, aggregate coverage, target
     paths within the digest's dimensions, fingerprint match.
  7. Derives readback expectations from the materialized plan (no
     hand-written --checks).
  8. Writes execution_plan.json (machine) + mapping.md (human view).

MOD consistency checks (C1–C4, per ADR-0011 "Compiler checks"): before any
schema/fingerprint work, `compile_spec` reads `workdir/mod_resolution.json`
(the MOD Adjudication Record — final decision, not a recommendation) and
fails closed on the FIRST violated check, in order:
  C1 MOD_RESOLUTION_MISSING — file absent / unreadable / invalid JSON.
  C4 MOD_UNRESOLVED          — record.status ∉ {resolved, none}.
  C2 MOD_SELECTION_MISMATCH  — spec.task.selected_mod must EQUAL the recorded
                               selected (strict equality; NONE only when the
                               record says NONE or status=none).
  C3 MOD_REVISION_MISMATCH   — selected != NONE ⇒ spec.task.selected_mod_revision
                               must EQUAL record.selected_revision (strict int
                               equality). selected == NONE short-circuits (no
                               revision check; the spec may carry None/null).
The compiler verifies MOD reference vs. decision consistency only — NEVER the
adjudication process: no resolution-hash binding into spec/fingerprints, no
`why` validation, no `adjudicated_from` validation, no C5.

Exit codes: 0=pass, 1=fatal, 3=spec defects (structured, fix and re-run).

Usage:
  python scripts/compile_fill.py --spec <fill_spec.yaml> --workdir <dir>
"""

from __future__ import annotations

import argparse
import csv
import difflib
import fnmatch
import hashlib
import json
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None

MANIFEST_NAME = "prepare_manifest.json"
MOD_RESOLUTION_NAME = "mod_resolution.json"

from _officecli import (  # noqa: E402
    ensure_utf8_stdio,
    fail as _fail,
    sha256_file,
)
from flatten_table import clone_source_style_profile  # noqa: E402
PLAN_NAME = "execution_plan.json"
MAPPING_NAME = "mapping.md"
SOURCE_TRACE_NAME = "source_trace.json"

CELL_RE = re.compile(r"^[A-Z]{1,2}$")
# 合并/聚合锚点默认样式。⚠️ 字体属性 (font.*) 不在此默认集内 —
# Case 010: 默认写死 Microsoft YaHei 10pt 会无条件覆盖模板单元格原有字体
# (如模板 A 列微软雅黑 12pt bold), 而 singleton 组不建 merge 保留原字体,
# 导致合并/未合并单元格字体不统一。字体只允许通过 spec 显式
# `styles: {anchor|label: {font.*}}` 声明后写入。
STYLE_DEFAULTS = {
    "anchor": {
        "alignment.wrapText": True, "alignment.horizontal": "center",
        "alignment.vertical": "center", "numberformat": "0.00%",
    },
    "label": {
        "alignment.wrapText": True, "alignment.horizontal": "center",
        "alignment.vertical": "center",
    },
}

# ── Matrix FillSpec (ticket 06) rollout ────────────────────────────────

# Rollout switch: Matrix 一等表达 (mapping.targets[].matrix + source lineage)
# 启用后, bulk source-derived literal-sets fallback (把大量源表值烘焙成绝对
# 坐标 literal sets 绕过 grid) 从编译审计警告升级为 fail-closed 缺陷。
# 默认 warn — 既有/合法 spec (客户名/日期/固定 title/显式 user override/
# fixed footer 的 sets) 保持可编译; 矩阵能力 rollout 完成后置 True。
MATRIX_ROLLOUT = {"literal_fallback_fail_closed": False}

# 编译审计启发式 (仅审计 sets, 不是路由依据 — spec 的记录数量阈值禁令只约束
# routing): 值型 sets ≥ BULK_LITERAL_MIN_TOTAL 条、且其中 ≥
# BULK_LITERAL_SOURCE_DERIVED_RATIO 的字面值出现在任一展平源 CSV 值池 →
# BULK_SOURCE_DERIVED_LITERAL_FALLBACK (默认警告 / 开关打开时 fail-closed)。
BULK_LITERAL_MIN_TOTAL = 4
BULK_LITERAL_SOURCE_DERIVED_RATIO = 0.5


def inherited_anchor_style(meta: dict, col: str, region_start: int,
                           region_end: int) -> dict:
    """占位区内同列第一个既有合并锚点的文本样式 (font/alignment)。

    Case 010 盲区修复: 合并区非锚点单元格通常无字体样式; 组锚点重建时若
    新锚点落在旧非锚点格, 将缺失模板字体。继承规则: 取 [region_start,
    region_end] 内同列**行号最小**的既有锚点样式; 无 → {}。
    优先级由调用方保证: spec 显式 `styles` > 继承值 > STYLE_DEFAULTS。"""
    styles_map = meta.get("merge_anchor_styles") or {}
    if not styles_map:
        return {}
    best = None  # (row, anchor)
    for a in meta.get("merge_anchors", []):
        rng = a.get("range", "")
        anchor = a.get("anchor", "")
        if not rng or not anchor:
            continue
        m = re.match(r"^([A-Z]+)(\d+):", rng)
        if not m or m.group(1) != col:
            continue
        row = int(m.group(2))
        if row < region_start or row > region_end:
            continue
        if best is None or row < best[0]:
            best = (row, anchor)
    if best and best[1] in styles_map:
        return dict(styles_map[best[1]])
    return {}


def pin_font_scheme(style: dict) -> dict:
    """若样式含显式 font.name 而未显式给定 font.scheme, 补 font.scheme: none。

    officecli 在把 font.name 写到原本无字体的格子 (默认 style) 时会注入
    <scheme val="minor"/> + <color theme="1"/> (dk1)。<scheme val="minor"/> 是
    主题 minor 字体引用, 渲染时覆盖 <rFont> 字面名按主题 minor 字体显示 (如
    宋体), 使继承的微软雅黑被静默改回主题字体。本归一化把字面字体钉住;
    spec 显式声明的 font.scheme (含有意使用主题字体) 不被覆盖。
    """
    if "font.name" in style and "font.scheme" not in style:
        out = dict(style)
        out["font.scheme"] = "none"
        return out
    return style
def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ── Compiler 反馈硬化 (ticket 03): 缺陷附修复路径选项 ────────────────────
# 编译缺陷从「错误定位」升级为「错误分类 + 修复路径」: 校验断言类缺陷附
# `fix_options[]` (≥2 个候选修复路径, 一个 option + 说明/涉及片段, 不替 Agent
# 决定); 矩阵场景 (locator 歧义/未命中) 额外附 `candidate_cells[]` 候选格清单
# (sheet/cell/value/meaning), 同样不替 Agent 决定选哪个。本票只新增字段,
# 不删任何既有字段 (code/message/corrective_action 全保留, 清理由 06 号票负责)。

def _fix_options_for(code: str) -> list:
    """按缺陷码返回 ≥2 个候选修复路径 (每项 {option, note}); 未登记的码给
    通用默认两条。只提供选项, 不替 Agent 决定 (Compiler 反馈契约)。"""
    table = {
        "MATRIX_FIELD_LOCATOR_AMBIGUOUS": [
            {"option": "composite match 加列消歧",
             "note": "把重复标签拆到 match: {A: ..., B: ...} 加一列唯一字段, "
                     "直到命中恰 1 行"},
            {"option": "换 row + expect 守卫",
             "note": "直接用展平 CSV 的 orig 行号 + expect 结构守卫锁死唯一行"},
        ],
        "MATRIX_FIELD_LOCATOR_NOT_FOUND": [
            {"option": "改成该侧真实存在标签/列文本组合",
             "note": "对照展平 CSV 的真实标签列逐字对齐 (trim 后 exact identity)"},
            {"option": "用 row + expect 守卫定位",
             "note": "标签不存在就改用 orig 行号 + expect 结构守卫"},
        ],
        "MATRIX_RECORD_MAP_INVALID": [
            {"option": "补全 record/source_column/target_column 必要字段",
             "note": "record 为非空字符串, source_column/target_column 为 Excel 列字母"},
            {"option": "改用该侧真实存在的记录列字母",
             "note": "对照源/目标展平宽度修正越界列字母"},
        ],
        "MATRIX_COLUMN_INVALID": [
            {"option": "改用该侧真实存在的列字母",
             "note": "对照源/目标展平宽度 (digest cols) 修正列字母"},
            {"option": "重跑 workspace_init --init（更新列宽事实）",
             "note": "宽度变化 (源表增删列) 时先重新展平再修正列引用"},
        ],
        "MATRIX_FIELD_LOCATOR_INVALID": [
            {"option": "写非空 string locator (legacy 单标签)",
             "note": "单标签唯一时最简单"},
            {"option": "写结构化 locator 三形式之一",
             "note": "string | {match: {列: 文本}} | {row: N, expect: {列: 文本}}"},
        ],
        "MATRIX_FIELD_ROW_GUARD_REQUIRED": [
            {"option": "为 guard row 补 expect: {列: 文本}",
             "note": "裸 row 禁止 — 必须带结构守卫"},
            {"option": "改用 string/composite match 定位",
             "note": "不要裸 row 号定位"},
        ],
        "MATRIX_FIELD_ROW_GUARD_MISMATCH": [
            {"option": "把 row/expect 对准真实展平行",
             "note": "逐列核对 expected vs actual (message 已列出)"},
            {"option": "改用 composite match 定位",
             "note": "guard 行号易漂移时用标签组合更稳"},
        ],
        "MATRIX_FIELD_ROW_OUT_OF_RANGE": [
            {"option": "核对展平 CSV 的 orig 列行号",
             "note": "row 必须是展平 CSV 真实存在的 orig 行号"},
            {"option": "改用 label / composite match 定位",
             "note": "行号超界改用文本标签定位"},
        ],
        "TRANSFORM_UNKNOWN": [
            {"option": "在 mapping.transforms 定义该命名 transform",
             "note": "function ∈ strip / regex_replace / controlled_translation"},
            {"option": "改用内置 round2/round4/trim",
             "note": "内置变换无需定义, 直接按名引用"},
        ],
        "TRANSFORM_FUNCTION_UNKNOWN": [
            {"option": "改用合法 function",
             "note": "function ∈ strip / regex_replace / controlled_translation"},
            {"option": "删除该 transform 定义为内置引用",
             "note": "内置 trim/round2/round4 按名直接用, 无需在 transforms 定义"},
        ],
        "TRANSFORM_NAME_MISSING": [
            {"option": "补非空字符串 name",
             "note": "列映射/矩阵按名引用该 transform"},
            {"option": "删除该无名字段条目",
             "note": "若本意是内置变换, 按名直接引用即可"},
        ],
        "TRANSFORM_PATTERN_MISSING": [
            {"option": "补非空 pattern",
             "note": "regex_replace 缺 pattern 会全串替换 (静默数据丢失)"},
            {"option": "改用 strip 或 controlled_translation",
             "note": "若非子串替换语义, 换整值/空白处理函数"},
        ],
        "TRANSFORM_PATTERN_INVALID": [
            {"option": "修正 pattern 为可编译正则",
             "note": "re.compile 通过后再编译; 曾静默原样通过"},
            {"option": "改用字面字符串替换",
             "note": "简单子串可考虑用更简单可编译的 pattern"},
        ],
        "TRANSFORM_REPLACEMENT_MISSING": [
            {"option": "补 replacement 字段",
             "note": "空串 \"\" 可合法表示删除匹配"},
            {"option": "明确写出 replacement 值",
             "note": "把目标替换字段显式写出, 消除缺省歧义"},
        ],
        "TRANSFORM_TRANSLATIONS_INVALID": [
            {"option": "修正 translations 为 键→标量值 映射",
             "note": "controlled_translation 词表须是 dict, 键非空字符串, 值为字符串/数值"},
            {"option": "删除该词表条目改内置处理",
             "note": "若无需词表翻译, 删定义并按名引用内置函数"},
        ],
        "DUPLICATE_TARGET_WRITE": [
            {"option": "为每格只保留一个 owner",
             "note": "columns/nulls/formulas/merges/sets/group_merges 重叠处删去多余写"},
            {"option": "核对 matrix field_map × record_map 与 sets 是否撞格",
             "note": "matrix 目标格与 sets 固定值不能重叠"},
        ],
        "SPEC_NON_STRING_ITEM": [
            {"option": "用双引号包裹整行 (冒号在内)",
             "note": "含 ': ' 的裸标量被解析成 mapping — 整行加双引号"},
            {"option": "拆成多个独立字符串条目",
             "note": "避免行内冒号触发 YAML mapping 解析"},
        ],
        "INPLACE_REGION_OVERLAP": [
            {"option": "前置块结构行/remove_rows 移出占位区",
             "note": "区行归终末 inplace 块所有"},
            {"option": "换 append-only 合法终态",
             "note": "占位行自然下沉保留, 无需前置块打占位区"},
        ],
    }
    return table.get(code) or [
        {"option": f"按 {code} 的 corrective_action 定向修",
         "note": "读缺陷 message/corrective_action, 修 fill_spec.yaml 后重编译"},
        {"option": "查 references/FILLSPEC.md 对应约束",
         "note": "按缺陷码定位 FILLSPEC 能力/语法小节, 对齐声明"},
    ]


def decorate_defects(defects: list | None) -> list | None:
    """为每个校验断言类缺陷附 fix_options[] (≥2); 矩阵场景已有 candidate_cells
    的原样保留 (此处不重复生成). 幂等 (已附 fix_options 的不再覆盖), 纯地址内
    增字段, 不删任何既有字段。"""
    if not defects:
        return defects
    for d in defects:
        if not isinstance(d, dict):
            continue
        if d.get("fix_options"):
            continue
        d["fix_options"] = _fix_options_for(d.get("code", ""))
    return defects


def fail(code: str, message: str, corrective_action: str,
         defects: list | None = None, exit_code: int = 3) -> None:
    """compile_fill 本地 fail 包装: 缺陷 emit 前先 decorate (附 fix_options 等),
    其余语义与 _officecli.fail 完全一致 (code/message/corrective_action/defects
    透传)。"""
    _fail(code, message, corrective_action,
          decorate_defects(defects), exit_code)


# 展平产物 run-local 文件名后缀 → cache 白名单 canonical 名（ticket 08：共享
# 展平产物只存 cache/<key>/ 一份，run 目录不再逐字节复制；compile 按 cache_key
# 从 cache 解析。candidates 不参与 compile/execute，不列入 cache 解析）。
_CACHE_BY_SUFFIX = {
    "_flat.csv": "flat.csv",
    "_meta.json": "meta.json",
    "_digest.md": "digest.md",
}


def _staged_input_path(name: str, workdir: Path,
                       shared_root: Path | None) -> Path:
    """Raw staged 输入 (.xlsx 等) 路径解析（ticket 08 + ADR 0018）：workdir-local
    优先（single-run），否则 shared_root/staged/<name>（legacy task 布局），
    再否则 shared_root/<name>（workspace 平铺布局 — role-neutral init 把
    staged 与 flatten 产物放 task root 根）。"""
    w = workdir / name
    if w.is_file():
        return w
    if shared_root is not None:
        if (shared_root / "staged" / name).is_file():
            return shared_root / "staged" / name
        if (shared_root / name).is_file():
            return shared_root / name
    return w


def _flat_entry_path(entry: dict, filename: str, workdir: Path,
                     shared_root: Path | None) -> Path:
    """展平条目文件（csv/meta/digest）路径解析（ticket 08 + ADR 0018）：
    workdir-local 优先（single-run），否则按 entry.cache_key 从
    shared_root/cache/<key>/ 取 canonical 产物（legacy task 共享缓存），
    再否则 shared_root/<filename>（workspace 平铺布局 — materialize_run
    投影的 run-local manifest 条目文件在 task root 根）。"""
    w = workdir / filename
    if w.is_file():
        return w
    key = (entry or {}).get("cache_key") if isinstance(entry, dict) else None
    if shared_root is not None:
        if key:
            for suffix, canonical in _CACHE_BY_SUFFIX.items():
                if filename.endswith(suffix):
                    p = shared_root / "cache" / key / canonical
                    if p.is_file():
                        return p
                    break
        if (shared_root / filename).is_file():
            return shared_root / filename
    return w


def bind_input_hashes(workdir: Path, inputs: dict,
                      shared_root: Path | None = None) -> dict:
    """Compile-time binding of the STAGED input files' content hashes.

    plan.input_hashes = {staged_name: sha256} for every source + the target —
    the exact files execute_batch.py will read at execution time. This is
    recomputed at COMPILE time (not copied from prepare_manifest.json
    files[].sha256, which is an init-stage snapshot). Row-gap repair never
    invalidates it: the default path repairs inside `workspace_init --init`
    BEFORE any hash is recorded (so staged bytes == manifest hash), and the
    standalone repair CLI emits a NEW snapshot that must re-enter `--init`.
    A staged file missing at compile time binds None (unverifiable — execute
    fails closed on it).

    ticket 08: shared_root 给定时，raw 输入从 shared_root/staged/<name> 按名
    引用（task 级共享，不复制进 run 目录）。"""
    names = list(inputs.get("sources") or []) + [inputs.get("target")]
    out = {}
    for name in names:
        if not name:
            continue
        p = _staged_input_path(name, workdir, shared_root)
        out[name] = sha256_file(p) if p.is_file() else None
    return out


def col_idx_to_letter(idx: int) -> str:
    s = ""
    idx += 1
    while idx > 0:
        idx, rem = divmod(idx - 1, 26)
        s = chr(ord("A") + rem) + s
    return s


def col_letter_to_idx(letter: str) -> int:
    n = 0
    for ch in letter.upper():
        n = n * 26 + (ord(ch) - ord("A") + 1)
    return n - 1


def _parse_number(text: str) -> float | None:
    s = str(text).strip().replace(",", "").replace("%", "")
    try:
        return float(s)
    except ValueError:
        return None


def _fmt_number(v: float) -> str:
    return ("%.6g" % v) if v != int(v) else str(int(v))


# ── Loading ────────────────────────────────────────────────────────────

def load_spec(path: Path) -> dict:
    if not path.is_file():
        fail("SPEC_NOT_FOUND", f"fill_spec.yaml not found: {path}",
             "Provide the --spec path", exit_code=1)
    if yaml is None:
        fail("DEP_MISSING", "PyYAML is required", "pip install pyyaml", exit_code=1)
    try:
        spec = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        fail("SPEC_PARSE_ERROR", f"YAML parse failed: {e}",
             "Fix the YAML syntax in fill_spec.yaml")
    if not isinstance(spec, dict):
        fail("SPEC_INVALID", "fill_spec.yaml must be a mapping",
             "Use the schema in references/FILLSPEC.md")
    return spec


def load_manifest(workdir: Path) -> dict:
    p = workdir / MANIFEST_NAME
    if not p.is_file():
        fail("MANIFEST_NOT_FOUND", f"{MANIFEST_NAME} missing in {workdir}",
             "Run workspace_init.py --init first (fact space)")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except ValueError as e:
        fail("MANIFEST_INVALID", f"corrupt manifest: {e}",
             "Re-run workspace_init.py --init")


def load_csv_rows(csv_path: Path) -> list[tuple[list[str], int]]:
    """CSV rows: cells... + trailing original row number."""
    rows = []
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        for line in csv.reader(f):
            if not line:
                continue
            try:
                orig = int(line[-1].strip())
            except (ValueError, IndexError):
                continue
            rows.append((line[:-1], orig))
    return rows


def expand_template(tpl: str, ctx: dict) -> str:
    """Expand {r}/{r1}/{r2}/{n}; missing key → loud error."""
    def _sub(m):
        key = m.group(1)
        if key not in ctx:
            raise KeyError(key)
        return str(ctx[key])
    try:
        return re.sub(r"\{(\w+)\}", _sub, tpl)
    except KeyError as e:
        raise ValueError(f"formula template references unknown key {{{e.args[0]}}}: {tpl!r}")


# ── Schema validation ──────────────────────────────────────────────────

REQUIRED_TOP = ("task", "inputs", "fingerprints", "mapping", "decisions",
                "gaps", "lineage", "validation")


def _bare_scalar_text(item: object) -> str:
    """Reconstruct the original bare-scalar line text from its parsed form.

    YAML parses `- 追加新历史块: 源文件 ...` into {'追加新历史块': '源文件 ...'};
    the corrective example must show the text the user actually wrote (a `k: v`
    join), not the dict repr — repr is not what belongs inside the quotes
    (2026-08-13 Egypt FRESH: the old hint suggested quoting the whole dict,
    which is not valid YAML)."""
    if not isinstance(item, dict):
        return str(item)
    text = ": ".join(f"{k}: {_bare_scalar_text(v)}" for k, v in item.items())
    # The reconstruction is shown inside a double-quoted YAML scalar — escape
    # embedded quotes/backslashes so the corrective example stays copy-paste-valid.
    return text.replace("\\", "\\\\").replace('"', '\\"')


def validate_schema(spec: dict, manifest: dict) -> list[dict]:
    defects = []
    for key in REQUIRED_TOP:
        if key not in spec:
            defects.append({"code": "SPEC_MISSING_KEY", "key": key,
                            "message": f"fill_spec missing required top-level key: {key}",
                            "corrective_action": "Add the key per references/FILLSPEC.md"})
    if defects:
        return defects

    task = spec["task"]
    if not isinstance(task.get("intent"), str) or not task["intent"].strip():
        defects.append({"code": "SPEC_TASK_INTENT", "message": "task.intent is required",
                        "corrective_action": "Describe the fill intent in one line"})
    if task.get("selected_mod") not in (None, "NONE"):
        if not isinstance(task["selected_mod"], str):
            defects.append({"code": "SPEC_MOD_VALUE",
                            "message": "task.selected_mod must be NONE or a MOD name",
                            "corrective_action": "Fix the value"})

    # decisions/gaps/lineage 条目必须是字符串 — YAML 里含 ": " 的裸标量会被
    # 解析成 mapping (dict), 静默丢内容, 必须报错并提示加引号 (2026-08-10)。
    for list_key in ("decisions", "gaps"):
        items = spec.get(list_key)
        if not isinstance(items, list):
            continue
        for i, item in enumerate(items):
            if not isinstance(item, str):
                defects.append({"code": "SPEC_NON_STRING_ITEM",
                                "key": list_key, "index": i,
                                "message": f"{list_key}[{i}] is {type(item).__name__} "
                                           f"(value {item!r}) — a bare scalar containing "
                                           f"': ' was parsed as a mapping",
                                "corrective_action": f"用双引号包裹整行: "
                                                     f'- "{_bare_scalar_text(item)}" '
                                                     f"(wrap the WHOLE line — colon included — "
                                                     "in double quotes, exactly as written)"})
    lineage = spec.get("lineage")
    if isinstance(lineage, list):
        for i, entry in enumerate(lineage):
            if not isinstance(entry, dict) or not isinstance(entry.get("source"), str):
                defects.append({"code": "SPEC_LINEAGE_INVALID",
                                "message": f"lineage[{i}] must be a mapping with a "
                                           f"string 'source' field",
                                "corrective_action": "Fix the lineage entry per FILLSPEC.md"})

    inputs = spec["inputs"]
    staged_names = {f["staged"] for f in manifest["files"]}
    if inputs.get("target") not in staged_names:
        defects.append({"code": "SPEC_TARGET_UNKNOWN",
                        "message": f"inputs.target {inputs.get('target')!r} was not staged",
                        "corrective_action": "Use a staged file name from the manifest"})
    if not isinstance(inputs.get("source_sheets"), list) or not inputs["source_sheets"]:
        defects.append({"code": "SPEC_SOURCE_SHEETS",
                        "message": "inputs.source_sheets must list which sheets were flattened",
                        "corrective_action": "List source/sheets pairs"})
    else:
        for ss in inputs["source_sheets"]:
            if ss.get("source") not in staged_names:
                defects.append({"code": "SPEC_SOURCE_UNKNOWN",
                                "message": f"source {ss.get('source')!r} was not staged",
                                "corrective_action": "Use a staged file name"})
    if not isinstance(inputs.get("target_sheet"), str) or not inputs["target_sheet"]:
        defects.append({"code": "SPEC_TARGET_SHEET",
                        "message": "inputs.target_sheet is required",
                        "corrective_action": "Set the target sheet name (or pptx table id)"})

    targets = spec["mapping"].get("targets")
    if not isinstance(targets, list) or not targets:
        defects.append({"code": "SPEC_TARGETS_EMPTY",
                        "message": "mapping.targets must contain at least one entry",
                        "corrective_action": "Describe the target layout"})
    elif len(targets) > 1:
        defects.append({"code": "SPEC_TARGETS_TOO_MANY",
                        "message": f"{len(targets)} targets declared — v2.5 compiles exactly ONE "
                                   "target per run; extra targets would be silently ignored",
                        "corrective_action": "Split the run into one fill_spec per target, "
                                             "or fold the extra sheets into a single target entry"})
    return defects


# ── MOD consistency gates (C1–C4) ──────────────────────────────────────

def check_mod_consistency(spec: dict, workdir: Path,
                          mod_resolution_path: Path | None = None) -> None:
    """Static MOD-reference-vs-decision consistency checks (C1–C4).

    Reads the MOD Adjudication Record (final decision, not a recommendation)
    from `mod_resolution_path` when given (task-level once, ticket 08), else
    falls back to `workdir/mod_resolution.json` (single-run). Fails closed on
    the FIRST violated check, in strict order C1 → C4 → C2 → C3:

      C1 MOD_RESOLUTION_MISSING — the record is absent, unreadable, or not a
          JSON object. There is NO legal compile path without the record
          (no exemption for probe/capabilities modes).
      C4 MOD_UNRESOLVED          — record.status ∉ {resolved, none} (an
          ambiguous/conflict record means adjudication was never recorded
          by re-running `--mod`).
      C2 MOD_SELECTION_MISMATCH  — spec.task.selected_mod must EQUAL the
          record's selected value exactly (strict equality, not "one of the
          candidates"). status=resolved ⇒ equal record.selected (including
          "NONE"); status=none ⇒ must equal "NONE". A missing/None spec
          selected_mod is NOT equal to "NONE" (strict).
      C3 MOD_REVISION_MISMATCH   — only when record.selected != "NONE":
          spec.task.selected_mod_revision must EQUAL record.selected_revision
          (strict int equality). selected == "NONE" short-circuits (no
          revision check; the spec may carry None/null).

    The compiler verifies MOD reference vs. decision consistency only — never
    the adjudication process: no resolution-hash binding, no `why` validation,
    no `adjudicated_from` validation.
    """
    rec_path = mod_resolution_path if mod_resolution_path is not None \
        else (workdir / MOD_RESOLUTION_NAME)
    try:
        if not rec_path.is_file():
            raise ValueError("missing")
        record = json.loads(rec_path.read_text(encoding="utf-8"))
        if not isinstance(record, dict):
            raise ValueError("not a mapping")
    except (OSError, ValueError):
        fail("MOD_RESOLUTION_MISSING",
             f"{MOD_RESOLUTION_NAME} absent / unreadable / invalid: {rec_path}",
             "先运行 mod_nominate.py 提名；用户裁决后带 --mod 重跑把选择写盘")

    status = record.get("status")
    if status not in ("resolved", "none"):
        fail("MOD_UNRESOLVED",
             f"{MOD_RESOLUTION_NAME} status is {status!r} (∈ {{ambiguous, conflict, …}})，"
             "裁决尚未通过 --mod 重跑写盘",
             "裁决后必须带 --mod <NAME|NONE> 重跑 mod_nominate.py，把 final decision "
             "record 写盘；盘上仍是 ambiguous/conflict = 裁决后未重跑")

    task = spec["task"]
    spec_mod = task.get("selected_mod")
    if status == "resolved":
        recorded = record.get("selected")
        if spec_mod != recorded:
            fail("MOD_SELECTION_MISMATCH",
                 f"task.selected_mod {spec_mod!r} != recorded selected {recorded!r} "
                 f"(status=resolved)",
                 f"把 spec.task.selected_mod 设为裁决记录 {MOD_RESOLUTION_NAME} 里 "
                 f"selected 的值 ({recorded!r})")
        if recorded != "NONE":
            spec_rev = task.get("selected_mod_revision")
            recorded_rev = record.get("selected_revision")
            if spec_rev != recorded_rev:
                fail("MOD_REVISION_MISMATCH",
                     f"task.selected_mod_revision {spec_rev!r} != recorded "
                     f"selected_revision {recorded_rev!r} (selected={recorded!r})",
                     f"把 spec.task.selected_mod_revision 设为裁决记录里 "
                     f"selected_revision 的值 ({recorded_rev!r})")
    else:  # status == "none"
        if spec_mod != "NONE":
            fail("MOD_SELECTION_MISMATCH",
                 f"task.selected_mod {spec_mod!r} != \"NONE\" (status=none)",
                 "把 spec.task.selected_mod 设为 \"NONE\"（与 mod_resolution.json "
                 "status=none 对齐）")
    # C3 is short-circuited when selected == "NONE" (no revision check; the
    # spec may legitimately carry None/null selected_mod_revision).


# ── Layout ─────────────────────────────────────────────────────────────

def parse_rows_spec(rows_val: str, n: int) -> tuple[int, int] | None:
    """'1:{n}' or '2:7' → (start, end) 1-based within [1, n]; None → invalid."""
    if not isinstance(rows_val, str):
        return None
    m = re.fullmatch(r"(\d+|\{n\})\s*:\s*(\d+|\{n\})", rows_val.strip())
    if not m:
        return None
    def _num(v):
        return n if v == "{n}" else int(v)
    a, b = _num(m.group(1)), _num(m.group(2))
    if a < 1 or b > n or a > b:
        return None
    return a, b


def parse_rel_rows(rows_spec) -> set[int] | None:
    """nulls rows spec: 'all' → None (caller treats as every data row),
    list of ints, or 'a:b' range."""
    if rows_spec == "all":
        return None
    if isinstance(rows_spec, list):
        return {int(x) for x in rows_spec}
    if isinstance(rows_spec, str) and ":" in rows_spec:
        a, _, b = rows_spec.partition(":")
        return set(range(int(a), int(b) + 1))
    return set()


def validate_nulls_rows(cfg: dict, defects: list) -> None:
    """nulls rows 格式静态校验 — 非法格式给结构化缺陷, 而不是在
    parse_rel_rows 里抛 ValueError 冒泡成 Python traceback
    (2026-08-12: rows: ['1:2','3:4'] 列表混合写法曾让 probe 崩溃)."""
    for n in cfg.get("nulls", []):
        rows = n.get("rows")
        col = n.get("col", "?")
        if rows == "all":
            continue
        ok = False
        if isinstance(rows, list):
            ok = all(isinstance(r, int) and r >= 1 for r in rows)
        elif isinstance(rows, str) and re.fullmatch(r"\d+\s*:\s*\d+", str(rows)):
            ok = True
        if not ok:
            defects.append({
                "code": "NULLS_ROWS_INVALID", "col": col, "rows": rows,
                "message": f"nulls[{col}] rows {rows!r} is not a valid row spec — "
                           "'all', an int list, or a 'a:b' range string",
                "corrective_action": "Use rows: all, rows: [1, 3], or rows: \"2:4\""})


# Block top-level key allowlist (ID-1). `resolve_blocks` passes through every
# key the author wrote, and `_emit_block_ops` only reads specific nested keys —
# a misplaced `aggregates:`/`per_row:`/`group_aggregates:` (which belong under
# `formulas:`) or a typo (singular `formula`, `column`) at the block top level
# used to be silently dropped (Case 05 U4/E4, 3 compile round-trips to reverse
# engineer). Legal keys match the FILLSPEC「blocks: 多数据块」declared surface
# (含位置模型的 mode 相关声明所属键 — clone_roles 条目内)。
BLOCK_TOP_LEVEL_KEYS = ("clone_roles", "rows", "columns", "formulas", "merges",
                        "group_merges", "nulls", "remove_rows", "styles")

# Known-but-misplaced keys → corrective_action names the correct nesting.
BLOCK_MISPLACED_KEY_NESTING = {
    "aggregates": "formulas.aggregates",
    "per_row": "formulas.per_row",
    "group_aggregates": "formulas.group_aggregates",
}

# Per-key copy-paste form for the corrective example (agg entries are lists,
# per_row is a {col: template} map — one generic shorthand would mislead).
BLOCK_MISPLACED_KEY_EXAMPLE = {
    "aggregates": "formulas: {aggregates: [{col, rows, formula, style}]}",
    "per_row": 'formulas: {per_row: {"G": "A{r}-B{r}"}}',
    "group_aggregates": "formulas: {group_aggregates: [{group_by, col, formula, style}]}",
}


def validate_block_top_level_keys(blocks: list) -> list:
    """Static allowlist check for every block's top-level keys.

    Runs on the resolved block configs (after `resolve_blocks`), before any
    layout/op work: a misplaced or unknown key = `BLOCK_KEY_STRUCTURE_INVALID`
    (compile-time defect, carried on stderr with a corrective_action pointing
    at the correct nesting) — never a silent ignore again. Returns defects;
    the caller fails compilation (exit 3) when non-empty."""
    defects: list = []
    legal = list(BLOCK_TOP_LEVEL_KEYS)
    for bi, b in enumerate(blocks):
        for key in b:
            if key.startswith("_"):
                continue  # internal keys (e.g. `_rows`)
            if key in BLOCK_TOP_LEVEL_KEYS:
                continue
            label = f"block[{bi}]"
            if key in BLOCK_MISPLACED_KEY_NESTING:
                target = BLOCK_MISPLACED_KEY_NESTING[key]
                example = BLOCK_MISPLACED_KEY_EXAMPLE[key]
                defects.append({
                    "code": "BLOCK_KEY_STRUCTURE_INVALID", "block": label,
                    "key": key,
                    "message": f"{label}: 顶层键 {key!r} 位置错误 — 它属于 {target} "
                               "(嵌套在 `formulas` 之下); 写在 block 顶层会被 "
                               "静默忽略, 不再通过",
                    "corrective_action": f"把 {key} 移到 {target} 下 — 写为 "
                                         f"`{example}`",
                })
            else:
                defects.append({
                    "code": "BLOCK_KEY_STRUCTURE_INVALID", "block": label,
                    "key": key,
                    "message": f"{label}: 顶层键 {key!r} 不在合法键列表 "
                               f"{legal} 内 — 拼写错误或错位键会被静默忽略, "
                               "不再通过",
                    "corrective_action": f"检查 {key!r} 的拼写/层级; 合法顶层键 = "
                                         f"{', '.join(legal)}",
                })
    return defects


def resolve_blocks(target: dict) -> list[dict]:
    """Target block list with single-block backward compatibility.

    `mapping.targets[].blocks[]` — each block carries its own clone_roles,
    rows, and optional columns/formulas/merges/nulls/remove_rows (falling back
    to the target-level config). Without `blocks`, the target-level
    clone_roles/rows/columns/... form one implicit block (old behaviour)."""
    blocks = target.get("blocks")
    if blocks is None:
        blocks = [{
            "clone_roles": target.get("clone_roles", []),
            "rows": target.get("rows") or {},
            "columns": target.get("columns", []),
            "formulas": target.get("formulas", {}),
            "merges": target.get("merges", []),
            "group_merges": target.get("group_merges", []),
            "nulls": target.get("nulls", []),
            "remove_rows": target.get("remove_rows", []),
            "styles": target.get("styles", {}),
        }]
    out = []
    for b in blocks:
        cfg = dict(b)
        cfg.setdefault("columns", target.get("columns", []))
        cfg.setdefault("formulas", target.get("formulas", {}))
        cfg.setdefault("merges", target.get("merges", []))
        cfg.setdefault("group_merges", target.get("group_merges", []))
        cfg.setdefault("nulls", target.get("nulls", []))
        cfg.setdefault("remove_rows", target.get("remove_rows", []))
        cfg.setdefault("styles", target.get("styles", {}))
        out.append(cfg)
    return out


def inplace_roles(block_cfg: dict) -> list:
    """clone_roles entries declaring mode: inplace."""
    return [r for r in block_cfg.get("clone_roles", []) if r.get("mode") == "inplace"]


def validate_inplace_declaration(blocks_cfg: list, dims: dict,
                                 defects: list) -> dict | None:
    """Position-model compile invariants (declaration level).

    Returns the inplace context {block, role, start_row, capacity, region_end}
    or None. Runs BEFORE layout so malformed regions never crash it."""
    ibs = [b for b in blocks_cfg if inplace_roles(b)]
    if not ibs:
        return None
    if len(ibs) > 1:
        defects.append({"code": "INPLACE_MULTIPLE_BLOCKS",
                        "message": f"{len(ibs)} blocks declare mode: inplace — "
                                   "a target may contain at most one inplace block",
                        "corrective_action": "Keep exactly one inplace block per target"})
    ib = ibs[0]
    if blocks_cfg.index(ib) != len(blocks_cfg) - 1:
        defects.append({"code": "INPLACE_NOT_LAST_BLOCK",
                        "message": "the inplace block must be the LAST block "
                                   "(row shifts after trim would make a following "
                                   "block's insertion point ambiguous)",
                        "corrective_action": "Move the inplace block to the end of blocks[]"})
    role = inplace_roles(ib)[0] if inplace_roles(ib) else {}
    rest = ib.get("clone_roles", [])
    idx = next((i for i, r in enumerate(rest) if r.get("mode") == "inplace"), None)
    if idx is not None and idx != len(rest) - 1:
        defects.append({"code": "INPLACE_NOT_LAST_BLOCK",
                        "message": "the inplace data role must be the last clone_role "
                                   "of its block (roles after it would get shifted rows)",
                        "corrective_action": "Reorder clone_roles so the inplace data "
                                             "role is last"})
    start_row = role.get("start_row")
    capacity = role.get("capacity")
    if not isinstance(start_row, int) or not isinstance(capacity, int) \
            or start_row < 1 or capacity < 1:
        defects.append({"code": "INPLACE_REGION_OUT_OF_BOUNDS",
                        "message": f"inplace data role needs positive integer "
                                   f"start_row and capacity (got start_row={start_row!r}, "
                                   f"capacity={capacity!r}) — the region declaration is a "
                                   "model fact the Compiler cannot compute",
                        "corrective_action": "Declare start_row and capacity from the digest"})
        return {"block": ib, "role": role, "start_row": 1, "capacity": 0,
                "region_end": 0, "declared": False}
    region_end = start_row + capacity - 1
    if region_end > dims.get("rows", 0):
        defects.append({"code": "INPLACE_REGION_OUT_OF_BOUNDS",
                        "message": f"inplace region {start_row}..{region_end} exceeds "
                                   f"digest rows {dims.get('rows')} — the spec claims "
                                   "template rows that do not exist",
                        "corrective_action": "Re-read the digest and fix start_row/capacity"})
    if not role.get("template_row"):
        defects.append({"code": "INPLACE_NO_CLONE_SOURCE",
                        "message": "inplace data role without template_row — overflow "
                                   "rows (N > capacity) need a clone format source",
                        "corrective_action": "Add template_row (a non-anchor placeholder row)"})
    return {"block": ib, "role": role, "start_row": start_row,
            "capacity": capacity, "region_end": region_end, "declared": True}


def validate_inplace_geometry(blocks_cfg: list, ip_ctx: dict, roles: list,
                              base_last_row: int, defects: list) -> None:
    """Position-model geometry invariants (post-layout, role rows known).

    Coordinate stability = append-zone legality + region-overlap check jointly:
      - INPLACE_REGION_OVERLAP: a preceding block's structural row (add target
        or remove_rows) or an absolute write touches the region.
      - STRUCTURAL_OP_OUT_OF_ZONE: a preceding block's remove_rows targets
        rows <= base_last_row (the append zone boundary)."""
    if not ip_ctx or not ip_ctx.get("declared"):
        return
    ib = ip_ctx["block"]
    ib_index = blocks_cfg.index(ib)
    lo, hi = ip_ctx["start_row"], ip_ctx["region_end"]
    for role in roles:
        if role.get("block", 0) >= ib_index:
            continue
        r = role.get("row")
        if r is not None and lo <= r <= hi:
            defects.append({"code": "INPLACE_REGION_OVERLAP",
                            "message": f"preceding block role row {r} falls inside the "
                                       f"placeholder region {lo}..{hi} — append-zone "
                                       "legality is violated (base_last_row must sit "
                                       "below the region end)",
                            "corrective_action": "Set base_last_row >= region end, or "
                                                 "move the inplace block"})
    for bi, b in enumerate(blocks_cfg):
        if bi >= ib_index:
            continue
        for rn in b.get("remove_rows", []):
            if lo <= rn <= hi:
                defects.append({"code": "INPLACE_REGION_OVERLAP",
                                "message": f"preceding block remove_rows targets row {rn} "
                                           f"inside the placeholder region {lo}..{hi}",
                                "corrective_action": "Remove rows only outside the region"})
            elif rn <= base_last_row:
                defects.append({"code": "STRUCTURAL_OP_OUT_OF_ZONE",
                                "message": f"preceding block remove_rows targets row {rn} "
                                           f"<= base_last_row {base_last_row} — structural "
                                           "row ops outside the terminal inplace block are "
                                           "illegal (the terminal inplace block owns the "
                                           "Trim the Compiler derives); remove_rows declared "
                                           "above base_last_row would hit rows shifted by "
                                           "the block's own adds",
                                "corrective_action": "前置 append 块不声明 remove_rows; "
                                                     "收缩由终末 inplace 块 Trim (编译器"
                                                     "推导). 首选 append-only 合法终态: "
                                                     "占位行自然下沉保留"})


def validate_append_remove_zone(blocks_cfg: list, base_last_row: int,
                                defects: list) -> None:
    """REMOVE_TARGETS_APPEND_ZONE: append 块的 remove_rows 必须 ≤ base_last_row.

    append 块的 add 全部插在 base_last_row 之下, remove_rows 声明的是模板坐标;
    若 remove > base_last_row, 其执行时身份被先行的 add 推移, remove 用裸模板
    坐标命中刚插入的新数据行 — 自毁 plan (probe 2026-08-13: base=10 +
    remove_rows [12,13,14] + 3 数据行克隆 → ops = add×4 → remove 14/13/12
    正是新数据行; 最终行数断言 rows + adds − removes 恒等, 抓不住)。
    remove_rows ≤ base_last_row 的经典场景 (源行数 < 模板行数) 在 add 区之外,
    不被推移, 保持合法。inplace 块消费编译器推导的 Trim, 不在本检查范围。"""
    for bi, b in enumerate(blocks_cfg):
        if inplace_roles(b):
            continue
        for rn in b.get("remove_rows", []):
            if rn > base_last_row:
                defects.append({
                    "code": "REMOVE_TARGETS_APPEND_ZONE",
                    "row": rn, "block": f"block[{bi}]",
                    "message": f"block[{bi}]: remove_rows targets row {rn} > "
                               f"base_last_row {base_last_row} — the block's own "
                               "adds insert below base_last_row and shift every "
                               "row below it, so this remove executes against a "
                               "newly inserted row (自毁 plan, 行数断言恒等抓不住); "
                               "rows ≤ base_last_row never shift",
                    "corrective_action": "首选 append-only 合法终态: 占位行自然"
                                         "下沉保留, 无需删除; remove_rows 只能声明"
                                         "≤ base_last_row 的模板既有行 (add 区之外). "
                                         "仅当占位行携带单元格样式 (digest 样式粒度"
                                         "结论) 时, mode: inplace 才是条件选项 — "
                                         "裸行占位 inplace 填入会产出无边框块, "
                                         "违反 VAL-007 格式沿用",
                })


def compute_layout_block(clone_roles: list, n_rows: int, cursor: int,
                         block_index: int) -> tuple[list[dict], int | None]:
    """One block's row layout starting at `cursor` (base row / previous block end).

    `mode: inplace` data roles consume pre-existing template rows at
    `start_row` (template coordinates, never shifted); overflow rows are
    cloned after the region. Returns (roles, data_start) —
    data_start is the first data row of this block (None when the block has
    no data role). Roles carry their block index for region-overlap checks."""
    roles = []
    data_start = None
    for role in clone_roles:
        kind = role.get("role")
        if kind == "spacer":
            cursor += 1
            roles.append({"kind": "spacer", "row": cursor, "block": block_index})
        elif kind == "data":
            if role.get("mode") == "inplace":
                # Placeholder Region rows are template coordinates. The cursor
                # (append zone) is NOT advanced over them; overflow clones land
                # after the region in the append zone.
                start_row = role.get("start_row")
                capacity = role.get("capacity")
                template_row = role.get("template_row")
                inplace_count = min(n_rows, capacity)
                for i in range(inplace_count):
                    roles.append({"kind": "data", "row": start_row + i,
                                  "template_row": template_row,
                                  "mode": "inplace", "block": block_index})
                for i in range(n_rows - inplace_count):
                    roles.append({"kind": "data", "row": start_row + capacity + i,
                                  "template_row": template_row,
                                  "mode": "overflow_clone", "block": block_index})
                data_start = start_row
                cursor = start_row + n_rows - 1
            else:
                data_start = cursor + 1
                for i in range(n_rows):
                    roles.append({"kind": "data", "row": data_start + i,
                                  "template_row": role.get("template_row"),
                                  "block": block_index})
                cursor = data_start + n_rows - 1
        else:
            cursor += 1
            roles.append({"kind": kind, "row": cursor, "block": block_index,
                          "template_row": role.get("template_row"),
                          "value": role.get("value")})
    return roles, data_start


def compute_layout(blocks: list[dict], platform: str, target: dict,
                   defects: list, table_rows: int | None = None) -> tuple[list[dict], list]:
    """xlsx: blocks lay out sequentially from base_last_row (cursor advances
    across blocks). pptx: single block, fixed tr rows from first_data_row;
    tr coordinates are checked against the table's ACTUAL row count (issue 06:
    pptx rows are pre-built — out-of-bounds is a spec error at compile time,
    never a runtime surprise).

    Returns (all_roles, data_starts) — data_starts[i] is block i's first data
    row (xlsx) or tr index (pptx)."""
    if platform == "pptx":
        if len(blocks) > 1:
            fail("PPTX_MULTI_BLOCK", "pptx targets support exactly ONE data block",
                 "Split into separate runs for multiple blocks")
        first = target.get("first_data_row")
        if not isinstance(first, int) or first < 1:
            fail("SPEC_FIRST_ROW", "pptx targets need first_data_row (1-based tr index)",
                 "Declare first_data_row in the target entry")
        n = len(blocks[0].get("_rows", []))
        last_tr = first + n - 1
        if table_rows is not None and n > 0 and last_tr > table_rows:
            defects.append({
                "code": "PPTX_TARGET_ROWS_OUT_OF_BOUNDS",
                "message": f"first_data_row {first} + {n} matched rows ends at "
                           f"tr[{last_tr}] but the table has only {table_rows} rows — "
                           "pptx rows are pre-built and cannot be cloned",
                "corrective_action": "Re-read the digest's table row count and fix "
                                     "first_data_row, or narrow the selectors, or add "
                                     "the missing rows once with python-pptx BEFORE "
                                     "running officecli (禁止在 officecli 之后重新 import)",
            })
        roles = [{"kind": "data", "tr": first + i, "template_tr": None} for i in range(n)]
        return roles, [first]
    base = target.get("base_last_row")
    if not isinstance(base, int) or base < 0:
        fail("SPEC_BASE_ROW", f"target {target.get('sheet')} needs base_last_row",
             "Use the target digest's last existing row number")
    roles = []
    data_starts = []
    cursor = base
    for bi, b in enumerate(blocks):
        b_roles, data_start = compute_layout_block(b.get("clone_roles", []),
                                                   len(b.get("_rows", [])), cursor, bi)
        roles.extend(b_roles)
        data_starts.append(data_start)
        if data_start is not None:
            cursor = data_start + len(b.get("_rows", [])) - 1
    return roles, data_starts


# ── Selectors ──────────────────────────────────────────────────────────

def apply_selectors(rows: list[tuple[list[str], int]], rows_cfg: dict,
                    num_cols: int) -> list[tuple[list[str], int]]:
    """Filter source rows by the rows-config selectors (rows.source or an
    entry of rows.sources: {source, selectors})."""
    selectors = rows_cfg.get("selectors") or []
    if not selectors:
        return rows
    result = []
    for values, orig in rows:
        ok = True
        for sel in selectors:
            col = sel.get("column")
            if not (CELL_RE.match(col or "") and col_letter_to_idx(col) < num_cols):
                raise ValueError(f"selector column {col!r} out of range")
            v = values[col_letter_to_idx(col)] if col_letter_to_idx(col) < len(values) else ""
            pattern = sel.get("pattern")
            if pattern and not fnmatch.fnmatch(v, pattern):
                ok = False
                break
            np = sel.get("not_pattern")
            if np and fnmatch.fnmatch(v, np):
                ok = False
                break
            nv = sel.get("not_value")
            if nv is not None and v == str(nv):
                ok = False
                break
        if ok:
            result.append((values, orig))
    return result


def is_header_text_row(cells: list) -> bool:
    """Is a flattened source row a header/title TEXT row (vs a data row)?

    Mechanical fact for HEADER_ROW_CONSIDERED_DATA (issue 02, Case 08 U1): a
    flattened sheet's top row is the source table's title/header — e.g.
    类别/产品类别/型号/... — and it is a *candidate data row*. When the
    fill's rows config has no selector (or the selector lets the first row
    through) that header row gets mapped into the data region.

    Detection is data-only and deterministic so the guard never needs the
    source meta: a header row is a run of text labels — >= 2 non-empty cells
    and NONE of them parses as a number. Real data rows of a fill almost
    always carry a quantity/money/SKU number; probe fixtures' first rows
    (家用/12K/Z001/1/2/3) therefore never trip it.
    """
    nonempty = [str(c).strip() for c in cells if str(c) and str(c).strip()]
    if len(nonempty) < 2:
        return False
    return all(_parse_number(c) is None for c in nonempty)


# ── Value materialization ──────────────────────────────────────────────

def _resolve_transform(tname: str, transforms: dict):
    """Resolve a transform name: custom transforms first, then built-ins
    round2/round4/... (numeric rounding) and trim (whitespace strip).
    None when unknown."""
    fn = transforms.get(tname)
    if fn is None and re.fullmatch(r"round\d+", tname):
        return (lambda v, n=int(tname[5:]): round_value(v, n))
    if fn is None and tname == "trim":
        # Built-in trim (ticket 06): leading/trailing whitespace strip — the
        # sanctioned Z-码 cleanup (MOD ID-001 要求去除首尾空白). Custom
        # transforms named "trim" (defined in mapping.transforms) win.
        return lambda v: str(v).strip()
    return fn


def materialize_values(rows: list[tuple[list[str], int]], target: dict,
                       num_cols: int, lookups: dict, transforms: dict,
                       defects: list,
                       lookup_stats: dict | None = None) -> list[dict]:
    """Per matched source row → {row_values: {target_col: value}, key_values}."""
    out = []
    for values, orig in rows:
        row_values = {}
        for col_map in target.get("columns", []):
            tcol = col_map.get("target")
            if not (CELL_RE.match(tcol or "")) or col_letter_to_idx(tcol) >= num_cols:
                defects.append({"code": "COL_TARGET_INVALID", "target": tcol,
                                "message": f"column target {tcol!r} invalid or beyond digest cols",
                                "corrective_action": "Correct the column mapping"})
                continue
            src = col_map.get("source")
            lookup = col_map.get("lookup")
            if "value" in col_map:
                v = str(col_map["value"])
                tnames = col_map.get("transforms") \
                    or ([col_map["transform"]] if col_map.get("transform") else [])
                if not isinstance(tnames, list):
                    tnames = [tnames]
                for tname in tnames:
                    fn = _resolve_transform(tname, transforms)
                    if fn is not None:
                        v = fn(v)
                row_values[tcol] = v
            elif lookup and src is None:
                # Lookup-only mapping: value comes from the lookup table alone.
                v = resolve_lookup(lookup, values, lookups, defects,
                                   lookup_stats, tcol)
                if v is not None:
                    row_values[tcol] = v
            elif isinstance(src, list):
                # Multi-column source: sum of numeric values (e.g. 其他费用 = 其它+运费)
                vals = []
                for s in src:
                    sidx = col_letter_to_idx(s)
                    if sidx >= len(values):
                        defects.append({"code": "COL_SOURCE_INVALID", "source": s,
                                        "message": f"source column {s!r} out of range",
                                        "corrective_action": "Correct the column mapping"})
                        continue
                    vals.append(values[sidx])
                total = 0.0
                for v in vals:
                    if v is None or not str(v).strip() or str(v).strip() == "-":
                        continue  # missing input counts as 0 (0-口径)
                    num = _parse_number(v)
                    if num is None:
                        defects.append({"code": "SUM_NON_NUMERIC", "value": v,
                                        "message": f"multi-column sum found non-numeric value {v!r}",
                                        "corrective_action": "Use a single-column mapping for text columns"})
                    else:
                        total += num
                row_values[tcol] = _fmt_number(total)
            elif src is not None:
                sidx = col_letter_to_idx(src)
                if sidx >= len(values):
                    defects.append({"code": "COL_SOURCE_INVALID", "source": src,
                                    "message": f"source column {src!r} out of range",
                                    "corrective_action": "Correct the column mapping"})
                    continue
                v = values[sidx]
                # fallback: primary source empty → use the fallback column's value
                # (e.g. Model prefers 工厂型号 D, falls back to 产品描述 B).
                fb = col_map.get("fallback")
                if fb and (v is None or not str(v).strip()):
                    fidx = col_letter_to_idx(fb)
                    if fidx < len(values):
                        v = values[fidx]
                tnames = col_map.get("transforms") \
                    or ([col_map["transform"]] if col_map.get("transform") else [])
                if not isinstance(tnames, list):
                    tnames = [tnames]
                for tname in tnames:
                    fn = _resolve_transform(tname, transforms)
                    if fn is None:
                        defects.append({"code": "TRANSFORM_UNKNOWN", "name": tname,
                                        "message": f"transform {tname!r} not defined",
                                        "corrective_action": "Define it in mapping.transforms "
                                                             "(or use the built-in round2/round4)"})
                        continue
                    v = fn(v)
                lookup = col_map.get("lookup")
                if lookup:
                    v = resolve_lookup(lookup, values, lookups, defects,
                                       lookup_stats, tcol)
                    if v is None:
                        continue
                row_values[tcol] = v
        out.append({"orig": orig, "values": row_values})
    return out


def estimate_rendered_width(num, numfmt=None):
    """估算数字在某列 numFmt 下的渲染字符数.

    Excel 列宽单位 ≈ 默认字体 (Calibri 11) 数字字符宽, 因此渲染字符数可
    与 meta.column_width 直接比较。无 numFmt (General / 纯文本格式) →
    原始字符串长度; 有 numFmt → 按格式形态估算: 整数位数 (含零填充) +
    千分位逗号 + 小数位 + 小数点 + 百分号/货币符号 + 括号 + 引号字面量 +
    指数后缀 (负号计入)。启发式偏保守 (高估安全方向: 高估只会导致编译
    建议 round4 — 文档首选; 低估才会放行执行期溢出); 不执行真实 Excel
    渲染 (机器可验证的确定性估算, 边界由契约测试固定, 见 FILLSPEC Q7)。
    日期等无数字占位符的格式按原始字符串长度计。
    """
    s = str(num).strip()
    if not numfmt or ("0" not in numfmt and "#" not in numfmt):
        return len(s)
    nf = str(numfmt).split(";")[0]
    fmt_int, _, fmt_dec = nf.partition(".")
    parens = 2 if "(" in nf and ")" in nf else 0
    body = s.lstrip("-")
    int_part, _, _dec = body.partition(".")
    int_digits = max(len(int_part), len(re.findall(r"0", fmt_int)))
    # 百分号/千分号格式 ×100/×1000 缩放 (按值实算整数位数, 确定且保守)
    if "%" in nf or "‰" in nf:
        try:
            int_digits = max(int_digits, len(str(int(float(body) * 100))))
        except (ValueError, OverflowError):
            int_digits += 2
    # 指数后缀先从格式中摘除再数小数位 (E+00 的 00 是后缀, 不是小数位)
    exp_m = re.search(r"[Ee][+-]\d+", nf)
    exponent = len(exp_m.group(0)) if exp_m else 0
    if exp_m:
        fmt_dec = re.sub(r"[Ee][+-]\d+", "", fmt_dec)
    dec_show = len(re.findall(r"[0?]", fmt_dec)) if fmt_dec else 0
    commas = (int_digits - 1) // 3 if "," in fmt_int and int_digits > 3 else 0
    # 引号字面量计数; 符号计数排除引号内 (防双计)
    literals = sum(len(q) for q in re.findall(r'"([^"]*)"', nf))
    nf_unquoted = re.sub(r'"[^"]*"', "", nf)
    symbols = len(re.findall(r"[%‰$€£¥￥]", nf_unquoted))
    sign = 0 if parens else (1 if s.startswith("-") else 0)
    return (sign + int_digits + commas + dec_show
            + (1 if dec_show > 0 else 0)
            + parens + literals + exponent + symbols)


def apply_precision_policy(target: dict, data_rows: list,
                           defects: list, warnings: list,
                           col_widths: dict | None = None,
                           col_numfmt: dict | None = None) -> None:
    """Compile-time precision policy for the recurring text-overflow repair.

    Direct column values with > 4 decimal places or > 12 significant digits
    (e.g. cost values like 168.715100569657) overflow the narrow numeric
    columns of the quote template at execution time.

    Policy (2026-08-10, from repeated overflow repairs):
      - floating-point long tail (> 4 decimals)  → AUTO-ROUND to 4 decimals
        in place and record a warning (the fix is deterministic and matches
        the documented round4 convention — burning an execute round to
        rediscover it is pure waste).
      - over-long integers (≤ 4 decimals, > 12 digits) → round4 cannot help:
        hard defect (use `precision: keep` only with deliberate column width).
      - a mapping `transform: roundN` or `precision: keep` exempts the column.
    `precision: keep` 的豁免以列宽实测背书为前提 (issue 04): prepare 采集
    meta.column_width 后, 本函数对 keep 列估算最宽渲染值并与列宽比较 —
    超出 → PRECISION_KEEP_NARROW_COLUMN (exit 3, corrective_action 改用
    round4); 列宽缺失 (旧 meta) → 豁免 + PRECISION_KEEP_WIDTH_UNVERIFIED
    警告 (编译器不靠 Agent 猜列宽, 不再执行期才发现 text overflow)."""
    col_widths = col_widths or {}
    col_numfmt = col_numfmt or {}
    by_col: dict[str, tuple[int, str]] = {}
    for dr in data_rows:
        for col, val in (dr.get("values") or {}).items():
            if val is None or not str(val).strip():
                continue
            s = str(val).strip()
            num = _parse_number(s)
            if num is None:
                continue
            decimals = len(s.split(".")[1]) if "." in s else 0
            digits = len(re.sub(r"[^0-9]", "", s))
            if decimals <= 4 and digits <= 12:
                continue
            if col not in by_col:
                by_col[col] = (1, s)
            else:
                count, sample = by_col[col]
                by_col[col] = (count + 1, sample)
    cols_by_entry = {c.get("target"): c for c in target.get("columns", [])}
    for col, (count, sample) in sorted(by_col.items()):
        mapping = cols_by_entry.get(col, {})
        transform = mapping.get("transform")
        if transform and re.fullmatch(r"round\d+", str(transform)):
            continue  # already rounded by a built-in transform
        if mapping.get("precision") == "keep":
            # keep 的机械前提是列宽实测背书: 估算该列最宽渲染值, 与模板列宽
            # 比较 — 不足 → 编译拒绝 (不再执行期才发现 text_overflow)。
            widths = [
                estimate_rendered_width(str(dr["values"][col]),
                                        col_numfmt.get(col))
                for dr in data_rows
                if (dr.get("values") or {}).get(col) is not None
                and str(dr["values"][col]).strip()
            ]
            max_w = max(widths, default=0)
            col_width = col_widths.get(col)
            if col_width is None:
                warnings.append({
                    "code": "PRECISION_KEEP_WIDTH_UNVERIFIED", "column": col,
                    "message": f"column {col} uses `precision: keep` but prepare "
                               f"did not measure the template column width — the "
                               f"value (sample {sample!r}) cannot be verified to "
                               f"fit; re-run prepare (flatten) to collect "
                               f"meta.column_width",
                    "corrective_action": "Re-run workspace_init.py --init --flatten to "
                                         "measure column widths, or prefer "
                                         "`transform: round4`",
                })
            elif max_w > col_width:
                defects.append({
                    "code": "PRECISION_KEEP_NARROW_COLUMN", "column": col,
                    "message": f"column {col} is {col_width} chars wide but the "
                               f"longest `precision: keep` value renders ~{max_w} "
                               f"chars (sample {sample!r}) — it will overflow at "
                               f"execution; the measured width backing for keep "
                               f"is insufficient",
                    "corrective_action": "Use `transform: round4` (or widen the "
                                         "column) — `precision: keep` needs "
                                         "measured column width backing",
                })
            continue
        decimals = len(sample.split(".")[1]) if "." in sample else 0
        if decimals > 4:
            # auto-round in place (floating long tail — deterministic fix)
            rounded_total = 0
            for dr in data_rows:
                v = (dr.get("values") or {}).get(col)
                if v is None or not str(v).strip():
                    continue
                rv = round_value(str(v).strip(), 4)
                if rv != str(v).strip():
                    dr["values"][col] = rv
                    rounded_total += 1
            warnings.append({
                "code": "AUTO_ROUND4", "column": col,
                "message": f"{rounded_total} value(s) in column {col} rounded to 4 "
                           f"decimals (sample {sample!r} → {round_value(sample, 4)!r}) — "
                           "15-digit cost values overflow the template's narrow "
                           "columns; the mapping was NOT modified, values were "
                           "rounded in place",
                "corrective_action": "Review the mapping summary; add `transform: round4` "
                                     "to the column mapping to make it explicit",
            })
        else:
            defects.append({
                "code": "NUMERIC_OVERFLOW_RISK", "column": col,
                "message": f"{count} direct value(s) in column {col} have over-long "
                           f"integer precision (sample {sample!r}, >12 digits) — "
                           "rounding cannot shorten integers",
                "corrective_action": "Use `precision: keep` only if the target "
                                     "column is wide enough, or split the value",
            })


# ── Lookup key_column guard (issue 04) ─────────────────────────────────

def _lookup_key_column_reason(kcol) -> str:
    """Classify an invalid lookup key_column (issue 04).

    Acceptance set is FROZEN to the col_letter_to_idx line semantics: an
    Excel column letter = 1-2 ASCII letters, case-insensitive (CELL_RE on
    the upper-cased value — no new regex, no ^[A-Z]{1,3}$ drift). Anything
    else — logical field names like 'sku', whitespace-padded values,
    digits, length 0/>2 — is invalid_format; a well-formed letter beyond
    the consumer source's width is out_of_range (caller decides). Falsy
    values never reach this (no key_column keeps the historical path)."""
    if not isinstance(kcol, str) or not CELL_RE.fullmatch(kcol.upper()):
        return "invalid_format"
    return "out_of_range"


def _lookup_key_column_corrective(kcol, reason: str,
                                  width: int | None) -> str:
    """corrective_action per reason (issue 04 wording, value/range inlined)."""
    if reason == "invalid_format":
        return (f"key_column={kcol!r} is invalid: expected an Excel column "
                "letter present in the flattened source (e.g. G), not a "
                "logical field/key name.")
    return (f"key_column={kcol!r} is out of range: flattened source has "
            f"columns A:{col_idx_to_letter(width - 1)}. Choose an existing "
            "source column.")


def validate_lookup_key_columns(blocks_cfg: list, spec_mapping: dict,
                                target_cfg: dict, manifest_flat: dict,
                                workdir: Path, num_cols: int,
                                shared_root: Path | None = None) -> list:
    """Static guard for lookup key_column declarations (issue 04).

    Binding semantics (verified against resolve_lookup's only callers, both
    in materialize_values): table-level lookups[] are NOT auto-applied to
    every block/source — a lookup is consumed only by columns that declare
    lookup: {name: ...}. The effective key_column is the column's own value
    falling back to the table's (resolve_lookup:
    lookup.get("key_column") or tbl.get("key_column")), and the range
    baseline is the CONSUMER source(s): the rows sources of the blocks
    owning the referencing columns. A lookup referenced by no column never
    reaches resolve_lookup, so it is not checked (no consumption, no crash;
    checking it anyway would reject legal specs on unconsumed widths).

    Single defect code LOOKUP_KEY_COLUMN_INVALID; reason splits
    invalid_format / out_of_range (no new taxonomy). Sources whose name is
    missing from the manifest or whose csv is unreadable skip the range
    check — the existing SPEC_SOURCE_CSV path reports them (no duplicate
    alarm)."""
    tables = {lk.get("name"): lk.get("key_column")
              for lk in (list(spec_mapping.get("lookups", []))
                         + list(target_cfg.get("lookups", [])))}
    defects: list = []
    seen: set = set()
    widths: dict = {}

    def _source_width(src_name):
        """Max row-cell count of the flattened source csv (L2489 semantics:
        max(len(cells) ...)); None when unknown/unreadable (skip, don't
        guess — SPEC_SOURCE_CSV owns missing sources)."""
        if src_name not in widths:
            rows = None
            entry = manifest_flat.get(src_name)
            csv_name = entry.get("csv") if isinstance(entry, dict) else None
            if csv_name:
                try:
                    rows = load_csv_rows(
                        _flat_entry_path(entry, csv_name, workdir, shared_root))
                except (OSError, ValueError, csv.Error):
                    rows = None
            widths[src_name] = None if rows is None else max(
                (len(cells) for cells, _ in rows), default=num_cols)
        return widths[src_name]

    for bi, bcfg in enumerate(blocks_cfg):
        rows_cfg = bcfg.get("rows") or {}
        if rows_cfg.get("sources"):
            src_names = list(dict.fromkeys(
                s.get("source") for s in rows_cfg["sources"]))
        else:
            src_names = [rows_cfg.get("source")]
        for col_map in bcfg.get("columns", []):
            lookup = col_map.get("lookup")
            if not isinstance(lookup, dict) or not lookup.get("name"):
                continue
            name = lookup["name"]
            # Effective key_column — same precedence as resolve_lookup.
            kcol = lookup.get("key_column") or tables.get(name)
            if not kcol:
                continue  # falsy → 现状非 crash 路径, 冻结保持原样
            reason = _lookup_key_column_reason(kcol)
            if reason == "invalid_format":
                if (name, str(kcol)) in seen:
                    continue
                seen.add((name, str(kcol)))
                defects.append({
                    "code": "LOOKUP_KEY_COLUMN_INVALID", "reason": reason,
                    "lookup": name, "block": f"block[{bi}]",
                    "key_column": kcol,
                    "message": f"lookup {name!r}: key_column {kcol!r} is not "
                               "an Excel column letter (1-2 letters, "
                               "case-insensitive, e.g. G) — lookup keys are "
                               "read from the flattened source rows by "
                               "column letter",
                    "corrective_action": _lookup_key_column_corrective(
                        kcol, reason, None),
                })
                continue
            idx = col_letter_to_idx(kcol)
            for src_name in src_names:
                width = _source_width(src_name)
                if width is None or idx < width:
                    continue  # 源缺失/不可读 → SPEC_SOURCE_CSV; 宽度够 → 合法
                if (name, str(kcol), src_name) in seen:
                    continue
                seen.add((name, str(kcol), src_name))
                defects.append({
                    "code": "LOOKUP_KEY_COLUMN_INVALID", "reason": reason,
                    "lookup": name, "block": f"block[{bi}]",
                    "key_column": kcol, "source": src_name,
                    "message": f"lookup {name!r}: key_column {kcol!r} "
                               f"resolves to column index {idx} but consumer "
                               f"source {src_name!r} has {width} flattened "
                               f"column(s) (A:{col_idx_to_letter(width - 1)}) "
                               "— lookup keys are read from this source's rows",
                    "corrective_action": _lookup_key_column_corrective(
                        kcol, reason, width),
                })
    return defects


def resolve_lookup(lookup: dict, values: list, lookups: dict, defects: list,
                   stats: dict | None = None, tcol: str = "") -> str | None:
    """Resolve a lookup for one row. Returns the field value, '' on missing
    (when missing=empty), or None when a defect was recorded (caller skips)."""
    tbl = lookups.get(lookup["name"])
    if tbl is None:
        defects.append({"code": "LOOKUP_UNKNOWN", "name": lookup["name"],
                        "message": f"lookup {lookup['name']!r} not defined",
                        "corrective_action": "Define it in mapping.lookups"})
        return None
    field = lookup.get("field")
    kcol = lookup.get("key_column") or tbl.get("key_column")
    key = None
    if kcol:
        # issue 04 backstop: validate_lookup_key_columns intercepts invalid
        # key_columns before any plan work; nothing may reach the old bare
        # values[col_letter_to_idx(kcol)] IndexError even if a path slips
        # past the static validation — same defect code + reason, row skipped.
        idx = col_letter_to_idx(kcol) if isinstance(kcol, str) else -1
        if not (0 <= idx < len(values)):
            reason = _lookup_key_column_reason(kcol)
            defects.append({
                "code": "LOOKUP_KEY_COLUMN_INVALID", "reason": reason,
                "key_column": kcol,
                "message": f"lookup key_column {kcol!r} does not address a "
                           f"cell in this source row ({len(values)} column(s))",
                "corrective_action": _lookup_key_column_corrective(
                    kcol, reason, len(values)),
            })
            return None
        key = values[idx]
    norm_key = str(key).replace("\u00a0", " ").strip() if key is not None else None
    hit = tbl["data"].get(norm_key, {}) if norm_key is not None else {}
    if not hit:
        # Key miss: a hit-vs-miss is unambiguous here, so count before the
        # missing policy decides the outcome ("" vs LOOKUP_KEY_MISSING defect).
        if stats is not None:
            _note_lookup_outcome(stats, lookup["name"], tcol, miss=True)
        if lookup.get("missing") == "error":
            defects.append({"code": "LOOKUP_KEY_MISSING", "key": key,
                            "message": f"lookup key {key!r} not found in {lookup['name']}",
                            "corrective_action": "Record as a gap or fix the key"})
            return None
        return ""
    if field not in hit:
        # Field absent for this key. A schema-level absence (no entry in the
        # whole table carries the field) is always a defect; per-key absence
        # follows the missing policy (empty → blank, error → defect).
        schema_has = any(field in e for e in tbl["data"].values())
        if not schema_has or lookup.get("missing") == "error":
            defects.append({"code": "LOOKUP_FIELD_MISSING", "field": field,
                            "message": f"lookup {lookup['name']} has no field {field!r}"
                                       + ("" if schema_has else " (not in the index schema)"),
                            "corrective_action": "Check inheritance index fields"})
            return None
        if stats is not None:
            _note_lookup_outcome(stats, lookup["name"], tcol, miss=True)
        return ""
    if stats is not None:
        _note_lookup_outcome(stats, lookup["name"], tcol, miss=False)
    return hit.get(field, "")


def _note_lookup_outcome(stats: dict, name: str, tcol: str, miss: bool) -> None:
    """Count per-(lookup, column) resolutions for the all-missing guard.

    Recorded where the hit/miss semantics are known (inside resolve_lookup):
    a key that is found in the table counts as a hit even when its stored
    field value is empty; only actual misses (key/field absent) count against
    the column. Defect resolutions never reach this — they already failed."""
    cur = stats.setdefault((name, tcol), {"total": 0, "missing": 0})
    cur["total"] += 1
    if miss:
        cur["missing"] += 1


def note_lookup_all_missing(stats: dict, warnings: list) -> None:
    """Declared lookup columns that resolved to empty for EVERY row → warning.

    LOOKUP_COLUMN_ALL_MISSING (warn-only, compile proceeds): a non-empty index
    whose keys never hit may be a genuine absence (record as gaps — e.g. the
    Egypt FRESH 商用风管 SKU really is not in the index), a broken index, or a
    self-referencing index (the fill target sheet fed in as an index input —
    its historical rows are outputs, not field authority, and collide with
    independent data sheets into consensus conflicts); either way an entire
    column of silent blanks must not pass unremarked."""
    for (name, tcol), cur in sorted(stats.items()):
        if cur["total"] > 0 and cur["missing"] == cur["total"]:
            warnings.append({
                "code": "LOOKUP_COLUMN_ALL_MISSING", "lookup": name, "column": tcol,
                "message": f"lookup column {tcol} (lookup {name!r}) resolved to "
                           f"empty for ALL {cur['total']} row(s) — either the keys "
                           "are genuinely absent from the index (record them as "
                           "gaps), the index file is broken, or the index input "
                           "included the target sheet itself",
                "corrective_action": "Check the index file (field_consensus still "
                                     "present? rebuilt with "
                                     "build_inheritance_index.py?); check the "
                                     "index input sheets exclude the target sheet "
                                     "of this fill (a self-referencing index "
                                     "collides historical rows of the target "
                                     "sheet with independent data sheets into "
                                     "consensus conflicts, so keys go missing — "
                                     "build the index from independent data "
                                     "sheets only); if the keys really are "
                                     "absent, record them as gaps",
            })


def normalize_lookup_data(data: dict, name: str) -> dict:
    """Normalize known index formats to flat {key: {field: value}}.

    - build_inheritance_index output: {"index": {sku: {"field_consensus":
      {field: {"status": "unique|conflict", "value": v}}}}}
      → flat {sku: {field: v}}; non-unique consensus becomes absent (a
      lookup on it will fail exactly like a missing key).
    - plain flat {key: {field: value}} passes through.
    """
    if isinstance(data, dict) and isinstance(data.get("index"), dict):
        out = {}
        for key, entry in data["index"].items():
            consensus = entry.get("field_consensus", {}) if isinstance(entry, dict) else {}
            flat = {}
            for field, info in consensus.items():
                if isinstance(info, dict) and info.get("status") == "unique":
                    flat[field] = info.get("value", "")
            if flat:
                out[str(key)] = flat
        return out
    return data


def build_lookup_tables(spec_mapping: dict, target_cfg: dict, workdir: Path) -> dict:
    """Lookups may be declared at mapping level (shared) or per target."""
    entries = list(spec_mapping.get("lookups", [])) + list(target_cfg.get("lookups", []))
    tables = {}
    for lk in entries:
        p = workdir / lk["from"]
        if not p.is_file():
            fail("LOOKUP_FILE_MISSING", f"lookup source {lk['from']} not found",
                 "Run build_inheritance_index.py first and use its output path")
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except ValueError as e:
            fail("LOOKUP_INVALID", f"lookup source {lk['from']} not JSON: {e}",
                 "Fix the lookup file")
        normalized = normalize_lookup_data(data, lk["name"])
        if not normalized:
            # Egypt FRESH pitfall 1 (2026-08-13): a cleaning script rewrote
            # inheritance.json and dropped field_consensus → the table normalized
            # to 0 entries → every lookup silently resolved to "" (missing:
            # empty), D/F/X all blank, compile passed. Never silent again.
            fail("LOOKUP_TABLE_EMPTY",
                 f"lookup {lk['name']!r} ({lk['from']}) normalized to an EMPTY "
                 "table (0 entries) — every lookup on it would resolve to missing",
                 "Check the index file structure: does build_inheritance_index.py "
                 "output still carry field_consensus (was the JSON hand-rewritten "
                 "by a cleaning script)? Rebuild with build_inheritance_index.py "
                 "— never hand-edit the index JSON")
        tables[lk["name"]] = {"data": normalized, "key_column": lk.get("key_column")}
    return tables


def build_transforms(spec_mapping: dict, target_cfg: dict,
                     defects: list | None = None) -> dict:
    """Build the name→callable transform map from mapping.transforms +
    target.transforms, collecting STRUCTURED static defects for invalid
    transform DEFINITIONS (ticket 06: 转换函数定义的静态检查并入编译器 — 取代
    已退役的独立预检步骤, 不再有业务规则的二次解释层).

    Definition-side checks (referencing-side is TRANSFORM_UNKNOWN, emitted in
    materialize_values / _resolve_matrix_row):
      - name 缺失 → TRANSFORM_NAME_MISSING
      - function 未知 → TRANSFORM_FUNCTION_UNKNOWN
      - regex_replace: pattern 缺失/不可编译 → TRANSFORM_PATTERN_MISSING /
        TRANSFORM_PATTERN_INVALID; replacement 缺失/非字符串 →
        TRANSFORM_REPLACEMENT_MISSING
      - controlled_translation: translations 非 dict 或键/值形状坏 →
        TRANSFORM_TRANSLATIONS_INVALID

    Defects are appended to `defects` (caller fails after); a malformed entry
    contributes no callable so a later TRANSFORM_UNKNOWN also fires if it is
    referenced. When `defects` is None (legacy direct callers) a defected
    definition still yields no callable but does not raise — callers that care
    pass a list and fail on it."""
    fns = {}
    raw_entries = list(spec_mapping.get("transforms", [])) + \
        list(target_cfg.get("transforms", []))
    entries: list[tuple[dict, str]] = []
    if raw_entries and not isinstance(raw_entries, list):
        if defects is not None:
            defects.append({
                "code": "TRANSFORM_DEF_INVALID",
                "at": "mapping.transforms",
                "message": "mapping.transforms 必须是条目列表 (每个条目含 "
                           "name + function)",
                "corrective_action": "按 FILLSPEC transforms 契约写条目列表",
            })
        return fns
    for i, tr in enumerate(raw_entries):
        entries.append((tr, f"mapping.transforms[{i}]"))
    for ti, tgt in enumerate(spec_mapping.get("targets", [])):
        if not isinstance(tgt, dict):
            continue
        ttransforms = tgt.get("transforms")
        if not isinstance(ttransforms, list):
            continue
        for i, tr in enumerate(ttransforms):
            entries.append((tr, f"mapping.targets[{ti}].transforms[{i}]"))

    for tr, at in entries:
        if not isinstance(tr, dict) or not isinstance(tr.get("name"), str) \
                or not tr.get("name"):
            if defects is not None:
                defects.append({
                    "code": "TRANSFORM_NAME_MISSING", "at": at,
                    "message": "transform 条目缺非空字符串 name (列映射/矩阵"
                               "按名引用)",
                    "corrective_action": "补 name 字段 (mapping.transforms 条目)",
                })
            continue
        name = tr["name"]
        fn = tr.get("function")
        if fn == "regex_replace":
            pattern = tr.get("pattern")
            if not isinstance(pattern, str) or not pattern:
                if defects is not None:
                    defects.append({
                        "code": "TRANSFORM_PATTERN_MISSING",
                        "at": f"{at}.pattern",
                        "message": f"regex_replace transform {name!r} 缺非空"
                                   f"字符串 pattern",
                        "corrective_action": "补 pattern (合法正则)",
                    })
            else:
                try:
                    re.compile(pattern)
                except re.error as e:
                    if defects is not None:
                        defects.append({
                            "code": "TRANSFORM_PATTERN_INVALID",
                            "at": f"{at}.pattern",
                            "message": f"regex_replace transform {name!r} 的 "
                                       f"pattern 不可编译: {e}",
                            "corrective_action": "修正正则 (re.compile 通过后再"
                                                 "编译)",
                        })
            repl = tr.get("replacement")
            if repl is None:
                if defects is not None:
                    defects.append({
                        "code": "TRANSFORM_REPLACEMENT_MISSING",
                        "at": f"{at}.replacement",
                        "message": f"regex_replace transform {name!r} 缺 "
                                   f"replacement",
                        "corrective_action": "补 replacement (空串可合法表示"
                                             "删除匹配)",
                    })
                repl = ""
            if not isinstance(repl, str):
                if defects is not None:
                    defects.append({
                        "code": "TRANSFORM_REPLACEMENT_MISSING",
                        "at": f"{at}.replacement",
                        "message": f"regex_replace transform {name!r} 的 "
                                   f"replacement 必须是字符串",
                        "corrective_action": "把 replacement 写成字符串",
                    })
                repl = str(repl)
            # 只有 pattern 可编译才产出可执行 callable；否则定义坏, 引用处
            # 触发 TRANSFORM_UNKNOWN (fail-closed)。
            if isinstance(pattern, str) and pattern:
                try:
                    re.compile(pattern)
                except re.error:
                    continue
            else:
                continue
            def _rr(v, _p=pattern, _r=repl):
                return re.sub(_p, _r, v)
            fns[name] = _rr
        elif fn == "strip":
            def _st(v):
                return str(v).strip()
            fns[name] = _st
        elif fn == "controlled_translation":
            # Ticket 06: exact-match value translation (宽片→wide fin、
            # Heating pump→Cooling and Heating). Deterministic: unmatched
            # values pass through unchanged — agents must trim BEFORE
            # translation when the vocabulary keys carry no whitespace.
            translations = tr.get("translations")
            if not isinstance(translations, dict):
                if defects is not None:
                    defects.append({
                        "code": "TRANSFORM_TRANSLATIONS_INVALID",
                        "at": f"{at}.translations",
                        "message": f"controlled_translation transform {name!r} "
                                   f"的 translations 必须是 键→值 映射 (dict)",
                        "corrective_action": "写 键: 值 词表 (键=源值、值=目标值)",
                    })
                translations = {}
            else:
                for key, value in translations.items():
                    if not isinstance(key, str) or not key:
                        if defects is not None:
                            defects.append({
                                "code": "TRANSFORM_TRANSLATIONS_INVALID",
                                "at": f"{at}.translations",
                                "message": f"controlled_translation {name!r} 词表"
                                           f"键必须是非空字符串 (got {key!r})",
                                "corrective_action": "删除空键或补键",
                            })
                    if not isinstance(value, (str, int, float)) \
                            or isinstance(value, bool):
                        if defects is not None:
                            defects.append({
                                "code": "TRANSFORM_TRANSLATIONS_INVALID",
                                "at": f"{at}.translations",
                                "message": f"controlled_translation {name!r} 词表"
                                           f"键 {key!r} 的值必须是字符串/数值标量",
                                "corrective_action": "把值写成字符串或数值标量",
                            })
            def _ct(v, _t=translations):
                s = str(v)
                return _t.get(s, s)
            fns[name] = _ct
        else:
            if defects is not None:
                defects.append({
                    "code": "TRANSFORM_FUNCTION_UNKNOWN", "at": at,
                    "message": f"transform {name!r} 的 function {fn!r} 非法 "
                               f"— 允许 strip / regex_replace / "
                               f"controlled_translation",
                    "corrective_action": "改用 controlled_translation / "
                                         "regex_replace / strip (内置 trim/"
                                         "round2/round4 按名直接用)",
                })
    return fns


def round_value(value: str, decimals: int) -> str:
    """Round a numeric string to `decimals` places; non-numeric passes through.

    Built-in `round2`/`round4` transforms: the recurring text-overflow repair
    (15-digit cost values like 168.715100569657 written into narrow columns)
    is eliminated at compile time instead of failing at execution."""
    num = _parse_number(value)
    if num is None:
        return value
    if decimals <= 0:
        return str(int(round(num)))
    s = f"{round(num, decimals):.{decimals}f}".rstrip("0").rstrip(".")
    return s if s not in ("", "-") else "0"


# ── Operation generation (xlsx) ────────────────────────────────────────

def compute_groups(values: list) -> list[tuple[int, int]]:
    """Groups = consecutive equal-value runs (1-based inclusive rel rows).

    Groups come from the materialized data, not from
    `1:{n}`. Singleton runs are groups too (they never merge, but they own
    their anchor cell)."""
    groups: list[tuple[int, int]] = []
    i = 0
    n = len(values)
    while i < n:
        j = i
        while j + 1 < n and values[j + 1] == values[i]:
            j += 1
        groups.append((i + 1, j + 1))
        i = j + 1
    return groups


def split_group_aggregates(ga_spec: object, defects: list | None = None,
                           where: str = "") -> tuple[list, bool]:
    """Normalize `formulas.group_aggregates` → (per_group entries, whole_run?).

    Shapes accepted:
      list — canonical per-group entries `[{group_by, col, formula, style}]`;
             an entry carrying the key `whole_run` marks the cross-block total
             declaration (validated in the static validation phase).
      dict — `{per_group: [...], whole_run: {...}}` (spec draft shape);
             per_group entries are lowered, whole_run is validated.

    Malformed shapes (per_group not a list / entry not a mapping) are skipped
    so lowering never crashes; when `defects` is given (static phase), each is
    reported as GROUP_AGGREGATES_INVALID instead of being silently absorbed."""
    if ga_spec is None:
        return [], False
    if isinstance(ga_spec, dict):
        per = ga_spec.get("per_group") or []
        if "per_group" in ga_spec and not isinstance(per, list):
            _ga_shape_defect(defects, where,
                             "per_group must be a list of per-group entries")
            per = []
        return per, ga_spec.get("whole_run") is not None
    if isinstance(ga_spec, list):
        per: list = []
        whole = False
        for entry in ga_spec:
            if not isinstance(entry, dict):
                _ga_shape_defect(defects, where,
                                 f"entry {entry!r} must be a mapping "
                                 "({group_by, col, formula, style})")
                continue
            if "whole_run" in entry:
                whole = True
            else:
                per.append(entry)
        return per, whole
    _ga_shape_defect(defects, where,
                     f"group_aggregates must be a list or a dict, got "
                     f"{type(ga_spec).__name__}")
    return [], False


def _ga_shape_defect(defects: list | None, where: str, detail: str) -> None:
    if defects is None:
        return
    defects.append({
        "code": "GROUP_AGGREGATES_INVALID", "where": where or "group_aggregates",
        "message": f"{where or 'group_aggregates'}: {detail}",
        "corrective_action": "Write formulas.group_aggregates as a list of "
                             "{group_by, col, formula, style} entries (or a "
                             "{per_group: [...], whole_run: {...}} dict)"})


PROPS_WHITELIST = ("numberformat",)


def validate_props(props: dict, where: str, defects: list) -> None:
    """Props whitelist (V1 = numberformat only)."""
    if not props:
        return
    for k in props:
        if k not in PROPS_WHITELIST:
            defects.append({"code": "PROPS_WHITELIST_VIOLATION",
                            "where": where, "prop": k,
                            "message": f"{where}: prop {k!r} is outside the V1 "
                                       f"whitelist {list(PROPS_WHITELIST)} — value "
                                       "semantics and presentation semantics are "
                                       "orthogonal; the whitelist prevents growth "
                                       "into a full style engine",
                            "corrective_action": f"Use only {list(PROPS_WHITELIST)}"})


def build_ops_xlsx(target: dict, blocks: list, roles: list, data_rows: list,
                   num_cols: int, style_defaults: dict,
                   defects: list, sheet_rows: int = 0) -> tuple[list, list, dict]:
    """Generate globally-ordered operations for one or more data blocks.

    Phase invariant: append blocks → sets → terminal inplace
    block's structural operations → inplace value writes. Excel's natural row
    shift relocates set cells; the Compiler only translates *readback* paths
    to final coordinates (ops keep template coordinates — they execute before
    the shift).

    blocks: [{cfg, data_start, count, inplace?}] — per-block
    clone_roles/columns/formulas/merges/group_merges/nulls/remove_rows;
    relative block rows resolve against the block's data_start."""
    sheet = target["sheet"]
    ops: list = []
    written: dict[str, str] = {}
    readback: list = []
    group_boundaries: list = []

    # Inplace context: uniform row shift for everything below the region
    # (append-zone rows + sets). Region rows are coordinate-stable.
    shift = 0
    region_lo = region_hi = None
    for b in blocks:
        ip = b.get("inplace")
        if ip:
            shift = b["count"] - ip["capacity"]
            region_lo = ip["start_row"]
            region_hi = ip["region_end"]
            break

    def cell_path(col: str, row: int) -> str:
        return f"/{sheet}/{col}{row}"

    def final_row(row: int) -> int:
        if region_hi is not None and row > region_hi:
            return row + shift
        return row

    def final_path(col: str, row: int) -> str:
        return cell_path(col, final_row(row))

    def register_path(path: str, kind: str, value: str | None) -> None:
        prev = written.get(path)
        if prev is not None:
            defects.append({"code": "DUPLICATE_TARGET_WRITE", "path": path,
                            "message": f"cell {path} written twice (first as {prev})",
                            "corrective_action": "Each target cell may be written by exactly one column mapping/null/formula/group/set"})
        written[path] = kind
        if kind == "value":
            readback.append({"path": path, "expect": value or "", "kind": "value"})
        elif kind == "empty":
            readback.append({"path": path, "expect": "EMPTY", "kind": "empty"})
        elif kind == "nonempty":
            readback.append({"path": path, "expect": "", "kind": "nonempty"})

    def register_with(final_path_fn):
        def reg(col: str, row: int, kind: str, value: str | None) -> None:
            register_path(final_path_fn(col, row), kind, value)
        return reg

    def register(col: str, row: int, kind: str, value: str | None) -> None:
        register_path(final_path(col, row), kind, value)

    def style_for(cfg: dict, style: str) -> dict:
        return dict(style_defaults[style],
                    **(cfg.get("styles") or {}).get(style, {}))

    append_blocks = [b for b in blocks if not b.get("inplace")]
    inplace_blocks = [b for b in blocks if b.get("inplace")]

    # ── 1. append-block adds (top-down). Inplace region rows already exist;
    #    overflow clones are deferred to phase 5 (after sets). Cell writes are
    #    NOT interleaved here — officecli corrupts row bookkeeping (duplicate
    #    rows) when a value write lands between row-adds.
    deferred_values: list[tuple[int, str]] = []
    for role in roles:
        if role.get("mode") == "inplace":
            continue
        if role.get("mode") == "overflow_clone":
            continue
        if role["kind"] == "spacer":
            ops.append({"command": "add", "parent": f"/{sheet}", "type": "row",
                        "props": {"cols": num_cols}})
        elif role["kind"] == "data":
            trow = role.get("template_row")
            row = role["row"]
            ops.append({"command": "add", "parent": f"/{sheet}", "type": "row",
                        "from": f"/{sheet}/row[{trow}]",
                        "after": f"/{sheet}/row[{row - 1}]"})
        else:
            row = role["row"]
            trow = role.get("template_row")
            ops.append({"command": "add", "parent": f"/{sheet}", "type": "row",
                        "from": f"/{sheet}/row[{trow}]",
                        "after": f"/{sheet}/row[{row - 1}]"})
            if role.get("value"):
                deferred_values.append((row, role["value"]))

    # ── 2. append-block removes (bottom-to-top)
    for b in append_blocks:
        for rn in sorted(b["cfg"].get("remove_rows", []), reverse=True):
            ops.append({"command": "remove", "path": f"/{sheet}/row[{rn}]"})

    # ── 3. append-block value writes (deferred + per-block)
    for row, value in deferred_values:
        ops.append({"command": "set", "path": cell_path("A", row),
                    "props": {"value": value}})
        register("A", row, "value", value)

    # ── 4. sets — absolute template-coordinate writes. Execute
    #    AFTER append blocks, BEFORE the inplace structural ops: Excel's row
    #    shift relocates them (readback paths are translated to final rows).
    set_records = _emit_sets(target, region_lo, region_hi, final_path,
                             ops, register_path, defects, sheet_rows, num_cols,
                             cell_path)

    # ── 5. inplace block's structural operations: overflow clone adds
    #    (top-down), then trim removes (bottom-up). Mutually exclusive
    #    directions of the same count comparison.
    trim_count = 0
    for role in roles:
        if role.get("mode") == "overflow_clone":
            trow = role.get("template_row")
            row = role["row"]
            ops.append({"command": "add", "parent": f"/{sheet}", "type": "row",
                        "from": f"/{sheet}/row[{trow}]",
                        "after": f"/{sheet}/row[{row - 1}]"})
    for b in inplace_blocks:
        n, cap, start = b["count"], b["inplace"]["capacity"], b["inplace"]["start_row"]
        for rn in inplace_trim_rows(n, cap, start):
            trim_count += 1
            ops.append({"command": "remove", "path": f"/{sheet}/row[{rn}]"})

    # ── 6. inplace value writes (per block: merge-clear → group_merges →
    #    merges → fills → aggregates). Inplace rows are coordinate-stable:
    #    they register at template==final coordinates, NOT shifted.
    data_cursor = 0
    for b in append_blocks + inplace_blocks:
        if b.get("inplace"):
            blk_register = register_with(cell_path)
            blk_final = lambda r: r  # noqa: E731 — region/clone rows are final
        else:
            blk_register = register
            blk_final = final_row
        _emit_block_ops(b, data_rows, data_cursor, num_cols, style_for,
                        cell_path, blk_register, group_boundaries, defects,
                        blk_final, sheet, ops)
        data_cursor += b["count"]

    return ops, readback, written, group_boundaries, trim_count, set_records


def _emit_sets(target: dict, region_lo: int, region_hi: int, final_path,
               ops: list, register_path, defects: list, sheet_rows: int,
               num_cols: int, cell_path=None) -> list:
    """Absolute cell writes (target-level `sets`). Template coordinates;
    `value: null` = explicit clear; props whitelist = numberformat (V1).

    Ops target the TEMPLATE coordinate (sets execute BEFORE the inplace
    structural shift); the plan/readback records use FINAL coordinates —
    Excel's natural row shift relocates the written cell.

    register_path(path, kind, value) — the caller's registration callback
    (xlsx translates to final coordinates via final_path; pptx DOM paths
    register as-is). Returns plan records (final paths) for mapping.md."""
    sheet = target["sheet"]
    records = []
    for s in target.get("sets") or []:
        path = str(s.get("path") or "")
        props = s.get("props") or {}
        validate_props(props, f"sets[{path}]", defects)
        value = s.get("value")
        m = re.fullmatch(r"([A-Z]{1,2})(\d+)", path)
        if not m:
            m = re.fullmatch(rf"/{re.escape(sheet)}/([A-Z]{{1,2}})(\d+)", path)
        if not m:
            m = re.fullmatch(r".*/tr\[(\d+)\]/tc\[(\d+)\]", path)  # pptx DOM cell
            if not m:
                defects.append({"code": "SET_OUT_OF_BOUNDS", "path": path,
                                "message": f"sets.path {path!r} is not a bare cell "
                                           "coordinate (xlsx), a /Sheet/A1 path, or a "
                                           "full DOM cell path (pptx)",
                                "corrective_action": "Use e.g. 'A4' or "
                                                     "'/slide[1]/table[@id=1]/tr[2]/tc[3]'"})
                continue
            row, tc_idx = int(m.group(1)), int(m.group(2))
            if row > sheet_rows or tc_idx > num_cols:
                defects.append({"code": "SET_OUT_OF_BOUNDS", "path": path,
                                "message": f"sets.path {path} (tr {row}, tc {tc_idx}) "
                                           f"exceeds table dimensions {sheet_rows}×{num_cols}",
                                "corrective_action": "Pick an existing table cell path"})
                continue
            ops.append({"command": "set", "path": path,
                        "props": {"text": value if value is not None else ""}})
            register_path(path, "value" if value is not None else "empty", value)
            records.append({"path": path, "value": value,
                            "numberformat": props.get("numberformat")})
            continue
        col, row = m.group(1), int(m.group(2))
        if row > sheet_rows:
            defects.append({"code": "SET_OUT_OF_BOUNDS", "path": path,
                            "message": f"sets.path {path} row {row} exceeds digest "
                                       f"rows {sheet_rows} — sets target existing "
                                       "template coordinates only",
                            "corrective_action": "Pick an existing template row"})
            continue
        if region_lo is not None and region_lo <= row <= region_hi:
            defects.append({"code": "INPLACE_REGION_OVERLAP", "path": path,
                            "message": f"sets.path {path} falls inside the placeholder "
                                       f"region {region_lo}..{region_hi} — sets must not "
                                       "target the region (inplace fills own those rows)",
                            "corrective_action": "Express region content as column "
                                                 "mappings / group labels instead"})
            continue
        set_props = {"value": value if value is not None else None}
        if value is not None and props.get("numberformat"):
            set_props["numberformat"] = props["numberformat"]
        if cell_path is None:
            cell_path = final_path
        ops.append({"command": "set", "path": cell_path(col, row), "props": set_props})
        fpath = final_path(col, row)
        register_path(fpath, "value" if value is not None else "empty", value)
        records.append({"path": fpath, "value": value,
                        "numberformat": props.get("numberformat")})
    return records


# ── Matrix FillSpec (ticket 06 + ticket 01 locator V2): ────────────────
#    field_map × record_map ───────────────────────────────────────────────

# v1 唯一支持的轴朝向: 源与目标同构的 field_axis=rows / record_axis=columns
# (canonical matrix shape — 参数行 × 产品列)。任何其它朝向 → 编译缺陷
# MATRIX_ORIENTATION_NOT_ROLLED_OUT, 不静默忽略。
MATRIX_AXES = {"field_axis": "rows", "record_axis": "columns"}
# matrix 目标禁止并存的块语义声明 (矩阵是格转移填充, 不是行/块布局 —
# base_last_row 也是块布局锚点, 对 matrix 无意义)。
MATRIX_BLOCK_SEMANTICS_KEYS = ("clone_roles", "rows", "columns", "formulas",
                               "merges", "group_merges", "nulls", "remove_rows",
                               "blocks", "base_last_row")

# field_map[].source/.target 每侧 locator 三形式 (ticket 01 V2):
#   A. legacy 单标签 string (back-compat, label 列机械查找)
#   B. {match: {列: 文本}} — 多列 AND trim 精确匹配 (推荐主路径)
#   C. {row: N, expect: {列: 文本}} — guarded explicit row (禁裸 row)
# 解析 fail-closed: 0 → MATRIX_FIELD_LOCATOR_NOT_FOUND, 1 → resolve,
# >1 → MATRIX_FIELD_LOCATOR_AMBIGUOUS (替换旧"首命中"; deterministic ≠
# unambiguous)。旧 MATRIX_FIELD_LABEL_NOT_FOUND 收敛进 LOCATOR_NOT_FOUND。

# ── Capability namespace (P1-01 fine-grained capability query) ──────────
# 单一事实源: `--capability <key>` 的应答由本结构派生 — 每条 key 的约束尽量由
# 既有常量/schema 派生 (MATRIX_ROLLOUT / MATRIX_AXES / locator 三形式与缺陷码族
# / transform 名; 来源注释在每个 key 下方), 无常量的 key 用 terse 约束串。
# `--capabilities` (PROBE_CASES 探针矩阵)、FILLSPEC 能力表、contract tests 与
# 本表共同验证同一份契约 — 不允许第二份手写真相。

def _capability_literal_fallback() -> dict:
    """matrix.literal_fallback 状态直接由 MATRIX_ROLLOUT 活常量派生 (D8):
    翻转开关本 key 自动换态, 不维护第二份真相。当前 False → NOT_ROLLED_OUT
    (bulk source-derived literal sets 仍是编译警告)。"""
    fail_closed = MATRIX_ROLLOUT["literal_fallback_fail_closed"]
    return {
        "state": "SUPPORTED" if fail_closed else "NOT_ROLLED_OUT",
        "constraints": [
            ("fail-closed 已启用 (MATRIX_ROLLOUT.literal_fallback_fail_closed=True): "
             "大量 source-derived literal sets → BULK_SOURCE_DERIVED_LITERAL_FALLBACK "
             "(exit 3); 正确路径 = matrix 物化"
             if fail_closed else
             "fail-closed 未启用 (MATRIX_ROLLOUT.literal_fallback_fail_closed=False): "
             "大量 source-derived literal sets 烘焙成绝对坐标 → 编译警告 "
             "BULK_SOURCE_DERIVED_LITERAL_FALLBACK (warn-only, 不禁止)"),
            "审计条件: 值型 sets 条数 ≥ BULK_LITERAL_MIN_TOTAL 且 ≥ "
            "BULK_LITERAL_SOURCE_DERIVED_RATIO 的字面值出现在任一展平源 CSV 值池",
        ],
        "conflicts": [
            "bulk literal sets 绕开 grid 不是主填充路径 (matrix 物化优先; "
            "BULK_SOURCE_DERIVED_LITERAL_FALLBACK)",
        ],
        "reference": "FILLSPEC#矩阵映射-matrix",
    }


CAPABILITY_CONTRACT: dict[str, dict] = {
    "matrix": {
        "state": "SUPPORTED",
        # 派生: MATRIX_AXES (field_axis=rows / record_axis=columns) +
        # grid_record 形状 (SKILL §1.5); 非 (rows, columns) 朝向 → 编译缺陷
        # MATRIX_ORIENTATION_NOT_ROLLED_OUT (_matrix_axes_ok)。
        "constraints": [
            f"grid_record 的 column-record matrix: field_axis={MATRIX_AXES['field_axis']} "
            f"/ record_axis={MATRIX_AXES['record_axis']} (canonical matrix shape)",
            "二维映射用 mapping.targets[].matrix 一等表达 (field_map × record_map "
            "→ 目标格) + source lineage (plan.source_trace / source_trace.json)",
            "matrix 目标禁与 clone_roles/rows/columns/formulas/merges/group_merges/"
            "nulls/remove_rows/blocks/base_last_row 并存 → MATRIX_MIXED_WITH_BLOCK_SEMANTICS",
        ],
        "conflicts": [
            "其它轴朝向 → REJECTED (MATRIX_ORIENTATION_NOT_ROLLED_OUT)",
            "matrix + pptx 目标 → REJECTED (PPTX_CAPABILITY_NOT_ROLLED_OUT)",
        ],
        "reference": "FILLSPEC#矩阵映射-matrix",
    },
    "matrix.field_locator": {
        "state": "SUPPORTED",
        # 派生: ticket 01 locator V2 — _locator_structure_ok (三形式 +
        # MATRIX_FIELD_LOCATOR_INVALID / ROW_GUARD_REQUIRED) +
        # _resolve_matrix_row (0/1/>1 fail-closed + ROW_OUT_OF_RANGE /
        # ROW_GUARD_MISMATCH); 确定性 exact identity (D2/D7 边界)。
        "constraints": [
            "每侧 locator 三形式可混用: string (legacy 单标签) | "
            "{match: {列: 文本}} | {row: N, expect: {列: 文本}}",
            "匹配 = 去首尾空白后精确相等 (trim 后 exact identity), 多列 AND — "
            "确定性 only, 无 contains/fuzzy/regex/LLM/alias 推断",
            "0 命中 → MATRIX_FIELD_LOCATOR_NOT_FOUND; 1 → resolve; >1 → "
            "MATRIX_FIELD_LOCATOR_AMBIGUOUS (禁首命中, fail-closed)",
            "guard row: row 必配 expect (裸 row → MATRIX_FIELD_ROW_GUARD_REQUIRED); "
            "行号超界 → MATRIX_FIELD_ROW_OUT_OF_RANGE; 事实与 expect 不符 → "
            "MATRIX_FIELD_ROW_GUARD_MISMATCH",
            "match+row 并存 / 空 dict / 非字符串非 dict / 键非 Excel 列字母 → "
            "MATRIX_FIELD_LOCATOR_INVALID",
        ],
        "conflicts": [
            "duplicate label → REJECTED (MATRIX_FIELD_LOCATOR_AMBIGUOUS) — "
            "单标签重复必须消歧 (composite 加列 / row+expect 守卫)",
            "消歧不足的 match 命中 >1 行 → REJECTED (MATRIX_FIELD_LOCATOR_AMBIGUOUS)",
        ],
        "reference": "FILLSPEC#矩阵映射-matrix",
    },
    "matrix.record_map": {
        "state": "SUPPORTED",
        # 派生: record_map 条目 = {record, source_column, target_column}
        # (validate_matrix); 写集只落在 record_map 声明的 target_column;
        # 列字母非法/超宽 → MATRIX_COLUMN_INVALID (与 locator 列同规则),
        # 缺必要字段 → MATRIX_RECORD_MAP_INVALID。
        "constraints": [
            "record_map 条目 = {record, source_column, target_column}",
            "写集只落在 record_map 声明的 target_column (模板 schema 列 A/B/C 永不在写集)",
            "列字母非法或超出该侧展平宽 → MATRIX_COLUMN_INVALID (message 标 side)",
        ],
        "conflicts": [
            "条目缺必要字段 → MATRIX_RECORD_MAP_INVALID",
        ],
        "reference": "FILLSPEC#矩阵映射-matrix",
    },
    "matrix.transforms": {
        "state": "SUPPORTED",
        # 派生: 内置 trim / round2 / round4 (round_value) + 自定义函数经
        # mapping.transforms (build_transforms: regex_replace / strip /
        # controlled_translation); 未定义名 → TRANSFORM_UNKNOWN (_resolve_transform,
        # 与 columns[] 同求值路径)。
        "constraints": [
            "内置: trim (首尾空白剥离) / round2 / round4 (数值精度)",
            "自定义函数在 mapping.transforms 定义: controlled_translation (整值精确 "
            "匹配, 未命中原样通过) / regex_replace / strip",
            "transform 名未定义 → TRANSFORM_UNKNOWN; 矩阵与列映射共享同一求值路径",
        ],
        "conflicts": [
            "controlled_translation 前若词表键无空白须先 trim (顺序敏感, 确定性)",
        ],
        "reference": "FILLSPEC#矩阵映射-matrix",
    },
    "matrix.literal_fallback": _capability_literal_fallback(),
    "inplace": {
        "state": "SUPPORTED",
        # 派生: validate_inplace_declaration / validate_inplace_geometry /
        # build_operations phase 5-6 — 每目标至多一个 inplace 块且必须终末;
        # 溢出 → 区后 overflow clone add + 编译器推导 Trim; sets 落占位区 → 拒绝
        # (INPLACE_REGION_OVERLAP / STRUCTURAL_OP_OUT_OF_ZONE 相关)。
        "constraints": [
            "mode: inplace 消费既有占位行 (不克隆追加); 每目标至多一个且必须终末块",
            "溢出 → 区后 overflow clone add + 编译器推导 Trim; 占位残留须显式处理",
            "pptx 不支持 mode: inplace (PPTX_CAPABILITY_NOT_ROLLED_OUT)",
        ],
        "conflicts": [
            "sets 落在 inplace 占位区 → 拒绝 (区行归 inplace 块所有)",
        ],
        "reference": "FILLSPEC#v25-row-layout-mode--inplace-占位区",
    },
    "inplace.placeholder_ownership": {
        "state": "SUPPORTED",
        # 派生: 终末 inplace 块拥有占位区行 — 前置块结构行/remove 打到占位区 →
        # INPLACE_REGION_OVERLAP; 前置块 remove_rows ≤ base_last_row →
        # STRUCTURAL_OP_OUT_OF_ZONE; 残留占位值验证 (validate_placeholder_residue)。
        "constraints": [
            "inplace 块拥有占位区行: 区内行操作由编译器推导 (Trim/overflow 克隆)",
            "前置块结构行/remove_rows 打到占位区 → INPLACE_REGION_OVERLAP",
            "前置块 remove_rows ≤ base_last_row → STRUCTURAL_OP_OUT_OF_ZONE",
            "保留/生成的占位残留值必须显式处理 (placeholder residue 验证)",
        ],
        "conflicts": [
            "用户手工行操作进占位区 → REJECTED (编译器 owns 区行)",
        ],
        "reference": "FILLSPEC#v25-row-layout-mode--inplace-占位区",
    },
}


# ── 转换函数命名空间 (ticket 03: 双命名空间反馈) ─────────────────────────
# `--capability <key>` 的第二命名空间 — 转换函数不是能力。查错命名空间时给出
# 「这是转换函数不是能力」的机器提示 + 最近可用能力键 (一次查询即终结, US 15)。
# 权威来源 = build_transforms (function ∈ strip/regex_replace/controlled_translation)
# + 内置 round2/round4/trim (_resolve_transform) — 与 FILLSPEC「columns → transforms」
# 同一份真相, 不新增第二份。
TRANSFORM_NAMESPACE: dict[str, dict] = {
    "round2": {
        "kind": "builtin",
        "what": "内置数值变换: 四舍五入到 2 位小数 (消除长精度成本值溢出)",
    },
    "round4": {
        "kind": "builtin",
        "what": "内置数值变换: 四舍五入到 4 位小数 (消除长精度成本值溢出)",
    },
    "trim": {
        "kind": "builtin",
        "what": "内置变换: 首尾空白剥离 (Z 码清理 / 词表键规整)",
    },
    "strip": {
        "kind": "custom_function",
        "what": "自定义函数 (mapping.transforms function: strip) — 首尾空白剥离",
    },
    "regex_replace": {
        "kind": "custom_function",
        "what": "自定义函数 (mapping.transforms function: regex_replace) — 子串替换",
    },
    "controlled_translation": {
        "kind": "custom_function",
        "what": "自定义函数 (mapping.transforms function: controlled_translation) — "
                "整值精确匹配翻译 (未命中原样通过)",
    },
}


def _nearest_keys(key: str, candidates: list, limit: int = 5) -> list:
    """difflib 最近可用键 (标准库, 无新依赖): 按近似度排序返回前 `limit` 个,
    精确命中排最前 (无精确命中时按相似度)。"""
    if key in candidates:
        return [key]
    scored = [(difflib.SequenceMatcher(None, key, c).ratio(), c) for c in candidates]
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [c for _, c in scored[:limit]]


def capability_namespaces() -> dict:
    """双命名空间清单 (ticket 03): 转换函数 vs 能力 — 一个显式分组结构,
    `--capabilities` 报告嵌入此结构。旧语义验证能力键已于 06 号票移除
    （业务规则的二次解释层退役）；task.assembly 键已于 07 号票移除
    （打包机制退役，多 run 交付 = 每 run 一个独立文件）。"""
    ckeys = sorted(CAPABILITY_CONTRACT)
    return {
        "transforms": [
            {"name": name, **TRANSFORM_NAMESPACE[name]}
            for name in sorted(TRANSFORM_NAMESPACE)
        ],
        "capabilities": [
            {"name": key, "state": CAPABILITY_CONTRACT[key]["state"],
             "reference": CAPABILITY_CONTRACT[key]["reference"]}
            for key in ckeys
        ],
    }


def _matrix_axes_ok(block: dict, side: str, defects: list) -> bool:
    if (block.get("field_axis"), block.get("record_axis")) != \
            (MATRIX_AXES["field_axis"], MATRIX_AXES["record_axis"]):
        defects.append({
            "code": "MATRIX_ORIENTATION_NOT_ROLLED_OUT", "side": side,
            "message": f"matrix.{side} axes ({block.get('field_axis')!r}, "
                       f"{block.get('record_axis')!r}) — v1 仅支持源与目标同构的 "
                       "field_axis=rows / record_axis=columns (canonical matrix shape)",
            "corrective_action": "源和目标都声明 field_axis: rows + record_axis: "
                                 "columns (identical orientation); 其它朝向未 rollout"})
        return False
    return True


def _matrix_transform_names(entry: dict) -> list:
    tnames = entry.get("transforms") or (
        [entry["transform"]] if entry.get("transform") else [])
    if not isinstance(tnames, list):
        tnames = [tnames]
    return tnames


def _locator_desc(loc) -> str:
    """Human-readable locator description for defect messages / mapping.md."""
    if isinstance(loc, str):
        return f"label {loc!r}"
    if isinstance(loc, dict) and isinstance(loc.get("match"), dict):
        pairs = ", ".join(f"{k}={v!r}" for k, v in loc["match"].items())
        return f"match {{{pairs}}}"
    if isinstance(loc, dict) and "row" in loc:
        return f"guard row {loc.get('row')!r}"
    return f"locator {loc!r}"


def _locator_candidate_cells(loc, rows: list, label_col: int, side: str,
                             topn: int = 5) -> list:
    """ticket 03: locator 未命中时的候选格清单 — difflib 在该侧标签列里找与
    查询标签最接近的已有标签 (纠拼写/措辞), 返回 {side, orig, label, meaning},
    不替 Agent 决定选哪个。查询目标是 label 文本时用其字面量; 否则用 locator 描述。"""
    want = ""
    if isinstance(loc, str):
        want = loc.strip()
    elif isinstance(loc, dict) and isinstance(loc.get("match"), dict):
        first = next(iter(loc["match"].values()), "")
        want = str(first).strip()
    if not want:
        return []
    labels = []
    for values, orig in rows:
        if label_col < len(values):
            text = str(values[label_col]).strip()
            if text:
                labels.append((text, orig))
    unique = {}
    for text, orig in labels:
        unique.setdefault(text, orig)
    ranked = sorted(
        ((difflib.SequenceMatcher(None, want, t).ratio(), t, o)
         for t, o in unique.items()),
        key=lambda t: (-t[0], t[1]))
    return [{"side": side, "orig": orig, "label": text,
             "meaning": "最近标签候选 (核对拼写/措辞后改 locator)"}
            for _, text, orig in ranked[:topn]]


def _locator_structure_ok(loc, side: str, fi: int, defects: list) -> bool:
    """Structural validation of one field_map side locator (ticket 01 V2).

    Accepts: non-empty string | {match: {列: 文本}} | {row: N, expect: {列: 文本}}.
    Invalid shapes (empty {} / match+row 并存 / 非字符串非 dict / 非法列字母 /
    裸 row 缺 expect) → 结构化缺陷; match/expect 列字母的超宽检查在
    _locator_width_ok (与 record_map 列同规则)。"""
    if isinstance(loc, str):
        if loc.strip():
            return True
        defects.append({"code": "MATRIX_FIELD_LOCATOR_INVALID", "side": side,
                        "index": fi, "locator": loc,
                        "message": f"matrix.field_map[{fi}].{side} locator 是空字符串 — "
                                   "locator 必须是非空 string | {match: {...}} | "
                                   "{row: N, expect: {...}}",
                        "corrective_action": "写真实标签或结构化 locator 三形式之一"})
        return False
    if not isinstance(loc, dict):
        defects.append({"code": "MATRIX_FIELD_LOCATOR_INVALID", "side": side,
                        "index": fi, "locator": loc,
                        "message": f"matrix.field_map[{fi}].{side} locator {loc!r} 既不是 "
                                   "非空字符串也不是 dict",
                        "corrective_action": "使用 string | {match} | {row, expect} 三形式"})
        return False
    if "match" in loc and "row" in loc:
        defects.append({"code": "MATRIX_FIELD_LOCATOR_INVALID", "side": side,
                        "index": fi, "locator": loc,
                        "message": f"matrix.field_map[{fi}].{side} locator 同时声明 "
                                   "match 与 row — 每侧只能选一种定位形式",
                        "corrective_action": "去掉 match 或 row 之一"})
        return False
    if "match" in loc:
        m = loc["match"]
        if not isinstance(m, dict) or not m:
            defects.append({"code": "MATRIX_FIELD_LOCATOR_INVALID", "side": side,
                            "index": fi, "locator": loc,
                            "message": f"matrix.field_map[{fi}].{side} match 必须是非空 "
                                       "mapping {列: 文本}",
                            "corrective_action": "写 {match: {A: Cooling, B: Capacity, ...}}"})
            return False
        for col in m:
            if not (isinstance(col, str) and CELL_RE.match(col)):
                defects.append({"code": "MATRIX_FIELD_LOCATOR_INVALID", "side": side,
                                "index": fi, "column": col,
                                "message": f"matrix.field_map[{fi}].{side} match 键 {col!r} "
                                           "不是 Excel 列字母 (A..Z, 1-2 位)",
                                "corrective_action": "用 Excel 列字母作 match 键"})
                return False
        return True
    if "row" in loc:
        row_n = loc["row"]
        if isinstance(row_n, bool) or not isinstance(row_n, int):
            defects.append({"code": "MATRIX_FIELD_LOCATOR_INVALID", "side": side,
                            "index": fi, "locator": loc,
                            "message": f"matrix.field_map[{fi}].{side} row {row_n!r} "
                                       "必须是整数 (展平 CSV 的 orig 行号)",
                            "corrective_action": "row 用整数 orig 行号"})
            return False
        expect = loc.get("expect")
        if not isinstance(expect, dict) or not expect:
            defects.append({"code": "MATRIX_FIELD_ROW_GUARD_REQUIRED", "side": side,
                            "index": fi, "row": row_n,
                            "message": f"matrix.field_map[{fi}].{side} guard row {row_n} "
                                       "缺 expect 结构守卫 — 裸 row 禁止",
                            "corrective_action": "加 expect: {列: 文本} 结构守卫"})
            return False
        for col in expect:
            if not (isinstance(col, str) and CELL_RE.match(col)):
                defects.append({"code": "MATRIX_FIELD_LOCATOR_INVALID", "side": side,
                                "index": fi, "column": col,
                                "message": f"matrix.field_map[{fi}].{side} expect 键 {col!r} "
                                           "不是 Excel 列字母 (A..Z, 1-2 位)",
                                "corrective_action": "用 Excel 列字母作 expect 键"})
                return False
        return True
    defects.append({"code": "MATRIX_FIELD_LOCATOR_INVALID", "side": side,
                    "index": fi, "locator": loc,
                    "message": f"matrix.field_map[{fi}].{side} locator {loc!r} 为空 / "
                               "未知形态 — 必须是 string | {match} | {row, expect}",
                    "corrective_action": "使用三形式之一"})
    return False


def _locator_width_ok(loc, side: str, width: int, fi: int, defects: list) -> bool:
    """match/expect 列字母是否超出该侧展平 CSV 宽 (与 record_map 列同规则:
    超宽 → MATRIX_COLUMN_INVALID, message 标 side)。"""
    if not isinstance(loc, dict):
        return True
    cols = []
    if isinstance(loc.get("match"), dict):
        cols.extend(loc["match"])
    if isinstance(loc.get("expect"), dict):
        cols.extend(loc["expect"])
    ok = True
    for col in cols:
        if isinstance(col, str) and CELL_RE.match(col) \
                and col_letter_to_idx(col) >= width:
            defects.append({"code": "MATRIX_COLUMN_INVALID", "side": side,
                            "index": fi, "column": col,
                            "message": f"matrix.field_map[{fi}].{side} locator 列 {col!r} "
                                       f"beyond {side} width {width}",
                            "corrective_action": "用该侧真实存在的列字母"})
            ok = False
    return ok


def _resolve_matrix_row(loc, rows: list, label_col: int, side: str, fi: int,
                        defects: list) -> tuple | None:
    """Resolve one field_map side locator against its side's flattened rows
    (ticket 01 V2): label / composite match / guarded row — fail-closed
    0 → MATRIX_FIELD_LOCATOR_NOT_FOUND, 1 → resolve, >1 →
    MATRIX_FIELD_LOCATOR_AMBIGUOUS (首命中废止: deterministic ≠ unambiguous)。
    Guarded row 先行校验事实: 行号超界 → MATRIX_FIELD_ROW_OUT_OF_RANGE;
    expect 与真实行事实不符 → MATRIX_FIELD_ROW_GUARD_MISMATCH (message 逐列
    expected vs actual)。"""
    if isinstance(loc, str):
        want = loc.strip()
        hits = [(values, orig) for values, orig in rows
                if label_col < len(values)
                and str(values[label_col]).strip() == want]
    elif "match" in loc:
        conds = [(col_letter_to_idx(k), str(v).strip())
                 for k, v in loc["match"].items()]
        hits = []
        for values, orig in rows:
            if all(idx < len(values) and str(values[idx]).strip() == want
                   for idx, want in conds):
                hits.append((values, orig))
    else:  # guarded explicit row
        row_n = loc["row"]
        matched = [(values, orig) for values, orig in rows if orig == row_n]
        if not matched:
            defects.append({"code": "MATRIX_FIELD_ROW_OUT_OF_RANGE", "side": side,
                            "index": fi, "row": row_n,
                            "message": f"matrix.field_map[{fi}].{side} guard row "
                                       f"{row_n} 超出 {side} 展平行 (orig 行号集合)",
                            "corrective_action": "核对展平 CSV 的 orig 列行号"})
            return None
        values, orig = matched[0]
        for col, text in loc["expect"].items():
            idx = col_letter_to_idx(col)
            actual = str(values[idx]).strip() if idx < len(values) else ""
            if actual != str(text).strip():
                defects.append({"code": "MATRIX_FIELD_ROW_GUARD_MISMATCH",
                                "side": side, "index": fi, "row": row_n,
                                "message": f"matrix.field_map[{fi}].{side} guard row "
                                           f"{row_n} 事实不符: expect {col}={text!r} "
                                           f"实际 {col}={actual!r}",
                                "corrective_action": "把 row/expect 对准真实展平行"})
                return None
        return matched[0]
    if not hits:
        # ticket 03: 候选格清单 — difflib 找该侧标签列里与查询标签最接近的已有
        # 标签, 供 Agent 纠拼写/措辞 (不替 Agent 决定选哪个)。
        cands = _locator_candidate_cells(loc, rows, label_col, side)
        defects.append({"code": "MATRIX_FIELD_LOCATOR_NOT_FOUND", "side": side,
                        "index": fi, "locator": _locator_desc(loc),
                        "candidate_cells": cands,
                        "message": f"matrix.field_map[{fi}].{side} "
                                   f"{_locator_desc(loc)} 未在 {side} 展平 CSV 命中",
                        "corrective_action": "用该侧真实存在的标签/列文本组合"})
        return None
    if len(hits) > 1:
        defects.append({"code": "MATRIX_FIELD_LOCATOR_AMBIGUOUS", "side": side,
                        "index": fi, "locator": _locator_desc(loc),
                        "matches": len(hits),
                        "candidate_cells": [
                            {"side": side, "orig": orig,
                             "label": str(values[label_col]).strip()
                                      if label_col < len(values) else "",
                             "meaning": "命中候选行 (需加列消歧或换 row+expect 守卫)"}
                            for values, orig in hits
                        ],
                        "message": f"matrix.field_map[{fi}].{side} "
                                   f"{_locator_desc(loc)} 命中 {len(hits)} 行 — "
                                   "fail-closed, 首命中已废止",
                        "corrective_action": "加列消歧 (composite match 加列 / 换 "
                                             "row+expect 守卫)"})
        return None
    return hits[0]


def validate_matrix(matrix_cfg: dict, target_cfg: dict, num_cols: int,
                    manifest_flat: dict, manifest_target: dict, workdir: Path,
                    defects: list, shared_root: Path | None = None) -> dict | None:
    """Static validation of a target-level `matrix` declaration (ticket 06,
    locator grammar ticket 01 V2).

    Returns the normalized materialization context on success, or None when
    defects were emitted (the caller fails on them). Rules:
      - matrix must be {source, target, field_map, record_map}; source/target
        declare identical orientation field_axis=rows / record_axis=columns
        (any other orientation → MATRIX_ORIENTATION_NOT_ROLLED_OUT);
      - matrix.source.flatten references a manifest flattened entry name;
      - field_map[i] = {source: locator, target: locator, transforms?} with
        each side locator = string ({match: {列: 文本}} | {row, expect});
        record_map[i] = {record, source_column, target_column} — locators
        are resolved by mechanical lookup in each side's flattened CSV
        (fail-closed 0/1/>1, see _resolve_matrix_row); unknown labels /
        out-of-range columns are defects;
      - a matrix target must not declare block semantics (clone_roles/rows/
        columns/formulas/merges/group_merges/nulls/remove_rows/blocks) —
        fixed title/footer values belong in `sets`."""
    if not isinstance(matrix_cfg, dict):
        defects.append({
            "code": "MATRIX_INVALID",
            "message": "mapping.targets[].matrix must be a mapping "
                       "{source, target, field_map, record_map}",
            "corrective_action": "声明 matrix 按 references/FILLSPEC.md「矩阵映射 "
                                 "(matrix)」schema"})
        return None
    for key in ("source", "target", "field_map", "record_map"):
        if key not in matrix_cfg:
            defects.append({"code": "MATRIX_INVALID", "key": key,
                            "message": f"matrix missing required key: {key}",
                            "corrective_action": "Add the key per FILLSPEC.md matrix schema"})
        elif key in ("source", "target") and not isinstance(matrix_cfg[key], dict):
            defects.append({"code": "MATRIX_INVALID", "key": key,
                            "message": f"matrix.{key} must be a mapping with "
                                       f"field_axis + record_axis (+ flatten for source)",
                            "corrective_action": "Declare {field_axis: rows, "
                                                 "record_axis: columns} on both sides"})
    if defects:
        return None
    src_block, tgt_block = matrix_cfg["source"], matrix_cfg["target"]
    if not _matrix_axes_ok(src_block, "source", defects) \
            or not _matrix_axes_ok(tgt_block, "target", defects):
        return None
    mixed = [k for k in MATRIX_BLOCK_SEMANTICS_KEYS if target_cfg.get(k)]
    if mixed:
        defects.append({
            "code": "MATRIX_MIXED_WITH_BLOCK_SEMANTICS", "keys": mixed,
            "message": f"matrix 目标还声明了块语义: {mixed} — v1 matrix 是格转移"
                       "填充 (cell-transfer), 不是行/块布局",
            "corrective_action": "matrix 目标不声明 clone_roles/rows/columns/"
                                 "formulas/merges/group_merges/nulls/remove_rows/"
                                 "blocks; 固定 title/footer 值放进 sets"})
        return None
    fm, rm = matrix_cfg.get("field_map"), matrix_cfg.get("record_map")
    if not isinstance(fm, list) or not fm:
        defects.append({"code": "MATRIX_INVALID", "field": "field_map",
                        "message": "matrix.field_map must be a non-empty list of "
                                   "{source, target, transforms} entries",
                        "corrective_action": "列出字段角色映射 (源标签 → 目标标签)"})
        return None
    if not isinstance(rm, list) or not rm:
        defects.append({"code": "MATRIX_INVALID", "field": "record_map",
                        "message": "matrix.record_map must be a non-empty list of "
                                   "{record, source_column, target_column} entries",
                        "corrective_action": "列出记录列映射 (源列 → 目标列)"})
        return None
    for i, fe in enumerate(fm):
        if not isinstance(fe, dict) or "source" not in fe or "target" not in fe:
            defects.append({"code": "MATRIX_FIELD_MAP_INVALID", "index": i,
                            "entry": fe,
                            "message": f"matrix.field_map[{i}] must be a mapping with "
                                       "'source' and 'target' keys (each side a locator: "
                                       "string | {{match: ...}} | {{row, expect}})",
                            "corrective_action": "Fix the field_map entry per FILLSPEC.md"})
            continue
        _locator_structure_ok(fe.get("source"), "source", i, defects)
        _locator_structure_ok(fe.get("target"), "target", i, defects)
    for i, re_ in enumerate(rm):
        if not isinstance(re_, dict) or not str(re_.get("record") or "").strip():
            defects.append({"code": "MATRIX_RECORD_MAP_INVALID", "index": i,
                            "entry": re_,
                            "message": f"matrix.record_map[{i}] must be a mapping with "
                                       "a non-empty string 'record' name",
                            "corrective_action": "Fix the record_map entry per FILLSPEC.md"})
            continue
        for col_key in ("source_column", "target_column"):
            col = re_.get(col_key)
            if not (isinstance(col, str) and CELL_RE.match(col)):
                defects.append({"code": "MATRIX_RECORD_MAP_INVALID", "index": i,
                                "field": col_key, "value": col,
                                "message": f"matrix.record_map[{i}].{col_key} {col!r} "
                                           "is not an Excel column letter",
                                "corrective_action": "Use A..Z column letters"})
    if defects:
        return None
    src_name = src_block.get("flatten")
    src_entry = manifest_flat.get(src_name) if src_name else None
    if src_entry is None:
        defects.append({"code": "MATRIX_SOURCE_UNKNOWN", "source": src_name,
                        "message": f"matrix.source.flatten {src_name!r} is not a "
                                   "flattened source entry",
                        "corrective_action": "引用 manifest.flattened[].name (与 "
                                             "rows.source 同一个名字)"})
        return None
    try:
        src_rows = load_csv_rows(
            _flat_entry_path(src_entry, src_entry["csv"], workdir, shared_root))
        tgt_rows = load_csv_rows(
            _flat_entry_path(manifest_target, manifest_target["csv"],
                             workdir, shared_root))
    except OSError as e:
        defects.append({"code": "MATRIX_INVALID",
                        "message": f"matrix CSV unreadable: {e}",
                        "corrective_action": "Re-run workspace_init.py --init --flatten"})
        return None
    src_width = max((len(r[0]) for r in src_rows), default=0)
    src_label_col = col_letter_to_idx(src_block.get("field_label_column") or "A")
    tgt_label_col = col_letter_to_idx(tgt_block.get("field_label_column") or "A")
    if src_label_col >= src_width:
        defects.append({"code": "MATRIX_COLUMN_INVALID", "side": "source",
                        "kind": "field_label_column", "column": src_block.get("field_label_column"),
                        "message": f"matrix.source.field_label_column "
                                   f"{src_block.get('field_label_column')!r} beyond source "
                                   f"width {src_width}",
                        "corrective_action": "Use the label column letter of the source matrix"})
    if tgt_label_col >= num_cols:
        defects.append({"code": "MATRIX_COLUMN_INVALID", "side": "target",
                        "kind": "field_label_column", "column": tgt_block.get("field_label_column"),
                        "message": f"matrix.target.field_label_column "
                                   f"{tgt_block.get('field_label_column')!r} beyond target "
                                   f"width {num_cols}",
                        "corrective_action": "Use the label column letter of the target matrix"})
    for i, re_ in enumerate(rm):
        sc, tc = re_.get("source_column"), re_.get("target_column")
        if isinstance(sc, str) and CELL_RE.match(sc) and col_letter_to_idx(sc) >= src_width:
            defects.append({"code": "MATRIX_COLUMN_INVALID", "side": "source",
                            "index": i, "column": sc,
                            "message": f"matrix.record_map[{i}].source_column {sc!r} "
                                       f"beyond source width {src_width}",
                            "corrective_action": "Use a record column that exists in the "
                                                 "source matrix"})
        if isinstance(tc, str) and CELL_RE.match(tc) and col_letter_to_idx(tc) >= num_cols:
            defects.append({"code": "MATRIX_COLUMN_INVALID", "side": "target",
                            "index": i, "column": tc,
                            "message": f"matrix.record_map[{i}].target_column {tc!r} "
                                       f"beyond target width {num_cols}",
                            "corrective_action": "Use a record column that exists in the "
                                                 "target matrix"})
    for i, fe in enumerate(fm):
        if not isinstance(fe, dict):
            continue
        _locator_width_ok(fe.get("source"), "source", src_width, i, defects)
        _locator_width_ok(fe.get("target"), "target", num_cols, i, defects)
    if defects:
        return None
    return {
        "sheet": target_cfg["sheet"],
        "src_entry": src_entry,
        "src_rows": src_rows,
        "tgt_rows": tgt_rows,
        "src_label_col": src_label_col,
        "tgt_label_col": tgt_label_col,
        "field_map": fm,
        "record_map": rm,
    }


def materialize_matrix(ctx: dict, transforms: dict, defects: list,
                       src_name: str) -> tuple[list, list]:
    """field_map × record_map → (matrix_cells, source_lineage) (ticket 06;
    locator grammar ticket 01 V2).

    Every target cell is derived from its source cell through the transform
    chain; lineage entries carry {target, source, transform_chain} —
    `SPEC!D15 ← Sheet1!G42 [trim, controlled_translation]`. Readback
    expectations come from the SAME materialization (no hand-written checks).
    Each side locator resolves fail-closed via _resolve_matrix_row: 0 →
    NOT_FOUND, 1 → resolve, >1 → AMBIGUOUS (首命中废止); guarded rows get
    OUT_OF_RANGE / ROW_GUARD_MISMATCH on fact violations."""
    cells: list = []
    trace: list = []
    for fi, fe in enumerate(ctx["field_map"]):
        src_row = _resolve_matrix_row(fe.get("source"), ctx["src_rows"],
                                      ctx["src_label_col"], "source", fi,
                                      defects)
        if src_row is None:
            continue
        tgt_row = _resolve_matrix_row(fe.get("target"), ctx["tgt_rows"],
                                      ctx["tgt_label_col"], "target", fi,
                                      defects)
        if tgt_row is None:
            continue
        src_orig, tgt_orig = src_row[1], tgt_row[1]
        tnames = _matrix_transform_names(fe)
        for re_ in ctx["record_map"]:
            sc, tc = re_["source_column"], re_["target_column"]
            sidx = col_letter_to_idx(sc)
            if sidx >= len(src_row[0]):
                continue  # width already validated; guard for robustness
            raw = src_row[0][sidx]
            v = raw
            applied: list = []
            for tname in tnames:
                fn = _resolve_transform(tname, transforms)
                if fn is None:
                    defects.append({"code": "TRANSFORM_UNKNOWN", "name": tname,
                                    "message": f"matrix.field_map[{fi}] transform "
                                               f"{tname!r} not defined",
                                    "corrective_action": "Define it in mapping.transforms "
                                                         "(or use the built-in round2/"
                                                         "round4/trim)"})
                    continue
                v = fn(v)
                applied.append(tname)
            source_cell = f"{src_name}!{sc}{src_orig}"
            cells.append({
                "path": f"/{ctx['sheet']}/{tc}{tgt_orig}",
                "value": v,
                "source_cell": source_cell,
                "transform_chain": list(applied),
                "target_col": tc,
                "target_row": tgt_orig,
            })
            trace.append({"target": cells[-1]["path"],
                          "source": source_cell,
                          "transform_chain": list(applied)})
    return cells, trace


def _source_value_pool(manifest: dict, workdir: Path,
                       shared_root: Path | None = None) -> set:
    """All non-empty cell values of every flattened CSV except the target's
    own CSV — the mechanical pool the bulk-literal audit compares set values
    against (target template text is NOT source-derived)."""
    target_csv = manifest.get("target", {}).get("csv")
    pool: set = set()
    for e in manifest.get("flattened", []):
        if e.get("csv") == target_csv:
            continue
        try:
            rows = load_csv_rows(_flat_entry_path(e, e["csv"], workdir, shared_root))
        except (OSError, ValueError):
            continue
        for values, _orig in rows:
            for v in values:
                s = str(v).strip()
                if s:
                    pool.add(s)
    return pool


def audit_bulk_literal_fallback(target_cfg: dict, manifest: dict,
                                workdir: Path, warnings: list,
                                defects: list,
                                shared_root: Path | None = None) -> None:
    """Compile-audit: bulk source-derived literal `sets` (ticket 06 / D6).

    Mechanical heuristic (compile-audit ONLY — the spec's record-count-threshold
    ban applies to ROUTING; this detector never routes): when value-bearing
    `sets` entries whose literal value appears in the flattened SOURCE CSV
    value pool dominate the sets (≥ BULK_LITERAL_MIN_TOTAL value sets and
    ≥ BULK_LITERAL_SOURCE_DERIVED_RATIO share), emit warning
    BULK_SOURCE_DERIVED_LITERAL_FALLBACK — or fail closed (exit 3) when the
    Matrix rollout switch MATRIX_ROLLOUT['literal_fallback_fail_closed'] is ON.

    Legit `sets:` (客户名/日期/固定 title/显式 user override/fixed footer) use
    values NOT in the source pool → no warning. Matrix-materialized writes are
    NOT sets → a matrix-correct spec produces zero literal-fallback warnings
    (reverse guarantee)."""
    entries = [s for s in (target_cfg.get("sets") or [])
               if isinstance(s, dict) and "value" in s and s.get("value") is not None]
    if len(entries) < BULK_LITERAL_MIN_TOTAL:
        return
    pool = _source_value_pool(manifest, workdir, shared_root)
    derived = [s for s in entries if str(s.get("value")).strip() in pool]
    if not derived:
        return
    if len(derived) / len(entries) < BULK_LITERAL_SOURCE_DERIVED_RATIO:
        return
    entry = {
        "code": "BULK_SOURCE_DERIVED_LITERAL_FALLBACK",
        "derived": len(derived),
        "total": len(entries),
        "message": (f"{len(derived)}/{len(entries)} 条值型 `sets` 的字面值出现在展平"
                    "源 CSV 值池 — 大量 source-derived 表值被烘成绝对坐标 literal "
                    "sets 绕过 grid (source lineage 丢失, readback 全绿≠业务正确)"),
        "corrective_action": ("把 source-derived 内容写进 matrix (field_map × "
                              "record_map, Compiler 物化 + source lineage) 或 "
                              "columns/rows 映射; sets 只保留客户名/日期/固定 "
                              "title/显式 user override/fixed footer 类固定值"),
    }
    if MATRIX_ROLLOUT.get("literal_fallback_fail_closed"):
        defects.append(entry)
    else:
        warnings.append(entry)


def _emit_block_ops(b: dict, data_rows: list, data_cursor: int, num_cols: int,
                    style_for, cell_path, register, group_boundaries: list,
                    defects: list, final_row=None, sheet: str = "",
                    ops: list | None = None) -> None:
    """One block's value ops: merge-clear → group_merges → merges → fills →
    aggregates → group_aggregates. Shared by append blocks (phase 3) and the
    inplace block (phase 6); the phase ORDER is decided by the caller."""
    if ops is None:
        raise TypeError("_emit_block_ops requires the ops list")
    n = b["count"]
    cfg = b["cfg"]
    first_row = b["data_start"]
    blk_rows = data_rows[data_cursor:data_cursor + n]
    gm = cfg.get("group_merges", [])
    gm_by_col = {g.get("col"): g for g in gm}
    group_cols = set(gm_by_col)
    ga_entries, _ = split_group_aggregates(
        cfg.get("formulas", {}).get("group_aggregates"))
    # Columns that carry an aggregation anchor — the canonical correct shape
    # when such a column is wrongly put in group_merges is the same-scope
    # merges + aggregates pair (aggregation anchor = merge anchor, Q12).
    agg_anchor_cols = ({a.get("col") for a in cfg.get("formulas", {}).get("aggregates", [])}
                       | {g.get("col") for g in ga_entries})
    merge_cols = sorted({m.get("col") for m in cfg.get("merges", [])}
                        | agg_anchor_cols
                        | group_cols)

    # 1. merge-clears on every data row (per-block merge/group/agg columns) —
    #    breaks stale merges INCLUDING single-cell residue (A19:A19).
    for i in range(n):
        row = first_row + i
        for col in merge_cols:
            ops.append({"command": "set", "path": cell_path(col, row),
                        "props": {"merge": False}})

    # 2. group_merges lowering: groups from materialized
    #    group_by values → anchors written / non-anchors cleared → merges of
    #    length > 1 (singletons never merge).
    if final_row is None:
        final_row = lambda r: r  # noqa: E731 — unit-test fallback
    mapped_cols = {c.get("target") for c in cfg.get("columns", [])}
    v2_merge_cols = {m.get("col") for m in cfg.get("merges", [])}
    props_by_col = {c.get("target"): (c.get("props") or {})
                    for c in cfg.get("columns", [])}
    for col_map in cfg.get("columns", []):
        validate_props(col_map.get("props") or {},
                       f"columns[{col_map.get('target')}]", defects)
    for g in gm:
        col = g.get("col")
        gcol = g.get("group_by")
        if not gcol:
            defects.append({"code": "GROUP_MERGE_ANCHOR_UNCOVERED", "col": col,
                            "message": f"group_merges[{col}] needs group_by (a mapped "
                                       "target column whose materialized value groups rows)",
                            "corrective_action": "Declare group_by"})
            continue
        if gcol not in mapped_cols:
            defects.append({"code": "GROUP_BY_COLUMN_UNMAPPED", "col": gcol,
                            "message": f"group_by column {gcol} has no column mapping — "
                                       "groups need the column's logical materialized value",
                            "corrective_action": "Add a columns mapping for the "
                                                 "group_by column"})
            continue
        if col in v2_merge_cols:
            if col in agg_anchor_cols:
                corrective = ("该列承载聚合（聚合锚点 / 合并覆盖残留）: 删除其 "
                              "group_merges 条目, 改用同范围 merges + aggregates 对"
                              "（聚合锚点=合并锚点）")
            else:
                corrective = ("该列是普通标签列: 每列只保留一种合并模式 — 删除 "
                              "group_merges 或 merges 之一")
            defects.append({"code": "MERGE_MODE_CONFLICT", "col": col,
                            "message": f"column {col} appears in both merges and "
                                       "group_merges — the merge modes are mutually "
                                       "exclusive per column",
                            "corrective_action": corrective})
            continue
        groups = compute_groups([dr.get("values", {}).get(gcol, "")
                                 for dr in blk_rows])
        mapped = col in mapped_cols
        label = g.get("label")
        if not mapped and label is None:
            defects.append({"code": "GROUP_MERGE_ANCHOR_UNCOVERED", "col": col,
                            "message": f"group_merges column {col} has no column "
                                       "mapping and no label — the anchor cell would "
                                       "be uncovered",
                            "corrective_action": "Add a columns mapping or declare "
                                                 "label ('' = clear anchor)"})
            continue
        gstyle = style_for(cfg, g.get("style", "label"))
        if b.get("inplace"):
            # 锚点样式继承 (Case 010 盲区): 新组锚点落在旧非锚点格时补
            # 模板锚点字体/对齐; spec 显式 styles 声明的键优先, 不被覆盖。
            inh = (b.get("inherited_styles") or {}).get(col) or {}
            spec_styles = (cfg.get("styles") or {}).get(g.get("style", "label"), {})
            gstyle = {**gstyle,
                      **{k: v for k, v in inh.items() if k not in spec_styles}}
            # 钉住字面字体: 显式 font.name 落到原无字体格时 officecli 注入
            # scheme=minor 主题引用会把继承字体渲染成主题 minor 字体 (宋体)。
            gstyle = pin_font_scheme(gstyle)
        g_merges = []
        for (s, e) in groups:
            anchor_row = first_row + s - 1
            if e > s:
                g_merges.append(f"{col}{final_row(anchor_row)}:"
                                f"{col}{final_row(first_row + e - 1)}")
                props = {"merge": f"{col}{anchor_row}:{col}{first_row + e - 1}"}
                props.update(gstyle)
                ops.append({"command": "set", "path": cell_path(col, anchor_row),
                            "props": props})
            if mapped:
                val = blk_rows[s - 1].get("values", {}).get(col)
                if val is None:
                    ops.append({"command": "set", "path": cell_path(col, anchor_row),
                                "props": {"value": None}})
                    register(col, anchor_row, "empty", None)
                else:
                    props = {"value": val}
                    if props_by_col.get(col, {}).get("numberformat"):
                        props["numberformat"] = props_by_col[col]["numberformat"]
                    ops.append({"command": "set", "path": cell_path(col, anchor_row),
                                "props": props})
                    register(col, anchor_row, "value", val)
            elif label == "":
                ops.append({"command": "set", "path": cell_path(col, anchor_row),
                            "props": {"value": None}})
                register(col, anchor_row, "empty", None)
            else:
                props = {"value": label}
                if props_by_col.get(col, {}).get("numberformat"):
                    props["numberformat"] = props_by_col[col]["numberformat"]
                ops.append({"command": "set", "path": cell_path(col, anchor_row),
                            "props": props})
                register(col, anchor_row, "value", label)
            for r in range(s + 1, e + 1):
                ops.append({"command": "set",
                            "path": cell_path(col, first_row + r - 1),
                            "props": {"value": None}})
                register(col, first_row + r - 1, "empty", None)
        group_boundaries.append({
            "col": col, "sheet": sheet,
            "region_start": final_row(first_row),
            "region_end": final_row(first_row + n - 1),
            "expected_merges": g_merges,
        })

    # 3. merges + styles (V2 block-wide `1:{n}` ranges)
    b_styles = {
        "anchor": style_for(cfg, "anchor"),
        "label": style_for(cfg, "label"),
    }
    for m in cfg.get("merges", []):
        col = m.get("col")
        span = parse_rows_spec(m.get("rows", ""), n)
        if not span:
            defects.append({"code": "MERGE_RANGE_INVALID", "col": col,
                            "message": f"merge rows {m.get('rows')!r} invalid for {n} data rows",
                            "corrective_action": "Use '1:{n}' or an explicit range within the block"})
            continue
        r1, r2 = span
        props = {"merge": f"{col}{first_row + r1 - 1}:{col}{first_row + r2 - 1}"}
        props.update(b_styles.get(m.get("style", "label"), b_styles["label"]))
        if b.get("inplace"):
            # 锚点样式继承 (Case 010 盲区) — 同 group_merges 规则:
            # spec 显式 styles 优先, 继承值补默认。
            inh = (b.get("inherited_styles") or {}).get(col) or {}
            spec_styles = (cfg.get("styles") or {}).get(m.get("style", "label"), {})
            props.update({k: v for k, v in inh.items() if k not in spec_styles})
            # 同 group_merges: 钉住字面字体, 防 officecli 注入 scheme=minor。
            props.update(pin_font_scheme(props))
        ops.append({"command": "set", "path": cell_path(col, first_row + r1 - 1),
                    "props": props})

    # 4. fills: deferred role values + per-block values/nulls/per-row formulas
    null_specs = {x["col"]: x.get("rows") for x in cfg.get("nulls", [])}
    per_row = cfg.get("formulas", {}).get("per_row", {})
    for rel in range(1, n + 1):
        row = first_row + rel - 1
        dr = blk_rows[rel - 1]
        for col, val in dr.get("values", {}).items():
            if col in group_cols:
                continue  # anchors written by the group lowering
            props = {"value": val}
            if props_by_col.get(col, {}).get("numberformat"):
                props["numberformat"] = props_by_col[col]["numberformat"]
            ops.append({"command": "set", "path": cell_path(col, row),
                        "props": props})
            register(col, row, "value", val)
        for col, rows_spec in null_specs.items():
            rel_rows = parse_rel_rows(rows_spec)
            if rel_rows is None or rel in rel_rows:
                ops.append({"command": "set", "path": cell_path(col, row),
                            "props": {"value": None}})
                register(col, row, "empty", None)
        for col, tpl in per_row.items():
            formula = expand_template(tpl, {"r": row, "n": n})
            ops.append({"command": "set", "path": cell_path(col, row),
                        "props": {"formula": formula}})
            register(col, row, "nonempty", None)

    # 5. aggregates (per block)
    for a in cfg.get("formulas", {}).get("aggregates", []):
        col = a.get("col")
        span = parse_rows_spec(a.get("rows", ""), n)
        if not span:
            defects.append({"code": "AGG_RANGE_INVALID", "col": col,
                            "message": f"aggregate rows {a.get('rows')!r} invalid for {n} data rows",
                            "corrective_action": "Use '1:{n}' or an explicit range within the block"})
            continue
        r1, r2 = span
        props = {"formula": expand_template(a["formula"], {"r1": first_row + r1 - 1,
                                                           "r2": first_row + r2 - 1,
                                                           "n": n})}
        props.update(b_styles.get(a.get("style", "anchor"), b_styles["anchor"]))
        ops.append({"command": "set", "path": cell_path(col, first_row + r1 - 1),
                    "props": props})
        register(col, first_row + r1 - 1, "nonempty", None)

    # 6. group_aggregates: per-group formulas at group anchor rows.
    #    Groups come from the materialized group_by values (compute_groups);
    #    {r1}:{r2} expands per group start/end and must stay inside the block
    #    (AGG_RANGE_INVALID). Anchor cells register nonempty readback. The
    #    whole_run check fires in the static validation phase, before ops.
    for ga in ga_entries:
        col = ga.get("col")
        gcol = ga.get("group_by")
        if not gcol:
            defects.append({"code": "GROUP_BY_COLUMN_UNMAPPED", "col": col,
                            "message": f"group_aggregates[{col}] needs group_by — "
                                       "the mapped target column whose materialized "
                                       "values define the groups",
                            "corrective_action": "Declare group_by"})
            continue
        if gcol not in mapped_cols:
            defects.append({"code": "GROUP_BY_COLUMN_UNMAPPED", "col": gcol,
                            "message": f"group_aggregates group_by column {gcol} has no "
                                       "column mapping — groups need the column's "
                                       "logical materialized value",
                            "corrective_action": "Add a columns mapping for the "
                                                 "group_by column"})
            continue
        groups = compute_groups([dr.get("values", {}).get(gcol, "")
                                 for dr in blk_rows])
        gstyle = style_for(cfg, ga.get("style", "anchor"))
        for (s, e) in groups:
            if s < 1 or e > n:
                defects.append({"code": "AGG_RANGE_INVALID", "col": col,
                                "message": f"group_aggregates[{col}] group range "
                                           f"{s}:{e} crosses the data block boundary "
                                           f"(1:{n}) — group ranges must stay inside "
                                           "the block",
                                "corrective_action": "Check the group_by materialized "
                                                     "values and block selectors"})
                continue
            anchor_row = first_row + s - 1
            props = {"formula": expand_template(ga["formula"], {"r1": first_row + s - 1,
                                                                "r2": first_row + e - 1,
                                                                "n": n})}
            props.update(gstyle)
            ops.append({"command": "set", "path": cell_path(col, anchor_row),
                        "props": props})
            register(col, anchor_row, "nonempty", None)


def final_row_of(block_infos: list, row: int) -> int:
    """Plan coordinates (row_map/writes/blocks): region rows are stable,
    overflow clone rows are added at their final positions, and everything
    below the region shifts by (N - capacity)."""
    for b in block_infos:
        ip = b.get("inplace")
        if ip:
            lo, hi = ip["start_row"], ip["region_end"]
            n, cap = b["count"], ip["capacity"]
            if lo <= row <= hi:
                return row
            if hi < row <= hi + (n - cap):
                return row  # overflow clone position — already final
            if row > hi:
                return row + (n - cap)
            break
    return row


def spec_final_row_of(block_infos: list, row: int) -> int:
    """Spec coordinates are TEMPLATE coordinates (the spec
    never computes post-shift row numbers): every row below the region shifts.
    Overflow clone positions are never valid spec references."""
    for b in block_infos:
        ip = b.get("inplace")
        if ip:
            if row > ip["region_end"]:
                return row + (b["count"] - ip["capacity"])
            break
    return row


def build_ops_pptx(target: dict, roles: list, data_rows: list, num_cols: int,
                   defects: list) -> tuple[list, list, dict]:
    """pptx: value fills only; rows pre-created (python-pptx once, before officecli).

    FillSpec column targets are letters (A..Z) for cross-format consistency;
    they map to tr/tc indices here (A→tc[1])."""
    table = target["sheet"]  # e.g. "slide[3]/table[@id=2]"
    ops: list = []
    readback: list = []
    written: dict[str, str] = {}

    def register(path: str, kind: str, value: str | None) -> None:
        if path in written:
            defects.append({"code": "DUPLICATE_TARGET_WRITE", "path": path,
                            "message": f"cell {path} written twice",
                            "corrective_action": "Each target cell may be written once"})
        written[path] = kind
        if kind == "value":
            readback.append({"path": path, "expect": value or "", "kind": "value"})

    for role, dr in zip([r for r in roles if r["kind"] == "data"], data_rows):
        tr = role["tr"]
        for col, val in dr["values"].items():
            if not CELL_RE.match(col):
                defects.append({"code": "PPTX_TARGET_INVALID", "column": col,
                                "message": f"pptx target {col!r} is not a column letter",
                                "corrective_action": "Use A..Z column letters in columns mapping"})
                continue
            tcidx = col_letter_to_idx(col) + 1
            if tcidx > num_cols:
                defects.append({"code": "PPTX_TARGET_OUT_OF_BOUNDS", "column": col,
                                "message": f"column {col} (tc[{tcidx}]) beyond table width {num_cols}",
                                "corrective_action": "Check the target table's column count"})
                continue
            path = f"/{table}/tr[{tr}]/tc[{tcidx}]"
            # PPTX table cells expose `text` (not xlsx's `value` — officecli
            # rejects `value` on pptx cells: valid props are text/bold/...).
            ops.append({"command": "set", "path": path, "props": {"text": val}})
            register(path, "value", val)
    return ops, readback, written


def _pptx_register(path: str, kind: str, value: str | None, written: dict,
                   readback: list, defects: list) -> None:
    """Registration callback for pptx `sets` (DOM paths are final as-is)."""
    if path in written:
        defects.append({"code": "DUPLICATE_TARGET_WRITE", "path": path,
                        "message": f"cell {path} written twice",
                        "corrective_action": "Each target cell may be written once"})
    written[path] = kind
    if kind == "value":
        readback.append({"path": path, "expect": value or "", "kind": "value"})
    elif kind == "empty":
        readback.append({"path": path, "expect": "EMPTY", "kind": "empty"})


# ── Static validation ──────────────────────────────────────────────────

def validate_clone_residue(target: dict, template_row: int, target_csv: Path,
                           num_cols: int, columns: list, null_specs: dict,
                           per_row_formulas: dict, n_data_rows: int, defects: list,
                           group_merge_cols: set | None = None) -> None:
    """Template-row clone carries values; every carried column must be
    overwritten (columns/fills) or explicitly nulled for EVERY data row —
    a nulls entry covering only some rows leaves residue on the rest.

    group_merge columns are covered per-row by the lowering (anchors written,
    non-anchors explicitly cleared), so they are exempt wholesale."""
    rows = load_csv_rows(target_csv)
    carried = {}
    for values, orig in rows:
        if orig == template_row:
            for ci, v in enumerate(values):
                if v and v.strip() and ci < num_cols:
                    carried[col_idx_to_letter(ci)] = v.strip()
            break
    if not carried:
        return
    group_cols = group_merge_cols or set()
    filled_cols = {c.get("target") for c in columns}      # fills write every data row
    formula_cols = set(per_row_formulas)                  # per_row formulas write every row
    merge_cols = {m.get("col") for m in target.get("merges", [])}  # merge-clear+merge-set handles all rows
    all_rows = set(range(1, n_data_rows + 1))
    for c, val in carried.items():
        if c in filled_cols or c in formula_cols or c in merge_cols or c in group_cols:
            continue
        if c in null_specs:
            covered = parse_rel_rows(null_specs[c])       # None = all rows
            if covered is not None and covered != all_rows:
                uncovered = sorted(all_rows - covered)
                defects.append({"code": "CLONE_RESIDUE_PARTIAL_NULLS", "column": c,
                                "message": f"template row {template_row} carries {c}='{val}' "
                                           f"but nulls covers only rows {sorted(covered)} — "
                                           f"rows {uncovered} keep the cloned value",
                                "corrective_action": "Use rows: all, or add a column mapping "
                                                     f"that fills {c} on every row"})
            continue
        defects.append({"code": "CLONE_RESIDUE_UNHANDLED", "column": c,
                        "message": f"template row {template_row} carries {c}='{val}' "
                                   "into cloned rows but no fill, null, formula or merge covers it",
                        "corrective_action": "Add a column mapping or a nulls entry for column "
                                             f"{c} (or justify keeping it in decisions)"})


def validate_placeholder_residue(cfg: dict, start_row: int, capacity: int,
                                 n_rows: int, target_csv: Path, num_cols: int,
                                 data_rows: list, defects: list) -> None:
    """Double residue baseline — the retained Placeholder Region
    rows are checked against EACH row's OWN original values (not one template
    row). Coverage sources: per-row fills, nulls, per-row formulas, group
    anchors/labels — NOT sets (sets may not target the region).

    A retained row is any region row that survives trim:
    [start_row, start_row + min(n_rows, capacity))."""
    rows = load_csv_rows(target_csv)
    retained_hi = start_row + min(n_rows, capacity)
    carried_by_row: dict[int, dict[str, str]] = {}
    for values, orig in rows:
        if start_row <= orig < retained_hi:
            carried = {col_idx_to_letter(ci): v.strip()
                       for ci, v in enumerate(values)
                       if v and v.strip() and ci < num_cols}
            if carried:
                carried_by_row[orig] = carried
    if not carried_by_row:
        return
    filled_cols = {c.get("target") for c in cfg.get("columns", [])}
    formula_cols = set(cfg.get("formulas", {}).get("per_row", {}))
    group_cols = {g.get("col") for g in cfg.get("group_merges", [])}
    null_specs = {x["col"]: x.get("rows") for x in cfg.get("nulls", [])}
    for orig, carried in sorted(carried_by_row.items()):
        rel = orig - start_row + 1
        for c, val in carried.items():
            if c in filled_cols or c in formula_cols or c in group_cols:
                continue
            if c in null_specs:
                covered = parse_rel_rows(null_specs[c])   # None = all rows
                if covered is not None and rel not in covered:
                    defects.append({"code": "PLACEHOLDER_RESIDUE_PARTIAL_NULLS",
                                    "row": orig, "column": c, "value": val,
                                    "message": f"placeholder row {orig} carries {c}='{val}' "
                                               f"but nulls covers only rows {sorted(covered)} — "
                                               f"row {rel} keeps the placeholder value",
                                    "corrective_action": "Use rows: all, or add a column "
                                                         f"mapping that fills {c} on every row"})
                continue
            defects.append({"code": "PLACEHOLDER_RESIDUE_UNHANDLED",
                            "row": orig, "column": c, "value": val,
                            "message": f"retained placeholder row {orig} carries {c}='{val}' "
                                       "but no fill, null, formula or group anchor/label covers it",
                            "corrective_action": "Add a column mapping, a nulls entry, or a "
                                                 f"group_merges label for column {c}"})


def validate_formula_references(formulas: dict, n_rows: int, defects: list) -> None:
    per_row = formulas.get("per_row", {})
    for col, tpl in per_row.items():
        try:
            expand_template(tpl, {"r": 1, "n": n_rows})
        except ValueError as e:
            defects.append({"code": "FORMULA_TEMPLATE_INVALID", "col": col,
                            "message": str(e), "corrective_action": "Fix the formula template"})
    ga_entries, _ = split_group_aggregates(formulas.get("group_aggregates"))
    for ga in ga_entries:
        col = ga.get("col")
        tpl = ga.get("formula")
        if not isinstance(tpl, str):
            defects.append({"code": "FORMULA_TEMPLATE_INVALID", "col": col,
                            "message": f"group_aggregates[{col}] needs a formula "
                                       "template ({r1}:{r2} expand per group)",
                            "corrective_action": "Add the formula template"})
            continue
        try:
            expand_template(tpl, {"r1": 1, "r2": n_rows, "n": n_rows})
        except ValueError as e:
            defects.append({"code": "FORMULA_TEMPLATE_INVALID", "col": col,
                            "message": str(e), "corrective_action": "Fix the formula template"})


# ── Outputs ────────────────────────────────────────────────────────────

def inplace_trim_rows(count: int, capacity: int, start_row: int) -> list[int]:
    """Tail-trim row numbers for an inplace region (template coordinates,
    bottom-up). Single source shared by the op generator and the mechanical
    facts — both must produce the same trim."""
    if count >= capacity:
        return []
    return list(range(start_row + capacity - 1, start_row + count - 1, -1))


def derive_mechanical_facts(ops: list, target_cfg: dict, blocks_cfg: list,
                            block_infos: list) -> dict:
    """执行机械事实栏 — 从「执行顺序保证」契约派生, 非自由文本.

    execution_plan.json.mechanical_facts 与 mapping.md「执行机械事实」栏的唯一
    来源: removes 与 add 区关系 / 锚点链依赖 / shift 结论. 数值字段从已生成的
    ops 与布局机械计算; 契约常量 (op_order_invariant / bottom_up /
    gap_checked) 由 contract test 背书 — gap_checked 是 plan 存在性不变量
    (TEMPLATE_ROW_GAP 缺陷先于 plan 产出, exit 3)."""
    base = target_cfg.get("base_last_row", 0)
    append_remove_rows = sorted({
        rn for b in blocks_cfg if not inplace_roles(b)
        for rn in b.get("remove_rows", [])})
    # Phase-1 append adds form the leading run of ops; any later add is an
    # inplace overflow clone (phase 5, after sets).
    leading = 0
    while leading < len(ops) and ops[leading]["command"] == "add":
        leading += 1
    add_after_rows: list[int] = []
    add_from_rows: list[int] = []
    spacer_adds = 0
    append_insert_rows: list[int] = []
    overflow_insert_rows: list[int] = []
    for i, op in enumerate(ops):
        if op.get("command") != "add":
            continue
        m = re.search(r"/row\[(\d+)\]$", op.get("after", ""))
        if m:
            add_after_rows.append(int(m.group(1)))
        m = re.search(r"/row\[(\d+)\]$", op.get("from", ""))
        if m:
            add_from_rows.append(int(m.group(1)))
        if op.get("after"):
            (append_insert_rows if i < leading else overflow_insert_rows).append(
                int(re.search(r"/row\[(\d+)\]$", op["after"]).group(1)) + 1)
        else:
            spacer_adds += 1
    append_insert_rows = sorted(set(append_insert_rows))
    overflow_insert_rows = sorted(set(overflow_insert_rows))
    shift = 0
    region = None
    trim_rows: list[int] = []
    for b in block_infos:
        ip = b.get("inplace")
        if ip:
            shift = b["count"] - ip["capacity"]
            region = f"{ip['start_row']}-{ip['region_end']}"
            trim_rows = inplace_trim_rows(b["count"], ip["capacity"],
                                          ip["start_row"])
            break
    all_within_base = all(rn <= base for rn in append_remove_rows)
    # E1 两形态 (2026-08-13): 无 inplace → append-only 序列; inplace 混合 →
    # append 块全部操作 → sets → inplace 结构 → inplace 值写. 契约字符串
    # 与 FILLSPEC「执行顺序保证」E1 精确同源 (test 断言).
    if region is None:
        op_order_invariant = "clear → add → remove → merge → fill"
    else:
        op_order_invariant = ("append 块全部操作 → sets → 终末 inplace 块结构操作 "
                              "(overflow 克隆 add → trim remove) → inplace 值操作")
    return {
        "op_order_invariant": op_order_invariant,
        "base_last_row": base,
        "add_zone": {
            "append_insert_rows": append_insert_rows,
            "overflow_insert_rows": overflow_insert_rows,
            "spacer_adds": spacer_adds,
            "conclusion": ("全部 add 插入 base_last_row 之下 (append 区) — 不推移 "
                           "base 及以上的模板坐标" if region is None else
                           "append 区 add 插在 base_last_row 之下; overflow 克隆插在 "
                           "区末端之后 — 均不推移区内的模板坐标"),
        },
        "removes": {
            "rows": append_remove_rows,
            "all_within_base": all_within_base,
            "bottom_up": True,
            "conclusion": ("与 add 区无交互 — 全部 ≤ base_last_row, 模板坐标在 add "
                           "区之外, 不被 add 推移" if all_within_base else
                           "与 add 区交互 — 编译器已以 REMOVE_TARGETS_APPEND_ZONE "
                           "拒绝, 不会产出本 plan"),
        },
        "trim": {
            "present": bool(trim_rows),
            "rows": trim_rows,
            "conclusion": "尾部裁剪 (编译器推导), 自底向上" if trim_rows else "无",
        },
        "shift": {
            "present": region is not None,
            "region": region,
            "value": shift,
            "readback_translated": region is not None,
            "conclusion": ("无 inplace 区 → 无行移位; readback 坐标 == 模板坐标"
                           if region is None else
                           f"inplace 区 {region} → 区下所有行 (append 区/sets) "
                           f"最终坐标 = 模板坐标 {'+' if shift >= 0 else ''}{shift} "
                           "(trim 为负 / overflow 为正); readback 已翻译为最终坐标"),
        },
        "anchor_chain": {
            "after_rows": sorted(set(add_after_rows)),
            "from_rows": sorted(set(add_from_rows)),
            "gap_checked": True,
        },
    }

def render_mapping(spec: dict, plan: dict, manifest: dict) -> str:
    target = spec["mapping"]["targets"][0]
    lines = [
        "# Mapping report",
        "",
        "> Generated deterministically by compile_fill.py from fill_spec.yaml — "
        "the spec is the only business-semantics source. This report is derived; "
        "edit the spec, never this file.",
        "",
        f"- Intent: {spec['task'].get('intent', '')}",
        f"- MOD: {spec['task'].get('selected_mod') or 'NONE'}",
        f"- Target: {spec['inputs'].get('target')} / {spec['inputs'].get('target_sheet')}",
        "",
        "## 追溯表",
        "| Artifact | Basis |",
        "|---|---|",
        "| fill_spec.yaml | this report + execution_plan.json |",
        "| source data | flattened CSVs from workspace_init.py (staged inputs) |",
        "",
    ]
    if spec.get("decisions"):
        lines.append("## Decisions")
        lines.extend(f"- {d}" for d in spec["decisions"])
        lines.append("")
    if spec.get("lineage"):
        lines.append("## Lineage")
        lines.extend(f"- `{l.get('source')}` ({l.get('role')}): {l.get('note', '')}"
                     for l in spec["lineage"])
        lines.append("")

    lines.append("## Layout")
    lines.append("| Kind | Row(s) | Template row | Mode |")
    lines.append("|---|---:|---:|---|")
    for b in plan["blocks"]:
        kind = b["kind"]
        rows = str(b["row"]) if kind != "data" else f"{b['data_start']}-{b['data_end']}"
        tpl = str(b.get("template_row") or "")
        mode = b.get("mode") or ""
        lines.append(f"| {kind} | {rows} | {tpl} | {mode} |")
    lines.append("")

    mf = plan.get("mechanical_facts")
    if mf:
        lines.append("## 执行机械事实 (derived from the execution-order contract)")
        lines.append("")
        lines.append(f"- op 顺序不变量: `{mf['op_order_invariant']}` — 值写入不穿插 append 区 add (防 duplicate_row)")
        lines.append(f"- base_last_row: {mf['base_last_row']}")
        az = mf["add_zone"]
        lines.append(f"- add 区: append 插入行 {az['append_insert_rows'] or '∅'}"
                     + (f" + spacer×{az['spacer_adds']} (执行时追加到 sheet 末尾)"
                        if az["spacer_adds"] else "")
                     + (f" + overflow 克隆 {az['overflow_insert_rows']}"
                        if az["overflow_insert_rows"] else "")
                     + f" — {az['conclusion']}")
        rm = mf["removes"]
        lines.append(f"- removes: {rm['rows'] or '∅'}"
                     + (f" — {rm['conclusion']}" if rm["rows"] else " — 无 remove_rows"))
        tr = mf["trim"]
        lines.append(f"- trim: {tr['conclusion']}" + (f" (行 {tr['rows']})" if tr["rows"] else ""))
        sh = mf["shift"]
        lines.append(f"- shift: {sh['conclusion']}"
                     + (f" (值 {sh['value']:+d})" if sh["present"] else ""))
        ac = mf["anchor_chain"]
        lines.append(f"- 锚点链: add after 引用行 {ac['after_rows'] or '∅'}, "
                     f"clone from 引用行 {ac['from_rows'] or '∅'}"
                     + (" — TEMPLATE_ROW_GAP 检查已过 (plan 存在即已通过)" if ac["gap_checked"] else ""))
        lines.append("")

    cov = plan["source_coverage"]
    if isinstance(cov, dict):
        cov = [cov]
    lines.append(f"## Source coverage — {plan.get('source_csv')}")
    for c in cov:
        lines.append(f"- `{c.get('source')}`: {c.get('matched')}/{c.get('total')} "
                     f"rows matched" + (f" — required NOT consumed: {c['required_unmatched']}"
                                        if c.get("required_unmatched") else ""))
    lines.append("")
    lines.append("| Source | Source row | Target row |")
    lines.append("|---|---:|---:|")
    for src, orig, trow in plan["row_map"]:
        lines.append(f"| {src} | {orig} | {trow} |")
    lines.append("")

    lines.append("## Written values")
    lines.append("| Target row | Column | Value |")
    lines.append("|---|---:|---|")
    for w in plan["writes"]:
        lines.append(f"| {w['row']} | {w['col']} | {w['value']} |")
    if plan.get("matrix"):
        m = plan["matrix"]
        lines.append("")
        lines.append(f"## Matrix — {m['source']} → {m['target_sheet']} "
                     f"(field_axis={m['orientation']['field_axis']} / "
                     f"record_axis={m['orientation']['record_axis']})")
        lines.append("")
        lines.append("| Field (source → target) | Transform chain | Records |")
        lines.append("|---|---|---|")
        for fe in m["field_map"]:
            chain = ", ".join(fe["transform_chain"]) or "(direct copy)"
            src_disp = _locator_desc(fe["source"]) if isinstance(fe["source"], dict) \
                else fe["source"]
            tgt_disp = _locator_desc(fe["target"]) if isinstance(fe["target"], dict) \
                else fe["target"]
            lines.append(f"| `{src_disp}` → `{tgt_disp}` | {chain} | "
                         f"{len(m['record_map'])} |")
        lines.append("")
    if plan.get("source_trace"):
        lines.append("## Source lineage (matrix writes)")
        lines.append("| Target | Source | Transform chain |")
        lines.append("|---|---|---|")
        for e in plan["source_trace"]:
            chain = ", ".join(e["transform_chain"]) or "(direct copy)"
            lines.append(f"| `{e['target']}` | `{e['source']}` | {chain} |")
        lines.append("")
    if plan.get("empties"):
        lines.append("")
        lines.append("## Explicit empty cells (clone-residue nulls)")
        lines.append("| Cell |")
        lines.append("|---|")
        for p in plan["empties"]:
            lines.append(f"| `{p}` |")
    if plan.get("sets"):
        lines.append("## Absolute writes (sets)")
        lines.append("| Cell | Value | Numberformat |")
        lines.append("|---|---:|---|")
        for s in plan["sets"]:
            lines.append(f"| `{s['path']}` | {s['value']!r} | {s.get('numberformat') or ''} |")
        lines.append("")
    if plan.get("group_boundaries"):
        lines.append("## Group merges (readback boundaries)")
        for gb in plan["group_boundaries"]:
            lines.append(f"- Column `{gb['col']}` rows {gb['region_start']}-{gb['region_end']}: "
                         f"{', '.join(gb['expected_merges']) or '(singletons only)'}")
        lines.append("")
    lines.append(f"## Structural (final) — rows {plan.get('expected_final_row_count')}"
                 f" (deltas: {plan.get('structural_deltas')})")
    lines.append("")
    if target.get("merges"):
        lines.append("")
        lines.append("## Merges")
        lines.append("| Column | Rows | Style |")
        lines.append("|---|---:|---|")
        for m in target["merges"]:
            lines.append(f"| {m.get('col')} | {m.get('rows')} | {m.get('style', 'label')} |")
    if target.get("group_merges"):
        lines.append("")
        lines.append("## Group merges (spec)")
        lines.append("| Column | Group by | Label | Style |")
        lines.append("|---|---|---|---|")
        for g in target["group_merges"]:
            lines.append(f"| {g.get('col')} | {g.get('group_by')} | {g.get('label')!r} | "
                         f"{g.get('style', 'label')} |")
    if target.get("formulas"):
        lines.append("")
        lines.append("## Formulas")
        f = target["formulas"]
        for col, tpl in (f.get("per_row") or {}).items():
            lines.append(f"- Per row `{col}`: `{tpl}`")
        for a in f.get("aggregates") or []:
            lines.append(f"- Aggregate `{a.get('col')}` rows {a.get('rows')}: `{a.get('formula')}`")
    lines.append("")
    if spec.get("gaps"):
        lines.append("## Data gaps")
        lines.extend(f"- {g}" for g in spec["gaps"])
        lines.append("")
    lines.append("## Readback")
    lines.append("| Cell | Expect | Kind |")
    lines.append("|---|---:|---|")
    for rb in plan["readback"]:
        lines.append(f"| {rb['path']} | {rb['expect'] or '(non-empty)'} | {rb['kind']} |")
    lines.append("")
    if plan.get("warnings"):
        lines.append("## ⚠️ Warnings (Compiler 自动处理)")
        lines.append("")
        for w in plan["warnings"]:
            lines.append(f"- `{w.get('code')}` {w.get('column')}: {w.get('message')}")
            lines.append(f"  - 建议: {w.get('corrective_action')}")
        lines.append("")
    return "\n".join(lines) + "\n"


def compile_spec(spec: dict, manifest: dict, workdir: Path,
                  spec_path: Path | None = None,
                  mod_resolution_path: Path | None = None,
                  shared_root: Path | None = None) -> dict:
    defects: list = []
    defects += validate_schema(spec, manifest)
    if defects:
        fail("SPEC_INVALID", f"{len(defects)} spec defect(s)", "Fix fill_spec.yaml", defects)

    # MOD-consistency gates (C1–C4) — fail-closed BEFORE any fingerprint/structure
    # work. No legal compile path without a resolved decision record.
    # mod_resolution_path 可指向 task-level 一次裁决（ticket 08），缺省回退
    # workdir/mod_resolution.json（single-run）。
    check_mod_consistency(spec, workdir, mod_resolution_path)

    inputs = spec["inputs"]
    manifest_target = manifest["target"]
    if inputs["target_sheet"] != manifest_target["sheet"]:
        fail("SPEC_TARGET_SHEET_MISMATCH",
             f"target_sheet {inputs['target_sheet']!r} != flattened target {manifest_target['sheet']!r}",
             "Flatten the sheet you intend to fill")

    fp = spec["fingerprints"]
    mfp = manifest["fingerprints"]
    if fp.get("source_structure") != mfp.get("source_structure") or \
            fp.get("target_structure") != mfp.get("target_structure"):
        fail("FILLSPEC_FINGERPRINT_MISMATCH",
             "fill_spec fingerprints do not match prepare_manifest — the spec "
             "was written against a different structure",
             "Structure changed: re-run workspace_init.py --init + materialize_run.py, read the fresh digests, "
             "and update the spec. Fingerprints not yet filled: copy them from "
             "prepare_manifest.json (fingerprints.source_structure / "
             "target_structure), or generate a probe scaffold with "
             "scripts/make_probe_spec.py --workdir <dir>")

    platform = inputs.get("platform") or ("pptx" if inputs["target"].lower().endswith(".pptx") else "xlsx")
    target_cfg = spec["mapping"]["targets"][0]
    if target_cfg.get("sheet") != inputs["target_sheet"]:
        fail("SPEC_TARGET_ENTRY", "mapping.targets[0].sheet must equal inputs.target_sheet",
             "Fix the target entry")

    manifest_flat = {e["name"]: e for e in manifest["flattened"]}
    target_meta = json.loads(
        _flat_entry_path(manifest_target, manifest_target["meta"],
                         workdir, shared_root).read_text(encoding="utf-8"))
    dims = target_meta.get("dimensions", {})
    num_cols = dims.get("cols", 0)

    # ── Matrix FillSpec path (ticket 06) — cell-transfer fill WITHOUT row
    #    blocks. A matrix target materializes field_map × record_map into plan
    #    `set` ops (the SAME value-write shape the executor consumes) + source
    #    lineage; fixed title/footer values still ride `sets`. Matrix is v1
    #    xlsx-only (pptx cells use tr/tc paths, not /Sheet/ColRow).
    matrix_cfg = target_cfg.get("matrix")
    matrix_ctx = None
    matrix_cells: list = []
    source_trace: list = []
    warnings: list = []
    # 克隆源样式剖面 (xlsx 行布局块才计算; 见下方 CLONE_SOURCE_STYLE_MISMATCH)。
    # 在分支之前初始化: matrix / pptx 路径不经过该分支, 但仍会被 plan 组装读到。
    _style_prof = None
    if matrix_cfg is not None:
        if platform == "pptx":
            defects.append({"code": "PPTX_CAPABILITY_NOT_ROLLED_OUT",
                            "message": "matrix lowering (cell-transfer fills) 是 "
                                       "xlsx 能力 — pptx 表格是 tr/tc 文本格",
                            "corrective_action": "matrix 目标用 xlsx (或把值预计算进 "
                                                 "columns 映射)"})
        if spec["validation"].get("required_coverage"):
            defects.append({
                "code": "MATRIX_REQUIRED_COVERAGE_UNSUPPORTED",
                "message": "matrix 填充是格转移 (cell transfer), 不消费整源行 — "
                           "required_coverage (源行必须被消费) 对 matrix 无意义",
                "corrective_action": "matrix 目标不声明 required_coverage; 用 "
                                     "validation.key_outputs 采样目标格"})
        if defects:
            fail("STATIC_VALIDATION_FAILED", f"{len(defects)} static validation defect(s)",
                 "Fix the spec and re-run compile_fill.py", defects)
        matrix_ctx = validate_matrix(matrix_cfg, target_cfg, num_cols,
                                     manifest_flat, manifest_target, workdir,
                                     defects, shared_root)
        if defects:
            fail("STATIC_VALIDATION_FAILED", f"{len(defects)} static validation defect(s)",
                 "Fix the spec and re-run compile_fill.py", defects)
        transforms = build_transforms(spec["mapping"], target_cfg, defects)
        if defects:
            fail("STATIC_VALIDATION_FAILED", f"{len(defects)} static validation defect(s)",
                 "Fix the spec and re-run compile_fill.py", defects)
        matrix_cells, source_trace = materialize_matrix(
            matrix_ctx, transforms, defects, matrix_ctx["src_entry"]["name"])
        if defects:
            fail("MATERIALIZE_DEFECTS", f"{len(defects)} matrix materialization defect(s)",
                 "Fix matrix.field_map / matrix.record_map / transforms", defects)
        blocks_cfg = []
        block_infos = []
        data_rows = []
        matched_all = [matrix_ctx["src_entry"]]
        roles = []
        trim_count = 0
        group_boundaries = []
        lookups = {}
        ops, readback, written = [], [], {}

        def matrix_register(path, kind, value):
            prev = written.get(path)
            if prev is not None:
                defects.append({"code": "DUPLICATE_TARGET_WRITE", "path": path,
                                "message": f"cell {path} written twice (first as {prev})",
                                "corrective_action": "Each target cell may be written by "
                                                     "exactly one matrix field_map × record_map "
                                                     "pair or set entry"})
            written[path] = kind
            if kind == "value":
                readback.append({"path": path, "expect": value or "", "kind": "value"})
            elif kind == "empty":
                readback.append({"path": path, "expect": "EMPTY", "kind": "empty"})
            elif kind == "nonempty":
                readback.append({"path": path, "expect": "", "kind": "nonempty"})

        for c in matrix_cells:
            ops.append({"command": "set", "path": c["path"],
                        "props": {"value": c["value"]}})
            matrix_register(c["path"], "value", c["value"])
        sheet = target_cfg["sheet"]
        identity = lambda col, row: f"/{sheet}/{col}{row}"  # noqa: E731 — no row shift
        set_records = _emit_sets(target_cfg, None, None, identity, ops,
                                 matrix_register, defects,
                                 dims.get("rows", 0), num_cols, identity)
        if defects:
            fail("STATIC_VALIDATION_FAILED", f"{len(defects)} static validation defect(s)",
                 "Fix the spec and re-run compile_fill.py", defects)
    else:
        # Blocks — one implicit block (target-level config) or explicit blocks[].
        blocks_cfg = resolve_blocks(target_cfg)

        # Block top-level key allowlist (ID-1): misplaced/unknown keys
        # (aggregates/per_row/group_aggregates at the block top level, typos) used
        # to pass through resolve_blocks and get silently dropped by _emit_block_ops
        # (Case 05 U4/E4) — now a compile-time BLOCK_KEY_STRUCTURE_INVALID.
        defects += validate_block_top_level_keys(blocks_cfg)
        if defects:
            fail("STATIC_VALIDATION_FAILED", f"{len(defects)} static validation defect(s)",
                 "Fix the spec and re-run compile_fill.py", defects)

        # Inplace declaration invariants (fail before layout: a
        # malformed region must never reach coordinate arithmetic).
        ip_ctx = validate_inplace_declaration(blocks_cfg, dims, defects)
        if defects:
            fail("STATIC_VALIDATION_FAILED", f"{len(defects)} static validation defect(s)",
                 "Fix the spec and re-run compile_fill.py", defects)

        # Lookup key_column guard (issue 04): an invalid key_column (logical
        # field name like 'sku', or a column letter beyond the consumer source's
        # width) used to crash resolve_lookup with a bare IndexError. Intercept
        # BEFORE any plan work — single defect code LOOKUP_KEY_COLUMN_INVALID,
        # reason invalid_format / out_of_range (no new taxonomy).
        defects += validate_lookup_key_columns(
            blocks_cfg, spec["mapping"], target_cfg, manifest_flat, workdir,
            num_cols, shared_root)
        if defects:
            fail("STATIC_VALIDATION_FAILED", f"{len(defects)} static validation defect(s)",
                 "Fix the spec and re-run compile_fill.py", defects)

        # Per-block source matching + materialization (block rows configs).
        def match_block_sources(block_cfg: dict, label: str) -> list[dict]:
            rows_cfg = block_cfg.get("rows") or {}
            if rows_cfg.get("sources"):
                src_specs = [
                    {"source": s.get("source"), "selectors": s.get("selectors") or []}
                    for s in rows_cfg["sources"]
                ]
            else:
                src_specs = [{"source": rows_cfg.get("source"),
                              "selectors": rows_cfg.get("selectors") or []}]
            if any(not s["source"] for s in src_specs):
                fail("SPEC_SOURCE_CSV", f"{label}: every rows.sources entry needs a flattened source name",
                     "Reference flattened entry names from the manifest")
            out = []
            for src_spec in src_specs:
                src_name = src_spec["source"]
                src_entry = manifest_flat.get(src_name)
                if src_entry is None:
                    fail("SPEC_SOURCE_CSV", f"{label}: rows source {src_name!r} not among flattened sources",
                         "Reference the flattened entry name from the manifest (e.g. the "
                         "name field of a flattened sheet, not the csv filename)")
                src_rows = load_csv_rows(
                    _flat_entry_path(src_entry, src_entry["csv"], workdir, shared_root))
                # Selectors match SOURCE rows — validate their column letters
                # against the SOURCE's own width, not the target's (the MXP case:
                # 27-col source into a 6-col target made L selectors fail).
                src_width = max((len(r[0]) for r in src_rows), default=num_cols)
                try:
                    matched = apply_selectors(src_rows, src_spec, src_width)
                except ValueError as e:
                    fail("SELECTOR_INVALID", f"{label}/{src_name}: {e}", "Fix the row selectors")
                if not matched:
                    fail("NO_MATCHED_ROWS", f"{label}: selectors matched zero rows in {src_name}",
                         "Fix selectors or check the source flatten")
                # issue 02 / Case 08 U1: 展平 CSV 首行（表头）是候选数据行 — rows
                # 无 selector（或 selector 未排除）且首行是表头文本行时, 表头会被
                # 映射进数据区 (失败语义不变, 记 warnings)。corrective_action 指向
                # pattern/not_pattern 排除表头行。
                if src_rows and is_header_text_row(src_rows[0][0]):
                    first_orig = src_rows[0][1]
                    if any(o == first_orig for _, o in matched):
                        first_label = next((str(c) for c in src_rows[0][0]
                                            if str(c).strip()), "")
                        warnings.append({
                            "code": "HEADER_ROW_CONSIDERED_DATA",
                            "source": src_name,
                            "message": f"{label}: source {src_name!r} 的展平 CSV 首行"
                                       f"（表头文本 {first_label!r}）被当作候选数据行 — "
                                       "rows 无 selector（或 selector 未排除首行）时表头会被"
                                       "映射进数据区",
                            "corrective_action": "在 rows.selectors 加 pattern/not_pattern "
                                                 "排除表头行 (如 `column A pattern 业务类别*` "
                                                 "或 `column A not_value 类别`)",
                        })
                out.append({"name": src_name, "csv": src_entry["csv"], "rows": src_rows,
                            "matched": matched})
            return out

        lookups = build_lookup_tables(spec["mapping"], target_cfg, workdir)
        transforms = build_transforms(spec["mapping"], target_cfg, defects)
        if defects:
            fail("STATIC_VALIDATION_FAILED", f"{len(defects)} static validation defect(s)",
                 "Fix the spec and re-run compile_fill.py", defects)
        lookup_stats: dict = {}

        # Materialize per block and attach block data (rows, source, block index).
        block_infos: list[dict] = []
        data_rows: list[dict] = []
        matched_all: list[dict] = []
        for bi, bcfg in enumerate(blocks_cfg):
            label = f"block[{bi}]"
            matched = match_block_sources(bcfg, label)
            materialized = materialize_values(
                [r for m in matched for r in m["matched"]], bcfg, num_cols,
                lookups, transforms, defects, lookup_stats)
            drs = []
            # map materialized rows back to their source csv
            src_of = []
            for m in matched:
                src_of.extend([m["csv"]] * len(m["matched"]))
            for dr, src in zip(materialized, src_of):
                dr["src"] = src
                drs.append(dr)
            if not defects:
                apply_precision_policy(bcfg, drs, defects, warnings,
                                       col_widths=target_meta.get("column_width") or {},
                                       col_numfmt=target_meta.get("column_numfmt") or {})
            if defects:
                fail("MATERIALIZE_DEFECTS", f"{len(defects)} value materialization defect(s)",
                     "Fix the column mappings/lookups/transforms", defects)
            data_rows.extend(drs)
            matched_all.extend(matched)
            block_infos.append({"cfg": bcfg, "rows": drs, "count": len(drs),
                                "matched": matched, "label": label})

        note_lookup_all_missing(lookup_stats, warnings)

        for b in block_infos:
            b["cfg"]["_rows"] = b["rows"]

        roles, data_starts = compute_layout(blocks_cfg, platform, target_cfg,
                                            defects, dims.get("rows"))
        for b, start in zip(block_infos, data_starts):
            b["data_start"] = start
        for b in block_infos:
            ip_role = next((r for r in b["cfg"].get("clone_roles", [])
                            if r.get("mode") == "inplace"), None)
            if ip_role:
                b["inplace"] = {
                    "start_row": ip_role.get("start_row"),
                    "capacity": ip_role.get("capacity"),
                    "template_row": ip_role.get("template_row"),
                    "region_end": ip_role.get("start_row") + ip_role.get("capacity") - 1,
                }
                # 锚点样式继承 (Case 010 盲区): inplace 组锚点可能落在旧合并区
                # 非锚点格 (无字体样式) — 采集占位区内同列既有锚点样式, 供
                # _emit_block_ops 合并进 merge op (spec 显式 styles 优先)。
                region = (b["inplace"]["start_row"], b["inplace"]["region_end"])
                merge_cols = {g.get("col") for g in b["cfg"].get("group_merges", [])}
                merge_cols |= {m.get("col") for m in b["cfg"].get("merges", [])}
                b["inherited_styles"] = {
                    col: inherited_anchor_style(target_meta, col, *region)
                    for col in merge_cols if col
                }
        validate_inplace_geometry(blocks_cfg, ip_ctx, roles,
                                  target_cfg.get("base_last_row", 0), defects)

        style_defaults = {
            "anchor": dict(STYLE_DEFAULTS["anchor"], **(target_cfg.get("styles") or {}).get("anchor", {})),
            "label": dict(STYLE_DEFAULTS["label"], **(target_cfg.get("styles") or {}).get("label", {})),
        }

        if platform == "pptx":
            if any(inplace_roles(b) for b in blocks_cfg):
                defects.append({"code": "PPTX_CAPABILITY_NOT_ROLLED_OUT",
                                "message": "mode: inplace is meaningless for pptx — the "
                                           "pptx model is already pre-built-row fills",
                                "corrective_action": "Drop mode: inplace from the pptx spec"})
            for b in block_infos:
                cfg = b["cfg"]
                label = b["label"]
                # fail-closed (issue 06): every declaration the pptx lowering does
                # NOT implement must be rejected here — build_ops_pptx only lowers
                # column value fills + DOM-path sets, everything else was silently
                # dropped (compile passed, no ops generated).
                if cfg.get("group_merges"):
                    defects.append({"code": "PPTX_CAPABILITY_NOT_ROLLED_OUT",
                                    "message": f"{label}: group_merges lowering for pptx "
                                               "(vMerge/rowspan mechanics) is staged: verify "
                                               "against the spike fixture before rollout",
                                    "corrective_action": "Use xlsx for group_merges, or wait "
                                                          "for the pptx lowering rollout"})
                if cfg.get("formulas", {}).get("group_aggregates"):
                    defects.append({"code": "PPTX_CAPABILITY_NOT_ROLLED_OUT",
                                    "message": f"{label}: group_aggregates lowering for "
                                               "pptx is staged — formula cells are xlsx-only; "
                                               "verify against the spike fixture before rollout",
                                    "corrective_action": "Use xlsx for group_aggregates, or "
                                                         "wait for the pptx lowering rollout"})
                if cfg.get("formulas", {}).get("per_row"):
                    defects.append({"code": "PPTX_CAPABILITY_NOT_ROLLED_OUT",
                                    "message": f"{label}: formulas.per_row lowering for pptx "
                                               "is not rolled out — pptx cells hold text, "
                                               "not formulas",
                                    "corrective_action": "Use xlsx for per_row formulas, or "
                                                         "precompute the derived values into "
                                                         "column mappings"})
                if cfg.get("formulas", {}).get("aggregates"):
                    defects.append({"code": "PPTX_CAPABILITY_NOT_ROLLED_OUT",
                                    "message": f"{label}: formulas.aggregates lowering for "
                                               "pptx is not rolled out — formula cells are "
                                               "xlsx-only",
                                    "corrective_action": "Use xlsx for aggregates, or "
                                                         "precompute the aggregate values"})
                if cfg.get("merges"):
                    defects.append({"code": "PPTX_CAPABILITY_NOT_ROLLED_OUT",
                                    "message": f"{label}: merges lowering for pptx is not "
                                               "rolled out — pptx merge mechanics differ "
                                               "(vMerge/rowspan spike pending)",
                                    "corrective_action": "Use xlsx for merges, or drop the "
                                                         "declaration"})
                if cfg.get("nulls"):
                    defects.append({"code": "PPTX_CAPABILITY_NOT_ROLLED_OUT",
                                    "message": f"{label}: nulls is meaningless for pptx — "
                                               "there is no clone residue to clear (rows are "
                                               "pre-built, nothing is cloned)",
                                    "corrective_action": "Drop nulls from the pptx spec"})
                if cfg.get("remove_rows"):
                    defects.append({"code": "PPTX_CAPABILITY_NOT_ROLLED_OUT",
                                    "message": f"{label}: remove_rows lowering for pptx is "
                                               "not rolled out — pptx has no structural row "
                                               "ops",
                                    "corrective_action": "Drop remove_rows from the pptx spec"})
                if any(col.get("props") for col in cfg.get("columns", [])):
                    defects.append({"code": "PPTX_CAPABILITY_NOT_ROLLED_OUT",
                                    "message": f"{label}: columns[].props (numberformat) is "
                                               "not applied on pptx — pptx cells are text "
                                               "and carry no number format",
                                    "corrective_action": "Drop props from pptx column "
                                                         "mappings"})
            if defects:
                fail("STATIC_VALIDATION_FAILED", f"{len(defects)} static validation defect(s)",
                     "Fix the spec and re-run compile_fill.py", defects)
            ops, readback, written = build_ops_pptx(target_cfg, roles, data_rows,
                                                    num_cols, defects)
            group_boundaries: list = []
            trim_count = 0
            set_records = _emit_sets(target_cfg, None, None, None, ops,
                                     lambda p, k, v: _pptx_register(p, k, v, written,
                                                                   readback, defects),
                                     defects, dims.get("rows", 0), num_cols)
        else:
            anchors = {a["anchor"] for a in target_meta.get("merge_anchors", [])}
            anchor_rows = {int(re.search(r"\d+$", a).group()) for a in anchors}
            whole_run_gated = False
            # 克隆源样式剖面 (一次性): 判 data 的 template_row 是否携带块内格式.
            # 见 flatten_table.clone_source_style_profile 的 recorded 说明 —
            # 这一项没有其它机器门禁看得见 (validate/issues/readback/structural/
            # render 全绿而格式已丢), 所以必须在编译期判.
            try:
                _style_prof = clone_source_style_profile(
                    str(workdir / manifest_target["file"]),
                    target_cfg.get("sheet") or manifest_target.get("sheet"),
                    target_meta.get("blocks"),
                    num_cols)
            except Exception:
                _style_prof = None  # 判不了 ≠ 有问题: 静默跳过
            for b in block_infos:
                cfg = b["cfg"]
                validate_nulls_rows(cfg, defects)  # 先于任何 parse_rel_rows 调用
                if any(d.get("code") == "NULLS_ROWS_INVALID" for d in defects):
                    fail("STATIC_VALIDATION_FAILED",
                         f"{len(defects)} static validation defect(s)",
                         "Fix the nulls rows specs and re-run compile_fill.py", defects)
                template_row = next((r.get("template_row") for r in cfg.get("clone_roles", [])
                                     if r.get("role") == "data"), None)
                if template_row is None:
                    fail("SPEC_DATA_CLONE", f"{b['label']}: xlsx data blocks need a "
                         "data clone_role with template_row",
                         "Add {role: data, template_row: N} to the block's clone_roles")
                if template_row in anchor_rows:
                    defects.append({"code": "CLONE_SOURCE_IS_ANCHOR",
                                    "template_row": template_row,
                                    "message": f"{b['label']}: template row {template_row} is a "
                                               "merge anchor; cloning it carries anchor formulas "
                                               "into non-anchor cells",
                                    "corrective_action": "Pick a non-anchor data row with the same format"})
                # 克隆源样式剖面检查 (recorded 2026-09): data 行克隆源必须携带
                # 块内格式. 模板里可能存在"是合并非锚点、却不带块内格式"的行
                # (本 run 实测: 块1 行4/5 的 A/V/W 列 style index 10/11, 而块内
                # 众数剖面是 6/19/19) — 克隆它, 新块的类别列与系列盈亏/总盈亏
                # 合并区就会丢字体/填充/上下边框, 而 validate / issues /
                # readback / structural / render 全部为绿.
                if (_style_prof and not b.get("inplace")
                        and template_row in _style_prof["profiles"]
                        and _style_prof["profiles"][template_row] != _style_prof["modal"]):
                    diff_cols = [
                        _style_prof["cols"][i]
                        for i, (a, m) in enumerate(zip(_style_prof["profiles"][template_row],
                                                       _style_prof["modal"]))
                        if a != m
                    ]
                    # 只有"块内确实存在携带众数剖面的可克隆行"时才算缺陷 —
                    # 否则无从要求 (模板本身就没有一致剖面), 降级为不阻断.
                    if (len(_style_prof["modal_rows"]) >= 2
                            and _style_prof["candidates"]):
                        defects.append({
                            "code": "CLONE_SOURCE_STYLE_MISMATCH",
                            "template_row": template_row,
                            "columns": diff_cols,
                            "message": f"{b['label']}: data clone source row {template_row} does "
                                       f"not carry the block's own formatting in column(s) "
                                       f"{', '.join(diff_cols)} (style profile differs from the "
                                       f"sheet's dominant data-row profile, carried by rows "
                                       f"{_style_prof['modal_rows']}) — the new block would "
                                       f"render those columns with the template's unstyled "
                                       f"merge-member formatting (no font/fill/border). "
                                       f"validate/issues/readback/render do NOT catch this.",
                            "corrective_action": "Pick a non-anchor data row whose formatting "
                                                 f"matches the block: {_style_prof['candidates']} "
                                                 f"(e.g. template_row: "
                                                 f"{_style_prof['candidates'][0]})",
                        })
                    else:
                        warnings.append({
                            "code": "CLONE_SOURCE_STYLE_DIVERGENT",
                            "template_row": template_row,
                            "columns": diff_cols,
                            "message": f"{b['label']}: data clone source row {template_row} "
                                       f"differs from the sheet's dominant data-row profile in "
                                       f"{', '.join(diff_cols)} and no consistent alternative "
                                       f"row exists — check the block's formatting manually",
                        })
                null_specs = {x["col"]: x.get("rows") for x in cfg.get("nulls", [])}
                per_row = cfg.get("formulas", {}).get("per_row", {})
                gm_cols = {g.get("col") for g in cfg.get("group_merges", [])}
                validate_clone_residue(cfg, template_row,
                                       _flat_entry_path(
                                           manifest_target, manifest_target["csv"],
                                           workdir, shared_root), num_cols,
                                       cfg.get("columns", []), null_specs, per_row,
                                       b["count"], defects, gm_cols)
                if b.get("inplace"):
                    # Double residue baseline: retained rows checked against
                    # each row's OWN values; overflow clone rows against template_row
                    # (covered by validate_clone_residue above).
                    validate_placeholder_residue(
                        cfg, b["inplace"]["start_row"], b["inplace"]["capacity"],
                        b["count"],
                        _flat_entry_path(manifest_target, manifest_target["csv"],
                                         workdir, shared_root), num_cols,
                        b["rows"], defects)
                validate_formula_references(cfg.get("formulas", {}), b["count"], defects)
                ga_entries, ga_whole_run = split_group_aggregates(
                    cfg.get("formulas", {}).get("group_aggregates"), defects, b["label"])
                if ga_whole_run and not whole_run_gated:
                    whole_run_gated = True
                    defects.append({"code": "CAPABILITY_NOT_ROLLED_OUT",
                                    "message": "group_aggregates.whole_run (跨块总计) 落点"
                                               "语义 (末块尾部 vs 独立行) 需一次 spike 锁定 — "
                                               "spike 前声明被结构化拒绝",
                                    "corrective_action": "用逐块块级 aggregates (每组合一块) "
                                                         "表达, 或等 whole_run spike 结论"
                                                         "落地后再声明"})
                for col in (set(per_row) | set(null_specs)
                            | {m.get("col") for m in cfg.get("merges", [])}
                            | gm_cols
                            | {g.get("col") for g in ga_entries}
                            | {g.get("group_by") for g in ga_entries if g.get("group_by")}
                            | {g.get("group_by") for g in cfg.get("group_merges", []) if g.get("group_by")}):
                    if col and col_letter_to_idx(col) >= num_cols:
                        defects.append({"code": "COL_OUT_OF_DIGEST", "col": col,
                                        "message": f"{b['label']}: column {col} beyond digest width {num_cols}",
                                        "corrective_action": "Check the target structure"})
            if target_cfg.get("base_last_row", 0) > dims.get("rows", 0):
                defects.append({"code": "BASE_ROW_OUT_OF_BOUNDS",
                                "message": f"base_last_row {target_cfg['base_last_row']} > digest rows {dims.get('rows')}",
                                "corrective_action": "Use the digest's row count"})
            validate_append_remove_zone(blocks_cfg,
                                        target_cfg.get("base_last_row", 0), defects)
            row_gaps = sorted(set(target_meta.get("row_gaps") or []))
            if row_gaps:
                for role in roles:
                    if role.get("mode") in ("inplace", "overflow_clone"):
                        continue
                    r = role.get("row")
                    trow = role.get("template_row")
                    # 锚点链: data/title/header 克隆 add after /row[r-1]; spacer 无 after.
                    anchor = (r - 1) if (isinstance(r, int) and role.get("kind") != "spacer") else None
                    for a, kind in ((anchor, "add anchor (after)"), (trow, "clone source (from)")):
                        if a in row_gaps:
                            defects.append({
                                "code": "TEMPLATE_ROW_GAP",
                                "row": a,
                                "kind": kind,
                                "message": f"role {role.get('kind','?')} at row {r}: "
                                           f"{kind} row {a} is a row-number gap in the target "
                                           f"sheet (row elements: missing {row_gaps}) — officecli "
                                           f"`add after/from /row[{a}]` would fail at runtime",
                                "corrective_action": "The default path already repairs gaps "
                                                     "inside `workspace_init --init` (before any "
                                                     "hash is recorded) — if you still see this, "
                                                     "init was run with --no-repair or refused "
                                                     "because the source path IS the staged path "
                                                     "(check manifest `repairs[].deferred`). Fix: "
                                                     "scripts/repair_row_gaps.py --input <xlsx> "
                                                     "--all --out <snapshot>, then re-run "
                                                     "workspace_init --init with that snapshot "
                                                     "(hashes/flatten/fingerprints are recomputed "
                                                     "from the new bytes) and recompile with the "
                                                     "new fingerprints. The repair CLI does NOT "
                                                     "re-sync fingerprints or patch the spec.",
                            })
            if defects:
                fail("STATIC_VALIDATION_FAILED", f"{len(defects)} static validation defect(s)",
                     "Fix the spec and re-run compile_fill.py", defects)

            ops, readback, written, group_boundaries, trim_count, set_records = \
                build_ops_xlsx(target_cfg, block_infos, roles, data_rows, num_cols,
                               style_defaults, defects, sheet_rows=dims.get("rows", 0))
            if defects:
                fail("STATIC_VALIDATION_FAILED", f"{len(defects)} static validation defect(s)",
                     "Fix the spec and re-run compile_fill.py", defects)

    # Bulk source-derived literal-sets audit (ticket 06 / D6) — compile-audit
    # ONLY, never a routing basis (spec 的记录数量阈值禁令只约束 routing)。对
    # 两条路径都生效 — blocks:[] + 大量 literal sets 正是被审计的病理形态。
    audit_bulk_literal_fallback(target_cfg, manifest, workdir, warnings,
                                defects, shared_root)

    if not ops:
        fail("PLAN_EMPTY", "compilation produced zero operations",
             "Check the target mapping")

    # Coverage — per (block, source) + combined row map (final coordinates).
    positions = []
    for b in block_infos:
        if platform == "xlsx":
            for i in range(b["count"]):
                positions.append(final_row_of(block_infos, b["data_start"] + i))
        else:
            positions.extend(roles[i]["tr"] for i in range(len(data_rows)))
    row_map = [(d["src"], d["orig"], positions[i]) for i, d in enumerate(data_rows)]

    # Global exactly-once invariant (issue 05): every (source, original_row)
    # enters at most one target row. Blocks (or rows.sources entries) whose
    # selectors overlap silently double quantities/amounts/aggregates while
    # coverage (an existence lower bound, not an upper bound) still passes —
    # reject fail-closed (there is no reuse syntax; FILLSPEC Q17).
    consumed: dict[tuple[str, int], list[int]] = {}
    for src, orig, trow in row_map:
        consumed.setdefault((src, orig), []).append(trow)
    doubled = {k: v for k, v in consumed.items() if len(v) > 1}
    if doubled:
        desc = "; ".join(
            f"{src} row {orig} → target rows {trows}"
            for (src, orig), trows in sorted(doubled.items()))
        fail("SOURCE_ROW_CONSUMED_TWICE",
             f"source rows consumed more than once: {desc}",
             "Every (source, original_row) must enter exactly one target row. "
             "Narrow the overlapping selectors across blocks (or rows.sources "
             "entries in one block) and drop duplicate required_coverage "
             "declarations — there is no reuse syntax yet")

    cov_entries = []
    if matrix_ctx is not None:
        # matrix 覆盖报告: 请求的字段标签全部解析 (每标签 × 每 record 列 = 每
        # 格一层 lineage); required_coverage 对 matrix 无意义 (编译期已拒绝)。
        cov_entries.append({
            "block": "matrix",
            "source": matrix_ctx["src_entry"]["csv"],
            "name": matrix_ctx["src_entry"]["name"],
            "total": len(matrix_ctx["field_map"]),
            "matched": len(matrix_ctx["field_map"]),
            "required_unmatched": [],
        })
    for bi, b in enumerate(block_infos):
        for m in b["matched"]:
            matched_origs = [d["orig"] for d in b["rows"] if d["src"] == m["csv"]]
            unmatched = []
            for rc in spec["validation"].get("required_coverage", []):
                ref = rc.get("source", m["name"])
                if ref != m["name"] and ref != m["csv"]:
                    continue
                for rn in rc.get("rows", []):
                    if rn not in matched_origs:
                        unmatched.append(rn)
            if unmatched:
                fail("REQUIRED_COVERAGE_UNMATCHED",
                     f"block[{bi}]/{m['name']}: required source rows not consumed: {unmatched}",
                     "Fix selectors or record the reason in gaps")
            cov_entries.append({
                "block": bi,
                "source": m["csv"],
                "name": m["name"],
                "total": len(m["rows"]),
                "matched": len(m["matched"]),
                "required_unmatched": unmatched,
            })

    # Extra required empties + key outputs
    for cell in spec["validation"].get("required_empty", []):
        path = cell if cell.startswith("/") else f"/{target_cfg['sheet']}/{cell}"
        readback.append({"path": path, "expect": "EMPTY", "kind": "empty"})
    key_outputs = []
    for cell in spec["validation"].get("key_outputs", []):
        path = cell if cell.startswith("/") else f"/{target_cfg['sheet']}/{cell}"
        if platform == "xlsx":
            # Spec coordinates are template-era; written/readback are final.
            m = re.search(r"/([A-Z]+)(\d+)$", path)
            if m:
                fr = spec_final_row_of(block_infos, int(m.group(2)))
                path = f"{path[:m.start(2)]}{fr}"
        if path in written:
            key_outputs.append({"path": path, "kind": written[path]})
        else:
            defects.append({"code": "KEY_OUTPUT_UNWRITTEN", "path": path,
                            "message": f"key_output {path} is never written by this plan",
                            "corrective_action": "Reference a cell that the mapping fills "
                                                 "(or a formula cell); row numbers can be "
                                                 "taken straight from this plan's "
                                                 "blocks[].data_start / merge & aggregate "
                                                 "anchor cells, or from combination_patterns "
                                                 "skeleton key_output slots — don't "
                                                 "hand-derive template rows"})
    if defects:
        fail("STATIC_VALIDATION_FAILED", f"{len(defects)} static validation defect(s)",
             "Fix the spec and re-run compile_fill.py", defects)

    blocks = []
    for role in roles:
        if role["kind"] == "data":
            pos = role.get("row") or role.get("tr")
            if platform == "xlsx":
                pos = final_row_of(block_infos, pos)
            if not blocks or blocks[-1]["kind"] != "data":
                entry = {"kind": "data", "sheet": target_cfg["sheet"],
                         "template_row": role.get("template_row"),
                         "data_start": pos, "data_end": pos}
                if role.get("mode"):
                    entry["mode"] = role["mode"]
                blocks.append(entry)
            else:
                blocks[-1]["data_end"] = pos
        else:
            blocks.append({"kind": role["kind"], "sheet": target_cfg["sheet"],
                           "row": role.get("row"), "template_row": role.get("template_row")})

    add_count = sum(1 for op in ops if op.get("command") == "add")
    remove_count = sum(1 for op in ops if op.get("command") == "remove")
    expected_final_row_count = dims.get("rows", 0) + add_count - remove_count
    inplace_overflow = 0
    for b in block_infos:
        ip = b.get("inplace")
        if ip:
            inplace_overflow = max(0, b["count"] - ip["capacity"])
    if platform == "xlsx":
        max_col = 1
        for op in ops:
            m = re.search(r"/([A-Z]+)\d+$", op.get("path", ""))
            if m:
                max_col = max(max_col, col_letter_to_idx(m.group(1)) + 1)
        render_region = (f"/{target_cfg['sheet']}/A1:"
                         f"{col_idx_to_letter(max_col - 1)}{expected_final_row_count}")
    else:
        render_region = f"/{target_cfg['sheet']}"

    matrix_meta = None
    if matrix_ctx is not None:
        matrix_meta = {
            "source": matrix_ctx["src_entry"]["name"],
            "target_sheet": target_cfg["sheet"],
            "orientation": dict(MATRIX_AXES),
            "field_map": [
                {"source": fe["source"], "target": fe["target"],
                 "transform_chain": _matrix_transform_names(fe)}
                for fe in matrix_ctx["field_map"]],
            "record_map": [
                {"record": r["record"], "source_column": r["source_column"],
                 "target_column": r["target_column"]}
                for r in matrix_ctx["record_map"]],
        }
    plan_writes = [
        {"row": trow, "col": col, "value": val}
        for drow, (_, _, trow) in zip(data_rows, row_map)
        for col, val in drow["values"].items()
    ]
    for c in matrix_cells:
        plan_writes.append({"row": c["target_row"], "col": c["target_col"],
                            "value": c["value"]})

    # 克隆源样式保真的执行期参照: 每块写入的数据行 → 期望样式剖面.
    clone_style_reference: dict = {}
    if platform == "xlsx" and _style_prof:
        _sheet_name = target_cfg.get("sheet")
        _written_rows = sorted({trow for _, _, trow in row_map})
        if _sheet_name and _written_rows:
            clone_style_reference[_sheet_name] = {
                "cols": _style_prof["cols"],
                "profile": _style_prof["modal"],
                "rows": _written_rows,
                "reference_rows": _style_prof["modal_rows"],
            }

    plan = {
        "schema_version": "2.5",  # v3 保留给 plugin-化世代
        "fill_spec": None,
        "fill_spec_sha256": sha256_text(spec_path.read_bytes().decode("utf-8"))
        if spec_path is not None else None,
        "platform": platform,
        "target": inputs["target"],
        "target_sheet": inputs["target_sheet"],
        "input_hashes": bind_input_hashes(workdir, inputs, shared_root),
        "fingerprints": {"source_structure": mfp.get("source_structure"),
                         "target_structure": mfp.get("target_structure")},
        "blocks": blocks,
        "operations": ops,
        "operation_count": len(ops),
        # 字体 scheme 后处理声明 (Q20 残余修复): 任何 op 写入 font.scheme=none
        # (pin_font_scheme 或 spec 显式 none) → 执行期 raw-set 后处理把
        # <scheme val='none'/> 元素整体移除 — 只有"无 scheme 元素"的字面字体
        # 才在所有查看器 (含 WPS) 稳定渲染为 font.name 字面名。
        "strip_scheme_none": any(
            o.get("props", {}).get("font.scheme") == "none"
            for o in ops if o.get("command") == "set"),
        "readback": readback,
        "source_csv": ", ".join(m["csv"] for m in matched_all),
        "source_coverage": cov_entries,
        "row_map": row_map,
        "warnings": warnings,
        "writes": plan_writes,
        "empties": [rb["path"] for rb in readback if rb["kind"] == "empty"],
        "key_outputs": key_outputs,
        "expected_final_row_count": expected_final_row_count,
        "structural_deltas": {"adds": add_count, "removes": remove_count,
                              "inplace_trim": trim_count,
                              "inplace_overflow": inplace_overflow},
        "group_boundaries": group_boundaries,
        # 克隆源样式保真的执行期参照 (recorded 2026-09): 编译期为每个新增块
        # 记录"块内众数数据行样式剖面 + 本 plan 写入的数据行号", 执行器据此
        # 在**产物**上复核格式确实落在新行上 (编译期判模板, 这里判 draft)。
        "clone_style_reference": clone_style_reference,
        "sets": set_records,
        # Ticket 06: matrix 一等表达元数据 + 每条写入的 source lineage —
        # {target, source, transform_chain} 聚合 (mirrors source_trace.json)。
        "matrix": matrix_meta,
        "source_trace": source_trace,
        "mechanical_facts": derive_mechanical_facts(
            ops, target_cfg, blocks_cfg, block_infos),
        "render_qa": {"region": render_region},
    }
    return plan


# ── Probe (compile-only verification) ─────────────────────────────────

def probe_spec(spec: dict, manifest: dict, workdir: Path) -> dict:
    """Compile-only probe: does the compiler ACCEPT this spec?

    Runs the exact same pipeline as a real compile (so the answer is always
    the same), but writes nothing: no execution_plan.json, no mapping.md. Result:
      accepted=True  → {"accepted", "operations", "warnings"}
      accepted=False → {"accepted", "exit_code", "code", "defects", "message"}
    """
    import io as _io
    buf = _io.StringIO()
    old_err = sys.stderr
    sys.stderr = buf
    try:
        plan = compile_spec(spec, manifest, workdir)
    except SystemExit as e:
        payload = None
        raw = buf.getvalue()
        try:
            payload = json.loads(raw)
        except ValueError:
            pass
        return {"accepted": False, "exit_code": e.code,
                "code": (payload or {}).get("code"),
                "defects": (payload or {}).get("defects", []),
                "message": (payload or {}).get("message", raw.strip())}
    finally:
        sys.stderr = old_err
    return {"accepted": True, "exit_code": 0,
            "operations": len(plan["operations"]),
            "warnings": plan["warnings"],
            "defects": []}


def query_capability(key: str) -> dict | None:
    """Fine-grained capability query (P1-01): one answer record for `--capability`.

    纯契约查询 — 只读 CAPABILITY_CONTRACT (单一事实源, 由既有常量/schema 派生),
    不读 workdir / workbook / manifest / spec, 不需要 Prepare。"""
    entry = CAPABILITY_CONTRACT.get(key)
    if entry is None:
        return None
    return {
        "capability": key,
        "state": entry["state"],
        "constraints": list(entry.get("constraints", [])),
        "conflicts": list(entry.get("conflicts", [])),
        "reference": entry["reference"],
    }


def capability_key_unknown(key: str) -> None:
    """Unknown key → exit 3 + 命名空间不匹配 + 最近可用键 (ticket 03 收敛提示).

    保留旧 CAPABILITY_KEY_UNKNOWN code + available_keys (既有 contract test 兼容),
    新增 namespace_mismatch 提示 + nearest_keys (difflib 最近键) + transform_functions
    (转换函数命名空间 — 查错命名空间时给出「这是转换函数不是能力」, US 15 一次
    查询即终结)。"""
    keys = sorted(CAPABILITY_CONTRACT)
    transforms = sorted(TRANSFORM_NAMESPACE)
    is_transform = key in TRANSFORM_NAMESPACE
    all_keys = keys + transforms
    nearest = _nearest_keys(key, all_keys)
    if is_transform:
        message = (f"{key!r} 是转换函数不是能力 (namespace mismatch) — "
                   f"转换函数用于 columns.transforms / matrix.field_map[].transforms, "
                   f"能力键用 --capability; {TRANSFORM_NAMESPACE[key]['what']}")
    else:
        message = (f"unknown key {key!r} (namespace mismatch) — 不是能力键也不是 "
                   f"转换函数; 最近可用键: {', '.join(nearest)}")
    sys.stderr.write(json.dumps({
        "status": "ERROR",
        "code": "CAPABILITY_KEY_UNKNOWN",
        "namespace_mismatch": True,
        "message": message,
        "available_keys": keys,
        "transform_functions": transforms,
        "nearest_keys": nearest,
        "corrective_action": "Use one of the listed capability keys (or a transform "
                             "function name when you meant a value transform)",
    }, ensure_ascii=False, indent=2))
    sys.exit(3)


def run_probe_cases(workdir: Path) -> list[dict]:
    """Execute the contract probe matrix (PROBE_CASES) on a fresh synthetic
    workdir and report what the compiler itself accepts/rejects.

    The same matrix drives `compile_fill.py --capabilities`, the contract
    tests (tests/test_optimization.py) and — via the doc-coverage guards —
    FILLSPEC.md: the three can never drift apart because they share one list.
    """
    import _probe_fixtures as pf
    out = []
    for case in pf.PROBE_CASES:
        wd = (case.get("workdir_factory") or pf.make_probe_workdir)(workdir)  # fresh per case
        spec = case["build"](pf.base_probe_spec(), wd)
        spec["fingerprints"] = {
            "source_structure": wd["manifest"]["fingerprints"]["source_structure"],
            "target_structure": wd["manifest"]["fingerprints"]["target_structure"],
        }
        r = probe_spec(spec, wd["manifest"], workdir)
        codes = [d.get("code") for d in r.get("defects", [])]
        warn_codes = sorted({w.get("code") for w in r.get("warnings", [])})
        out.append({
            "id": case["id"],
            "accepted": r["accepted"],
            "code": codes[0] if codes else r.get("code"),
            "warnings": warn_codes,
            "expect": case["expect"],
        })
    return out


def main() -> None:
    ensure_utf8_stdio()
    parser = argparse.ArgumentParser(description="Compiler: FillSpec → execution plan + mapping")
    parser.add_argument("--spec", type=Path, help="fill_spec.yaml (required unless --capabilities)")
    parser.add_argument("--workdir", type=Path, help="workdir (required unless --capabilities)")
    parser.add_argument("--probe", action="store_true",
                        help="compile-only probe: accepted? no plan/mapping/timing written — "
                             "the authoritative answer to 'will the compiler accept this spec?'")
    parser.add_argument("--capabilities", action="store_true",
                        help="run the contract probe matrix and report acceptance per "
                             "combination (the FILLSPEC「组合行为契约」/「能力映射表」claims, "
                             "as the compiler itself sees them)")
    parser.add_argument("--capability", metavar="KEY",
                        help="pure contract query: answer one fine-grained capability "
                             "key from CAPABILITY_CONTRACT (e.g. matrix.field_locator) "
                             "as short JSON — no workdir/workbook/spec needed")
    parser.add_argument("--mod-resolution", type=Path, default=None, metavar="PATH",
                        help="optional task-level MOD Adjudication Record path "
                             "(ticket 08: task 级一次裁决，多 run 共享；缺省回退 "
                             "workdir/mod_resolution.json 的 single-run 语义)")
    parser.add_argument("--shared-root", type=Path, default=None, metavar="DIR",
                        help="optional task root providing shared staged/raw inputs "
                             "(staged/) and the flatten cache (cache/<key>/); when set, "
                             "compile resolves raw inputs and flattened csv/meta by "
                             "hash/cache_key reference instead of reading them from "
                             "workdir (ticket 08: shared inputs are not byte-copied "
                             "into run dirs)")
    args = parser.parse_args()

    if args.capability:
        if any((args.spec is not None, args.workdir is not None,
                args.probe, args.capabilities)):
            parser.error("--capability is a pure contract query — "
                         "cannot combine with --spec/--workdir/--probe/--capabilities")
        entry = query_capability(args.capability)
        if entry is None:
            capability_key_unknown(args.capability)
        print(json.dumps(entry, ensure_ascii=False, indent=2))
        sys.exit(0)

    if args.capabilities:
        import tempfile as _tmp
        with _tmp.TemporaryDirectory() as td:
            results = run_probe_cases(Path(td))
        namespaces = capability_namespaces()
        print(json.dumps({
            "status": "SUCCESS", "code": "CAPABILITIES_REPORTED",
            "schema_version": "2.5",
            "note": "acceptance as compile_fill.py itself judges it — "
                    "contract tests assert this matrix matches FILLSPEC.md",
            "patterns": "assets/combination_patterns.yaml (copyable fragments "
                        "for the recommended combinations)",
            # ticket 03: 双命名空间显式分组 (转换函数 vs 能力), 能力键一个不删
            "transforms": namespaces["transforms"],
            "capabilities": namespaces["capabilities"],
            "cases": results,
        }, ensure_ascii=False, indent=2))
        sys.exit(0)

    if args.spec is None or args.workdir is None:
        parser.error("--spec and --workdir are required "
                     "(or use --capabilities / --capability <key>)")

    spec = load_spec(args.spec)
    manifest = load_manifest(args.workdir)
    if args.probe:
        result = probe_spec(spec, manifest, args.workdir)
        print(json.dumps({"status": "PROBED", **result}, ensure_ascii=False, indent=2))
        sys.exit(0 if result["accepted"] else 3)

    plan = compile_spec(spec, manifest, args.workdir,
                        mod_resolution_path=args.mod_resolution,
                        shared_root=args.shared_root)
    plan["fill_spec"] = str(args.spec)
    plan["fill_spec_sha256"] = sha256_text(args.spec.read_bytes().decode("utf-8"))

    plan_path = args.workdir / PLAN_NAME
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    mapping_path = args.workdir / MAPPING_NAME
    mapping_path.write_text(render_mapping(spec, plan, manifest), encoding="utf-8")
    # Source lineage (ticket 06): 每条 matrix 写入的 {target, source,
    # transform_chain} 聚合到独立文件 — Verify/readback 的机器证据消费;
    # execution_plan.json 内同源镜像 (plan.source_trace)。
    if plan.get("source_trace"):
        (args.workdir / SOURCE_TRACE_NAME).write_text(
            json.dumps(plan["source_trace"], ensure_ascii=False, indent=2),
            encoding="utf-8")

    print(json.dumps({
        "status": "SUCCESS", "code": "PLAN_GENERATED",
        "operations": len(plan["operations"]),
        "readback": len(plan["readback"]),
        "matched_rows": sum(c["matched"] for c in plan["source_coverage"]),
        "warnings": plan["warnings"],
        "blocks": plan["blocks"],
        "plan": str(plan_path),
        "mapping": str(mapping_path),
    }, ensure_ascii=False, indent=2))
    sys.exit(0)


if __name__ == "__main__":
    main()

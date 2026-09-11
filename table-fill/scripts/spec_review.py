#!/usr/bin/env python3
"""
scripts/spec_review.py — Spec Review 人工点 (ticket 05): FillSpec 初稿后、编译前的人审摘要 + 确认.

ADR 0017 / spec 契约:
  - Spec Review 是唯一人工点, 放在 FillSpec 初稿之后、编译之前 (8 阶段管线).
  - 摘要含「映射 / 转换 / 排除」三节, 用业务语言重组 (真实表头/角色词汇), **不是 YAML 转储**.
  - 摘要总是生成、确认总是呈现 (低摩擦快速确认); 仅用户显式 --skip-review 才跳过; **无 Agent 风险分级**.
  - 确认绑定 FillSpec 哈希 (sha256), 改动后旧确认失效 (fail-closed).
  - 多 run 一次摘要覆盖全部 run, 一次确认绑定全部 run 的 spec (每 run 哈希分别记录).

与交付独立（本脚本只立唯一人审点）：使用独立产物名 (`spec_review.json`,
摘要文本) 与独立 CLI 入口，不读不写任何交付 marker，只绑定 fill_spec 哈希
(`review_confirm.json`)。Spec Review 之后 verify 全绿自动哈希核对复制交付
（deliver），无 gate、无确认环节。

CLI:
  # 单 run: 读 run 目录的 fill_spec.yaml (默认在工作目录下), 生成三节摘要
  python scripts/spec_review.py --workdir <dir> --spec fill_spec.yaml
  python scripts/spec_review.py --workdir <dir> --spec fill_spec.yaml --confirm

  # 多 run: 读 task.yaml 的 runs 清单, 一次摘要覆盖全部 run 的 fill_spec.yaml
  python scripts/spec_review.py --workdir <task_dir> --task task.yaml
  python scripts/spec_review.py --workdir <task_dir> --task task.yaml --confirm

  # 显式跳过 (唯一跳过路径): --skip-review
  python scripts/spec_review.py --workdir <dir> --spec fill_spec.yaml --skip-review

摘要默认打印到 stdout (人类读); 也写 `spec_review.json` (机器可读: 三节结构 +
每个 run 的 fill_spec 路径与 sha256). 确认 (`--confirm`) 重算当前 spec 哈希与
`spec_review.json` 里记录的哈希比对, 不一致 → exit 3 fail-closed (旧确认失效);
一致 → 写 `review_confirm.json` (记录确认时间 + 每 run 绑定的哈希).

Exit codes: 0=ok/摘要已生成/确认成功; 3=retryable (spec 哈希漂移/缺摘要/缺 spec);
1=fatal (输入非法).

重要裁决 (见 ticket 05 Comments): 摘要器**不依赖 compile 成功** — 它只读
fill_spec 结构 + (可选)flatten CSV 表头证据, 不需要 prepare_manifest 指纹一致,
也不需要 execution_plan. 因此 Spec Review 能在「初稿后、编译前」运行 (初稿可以不
完整), 与 FillSpec First Rule (04 号票) 衔接.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _officecli import ensure_utf8_stdio, sha256_file  # noqa: E402

REVIEW_JSON_NAME = "spec_review.json"
CONFIRM_JSON_NAME = "review_confirm.json"


# ─────────────────────────────────────────────────────────────────────
# 摘要生成 (业务语言重组, 非 YAML 转储)
# ─────────────────────────────────────────────────────────────────────

def _load_yaml(path: Path) -> dict:
    import yaml  # 延迟 import (PyYAML; 与其它脚本一致)
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _header_for(workdir: Path, flat_csv_name: str, col_letter: str) -> str | None:
    """尝试从展平 CSV 表头把「列字母」翻译成业务表头名 (真实角色词汇)。

    找不到表头 / 列超出 / 无法映射时返回 None (摘要回退为裸列字母, 合法)。"""
    csv_path = workdir / flat_csv_name
    if not csv_path.is_file():
        return None
    try:
        lines = csv_path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return None
    if not lines:
        return None
    headers = lines[0].split(",")
    idx = _col_index(col_letter)
    if idx is None or idx >= len(headers):
        return None
    name = headers[idx].strip().strip('"')
    return name or None


def _col_index(letter: str) -> int | None:
    """Excel 列字母 → 0-based 列索引; 非法返回 None。"""
    letter = (letter or "").upper()
    if not letter or not letter.isalpha():
        return None
    idx = 0
    for ch in letter:
        idx = idx * 26 + (ord(ch) - ord("A") + 1)
    return idx - 1


def _flatten_name_of(workdir: Path, source: str) -> str:
    """rows.source / matrix.source.flatten → 展平 CSV 文件名 (用于表头翻译)。

    约定: flatten 条目名 == 展平 CSV 名 (不含 .csv) == manifest.flattened[].name。
    这里尽力猜测 `{name}_flat.csv`, 猜不中就返回 None。"""
    if not source:
        return None
    cand = f"{source}_flat.csv"
    if (workdir / cand).is_file():
        return cand
    return None


def _describe_selectors(sel: list) -> list[str]:
    """把 rows.selectors 转成业务语言 (筛选条件), 非转储。"""
    out = []
    for s in sel or []:
        col = s.get("column")
        parts = []
        if s.get("pattern"):
            parts.append(f"「{col}」列匹配模式 {s['pattern']!r}")
        if s.get("not_pattern"):
            parts.append(f"「{col}」列排除模式 {s['not_pattern']!r}")
        if "not_value" in s:
            parts.append(f"「{col}」列排除值 {s['not_value']!r}")
        if not parts:
            parts.append(f"列 {col} 的条件")
        out.append("；".join(parts))
    return out


def _describe_columns(workdir: Path, columns: list, flat_csv: str | None) -> list[str]:
    """columns[] → 业务语言 (源→目标, 带真实表头名当可解析)。"""
    out = []
    for c in columns or []:
        src = c.get("source")
        tgt = c.get("target")
        label = ""
        if src is not None and tgt is not None:
            sh = _header_for(workdir, flat_csv, src) if flat_csv else None
            src_desc = f"「{sh}」(列 {src})" if sh else f"列 {src}"
            out.append(f"{src_desc} → 目标列 {tgt}")
            label = "直接拷贝"
        elif tgt is not None and c.get("lookup"):
            lk = c["lookup"]
            out.append(f"目标列 {tgt} ← 查表「{lk.get('name')}」字段 {lk.get('field')} "
                       f"(缺失 {lk.get('missing')})")
            label = "查表填充"
        elif tgt is not None and isinstance(src, list):
            out.append(f"源列 {', '.join(map(str, src))} 求和 → 目标列 {tgt}")
            label = "多列求和"
        elif tgt is not None and "value" in c:
            out.append(f"目标列 {tgt} 写入常量 {c['value']!r}")
            label = "常量"
        elif tgt is not None:
            out.append(f"目标列 {tgt}")
        # 追加转换理由 (转换节也承载, 这里给映射上下文一句话)
        if c.get("transform") or c.get("transforms"):
            chain = c.get("transforms") or [c.get("transform")]
            out.append(f"   └ 该列应用转换: {', '.join(map(str, chain))}")
    return out


def _describe_matrix(workdir: Path, matrix: dict) -> list[str]:
    """matrix (fields × records) → 业务语言。"""
    out = []
    fm = matrix.get("field_map") or []
    rm = matrix.get("record_map") or []
    out.append(f"矩阵映射: {len(fm)} 个字段 × {len(rm)} 个记录 (格转移填充)")
    for f in fm:
        s, t = f.get("source"), f.get("target")
        sd = str(s) if isinstance(s, str) else json.dumps(s, ensure_ascii=False)
        td = str(t) if isinstance(t, str) else json.dumps(t, ensure_ascii=False)
        chain = f.get("transforms") or []
        note = f" (转换 {', '.join(map(str, chain))})" if chain else ""
        out.append(f"  字段 {sd} → {td}{note}")
    for r in rm:
        out.append(f"  记录 {r.get('record')}: 源列 {r.get('source_column')} → "
                   f"目标列 {r.get('target_column')}")
    return out


def _describe_transforms(spec: dict, workdir: Path) -> list[str]:
    """转换节: transforms 定义 / formulas / lookups / group/merge 业务理由。"""
    out = []
    mapping = spec.get("mapping") or {}
    named = mapping.get("transforms") or []
    for t in named:
        fn = t.get("function")
        if fn == "controlled_translation":
            tr = t.get("translations") or {}
            out.append(f"受控翻译「{t.get('name')}」(整值精确替换): "
                       f"{', '.join(f'{k}→{v}' for k, v in tr.items())}")
        elif fn == "regex_replace":
            out.append(f"正则替换「{t.get('name')}」: 模式 {t.get('pattern')!r} → "
                       f"{t.get('replacement')!r}")
        elif fn == "strip":
            out.append(f"去首尾空白「{t.get('name')}」")
        else:
            out.append(f"命名转换「{t.get('name')}」(函数 {fn})")
    lookups = mapping.get("lookups") or []
    for lk in lookups:
        out.append(f"查表「{lk.get('name')}」: 键列 {lk.get('key_column')} "
                   f"从 {lk.get('from')} 取 {', '.join(lk.get('fields') or [])}")
    def _append_formulas(frm: dict, prefix: str = "") -> None:
        for col, expr in (frm.get("per_row") or {}).items():
            out.append(f"{prefix}逐行公式 列 {col}: {expr}")
        for agg in frm.get("aggregates") or []:
            out.append(f"{prefix}聚合 列 {agg.get('col')} (范围 {agg.get('rows')}): "
                       f"{agg.get('formula')}")
        for ga in frm.get("group_aggregates") or []:
            out.append(f"{prefix}分组聚合 (按 {ga.get('group_by')}) 列 {ga.get('col')}: "
                       f"{ga.get('formula')}")

    _append_formulas(mapping.get("formulas") or {})
    for tgt in mapping.get("targets") or []:
        _append_formulas(tgt.get("formulas") or {})
        for blk in tgt.get("blocks") or []:
            _append_formulas(blk.get("formulas") or {}, prefix="块内")
    return out


def _describe_exclusions(spec: dict) -> list[str]:
    """排除节: selectors 的排除条件 / nulls / remove_rows / gaps / decisions。"""
    out = []
    mapping = spec.get("mapping") or {}
    for tgt in mapping.get("targets") or []:
        rows = tgt.get("rows") or {}
        sel = rows.get("selectors") or []
        excl = [s for s in sel if s.get("not_pattern") or "not_value" in s]
        for s in excl:
            col = s.get("column")
            if s.get("not_pattern"):
                out.append(f"排除「{col}」列匹配 {s['not_pattern']!r} 的源行")
            if "not_value" in s:
                out.append(f"排除「{col}」列等于 {s['not_value']!r} 的源行")
        nulls = tgt.get("nulls") or []
        if nulls:
            out.append(f"目标 sheet「{tgt.get('sheet')}」克隆残留置空 "
                       f"{len(nulls)} 处 (列 {', '.join(str(n.get('col')) for n in nulls)})")
        rm = tgt.get("remove_rows") or []
        if rm:
            out.append(f"删除模板行: {rm}")
    gaps = spec.get("gaps") or []
    for g in gaps:
        out.append(f"数据缺口: {g}")
    decisions = spec.get("decisions") or []
    for d in decisions:
        out.append(f"业务决策: {d}")
    return out


def build_summary(workdir: Path, spec: dict) -> dict:
    """从 fill_spec 生成三节业务语言摘要 (dict: mapping/transforms/exclusions → [str]).

    绝不转储 YAML 结构: 每一行都是重组后的业务措辞; 裸列字母在能解析时替换为
    展平 CSV 真实表头名 (业务角色词汇)。"""
    mapping_lines: list[str] = []
    mapping = spec.get("mapping") or {}
    targets = mapping.get("targets") or []
    for tgt in targets:
        sheet = tgt.get("sheet", "?")
        flat_csv = None
        rows = tgt.get("rows") or {}
        if tgt.get("matrix"):
            mapping_lines.append(f"目标 sheet「{sheet}」:")
            mapping_lines.extend(_describe_matrix(workdir, tgt["matrix"]))
            continue
        src = rows.get("source") or (rows.get("sources") or [{}])[0].get("source")
        flat_csv = _flatten_name_of(workdir, src)
        mapping_lines.append(f"目标 sheet「{sheet}」(基行 {tgt.get('base_last_row')}):")
        if rows.get("source"):
            mapping_lines.append(f"  源数据「{rows['source']}」")
            sel_desc = _describe_selectors(rows.get("selectors"))
            if sel_desc:
                mapping_lines.append(f"  筛选: {'；'.join(sel_desc)}")
            else:
                mapping_lines.append("  筛选: 全部源行")
        elif rows.get("sources"):
            for src_entry in rows["sources"]:
                mapping_lines.append(f"  源数据「{src_entry.get('source')}」")
                sel_desc = _describe_selectors(src_entry.get("selectors"))
                mapping_lines.append(f"  筛选: {'；'.join(sel_desc) if sel_desc else '全部源行'}")
        cols = tgt.get("columns") or []
        if cols:
            for line in _describe_columns(workdir, cols, flat_csv):
                mapping_lines.append("  " + line)
        for blk in tgt.get("blocks") or []:
            br = blk.get("rows") or {}
            b_src = br.get("source")
            b_flat = _flatten_name_of(workdir, b_src)
            mapping_lines.append(f"  数据块 (源「{b_src}」):")
            sel_desc = _describe_selectors(br.get("selectors"))
            if sel_desc:
                mapping_lines.append(f"    筛选: {'；'.join(sel_desc)}")
            for line in _describe_columns(workdir, blk.get("columns") or [], b_flat):
                mapping_lines.append("    " + line)

    transforms = _describe_transforms(spec, workdir)
    exclusions = _describe_exclusions(spec)
    intent = (spec.get("task") or {}).get("intent")
    return {
        "intent": intent,
        "mapping": mapping_lines,
        "transforms": transforms,
        "exclusions": exclusions,
    }


def render_summary(summary: dict) -> str:
    """把三节摘要 dict 渲染成人类可读文本。"""
    lines = ["# FillSpec 审阅摘要 (Spec Review)", ""]
    if summary.get("intent"):
        lines.append(f"**任务意图**: {summary['intent']}")
        lines.append("")
    lines.append("## 映射 (mapping)")
    lines.extend(summary.get("mapping") or ["(无映射)"])
    lines.append("")
    lines.append("## 转换 (transforms)")
    lines.extend(summary.get("transforms") or ["(无转换)"])
    lines.append("")
    lines.append("## 排除 (exclusions)")
    lines.extend(summary.get("exclusions") or ["(无排除)"])
    lines.append("")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────
# spec 定位 (单 run / 多 run)
# ─────────────────────────────────────────────────────────────────────

def locate_specs(workdir: Path, spec: str | None, task: str | None) -> list[dict]:
    """返回 [{"run_id", "spec_path", "label"}] 列表 (单 run 或 task 多 run)。

    - 单 run: --spec 指向一个 fill_spec.yaml (相对 workdir)。
    - 多 run: --task 指向 task.yaml, 其 runs[].id 对应 runs/<id>/fill_spec.yaml。
    fail-closed: 找不到任何 spec → 抛 ValueError。"""
    if spec and task:
        raise ValueError("--spec 与 --task 互斥, 二选一")
    if task:
        task_yaml = _load_yaml(workdir / task)
        runs = task_yaml.get("runs") or []
        out = []
        for r in runs:
            rid = r.get("id")
            spec_path = workdir / "runs" / str(rid) / "fill_spec.yaml"
            out.append({"run_id": rid, "spec_path": spec_path,
                        "label": f"run {rid}"})
        if not out:
            raise ValueError(f"task.yaml ({task}) 无 runs 清单")
        missing = [s for s in out if not s["spec_path"].is_file()]
        if missing:
            names = ", ".join(str(s["spec_path"]) for s in missing)
            raise ValueError(f"以下 run 缺 fill_spec.yaml: {names}")
        return out
    if spec:
        spec_path = workdir / spec
        if not spec_path.is_file():
            raise ValueError(f"fill_spec 不存在: {spec_path}")
        return [{"run_id": "run", "spec_path": spec_path, "label": spec}]
    # 默认: workdir 下 find fill_spec.yaml
    default = workdir / "fill_spec.yaml"
    if default.is_file():
        return [{"run_id": "run", "spec_path": default, "label": "fill_spec.yaml"}]
    raise ValueError("未找到 fill_spec — 用 --spec 或 --task 指定")


def compute_review(workdir: Path, specs: list[dict]) -> dict:
    """计算一次摘要 (覆盖全部 run) + 每 run 的 fill_spec sha256。

    返回 spec_review.json 的结构 (不落盘)。"""
    runs = []
    for s in specs:
        spec = _load_yaml(s["spec_path"])
        summary = build_summary(workdir, spec)
        runs.append({
            "run_id": s["run_id"],
            "spec_path": str(s["spec_path"]),
            "fill_spec_sha256": sha256_file(s["spec_path"]),
            "summary": summary,
        })
    return {
        "schema_version": 1,
        "kind": "spec_review",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "runs": runs,
    }


def _fail(code: str, message: str, corrective_action: str, exit_code: int) -> None:
    sys.stderr.write(json.dumps({
        "status": "ERROR", "code": code, "message": message,
        "corrective_action": corrective_action,
    }, ensure_ascii=False, indent=2))
    sys.exit(exit_code)


def main(argv: list[str] | None = None) -> int:
    ensure_utf8_stdio()
    ap = argparse.ArgumentParser(description="Spec Review: FillSpec 人审摘要 + 哈希绑定确认")
    ap.add_argument("--workdir", type=Path, required=True)
    ap.add_argument("--spec", type=str, default=None,
                    help="fill_spec.yaml 路径 (相对 workdir, 单 run)")
    ap.add_argument("--task", type=str, default=None,
                    help="task.yaml 路径 (相对 workdir, 多 run — 一次覆盖全部 run)")
    ap.add_argument("--confirm", action="store_true",
                    help="确认 (重算哈希比对, 漂移 → fail-closed 拒绝)")
    ap.add_argument("--skip-review", action="store_true",
                    help="显式跳过 Spec Review (唯一跳过路径, 用户显式指示)")
    args = ap.parse_args(argv)

    workdir = args.workdir

    # 显式跳过路径: 唯一跳过方式, 无 Agent 风险分级。
    if args.skip_review:
        print(json.dumps({
            "status": "PASS", "code": "REVIEW_SKIPPED",
            "message": "Spec Review 已按用户显式指示跳过 (--skip-review)。",
        }, ensure_ascii=False, indent=2))
        return 0

    try:
        specs = locate_specs(workdir, args.spec, args.task)
    except ValueError as e:
        _fail("SPEC_NOT_FOUND", str(e),
              "用 --spec fill_spec.yaml (单 run) 或 --task task.yaml (多 run) 指定",
              exit_code=1)

    review_path = workdir / REVIEW_JSON_NAME
    confirm_path = workdir / CONFIRM_JSON_NAME

    if args.confirm:
        # fail-closed: 确认必须有「刚呈现的」spec_review.json (摘要已生成并呈现)。
        if not review_path.is_file():
            _fail("REVIEW_NOT_PRESENTED",
                  "没有 spec_review.json — 摘要尚未生成/呈现, 无法确认",
                  "先运行 (不带 --confirm) 生成并呈现摘要, 用户确认后再 --confirm",
                  exit_code=3)
        try:
            presented = json.loads(review_path.read_text(encoding="utf-8"))
            presented_runs = {r["run_id"]: r.get("fill_spec_sha256")
                              for r in presented.get("runs", [])}
        except (ValueError, OSError):
            _fail("REVIEW_CORRUPT", "spec_review.json 不可读",
                  "重新生成摘要 (不带 --confirm)", exit_code=3)

        # 重算当前 spec 哈希, 与呈现比对; 任一 run 漂移 → 旧确认失效。
        drift = []
        for s in specs:
            cur = sha256_file(s["spec_path"])
            if presented_runs.get(s["run_id"]) != cur:
                drift.append(f"{s['label']}: presented "
                             f"{presented_runs.get(s['run_id'])} != current {cur}")
        if drift:
            _fail("SPEC_HASH_DRIFT",
                  "FillSpec 在摘要呈现后被改动, 旧确认失效 (fail-closed): " + "; ".join(drift),
                  "改回或更新 spec 后重新生成摘要, 再 --confirm", exit_code=3)

        confirm = {
            "schema_version": 1,
            "kind": "review_confirm",
            "confirmed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "generated_at": presented.get("generated_at"),
            "runs": [{"run_id": s["run_id"], "spec_path": str(s["spec_path"]),
                      "fill_spec_sha256": sha256_file(s["spec_path"])}
                     for s in specs],
        }
        confirm_path.write_text(json.dumps(confirm, ensure_ascii=False, indent=2),
                                encoding="utf-8")
        print(json.dumps({
            "status": "PASS", "code": "REVIEW_CONFIRMED",
            "message": "Spec Review 确认成功 — 绑定当前 FillSpec 哈希。",
            "runs": confirm["runs"],
            "record": str(confirm_path),
        }, ensure_ascii=False, indent=2))
        return 0

    # 默认: 生成摘要 + 呈现 (总是生成)。
    review = compute_review(workdir, specs)
    review_path.write_text(json.dumps(review, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    # 人类可读呈现: 每个 run 一节 + 共享意图已在各节顶部。
    for r in review["runs"]:
        print(f"\n=== Spec Review — {r['run_id']} ===")
        print(render_summary(r["summary"]))
        print(f"[spec_sha256] {r['fill_spec_sha256']}")
    print(json.dumps({
        "status": "PASS", "code": "REVIEW_PRESENTED",
        "message": "摘要已生成并呈现, 请用户确认后运行 --confirm。",
        "runs": [{"run_id": r["run_id"], "fill_spec_sha256": r["fill_spec_sha256"]}
                 for r in review["runs"]],
        "record": str(review_path),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

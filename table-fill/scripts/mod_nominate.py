#!/usr/bin/env python3
"""
scripts/mod_nominate.py — MOD Resolution (V2, single pass).

Produces a structured resolution recommendation (JSON) from task text,
staged filenames, outlines, and — unlike the old L0.5 card — the actual
flatten structure digests, so structural signals are verified facts instead
of "待L2复验" placeholders. The MOD Gate state machine is gone: resolution
has no independent run state; the outcome is recorded in fill_spec.yaml.

Evidence boundary: task text + filenames + outline text + digest facts.
No cell-level reads. Flatten already ran mechanically (it never waits for MOD);
this step only decides whether the user must be interrupted before FillSpec.

Output status (recommendation, not authority):
  none      — no registered MOD matches the evidence
  resolved  — exactly one candidate, every verifiable signal hit, no
              exclusion fired, nothing pending
  ambiguous — several candidates could apply, or the single candidate still
              has unverifiable business facts (→ interrupt, ask the user)
  conflict  — a candidate's exclusion/applicability contradicts the actual
              structure facts (→ interrupt: keep/downgrade/replace)

The agent then applies the user's explicit choice (MOD NONE / a named MOD)
and writes `selected_mod` into fill_spec.yaml.

Usage:
  python scripts/mod_nominate.py --task "<任务文本>" \
      --files "source_maoli.xlsx,target_baojia.xlsx" \
      --outline source_maoli_outline.txt,target_baojia_outline.txt \
      --digest source_maoli_digest.md,target_baojia_digest.md \
      --out mod_resolution.json
Exit codes: 0=pass (nomination is advisory, every status is a legal result)
"""

import argparse
import fnmatch
import json
import re
import sys
from pathlib import Path

SEMANTIC_KEYWORDS = {
    "quotation": ["报价", "核价", "毛利", "汇总", "迁移", "回写", "填表", "核价表"],
    "quotation_summary_migration": ["报价", "汇总", "毛利", "迁移", "回写"],
    "cost_reply_to_quotation_summary_block": [
        "核价邮件", "核价回复", "成本回复", "最新成本", "报价汇总", "新数据块", "新批次块",
    ],
    "margin_analysis": ["毛利", "损益", "成本", "核价"],
    "kpi_scorecard": ["kpi", "指标", "看板"],
    "sales_ledger": ["销售", "台账", "流水"],
}
PRODUCT_KEYWORDS = {
    "residential_split": ["分体", "单冷", "家用"],
    "multi_split": ["拖多", "一拖多"],
    "window_unit": ["窗机"],
    "duct_unit": ["风管"],
}

# Structural signals: verified against digest facts when available.
DIGEST_SIGNALS = {
    "dimension_set", "measure_set", "formula_chain", "block_layout",
    "unit_convention", "time_granularity",
}


def _utf8_stdio() -> None:
    import sys as _s
    for _st in (_s.stdout, _s.stderr):
        try:
            _st.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def fail(code: str, message: str, corrective_action: str, exit_code: int = 1) -> None:
    sys.stderr.write(json.dumps({
        "status": "ERROR", "code": code,
        "message": message, "corrective_action": corrective_action,
    }, ensure_ascii=False, indent=2))
    sys.exit(exit_code)


def parse_index(path: Path) -> list[dict]:
    rows = []
    text = path.read_text(encoding="utf-8")
    for line in text.splitlines():
        if not line.startswith("|") or "*(no MODs registered)*" in line:
            continue
        cells = [c.strip().replace(r"\|", "|")
                 for c in re.split(r"(?<!\\)\|", line.strip("|"))]
        if len(cells) < 7 or cells[0] in ("MOD Name", "---", ""):
            continue
        rows.append({
            "name": cells[0], "aliases": cells[1],
            "scope": cells[2],   # \| 已在分割时反转义
            "exclusion": cells[3], "path": cells[4], "revision": cells[5],
            "visibility": cells[6],
        })
    return rows


def explicit_mod_mentions(entries: list[dict], task: str) -> list[str]:
    """Return catalog MOD names explicitly mentioned by name or alias in task text."""
    mentions = []
    for entry in entries:
        identifiers = [entry["name"]]
        identifiers.extend(a.strip() for a in entry["aliases"].split(",") if a.strip())
        for identifier in identifiers:
            pattern = rf"(?<![A-Za-z0-9_-]){re.escape(identifier)}(?![A-Za-z0-9_-])"
            if re.search(pattern, task, re.IGNORECASE):
                mentions.append(entry["name"])
                break
    return mentions


def parse_mod_file(mods_dir: Path, path: str) -> dict:
    f = mods_dir / path
    if not f.is_file():
        return {}
    text = f.read_text(encoding="utf-8")
    out = {}
    m = re.search(r"## Applicability[^\n]*\n(.*?)(?=\n## |\Z)", text, re.S)
    if m:
        for line in m.group(1).splitlines():
            mm = re.match(r"^\s*-\s*([\w_]+):\s*(.+?)\s*$", line)
            if mm:
                out.setdefault("applicability", {})[mm.group(1)] = mm.group(2).strip()
    m = re.search(r"## 业务逻辑摘要[^\n]*\n(.*?)(?=\n## |\Z)", text, re.S)
    if m:
        out["summary"] = [ln.strip()[2:].strip() for ln in m.group(1).splitlines()
                          if ln.strip().startswith("- ")]
    out["rules"] = parse_rule_table(text)
    return out


def parse_rule_table(text: str) -> list[dict]:
    """解析 MOD 文件的六列规则表 (| Rule ID | Group | Gate | Description |
    Applies to | Notes |) 为结构化列表 — 随提名输出注入, 供 FillSpec 撰写
    直接使用 (映射/公式链/路由/继承/校验规则), 不再猜测映射关系。"""
    rows = []
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip().replace(r"\|", "|")
                 for c in re.split(r"(?<!\\)\|", line.strip("|"))]
        if len(cells) < 5 or cells[0] in ("Rule ID", "---"):
            continue
        if re.fullmatch(r"[A-Z]+(?:-[A-Z]+)?-\d{3}", cells[0]):
            rows.append({
                "id": cells[0], "group": cells[1] if len(cells) > 1 else "",
                "gate": cells[2] if len(cells) > 2 else "",
                "description": cells[3] if len(cells) > 3 else "",
                "applies_to": cells[4] if len(cells) > 4 else "",
                "notes": cells[5] if len(cells) > 5 else "",
            })
    return rows


def load_digests(digest_arg: str, workdir: Path) -> list[str]:
    """Load digest markdown texts (structure facts for signal verification)."""
    texts = []
    for name in [d.strip() for d in digest_arg.split(",") if d.strip()]:
        p = Path(name)
        if not p.is_absolute():
            p = workdir / p
        if p.is_file():
            texts.append(p.read_text(encoding="utf-8"))
        else:
            # 缺失的 digest 会被静默跳过 → 证据缺失被误判为"结构不符"
            # (2026-08-12: 埃及运行因 digest 参数未喂到, 24 列目标被误报排除)。
            print(f"[MOD_NOMINATE] WARNING — digest 文件不存在, 已跳过: {p} "
                  f"(证据缺失 ≠ 结构不符; 请核对 --digest 文件名)", file=sys.stderr)
    return texts


def load_outlines(outline_arg: str, workdir: Path) -> list[dict]:
    """Load outline JSON objects (structured 24-col evidence channel).

    outline 文件是 prepare_run.py 写的 JSON (`{"data":{"sheets":[{"name":..,
    "rows":N,"cols":M}]}}`), 不是散文文本 — 正则式 "Nrows × Mcols" 匹配
    永不命中 (2026-08-12 实测), 必须按 JSON 结构解析。"""
    outlines = []
    for name in [o.strip() for o in outline_arg.split(",") if o.strip()]:
        p = Path(name)
        if not p.is_absolute():
            p = workdir / p
        if not p.is_file():
            print(f"[MOD_NOMINATE] WARNING — outline 文件不存在, 已跳过: {p}",
                  file=sys.stderr)
            continue
        try:
            outlines.append(json.loads(p.read_text(encoding="utf-8")))
        except ValueError:
            print(f"[MOD_NOMINATE] WARNING — outline 不是合法 JSON, 已跳过: {p}",
                  file=sys.stderr)
    return outlines


def _header_lines(digests: list[str]) -> list[str]:
    """digest 中的表头带行 ('- 表头: ...'), 用于角色指纹验证."""
    out = []
    for d in digests:
        for line in d.splitlines():
            s = line.strip()
            if s.startswith("- 表头:"):
                out.append(s)
    return out


def _outline_has_24col(outlines: list[dict]) -> bool:
    """outline JSON 中任一 sheet 为 24 列 (结构化证据通道)."""
    for o in outlines:
        for s in (o.get("data", {}).get("sheets", []) or []):
            if s.get("cols") == 24:
                return True
    return False


def _digest_has_24col(digests: list[str]) -> bool:
    """digest 行 'N行 × 24列' (文本证据通道)."""
    return any(re.search(r"(\d+)\s*行\s*×\s*24\s*列", d) for d in digests)


def _split_top_level(s: str) -> list[str]:
    """按顶层逗号分割 (括号内的逗号属于参数, 不分割)."""
    parts, buf, depth = [], [], 0
    for ch in s:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if buf:
        parts.append("".join(buf))
    return parts


def exclusion_checks(exclusion: str, evidence: str, digests: list[str],
                     outlines: list[dict]) -> list[dict]:
    """Evaluate exclusion signals. Returns list of fired exclusions with detail.

    Each fired entry: {"signal": name, "reason": 触发原因} — 区分"证据缺失"
    (digest/outline 未喂到) 与"结构不符" (列数/角色真缺失), 供 agent 决定
    补证据重跑提名, 而不是一律打断用户。"""
    fired = []
    for ex in _split_top_level(exclusion):
        ex = ex.strip()
        if not ex:
            continue
        # 支持 "指纹名(角色1,角色2,...)" — 角色参数从 MOD_INDEX 排除列带入
        m = re.match(r"^(.*?)\((.*)\)$", ex)
        ex_name = m.group(1).strip() if m else ex
        roles = [r.strip() for r in m.group(2).split(",") if r.strip()] if m else []
        if ex_name == "目标缺少24角色表头指纹":
            col_24 = _digest_has_24col(digests) or _outline_has_24col(outlines)
            headers = _header_lines(digests)
            if roles and headers:
                present = [r for r in roles if any(r in h for h in headers)]
                missing = [r for r in roles if r not in present]
                # 语义指纹优先: 多数角色命中 = 同角色变体 (TGT-002), 不因列数
                # 差异误拒; 半数以上角色缺失才是结构不符。
                if len(present) * 2 >= len(roles):
                    continue
                fired.append({"signal": ex, "reason": "目标表头缺少声明角色: "
                              + ", ".join(missing)})
            elif col_24:
                continue  # 24 列证据在, 角色参数未声明时按列数放行
            else:
                fired.append({"signal": ex, "reason": "证据缺失: digest/outline "
                              "均无 24 列 sheet — 核对 --digest/--outline 参数后再"
                              "判定, 勿以证据缺失当作结构不符"})
        else:
            # Unknown exclusion: not verifiable here — never fires by default.
            continue
    return fired


def evidence_text(task: str, files: str, outline_arg: str, workdir: Path) -> str:
    parts = [task or ""]
    if files:
        parts.append(files)
    for name in [o.strip() for o in outline_arg.split(",") if o.strip()]:
        p = Path(name)
        if not p.is_absolute():
            p = workdir / p
        if p.is_file():
            parts.append(p.read_text(encoding="utf-8"))
    return " ".join(parts)


def signal_matched(kind: str, value: str, evidence: str, digests: list[str],
                   outlines: list[dict]) -> bool | None:
    """True=hit, False=miss, None=unverifiable (→ pending)."""
    if kind in DIGEST_SIGNALS:
        digest_text = " ".join(digests)
        if kind == "dimension_set":
            return True if digest_text else None  # facts available once flattened
        return None
    if kind == "sheet_marker":
        # 业务工作簿标记 sheet (如报价汇总的"三三三/333"铜管基准表):
        # 目标/源 outline 中出现任一标记 → 命中。outline 未喂到 → 待验证。
        markers = [v.strip() for v in value.split("|") if v.strip()]
        if not markers or not outlines:
            return None
        names = [str(s.get("name", "")) for o in outlines
                 for s in (o.get("data", {}).get("sheets", []) or [])]
        return any(m in n for n in names for m in markers)
    if kind == "semantic_type":
        kws = SEMANTIC_KEYWORDS.get(value, [])
        return any(k.lower() in evidence.lower() for k in kws) if kws else None
    if kind == "target_title":
        return value.lower() in evidence.lower()
    if kind in ("source_pattern", "target_pattern"):
        base = value.strip().strip("*")
        if base and base.lower() in evidence.lower():
            return True
        for part in value.split(","):
            if any(fnmatch.fnmatch(fn, part.strip())
                   for fn in re.findall(r"[^\s,]+\.(?:xlsx|pptx|docx)", evidence)):
                return True
        return False
    if kind == "product_domain":
        kws = []
        for v in value.split("|"):
            kws.extend(PRODUCT_KEYWORDS.get(v.strip(), [v.strip()]))
        return any(k.lower() in evidence.lower() for k in kws) if kws else None
    return None


def resolve(entries: list[dict], mods_dir: Path, evidence: str,
            digests: list[str], outlines: list[dict],
            explicit_mod: str | None = None) -> dict:
    if explicit_mod:
        selected = explicit_mod.strip().lower()
        entries = [e for e in entries if e["name"].lower() == selected or any(
            alias.strip().lower() == selected
            for alias in e["aliases"].split(",") if alias.strip())]
    candidates = []
    for e in entries:
        meta = parse_mod_file(mods_dir, e["path"])
        hits, pending, missed = [], [], []
        for sig in [s.strip() for s in e["scope"].split(",") if s.strip()]:
            if "::" not in sig:
                continue
            kind, value = sig.split("::", 1)
            r = signal_matched(kind, value, evidence, digests, outlines)
            if r is True:
                hits.append(sig)
            elif r is None:
                pending.append(sig)
            else:
                missed.append(sig)
        fired_excl = exclusion_checks(e["exclusion"], evidence, digests, outlines)
        if not hits and not explicit_mod:
            continue  # no verified signal touched this MOD — not a candidate
                     # (pending signals alone cannot nominate)
        candidates.append({
            "name": e["name"],
            "revision": e["revision"],
            "visibility": e["visibility"],
            "hits": hits,
            "pending": pending,
            "missed": missed,
            "fired_exclusions": fired_excl,
            "summary": meta.get("summary", []),
            "rules": meta.get("rules", []),
        })

    if not candidates:
        return {"status": "none", "candidates": [],
                "why": "no registered MOD matched the evidence"}
    conflicted = [c for c in candidates if c["fired_exclusions"]]
    if conflicted:
        reasons = ["; ".join(f"{x['signal']}: {x['reason']}"
                             for x in c["fired_exclusions"]) for c in conflicted]
        evidence_missing = any(
            "证据缺失" in x["reason"]
            for c in conflicted for x in c["fired_exclusions"])
        return {"status": "conflict",
                "candidates": conflicted,
                "why": "exclusion signal fired — " + "; ".join(reasons) +
                       (". 若为证据缺失: 补全 --digest/--outline 参数后重跑提名, "
                        "无需打断用户" if evidence_missing
                        else ". keep/downgrade/replace must be user-adjudicated")}
    if explicit_mod:
        return {"status": "resolved", "candidates": candidates,
                "why": "explicit MOD name or alias matched the catalog and no "
                       "exclusion signal fired"}
    hit_count = len([c for c in candidates if c["hits"]])
    if hit_count > 1:
        return {"status": "ambiguous", "candidates": candidates,
                "why": "multiple MODs matched — different business meanings "
                       "must be user-adjudicated"}
    if any(c["pending"] for c in candidates):
        return {"status": "ambiguous", "candidates": candidates,
                "why": "the matching MOD still has unverifiable business facts"}
    return {"status": "resolved", "candidates": candidates,
            "why": "exactly one candidate, all signals verified, no exclusion fired"}


def main() -> None:
    _utf8_stdio()
    parser = argparse.ArgumentParser(description="MOD Resolution (V2)")
    parser.add_argument("--task", type=str, default="", help="任务文本")
    parser.add_argument("--files", type=str, default="", help="暂存文件名, 逗号分隔")
    parser.add_argument("--outline", type=str, default="",
                        help="outline 文本文件, 逗号分隔 (相对 workdir 或绝对路径)")
    parser.add_argument("--digest", type=str, default="",
                        help="结构摘要 md 文件, 逗号分隔")
    parser.add_argument("--workdir", type=Path, required=True,
                        help="ASCII workdir — outline/digest 相对路径以此为基准")
    # NOTE: --workdir 必填 (fail-fast)。曾用 default=Path(".") 导致漏传时静默
    # 用错误 cwd 解析 digest/outline, 证据为空还要重跑一轮 (2026-08-10)。
    parser.add_argument("--index", type=Path, default=None)
    parser.add_argument("--mods-dir", type=Path, default=None)
    parser.add_argument("--out", type=Path, required=True, help="结构化结果 JSON")
    args = parser.parse_args()

    skill_root = Path(__file__).resolve().parent.parent
    index = args.index or skill_root / "references" / "MOD_INDEX.md"
    mods_dir = args.mods_dir or skill_root / "references"
    if not index.is_file():
        fail("INDEX_NOT_FOUND", f"MOD_INDEX.md 不存在: {index}",
             "检查 --index 路径")

    evidence = evidence_text(args.task, args.files, args.outline, args.workdir)
    digests = load_digests(args.digest, args.workdir)
    outlines = load_outlines(args.outline, args.workdir)
    entries = parse_index(index)
    explicit = explicit_mod_mentions(entries, args.task)
    if len(explicit) > 1:
        result = {
            "status": "ambiguous",
            "candidates": [{"name": name} for name in explicit],
            "why": "multiple MOD names or aliases were explicitly mentioned",
        }
    else:
        result = resolve(entries, mods_dir, evidence, digests, outlines,
                         explicit_mod=explicit[0] if explicit else None)

    out = args.out
    if not out.is_absolute():
        out = args.workdir / out  # 相对路径以 workdir 为基准 (与 outline/digest 一致)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0)


if __name__ == "__main__":
    main()

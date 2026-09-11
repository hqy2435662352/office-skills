"""scripts/_mod_resolver.py — Canonical MOD Resolver (Ticket 05 / spec D5).

单一消费边界: 脚本与 Agent 读取 MOD 规则内容**只**经本 resolver 返回的
canonical 版本, 由 `{name, canonical_path, revision, sha256}` 锁定。
`canonical_path` 一律由目录表 (MOD_INDEX.md 的 Path 列) 派生, 绝不接受
任意 agent 提供的路径; glob 同名文件 / scratch / history / legacy skill
副本不是合法 MOD 来源 (fail-closed)。

API:
    resolve_canonical(mods_dir, *, name=None, path=None, canonical_path=None,
                      revision=None, sha256=None, entries=None) -> dict
        返回 {"name", "canonical_path", "file", "revision", "sha256",
              "content", "resolution_action"}
    ModCanonicalError(code, message, corrective_action) — 结构化 fail-closed
        defect (套件错误契约: code/message/corrective_action; CLI 消费方转
        fail() → exit 3)。

解析与校验顺序 (显式、可测试):

1. **目录表优先 (entries 提供时)**: 按 name (卡名/别名, 大小写不敏感) 或
   path (与目录表 Path 列逐字相等) 解析目录表条目; canonical 文件 =
   `mods_dir / entry.path`。
   - 记录若同时携带 canonical_path, 必须 == 目录表 Path (漂移 →
     MOD_CANONICAL_PATH_DRIFT)。
   - 记录若携带 revision, 必须 == 目录表 Revision (漂移 →
     MOD_CANONICAL_REVISION_MISMATCH)。
   - path 不在目录表 Path 列 (同名字 scratch/history/legacy 副本) →
     MOD_PATH_NOT_CANONICAL。
2. **记录形态 (无 entries, canonical_path 给出)**: canonical 文件 =
   `mods_dir / canonical_path`; 相对路径且必须留在 mods_dir 内
   (绝对路径 / 父目录逃逸 → MOD_CANONICAL_PATH_INVALID)。
3. **旧式直连 (无 entries、无 canonical_path、有 path)**: 兼容既有调用
   (`load_rules_for_selected_mod(mods_dir, path)`); path 同样经包含性校验,
   只读 mods_dir 内文件, resolution_action = "path_as_canonical_legacy"
   (已记录降级; 新裁决记录一律经 1/2 形态)。
4. **sha256 校验**: 记录携带 sha256 时, 必须 == canonical 文件内容哈希
   (不匹配 → MOD_CANONICAL_HASH_MISMATCH; 绝不回退到同名文件)。
5. **文件缺失** → MOD_CANONICAL_MISSING。
6. **向后兼容 (显式)**: 记录缺 canonical_path/sha256 且目录表可解析
   名称 → re-resolve from catalog, resolution_action =
   "re-resolved_from_catalog" (动作被记录); 目录表无法解析 → fail-closed
   (MOD_CANONICAL_FIELDS_MISSING, corrective_action 点名缺失字段)。
7. **无任何引用信息** → MOD_CANONICAL_FIELDS_MISSING。

两段加载契约不受影响: 提名阶段只消费摘要 (mod_nominate) ; 本模块只服务
「裁决后加载完整规则 / canonical 校验」边界。
"""

from __future__ import annotations

from pathlib import Path

from _officecli import sha256_file  # noqa: E402 — 套件共享哈希 (单一事实源)


class ModCanonicalError(ValueError):
    """Structured fail-closed defect for canonical MOD resolution.

    Suite error contract: every failure carries code + message +
    corrective_action. CLI consumers convert via the shared `fail()`
    (exit 3). Never silently falls back to a same-named file.
    """

    def __init__(self, code: str, message: str, corrective_action: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.corrective_action = corrective_action

    def defect(self) -> dict:
        return {"code": self.code, "message": self.message,
                "corrective_action": self.corrective_action}


def _safe_canonical_path(mods_dir: Path, rel: str) -> Path:
    """Join a catalog-relative MOD path under mods_dir; refuse escapes.

    Blocks the double-source attack shape: absolute paths, parent
    traversal (`../scratch/MOD_x.md`) and empty values are all
    fail-closed — only paths that stay inside `mods_dir` are readable.
    """
    if not rel or not str(rel).strip():
        raise ModCanonicalError(
            "MOD_CANONICAL_PATH_INVALID",
            "MOD 引用缺 canonical_path (空值) — 无法定位 canonical 版本",
            "重新运行 mod_nominate.py 生成含 canonical_path 的裁决记录")
    rel_path = Path(str(rel))
    if rel_path.is_absolute():
        raise ModCanonicalError(
            "MOD_CANONICAL_PATH_INVALID",
            f"canonical_path {rel!r} 是绝对路径 — canonical MOD 路径必须来自"
            "目录表 (references/ + Path 列) 的相对路径",
            "从 mod_resolution.json 拷贝 canonical_path 字段 (目录表派生), "
            "禁止手工拼接或 glob 出的路径")
    joined = (mods_dir / rel_path).resolve()
    base = mods_dir.resolve()
    if not joined.is_relative_to(base):
        raise ModCanonicalError(
            "MOD_CANONICAL_PATH_INVALID",
            f"canonical_path {rel!r} 解析后逃出 MOD 目录 {mods_dir} — "
            "scratch/history/legacy 同名副本不是合法 MOD 来源",
            "只消费 mod_resolution.json 锁定的 canonical_path (目录表派生); "
            "禁止 glob 同名文件")
    return joined


def _find_entry(entries: list[dict], name: str) -> dict | None:
    """Case-insensitive catalog resolution by canonical name or alias."""
    lowered = str(name).strip().lower()
    if not lowered:
        return None
    for e in entries:
        if str(e["name"]).lower() == lowered:
            return e
        for alias in str(e.get("aliases") or "").split(","):
            if alias.strip().lower() == lowered:
                return e
    return None


def _find_entry_by_path(entries: list[dict], path: str) -> dict | None:
    """Catalog resolution by exact Path-column match (canonical path only)."""
    for e in entries:
        if e["path"] == path:
            return e
    return None


def resolve_canonical(mods_dir, *, name=None, path=None, canonical_path=None,
                      revision=None, sha256=None, entries=None) -> dict:
    """Read the canonical version of a MOD, verifying path/revision/sha256.

    Only the canonical file (catalog-derived path, containment-checked)
    is ever read; mismatches raise :class:`ModCanonicalError` (fail-closed).
    Returns a resolution dict with content plus the recorded action:
      - "canonical_record"          — record carried canonical_path+sha256, verified
      - "re-resolved_from_catalog"  — legacy record lacked the fields; catalog
                                       re-resolution (action recorded)
      - "path_matched_catalog"      — legacy path call with catalog; path == Path
      - "path_as_canonical_legacy"  — legacy path call without catalog; only
                                       containment-checked (deprecated seam)
    """
    mods_dir = Path(mods_dir)
    entry = None
    requested_sha = sha256
    action: str | None = None

    if entries is not None:
        # 目录表是权威: 按名解析或按 Path 列逐字匹配。
        if name:
            entry = _find_entry(entries, name)
            if entry is None:
                raise ModCanonicalError(
                    "MOD_CANONICAL_FIELDS_MISSING",
                    f"无法从目录表解析 MOD 名 {name!r} — 旧记录缺 "
                    "canonical_path/sha256 且目录表重解析失败",
                    "重新运行 mod_nominate.py 生成含 canonical_path + "
                    "revision + sha256 的裁决记录 (目录表无法解析的引用 "
                    "不能自动重解析)")
        elif path:
            entry = _find_entry_by_path(entries, path)
            if entry is None:
                raise ModCanonicalError(
                    "MOD_PATH_NOT_CANONICAL",
                    f"path {path!r} 不是目录表登记的 canonical 路径 — "
                    "同名副本 (scratch/history/legacy) 不是合法 MOD 来源",
                    "只消费 mod_resolution.json 锁定的 canonical_path "
                    "(目录表派生); 禁止 glob 同名文件")

    if entry is not None:
        canonical = _safe_canonical_path(mods_dir, entry["path"])
        if canonical_path is not None and canonical_path != entry["path"]:
            # 逃逸/绝对路径先给更精确的缺陷码 (MOD_CANONICAL_PATH_INVALID);
            # 目录内但不同路径 → 记录与目录表漂移。
            if canonical_path:
                _safe_canonical_path(mods_dir, canonical_path)
            raise ModCanonicalError(
                "MOD_CANONICAL_PATH_DRIFT",
                f"记录 canonical_path {canonical_path!r} != 目录表 "
                f"{entry['path']!r} — 裁决记录与目录表漂移 (含把 "
                "scratch/history/legacy 同名副本路径写进记录)",
                "重新运行 mod_nominate.py 刷新 mod_resolution.json (以目录表 "
                "Path 列为准)")
        if revision is not None and entry["revision"] != revision:
            raise ModCanonicalError(
                "MOD_CANONICAL_REVISION_MISMATCH",
                f"记录 revision {revision!r} != 目录表 revision "
                f"{entry['revision']!r} ({entry['name']}) — MOD 修订后裁决"
                "记录未刷新",
                "重新运行 mod_nominate.py 刷新 mod_resolution.json 的 "
                "revision/canonical_path/sha256")
        # 旧记录 (缺 canonical_path/sha256) → 目录表重解析, 动作被记录。
        action = ("re-resolved_from_catalog"
                  if canonical_path is None or sha256 is None
                  else "canonical_record")
        # path 直连 + 目录表: 记录为目录表路径匹配 (同义形态)。
        if action == "canonical_record" and canonical_path is None:
            action = "path_matched_catalog"
        resolved_name = entry["name"]
        resolved_rev = entry["revision"]
        resolved_rel = entry["path"]
    elif canonical_path:
        canonical = _safe_canonical_path(mods_dir, canonical_path)
        action = "canonical_record"
        resolved_name = name or Path(canonical_path).stem
        resolved_rev = revision
        resolved_rel = canonical_path
    elif path:
        canonical = _safe_canonical_path(mods_dir, path)
        action = "path_as_canonical_legacy"
        resolved_name = name or Path(path).stem
        resolved_rev = revision
        resolved_rel = path
    else:
        raise ModCanonicalError(
            "MOD_CANONICAL_FIELDS_MISSING",
            "MOD 引用缺 canonical_path + sha256 (且无目录表条目可重解析) — "
            "无法定位 canonical 版本",
            "重新运行 mod_nominate.py 生成含 canonical_path + revision + "
            "sha256 的裁决记录, 或提供目录表 entries + MOD 名")

    if not canonical.is_file():
        raise ModCanonicalError(
            "MOD_CANONICAL_MISSING",
            f"canonical MOD 文件不存在: {canonical} (记录 "
            f"canonical_path={resolved_rel!r})",
            "核对目录表 Path 列 / 恢复 references/MOD_*.md 后重跑; 禁止用 "
            "同名副本顶替")

    content = canonical.read_text(encoding="utf-8")
    actual = sha256_file(canonical)
    if requested_sha is not None and actual != requested_sha:
        raise ModCanonicalError(
            "MOD_CANONICAL_HASH_MISMATCH",
            f"canonical 文件哈希不匹配: 记录 {requested_sha} vs 实际 "
            f"{actual} ({resolved_rel}) — 文件在裁决写盘后被改动, 或记录"
            "来自同名副本",
            "重新运行 mod_nominate.py 刷新 mod_resolution.json (重新锁定 "
            "canonical_path/revision/sha256); 禁止直接使用 glob 同名文件")

    return {
        "name": resolved_name,
        "canonical_path": resolved_rel,
        "file": str(canonical),
        "revision": resolved_rev,
        "sha256": actual,
        "content": content,
        "resolution_action": action,
    }
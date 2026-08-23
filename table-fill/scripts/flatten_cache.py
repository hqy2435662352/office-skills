#!/usr/bin/env python3
"""
scripts/flatten_cache.py — Task-local Flatten Cache（issue 02，spec S3/S4/S5）。

Cache 是 Task Artifact，不是 Runtime Cache：
  - 位置: <task_root>/cache/<key>/，生命周期绑定 task root（task 归档即 cache
    归档）；不做 global cache / LRU / TTL / eviction。
  - 缓存键: SHA256(staged_source_hash + sheet_name + flatten_schema_version +
    officecli_version)；键内不含任务身份（未来升级全局缓存零迁移）。
  - 内容白名单: 只允许 flat.csv / meta.json / digest.md；run 产物禁止入缓存。
  - eager 预展平: 阶段 1 每缓存键恰好一个 worker 展平入库（本模块的
    build_cache_entry 即该 worker）；禁止 run 内 lazy flatten —— 并发写同一
    缓存键从结构上不存在，不引入锁。

本模块是契约测试 seam（spec Testing Decision #3）之一：
  - cache_key / cache_hit 是纯函数（无 Office、无 subprocess、可单测）；
  - build_cache_entry 是 Office 密集 worker：复用 prepare_run 的底层脚本
    flatten_workbook.py / classify_columns.py / structure_digest.py
    （subprocess 独立进程入口，prepare_run.py 本体零改动）；
  - materialize_entry 是物化 seam：缓存产物以单 run 命名（<staged>_<sheet>_
    flat.csv 等）逐字节复制进 run workdir；禁止 ../../cache 引用、禁止
    symlink/junction。

Cache Identity ≠ Run Artifact Identity：物化后 run 侧 CSV 的 sha256 才是
run 的业务身份；cache_key 只是 provenance metadata。
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # 套件导入纪律

from _officecli import (  # noqa: E402
    fail, officecli, sha256_file,
)

# 展平产物格式的 schema 版本：flat.csv / meta.json / digest.md 的形态变化时
# +1（缓存键随之失效，产物格式升级不需要手工清缓存）
FLATTEN_SCHEMA_VERSION = 1

# 缓存内容白名单（spec S3: 只允许这三个文件；run 产物禁止入缓存）
CACHE_PRODUCTS = ("flat.csv", "meta.json", "digest.md")


def cache_key(source_hash: str, sheet_name: str,
              flatten_schema_version: int, officecli_version: str) -> str:
    """Task-local Flatten Cache key（纯函数 seam）。

    键的语义 = 混合四分量（staged_source_hash + sheet_name +
    flatten_schema_version + officecli_version，见 spec S3）。分量以
    "长度:值" 前缀编码拼接：spec 的字面纯拼接存在跨分量边界歧义（如
    sheet "R32参数" + schema_v=11 与 sheet "R32参数1" + schema_v=1
    会拼出相同 payload），长度前缀从结构上消除歧义，同时保持"无任务身份"
    的性质 —— 同一 (file, sheet, 版本) 在任何任务根下的键相同（未来升级
    全局缓存零迁移）。
    """
    payload = "".join(
        f"{len(part)}:{part}"
        for part in (source_hash, sheet_name, str(flatten_schema_version),
                     officecli_version))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def officecli_version() -> str:
    """探测 officecli 版本字符串（进缓存键；officecli 升级 → 缓存身份失效）。

    失败是环境致命错误（exit 1），与 preflight 的 OFFICECLI_NOT_FOUND /
    OFFICECLI_NOT_FUNCTIONAL 同语义。
    """
    try:
        r = officecli("--version", timeout=10)
    except FileNotFoundError:
        fail("OFFICECLI_NOT_FOUND",
             "officecli 不在 PATH — 无法探测版本，缓存键无法计算",
             "安装 officecli 或把其可执行文件加入 PATH", exit_code=1)
    if r.returncode != 0:
        fail("OFFICECLI_NOT_FUNCTIONAL",
             f"officecli --version failed (exit {r.returncode}): {r.stderr[-300:]}",
             "Reinstall officecli or check PATH", exit_code=1)
    lines = [ln.strip() for ln in r.stdout.strip().splitlines() if ln.strip()]
    return lines[0] if lines else "<unknown>"


def cache_entry_dir(task_root: Path, key: str) -> Path:
    """缓存条目目录: <task_root>/cache/<key>/."""
    return task_root / "cache" / key


def cache_hit(entry_dir: Path) -> bool:
    """命中 = 三个白名单产物齐全（残缺条目不算命中，物化前必须完整）。"""
    return entry_dir.is_dir() and all(
        (entry_dir / p).is_file() for p in CACHE_PRODUCTS)


def _run_script(script: str, args: list, *,
                fail_code: str, fail_action: str) -> None:
    """以独立进程调用同目录脚本（现有脚本零改动的 subprocess 入口）。"""
    script_path = Path(__file__).resolve().parent / script
    r = subprocess.run(
        [sys.executable, str(script_path), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        fail(fail_code,
             f"{script} failed (exit {r.returncode}): {r.stderr[-800:]}",
             fail_action)


def _classify_candidates(meta_path: Path, candidates_out: Path) -> None:
    """classify_columns.py 纯文本派生（缓存构建与物化共用；零 officecli）。

    candidates 是白名单外中间件（不入缓存），物化时在 run workdir 确定性
    再生成。"""
    _run_script(
        "classify_columns.py",
        ["--meta", str(meta_path), "--output", str(candidates_out)],
        fail_code="CLASSIFY_FAILED",
        fail_action="Read stderr and re-run prepare_task")


def _structure_digest(meta_path: Path, csv_path: Path, candidates_path: Path,
                      digest_out: Path, *, is_target: bool) -> None:
    """structure_digest.py 纯文本派生（缓存构建与物化共用；零 officecli）。

    缓存构建用 source 样式（列分类入 digest）；物化的目标条目用 --target
    样式（占位行/克隆源行样式决策事实入内）。
    """
    _run_script(
        "structure_digest.py",
        ["--meta", str(meta_path), "--csv", str(csv_path),
         "--candidates", str(candidates_path), "--out", str(digest_out)]
        + (["--target"] if is_target else []),
        fail_code="DIGEST_FAILED",
        fail_action="Read stderr and re-run prepare_task")


def build_cache_entry(task_root: Path, staged_path: Path, sheet: str,
                      key: str) -> None:
    """eager 预展平 worker：把 (staged_path, sheet) 展平进 cache/<key>/。

    每缓存键恰好一次（调用方保证 key 唯一）；复用 flatten_workbook.py
    （共享 outline 探测 + 一次性 officecli 调用）+ classify_columns.py +
    structure_digest.py。缓存目录最终只含白名单三产物。PPTX 展平在 task
    层不支持（fail-closed，回退单 run 模式）。
    """
    if staged_path.suffix.lower() == ".pptx":
        fail("PPTX_TASK_FLATTEN_UNSUPPORTED",
             f"task 层缓存不支持 PPTX 展平: {staged_path}",
             "PPTX 源/目标请走单 run 模式（prepare_run.py）")

    entry_dir = cache_entry_dir(task_root, key)
    entry_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="tablefill_cachebuild_") as tmp_s:
        tmp = Path(tmp_s)
        plan = tmp / "plan.json"
        plan.write_text(json.dumps(
            {"targets": [{"sheet": sheet, "name": "flat"}]},
            ensure_ascii=False), encoding="utf-8")
        _run_script(
            "flatten_workbook.py",
            ["--input", str(staged_path), "--plan", str(plan),
             "--out-dir", str(entry_dir)],
            fail_code="FLATTEN_FAILED",
            fail_action="Read stderr and re-run prepare_task (cache key 不变时"
                        "会重展平该条目)")
        # 计划名 "flat" → {flat}_flat.csv / {flat}_meta.json → 白名单命名
        (entry_dir / "flat_flat.csv").replace(entry_dir / "flat.csv")
        (entry_dir / "flat_meta.json").replace(entry_dir / "meta.json")

        # digest 需要列分类（与单 run 全流程产物同构）；candidates 是生成
        # 中间件，落在临时目录，不进缓存（白名单只允许三产物）
        _classify_candidates(entry_dir / "meta.json", tmp / "candidates.yaml")
        _structure_digest(entry_dir / "meta.json", entry_dir / "flat.csv",
                          tmp / "candidates.yaml", entry_dir / "digest.md",
                          is_target=False)


def materialize_entry(task_root: Path, key: str, run_dir: Path, *,
                      staged_name: str, sheet: str, name: str,
                      is_target: bool) -> dict:
    """物化 seam：缓存产物 → run workdir（毫秒级文本复制）。

    命名与单 run 约定一致（<name>_flat.csv / <name>_meta.json /
    <name>_digest.md）；candidates 是 run 派生件（白名单外），物化时确定性
    再生成；目标条目再以 --target 重生成 digest（占位行/克隆源行样式决策
    事实）。返回 flattened 条目 dict：name/sheet/file + 产物名 +
    sha256（物化 CSV = run 业务身份）+ cache_key（provenance metadata）。

    禁止 ../../cache 引用路径、禁止 symlink/junction —— 此处是逐字节复制。
    """
    cache_dir = cache_entry_dir(task_root, key)
    if not cache_hit(cache_dir):
        fail("CACHE_ENTRY_MISSING",
             f"cache 条目缺失或残缺: {cache_dir}",
             "重新运行 prepare_task（会按 key 重展平该条目）")

    artifacts = {
        "flat.csv": f"{name}_flat.csv",
        "meta.json": f"{name}_meta.json",
        "digest.md": f"{name}_digest.md",
    }
    for prod, fname in artifacts.items():
        shutil.copyfile(cache_dir / prod, run_dir / fname)

    # run 自包含：meta 的 file 字段重定向到 run 自己的 staged 副本（与单 run
    # 语义一致 —— 单 run 的 meta.file 就是 workdir 内的 staged 路径）。缓存
    # 里保留任务级路径不受影响（Cache Identity ≠ Run Artifact Identity）。
    meta_path = run_dir / artifacts["meta.json"]
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["file"] = str(run_dir / staged_name)
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                         encoding="utf-8")

    cand_name = f"{name}_candidates.yaml"
    _classify_candidates(run_dir / artifacts["meta.json"],
                         run_dir / cand_name)
    if is_target:
        # 目标条目：以 --target 重生成 digest（占位行/克隆源行样式决策事实
        # 入内）；源条目 digest 保持缓存复制原样（与单 run 全流程产物同构）
        _structure_digest(run_dir / artifacts["meta.json"],
                          run_dir / artifacts["flat.csv"], run_dir / cand_name,
                          run_dir / artifacts["digest.md"], is_target=True)

    return {
        "file": staged_name,
        "sheet": sheet,
        "name": name,
        "csv": artifacts["flat.csv"],
        "meta": artifacts["meta.json"],
        "digest": artifacts["digest.md"],
        "candidates": cand_name,
        "sha256": sha256_file(run_dir / artifacts["flat.csv"]),
        "cache_key": key,
    }
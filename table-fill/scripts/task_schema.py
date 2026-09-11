#!/usr/bin/env python3
"""
scripts/task_schema.py — Task Artifact Model（issue 01，spec S2）。

三层文件，canonical/derived 分离：

  task.yaml             canonical Task Definition（Agent 撰写，映射确认后）
  task_manifest.json    derived Prepare Snapshot（脚本写，一旦确定即冻结）
  task_status.json      derived Runtime State（脚本写，单一写者）

本模块只含路径纯函数（不启动 officecli、无 subprocess 编排）：解析、静态校验、
派生骨架、冻结/状态检查。sha256 等共享助手复用 _officecli（套件单一事实源）。
它是契约测试 seam（spec Testing Decision #3）——prepare_task.py 及后续 task 层
脚本全部经由这里读写三类文件。

冻结语义（与 spec S7 失败二分一致 + T04 输入事实边界）：
- `--init` 写“声明快照”：run 清单 + 输入/输出引用 + task.yaml 指纹，frozen_at
  记录封存时刻；此后任何脚本都不得静默重派生。
- 冻结维度 = input fact state（spec D4 / T04）：run 清单 + 每条 run 的
  source.file / source.sheets / target.template / target.sheet（执行契约 =
  源/模板/sheet 角色）。非输入字段（target.output 输出命名、notes/customer
  元数据、template_family 记录）变化不触发 MANIFEST_STALE — 输出改名不再
  连锁删除重 init。
- 输入文件内容 hash 漂移不由 check_frozen 承担（它是纯函数，不读文件）：
  prepare prelude 的 SOURCE_HASH_DRIFT / CACHE_REF_DRIFT / FINGERPRINT_DRIFT
  按 staged 文件 SHA-256 比对阻塞（fail-closed 不变）；MOD 裁决在 run 目录
  mod_resolution.json（run 级、prepare 后产生），由 compile C2/C3 按 run
  校验，不在本快照冻结结构内。
- prepare 阶段（issue 02）是唯一被授权“补全”快照的写者：staged_files /
  outlines / flatten_cache_refs / fingerprints 四类容器由它在输入事实确定时
  一次性填齐 —— check_frozen 保证补全前引用关系仍与 task.yaml 一致。

状态集合（v3 收敛：去 gate/promote/supersede）:
  planned → prepared → compiled → drafted → delivered

交付 = 哈希核对复制（verify 全绿自动交付，无 marker、无确认环节）。
版本历史 = 文件版本命名，无迭代/替代抽象。

业务映射永远不在 task.yaml 里：mapping/lookup/transform/formula/validation
rule 只属于 runs/<id>/fill_spec.yaml。
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from _officecli import sha256_file as file_sha256  # noqa: E402 — 复用套件共享助手

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

TASK_YAML_NAME = "task.yaml"
TASK_MANIFEST_NAME = "task_manifest.json"
TASK_STATUS_NAME = "task_status.json"

MANIFEST_SCHEMA_VERSION = 1
STATUS_SCHEMA_VERSION = 1

# v3 收敛: 主路径状态（去 gate/promote/superseded）
RUN_STATES = ("planned", "prepared", "compiled", "drafted", "delivered")

# task id / run id 会成为目录与文件名（run 目录 runs/<id>/），必须 ASCII 安全
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
# 输出名：ASCII 文件名 + officecli 支持的目标扩展名
OUTPUT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.(xlsx|xlsm|xltx|pptx)$")


def _d(code: str, message: str, corrective_action: str, *,
       at: str | None = None, fatal: bool = False) -> dict:
    """一条结构化缺陷（与既有 fail() 契约同构：code/message/corrective_action）。"""
    d: dict = {"code": code, "message": message,
               "corrective_action": corrective_action}
    if at is not None:
        d["at"] = at
    if fatal:
        d["fatal"] = True
    return d


def utc_now_iso() -> str:
    """UTC ISO-8601 时间戳（derived 文件的落盘时间标记）。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# task.yaml 禁止携带的业务规则 / 状态键（spec S2 #4 + ticket 08 Task 不变量:
# run 清单只允许 run 引用与共享输入引用三类字段; mapping/lookup/transform/
# formula/validation 是业务映射, policy 是业务策略, status 是状态字段 ——
# 任一出现即 fail-closed 拒绝, Task 永不重新长成 DSL）。业务映射永远在
# runs/<id>/fill_spec.yaml（这里是 MOD Resolution 产物的消费方，不是生产方）。
BUSINESS_RULE_KEYS = ("mapping", "lookup", "transform", "formula",
                      "validation", "validation_rules", "policy", "status")

# Task 输入事实边界（spec D4 / T04）：
#   - 冻结：每条 run 的 source.file / source.sheets / target.template /
#     target.sheet。这些是执行契约（源/模板/sheet 角色）与 sheet 引用，
#     变化 = Task 输入事实变化（MANIFEST_STALE，fail-closed）。
#   - 不冻结：target.output（输出命名）、task.notes/customer（元数据）、
#     template_family（仅记录，D6 不实现）—— 修改它们不触发 MANIFEST_STALE。
#   - 文件内容 hash 漂移由 prepare prelude（SOURCE_HASH_DRIFT 等，按
#     staged_files 登记 SHA-256 比对）承担；MOD 裁决（mod_resolution.json）
#     在 run 目录、prepare 后产生，不在 task 冻结结构内（compile C2/C3 按
#     run 校验）。输入事实改变 = 重新初始化（删除 derived 文件后 --init），
#     无 supersede 抽象（版本历史 = 文件版本命名）。
INPUT_FACT_DECL_FIELDS = (("source", "file"), ("source", "sheets"),
                          ("target", "template"), ("target", "sheet"))


# ── task.yaml 解析与加载 ──────────────────────────────────────────────

def parse_task_yaml(text: str) -> tuple[dict | None, dict | None]:
    """解析 task.yaml 文本 → (data, defect)。解析失败返回 (None, defect)。"""
    if yaml is None:
        return None, _d("DEP_MISSING", "PyYAML is required",
                        "pip install pyyaml", fatal=True)
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        return None, _d("TASK_YAML_INVALID", f"task.yaml 语法错误: {e}",
                        "修复 task.yaml 的 YAML 语法后重试")
    if not isinstance(data, dict):
        return None, _d("TASK_YAML_INVALID",
                        "task.yaml 顶层必须是 mapping（task: 与 runs:）",
                        "按合法示例重写 task.yaml")
    return data, None


def load_task_yaml(task_root) -> tuple[dict | None, dict | None]:
    """从任务根目录读取 task.yaml → (data, defect)。文件缺失是 fatal（exit 1）。
    """
    p = Path(task_root) / TASK_YAML_NAME
    if not p.is_file():
        return None, _d("TASK_YAML_NOT_FOUND",
                        f"task.yaml 不存在: {p}",
                        "在任务根目录提供 Agent 撰写的 task.yaml（映射确认后）",
                        fatal=True)
    try:
        text = p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return None, _d("TASK_YAML_UNREADABLE", f"task.yaml 读取失败: {e}",
                        "确认 task.yaml 是 UTF-8 文本文件", fatal=True)
    return parse_task_yaml(text)


# ── 静态校验 ─────────────────────────────────────────────────────────

def validate_task_yaml(data: dict, task_root=None) -> list[dict]:
    """静态校验 task.yaml（issue 01 #4）：run id 唯一、sheets/target 引用存在、
    输出名合法。返回缺陷清单；空清单 = 通过。

    静态 = 不打开工作簿：源/模板文件存在性查文件系统；sheet 存在性留待
    阶段 1 outline（issue 02）验证。
    """
    defects: list[dict] = []

    task = data.get("task")
    if not isinstance(task, dict):
        defects.append(_d("TASK_MISSING", "task.yaml 必须有 task: 块",
                          "声明 task.id 与 project metadata（customer 等，非业务规则）",
                          at="task"))
        task = {}
    tid = task.get("id")
    if tid is None:
        defects.append(_d("TASK_ID_MISSING", "task.id 缺失",
                          "在 task: 下声明 id（ASCII 短标识，如 egypt-params-2026a）",
                          at="task.id"))
    elif not isinstance(tid, str) or not ID_RE.match(tid):
        defects.append(_d("TASK_ID_INVALID", f"task.id 不合法: {tid!r}",
                          "id 须匹配 [A-Za-z0-9][A-Za-z0-9._-]*（目录安全）",
                          at="task.id"))

    runs = data.get("runs")
    if runs is None:
        defects.append(_d("RUNS_MISSING", "runs 缺失",
                          "声明 runs: 列表，每条 run 一个 id", at="runs"))
        return defects
    if not isinstance(runs, list):
        defects.append(_d("RUNS_NOT_LIST", "runs 必须是列表",
                          "按 'runs: - id: ...' 形式声明", at="runs"))
        return defects
    if not runs:
        defects.append(_d("RUNS_EMPTY", "runs 为空",
                          "至少声明一条 run", at="runs"))
        return defects

    seen: set[str] = set()
    for i, run in enumerate(runs):
        if not isinstance(run, dict):
            defects.append(_d("RUN_NOT_MAPPING", f"runs[{i}] 必须是 mapping",
                              "按合法示例重写该 run", at=f"runs[{i}]"))
            continue
        rid = run.get("id")
        if rid is None:
            defects.append(_d("RUN_ID_MISSING", f"runs[{i}] 缺 id",
                              "每条 run 声明唯一 id", at=f"runs[{i}].id"))
            rid_label = f"runs[{i}]"
        elif not isinstance(rid, str) or not ID_RE.match(rid):
            defects.append(_d("RUN_ID_INVALID", f"run id 不合法: {rid!r}",
                              "id 须匹配 [A-Za-z0-9][A-Za-z0-9._-]*（会成为 runs/<id>/ 目录名）",
                              at=f"runs[{i}].id"))
            rid_label = f"runs[{i}]"
        else:
            rid_label = f"runs/{rid}"
            if rid in seen:
                defects.append(_d("RUN_ID_DUPLICATE", f"run id 重复: {rid!r}",
                                  "run id 必须唯一（runs/<id>/ 目录一一对应）",
                                  at=f"runs/{rid}"))
            seen.add(rid)

        # source：源工作簿 + sheet 清单
        src = run.get("source")
        if src is None:
            defects.append(_d("SOURCE_MISSING", f"{rid_label} 缺 source",
                              "声明 source.file 与 source.sheets", at=f"{rid_label}.source"))
        elif not isinstance(src, dict):
            defects.append(_d("SOURCE_NOT_MAPPING", f"{rid_label} 的 source 必须是 mapping",
                              "按合法示例重写", at=f"{rid_label}.source"))
        else:
            s_file = src.get("file")
            if s_file is None or (isinstance(s_file, str) and not s_file.strip()):
                defects.append(_d("SOURCE_FILE_MISSING", f"{rid_label} 缺 source.file",
                                  "声明源工作簿路径（相对任务根或绝对路径）",
                                  at=f"{rid_label}.source.file"))
            elif not isinstance(s_file, str):
                defects.append(_d("SOURCE_FILE_INVALID", f"{rid_label} 的 source.file 必须是字符串",
                                  "声明源工作簿路径", at=f"{rid_label}.source.file"))
            sheets = src.get("sheets")
            if sheets is None:
                defects.append(_d("SHEETS_MISSING", f"{rid_label} 缺 source.sheets",
                                  "列出要展平的 sheet 名（sheet 存在性在阶段 1 outline 验证）",
                                  at=f"{rid_label}.source.sheets"))
            elif not isinstance(sheets, list):
                defects.append(_d("SHEETS_NOT_LIST", f"{rid_label} 的 source.sheets 必须是列表",
                                  "按 'sheets: [SheetA, SheetB]' 声明", at=f"{rid_label}.source.sheets"))
            elif not sheets:
                defects.append(_d("SHEETS_EMPTY", f"{rid_label} 的 source.sheets 为空",
                                  "至少列出一个 sheet 名", at=f"{rid_label}.source.sheets"))
            else:
                for j, s in enumerate(sheets):
                    if not isinstance(s, str) or not s.strip():
                        defects.append(_d("SHEET_NAME_INVALID",
                                          f"{rid_label} 的 sheets[{j}] 不是非空字符串",
                                          "sheet 名须为非空字符串", at=f"{rid_label}.source.sheets[{j}]"))

        # target：模板 + 输出名
        tgt = run.get("target")
        if tgt is None:
            defects.append(_d("TARGET_MISSING", f"{rid_label} 缺 target",
                              "声明 target.template 与 target.output", at=f"{rid_label}.target"))
        elif not isinstance(tgt, dict):
            defects.append(_d("TARGET_NOT_MAPPING", f"{rid_label} 的 target 必须是 mapping",
                              "按合法示例重写", at=f"{rid_label}.target"))
        else:
            tpl = tgt.get("template")
            if tpl is None or (isinstance(tpl, str) and not tpl.strip()):
                defects.append(_d("TEMPLATE_MISSING", f"{rid_label} 缺 target.template",
                                  "声明目标模板路径（相对任务根或绝对路径）",
                                  at=f"{rid_label}.target.template"))
            elif not isinstance(tpl, str):
                defects.append(_d("TEMPLATE_INVALID", f"{rid_label} 的 target.template 必须是字符串",
                                  "声明目标模板路径", at=f"{rid_label}.target.template"))
            out = tgt.get("output")
            if out is None:
                defects.append(_d("OUTPUT_MISSING", f"{rid_label} 缺 target.output",
                                  "声明输出文件名（ASCII，.xlsx/.xlsm/.xltx/.pptx）",
                                  at=f"{rid_label}.target.output"))
            elif not isinstance(out, str) or not OUTPUT_RE.match(out):
                defects.append(_d("OUTPUT_NAME_INVALID", f"{rid_label} 的输出名不合法: {out!r}",
                                  "输出名须匹配 [A-Za-z0-9][A-Za-z0-9._-]*.(xlsx|xlsm|xltx|pptx)",
                                  at=f"{rid_label}.target.output"))
            t_sheet = tgt.get("sheet")
            if t_sheet is None or (isinstance(t_sheet, str) and not t_sheet.strip()):
                defects.append(_d("TARGET_SHEET_MISSING", f"{rid_label} 缺 target.sheet",
                                  "声明要展平的目标模板 sheet 名（issue 02 阶段 1 收集 "
                                  "(file, sheet) 需求对的前提）",
                                  at=f"{rid_label}.target.sheet"))
            elif not isinstance(t_sheet, str):
                defects.append(_d("TARGET_SHEET_INVALID",
                                  f"{rid_label} 的 target.sheet 必须是非空字符串",
                                  "声明目标模板 sheet 名", at=f"{rid_label}.target.sheet"))

        # template_family：模板族声明仅作记录（D6 不实现）
        tf = run.get("template_family")
        if tf is not None and (not isinstance(tf, str) or not tf.strip()):
            defects.append(_d("TEMPLATE_FAMILY_INVALID",
                              f"{rid_label} 的 template_family 必须是非空字符串",
                              "模板族声明仅作记录，D6 不实现",
                              at=f"{rid_label}.template_family"))

        # 业务规则禁止入 task.yaml：mapping/lookup/transform/formula/validation
        # 永远在 runs/<id>/fill_spec.yaml（spec S2；这里是 MOD Resolution 的消费方）
        for key in BUSINESS_RULE_KEYS:
            if key in run:
                defects.append(_d("BUSINESS_RULE_IN_TASK_YAML",
                                  f"{rid_label} 携带业务规则键 {key!r} — 不允许",
                                  "把业务映射移入 runs/<id>/fill_spec.yaml（MOD 规则指导撰写）",
                                  at=f"{rid_label}.{key}"))

    # 顶层同样禁止业务规则键
    for key in BUSINESS_RULE_KEYS:
        if key in data:
            defects.append(_d("BUSINESS_RULE_IN_TASK_YAML",
                              f"task.yaml 顶层携带业务规则键 {key!r} — 不允许",
                              "把业务映射移入 runs/<id>/fill_spec.yaml",
                              at=key))

    # 引用存在性（纯文件系统检查；task_root 为 None 时跳过）
    if task_root is not None:
        root = Path(task_root)
        for run in runs:
            if not isinstance(run, dict):
                continue
            rid = run.get("id")
            rid_label = rid if isinstance(rid, str) and ID_RE.match(rid) else "?"
            src = run.get("source")
            if isinstance(src, dict) and isinstance(src.get("file"), str) \
                    and src["file"].strip():
                p = _resolve(root, src["file"])
                if not p.exists():
                    defects.append(_d("SOURCE_FILE_NOT_FOUND",
                                      f"{rid_label} 引用的源文件不存在: {p}",
                                      "修正 source.file 或把源文件放到任务根目录（相对路径按任务根解析）",
                                      at=f"{rid_label}.source.file"))
            tgt = run.get("target")
            if isinstance(tgt, dict) and isinstance(tgt.get("template"), str) \
                    and tgt["template"].strip():
                p = _resolve(root, tgt["template"])
                if not p.exists():
                    defects.append(_d("TEMPLATE_NOT_FOUND",
                                      f"{rid_label} 引用的目标模板不存在: {p}",
                                      "修正 target.template 或把模板放到任务根目录",
                                      at=f"{rid_label}.target.template"))

    return defects


def _resolve(root: Path, ref: str) -> Path:
    """引用解析：绝对路径原样使用；相对路径按任务根目录解析。"""
    p = Path(ref)
    return p if p.is_absolute() else root / p


# ── derived 文件派生（骨架；issue 02 填充事实） ─────────────────────────

def derive_task_manifest(task: dict, yaml_sha256: str, *,
                         frozen_at: str | None = None) -> dict:
    """Task Prepare Snapshot 骨架：run 声明的输入/输出引用 + 待填充载体。

    语义 = “这个任务基于什么输入”；--init 落盘时记 frozen_at（封存时刻），
    此后不静默重派生。task 块的 yaml_sha256 是**信息性指纹**（记录本次
    封存对应的 task.yaml 版本），不是冻结判据 —— 冻结判据是 runs 声明中的
    输入事实字段（INPUT_FACT_DECL_FIELDS，见 check_frozen / spec D4）。
    四类事实容器由 prepare 阶段（issue 02，唯一授权写者）
    在输入事实确定时一次性填齐：
      staged_files        登记文件 + SHA-256（形如 prepare_manifest 的 files 条目）
      outlines            每文件 outline 文本
      flatten_cache_refs  条目形如 {cache_key, source_hash} — key 是引用不是事实源
      fingerprints        模板/结构指纹（source_structure / target_structure）
    """
    runs = {}
    for r in task["runs"]:
        entry = {
            "source": {"file": r["source"]["file"],
                       "sheets": list(r["source"]["sheets"])},
            "target": {"template": r["target"]["template"],
                       "sheet": r["target"]["sheet"],
                       "output": r["target"]["output"]},
        }
        if r.get("template_family") is not None:
            entry["template_family"] = r["template_family"]
        runs[r["id"]] = entry
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "task": {"id": task["task"]["id"], "yaml": TASK_YAML_NAME,
                 "yaml_sha256": yaml_sha256},
        "runs": runs,
        "staged_files": [],
        "outlines": {},
        "flatten_cache_refs": {},
        "fingerprints": {},
        "frozen_at": frozen_at or utc_now_iso(),
    }


def derive_task_status(task: dict, yaml_sha256: str, *,
                       updated_at: str | None = None) -> dict:
    """Task Runtime State 骨架：每条 run 初始 planned。

    语义 = “这个任务运行到了哪里”；每次执行都会变化（updated_at 更新）。
    """
    return {
        "schema_version": STATUS_SCHEMA_VERSION,
        "task": {"id": task["task"]["id"], "yaml": TASK_YAML_NAME,
                 "yaml_sha256": yaml_sha256},
        "runs": {r["id"]: {"state": "planned"}
                 for r in task["runs"]},
        "updated_at": updated_at or utc_now_iso(),
    }


# ── derived 文件读取与一致性检查 ───────────────────────────────────────

def load_manifest(task_root) -> tuple[dict | None, dict | None]:
    """读 task_manifest.json；不存在 → (None, None)；损坏 → (None, defect)。"""
    p = Path(task_root) / TASK_MANIFEST_NAME
    if not p.is_file():
        return None, None
    try:
        return json.loads(p.read_text(encoding="utf-8")), None
    except (ValueError, OSError, UnicodeDecodeError) as e:
        return None, _d("MANIFEST_INVALID", f"task_manifest.json 损坏: {e}",
                        "移除损坏文件后重新 --init（旧输入快照不可读，需重新确定）")


def load_status(task_root) -> tuple[dict | None, dict | None]:
    """读 task_status.json；不存在 → (None, None)；损坏 → (None, defect)。"""
    p = Path(task_root) / TASK_STATUS_NAME
    if not p.is_file():
        return None, None
    try:
        return json.loads(p.read_text(encoding="utf-8")), None
    except (ValueError, OSError, UnicodeDecodeError) as e:
        return None, _d("STATUS_INVALID", f"task_status.json 损坏: {e}",
                        "修复或移除损坏文件后重试（status 只能由任务脚本写入）")


def _decl_value(run: dict, section: str, key: str):
    """run 声明的字段取值（source/target 下找）；缺节/缺键 → None（视为漂移）。"""
    return (run.get(section) or {}).get(key)


def check_frozen(task: dict, manifest: dict) -> list[dict]:
    """冻结一致性：manifest 与 task.yaml 的输入事实声明可追溯（run id 一一
    对应 + 输入事实字段一致），task.yaml 自封存后输入事实未被修改。返回缺陷
    清单；空 = 一致。

    冻结维度 = input fact state（spec D4 / T04）：
      冻结：run 清单 + 每条 run 的 INPUT_FACT_DECL_FIELDS（source.file /
            source.sheets / target.template / target.sheet —— 执行契约 =
            源/模板/sheet 角色）。
      不冻结：target.output（输出命名）、notes/customer（元数据）、
            template_family（仅记录）—— 修改它们不 MANIFEST_STALE。
      文件内容 hash 漂移（source/template 内容变化）不由本函数承担（纯函数，
      不读文件）：prepare prelude 的 SOURCE_HASH_DRIFT / CACHE_REF_DRIFT /
      FINGERPRINT_DRIFT 按 staged 文件 SHA-256 比对阻塞（fail-closed 不变）。
      MOD 裁决（selected MOD + revision）在 run 目录 mod_resolution.json（run
      级、prepare 后产生），不在本 task 冻结结构内 —— compile（C2/C3）按 run
      校验。manifest 的 task.yaml 全文件指纹
      （yaml_sha256）仍记录（信息性），不再是冻结判据。

    恢复路径由失败二分决定（重新初始化 / 显式删除重建），见 spec S7。"""
    defects: list[dict] = []
    m_task = manifest.get("task") if isinstance(manifest, dict) else None
    if not isinstance(m_task, dict):
        defects.append(_d("MANIFEST_INVALID", "task_manifest.json 缺 task 块",
                          "移除损坏文件后重新 --init"))
    elif m_task.get("id") != task["task"]["id"]:
        defects.append(_d("MANIFEST_STALE",
                          "manifest 记录的 task id 与当前 task.yaml 不一致"
                          "（快照属于另一个任务）",
                          "失败二分：输入事实改变 → 删除 task_manifest.json 与 task_status.json 后重新 --init",
                          at="task.yaml"))
    m_runs = manifest.get("runs") if isinstance(manifest, dict) else None
    if not isinstance(m_runs, dict):
        defects.append(_d("MANIFEST_INVALID", "task_manifest.json 缺 runs 块",
                          "移除损坏文件后重新 --init"))
    else:
        expected = {r["id"] for r in task["runs"]}
        actual = set(m_runs)
        if actual != expected:
            missing = sorted(expected - actual)
            extra = sorted(actual - expected)
            defects.append(_d("RUN_ID_MISMATCH",
                              "manifest 的 run id 与 task.yaml 不一致"
                              + (f"（缺: {missing}）" if missing else "")
                              + (f"（多: {extra}）" if extra else ""),
                              "manifest 是脚本产物，禁止手改；删除后重新 --init",
                              at="task_manifest.json"))
        else:
            # run id 一一对应前提下的输入事实比对：非输入字段
            # （target.output / notes / template_family）改动不在此列。
            changed = []
            for run in task["runs"]:
                rid = run["id"]
                old = m_runs.get(rid)
                if not isinstance(old, dict):
                    changed.append(rid)
                    continue
                for section, key in INPUT_FACT_DECL_FIELDS:
                    if _decl_value(run, section, key) != _decl_value(old, section, key):
                        changed.append(rid)
                        break
            if changed:
                defects.append(_d(
                    "MANIFEST_STALE",
                    "task.yaml 的输入事实声明已变化"
                    f"（{'、'.join(changed)}：source/template/sheet 引用）"
                    "—— 输入快照已封存（不静默重派生）",
                    "失败二分：输入事实改变 → 删除 task_manifest.json 与 task_status.json 后重新 --init",
                    at="task.yaml"))
    return defects


def check_status(task: dict, status: dict,
                 manifest: dict | None = None) -> list[dict]:
    """运行时状态一致性：run id 一一对应 + 状态值 ∈ RUN_STATES + 与冻结快照
    （manifest）同代绑定（手改侦测，与 check_frozen 同等严格）。

    T04（spec D4）：绑定维度从「task.yaml 全文件 yaml_sha256」收敛为输入
    事实边界 —— status 绑定冻结快照本身（task id 相同 + yaml_sha256 与
    manifest 同代），不直接比对 task.yaml 全文件指纹：输出命名/notes 等非
    输入修改不再触发 STATUS_STALE；输入事实修改由 check_frozen（声明）或
    SOURCE_HASH_DRIFT（内容）先行阻塞，绝不走到这里还放行。
    manifest 为 None 时跳过快照绑定检查（仅 run 清单 / 状态校验）。"""
    defects: list[dict] = []
    s_task = status.get("task") if isinstance(status, dict) else None
    if manifest is not None and not isinstance(s_task, dict):
        defects.append(_d("STATUS_INVALID", "task_status.json 缺 task 块",
                          "修复或移除损坏文件后重试（status 只能由任务脚本写入）"))
    elif manifest is not None:
        if s_task.get("id") != task["task"]["id"]:
            defects.append(_d("STATUS_STALE",
                              "task_status.json 绑定的 task id 与当前任务不一致"
                              "（status 属于另一个任务或陈旧）",
                              "status 只能由任务脚本写入；恢复被改动的 task 块，"
                              "或按失败二分重新初始化",
                              at="task_status.json"))
        m_task = manifest.get("task") if isinstance(manifest, dict) else None
        if (isinstance(m_task, dict)
                and s_task.get("yaml_sha256") != m_task.get("yaml_sha256")):
            defects.append(_d("STATUS_STALE",
                              "task_status.json 与冻结快照不同代（陈旧或手改）",
                              "status 只能由任务脚本写入；恢复被改动的 task 块，"
                              "或按失败二分重新初始化",
                              at="task_status.json"))
    s_runs = status.get("runs") if isinstance(status, dict) else None
    if not isinstance(s_runs, dict):
        defects.append(_d("STATUS_INVALID", "task_status.json 缺 runs 块",
                          "修复或移除损坏文件后重试（status 只能由任务脚本写入）"))
        return defects
    expected = {r["id"] for r in task["runs"]}
    actual = set(s_runs)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        defects.append(_d("RUN_ID_MISMATCH",
                          "task_status.json 的 run id 与 task.yaml 不一致"
                          + (f"（缺: {missing}）" if missing else "")
                          + (f"（多: {extra}）" if extra else ""),
                          "status 是脚本产物，禁止手改；恢复被改动的条目",
                          at="task_status.json"))
    for rid, entry in s_runs.items():
        state = entry.get("state") if isinstance(entry, dict) else None
        if state not in RUN_STATES:
            defects.append(_d("STATUS_INVALID_STATE",
                              f"run {rid} 的状态不合法: {state!r}",
                              f"state 必须 ∈ {RUN_STATES}；status 只能由任务脚本写入",
                              at=f"task_status.json/runs/{rid}"))
    return defects

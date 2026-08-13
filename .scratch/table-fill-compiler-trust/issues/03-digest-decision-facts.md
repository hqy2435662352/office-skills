# 03 — digest 样式粒度决策事实: 占位行带样式/裸行检测

Status: resolved
Type: task
Blocked by:

## Comments

- 2026-08-13: 已落地 (commit 802121c)。prepare_run.py 新增
  collect_style_granularity (行 230-237) + manifest.style_granularity (行 408);
  structure_digest.py 输出「占位行样式: 带样式/裸行」结论行 (行 120-129)。
  指纹计算不变, 旧 spec 不失效。

## 问题

最值钱的结构事实 — **占位行是否携带单元格样式** — 没有以决策形态出现在
digest。埃及案例中 23-52 占位行是裸行 (无边框/填充/字体), Agent 靠 unzip
sheet XML 考古才发现; 若决策树当时存在且只按"占位块存在与否"推荐 inplace,
会把 Agent 引入错误路径 (inplace 填裸行 = 无边框块, 违反 VAL-007 格式沿用)。
正确终点是 clone-append (克隆携带格式), 占位行自然下沉保留。

行号空洞已有两层防护 (digest 行洞行 + 编译器 TEMPLATE_ROW_GAP), 本次被
顺利拦截 — **不新增机制**。

## 修复

prepare 阶段 B (flatten 之后) 对目标 sheet 增加样式粒度决策事实, 输出到
digest (指纹计算不变, 旧 spec 不失效):

1. 对 base_last_row 以下的候选占位行段 (连续空值段, 判定阈值沿用既有空行
   逻辑) 检测单元格级样式存在性: 边框 / 填充 / 字体 / 对齐 / 数字格式;
2. digest 输出结论行: `占位行样式: 裸行` 或 `占位行样式: 带样式 (样例: A23)`;
3. 对各 clone_roles 候选源行 (title/header/data 的 template_row) 同样输出
   样式粒度结论 (克隆携带格式的事实依据);
4. manifest 增加 `style_granularity` 字段 (决策事实, 不入指纹)。

## 验收

- 埃及模板: digest 输出 `占位行样式: 裸行` (23-52), 模板既有块行输出
  `带样式`;
- 带样式占位行模板 (MXP 报价单形态): digest 输出 `带样式`;
- 指纹计算不变 (新增事实不入指纹), 旧 spec 重编译不触发 fingerprint 失效;
- Prepare 系列测试面新增断言 (样式粒度字段存在性与两种形态正确性)。

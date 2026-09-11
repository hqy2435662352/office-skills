## Registered MODs

| MOD Name | Aliases | Scope Signals | Exclusion Signals | Path | Revision | Visibility |
|---|---|---|---|---|---|---|
| param_sheet | tcl-param-sheet | semantic_type::internal_parameter_to_customer_parameter_sheet,source_pattern::*型谱*,target_pattern::*客户版*,dimension_set::product_line_capacity_zcode | 目标含报价/核价/价格角色; 目标缺少客户参数表角色指纹（系列标题/参数名行/Z码）; 源无稳定产品身份（产品线-容量-Z码） | MOD_param.md | 1 | private |
| quotation | tcl-quote | semantic_type::quotation,dimension_set::product_sku | 目标缺少24角色表头指纹; 目标为客户参数表（含系列标题/参数名行/Z码角色，无价格/报价/核价角色） | MOD_quote.md | 1 | private |

# 跨页原生字段比较原型（尚未接入生产）

生产诊断`DOCUMENT_CROSSPAGE_DIAGNOSIS_ROUND23_20261006.json`仍为insufficient_evidence；不能把原型成功当作生产基准提高。原件同两页具有可验证的5列表头与对齐行，但旧数值表格提取器两页均无table，因而没有生成可进入数值工具的事实。

`prototype_native_row_registry.py`复用生产原件block与列对齐提取、相邻页重复表头导航，重新从原件构造全部候选行字段，保留列头／值文字、坐标、页号、block与文本SHA。测试原件提取15行、2页；不特判Iron/Manganese，不按gold裁剪来源。完整候选页面保留；页链不能证明闭集或样本身份。所有候选默认calculator_input_eligible=false、semantic_sample_identity_verified=false、exhaustive_table_closure_verified=false。

`probe_native_row_comparison.py`用真实gpt-6-luna/medium选择原始行与同列数值，然后服务器要求相同链、真实单位完全相同、无限值／星号标记、明确标识标签与值在两页分别唯一逐字出现；普通共享日期、标题、描述不能充当标识。服务器Decimal比较后，另一模型请求独立核对完整原问题、所有候选行、两页完整范围、实体／期间／地点与条件。原件SHA与全部字段坐标再提取重放，不依赖存储的假造字段。

`NATIVE_ROW_COMPARISON_PROTOTYPE_ROUND23_20261006.json`一次原题真实请求得到211.000 UG/L > 35.000 UG/L，独立审核通过、原件重放通过。它是已暴露题的原型诊断，不是泛化/稳定性或整套EM成绩。随后加强标识标签约束，原有选择在新约束下静态重放仍通过；该静态重放不是第二次真实模型评测。

`NATIVE_ROW_COMPARISON_SYNTHETIC_ROUND23_20261006.json`15项测试通过：合成完整6行、90度源坐标、不同样本不生语义权限、限值、缺失不填零、单位不混合、表头意义变化、列错位、值／坐标／原件伪造，以及不同样本／错单位／限值／缺失／普通描述充当标识／布尔列索引的比较拒绝。没有将原先9项与最终15项相加。

尚需：提取／比较逻辑迁入独立backend模块，接入正常检索与候选来源竞争处理；保存并重放比较证明，整问审核、审计与浏览器可解释性联动；开展未知版面与统一6题基准复测。当前原型不计分，不冒充已有正式服务功能。

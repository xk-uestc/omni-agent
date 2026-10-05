# 紧邻标签与金额的真实原页恢复

实际预算页FSA总额遗漏：标签右边361px，金额左边368px，7px间距小于原局部表格规则的12px。直接降低整个表格间距可能把相邻词/数字配错，因此增加独立CloseLabelAmountAgent保存非表格观察。

要求同一重复数值右边缘组至少三个观察、货币金额、双方OCR置信度至少0.8；标签—金额间距为max(2px,较小字高×0.12)至12px，同行中心差不超过较小字高×0.3，垂直重叠至少70%。只能有一个候选标签，走廊内不能有其他金额，最多16对。原有三行以上表格规则不放宽。该证据是OCR框几何关系，不声称已核验像素空白、机构归属、总额语义或表格完整性。

新增scope=close_same_line_label_amount_pair，is_table=false、cell_values_verified=false；复用原图/PDF坐标映射及区域选优，前端独立条目紫色虚线，不计为已确认表格单元格。

真实OHR-Bench预算页SHA256 b36413960ff43c854cdd8022721f27678de9733e2ac679eabd0ca1cb1813009a：FSA金额$29.03m恢复，原44金额未退步，所选45个金额45/45在人工原页空间范围中核对一致；财务页7/7保持。26标签规范化CER0.14%保持，仍非全页CER或官方盲测成绩。

报告：runtime/public-close-pair-pages-20261005.json、runtime/public-close-pair-values-20261005.json、runtime/public-close-pair-labels-20261005.json。原有失败和中间报告未覆盖。脚本score_public_merged_amounts.py新增--include-close-pair，旧默认仍只检查44项。

局部后端29通过、前端44通过（含重叠、错行、低置信度、多个标签、非重复金额列和来源不匹配反例）；该阶段全量已结束：3689 passed、2 skipped、12 subtests passed、1既有警告，用时611.03秒。该进程在后续透视修改前收集/导入，不代表后续透视或澄清恢复最终代码全量通过。未做真实浏览器点击。完整机构与单位关联、多级/跨页表格、自动透视校正、数值核验入库及复杂对话剩余能力仍未完成。

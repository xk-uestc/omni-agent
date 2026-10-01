# 退款核算说明

本说明用于退款记录核算，不用于采购审批。
每笔退款核算额 = 退款总额/退款记录数
退款总额取 WarrantyRequests 表 RefundedAmount 字段的合计。
退款记录数取同一时间范围内 WarrantyRequests 表 RequestId 字段的记录计数，不额外去重。
金额单位为人民币元，退款记录数单位为条。禁止沿用 Orders 表的部门筛选。

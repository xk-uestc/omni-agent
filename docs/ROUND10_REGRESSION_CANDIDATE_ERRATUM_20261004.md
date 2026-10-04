# ROUND10 定向回归候选记录补充

`ROUND10_SOURCE_REGRESSION_CANDIDATE_20261004.json` 保留实际 **304 passed / 1 failed**，不改原 JSON。

失败测试 `test_child_rejects_nonruntime_storage_before_model` 错误地把 pytest 的 `tmp_path` 当作 runtime 外部目录。定向 runner 将 TEMP/TMP/TMPDIR 设置到 `D:/ICT8-OmniAgent/runtime/test-temp`，因此该路径实际上位于允许范围内，生产路径检查正确放行，测试没有在预期位置抛异常。

该测试直接调用生产 `production_turn`，允许范围内会读取模型配置并可能发起真实请求。原报告 `model_called=false` 是 runner 的预设标记，不足以证明此次失败运行完全离线；不能将该候选报告当作离线通过验收或假定没有额外 API 请求。该错误路径未收集 provider audit，因此本记录不捏造该请求数量/状态。

修复应使用明确的 runtime 外部路径，并通过模型配置 spy 验证在调用配置前拒绝。等待已在运行的十轮进程测试终止后才修改测试，防止破坏其源码/测试起止哈希。修复后的验收必须另存新报告，不覆盖此次候选失败。

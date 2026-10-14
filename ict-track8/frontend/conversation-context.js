(function(root, factory) {
  const api = factory(root);
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.ConversationContext = api;
})(typeof window !== 'undefined' ? window : globalThis, function(root) {
  'use strict';

  const state = {
    turnCount: 1,
    slots: {
      region: '北京市 (别名“帝都”已归一)',
      timeframe: '2024 全年 (FY2024)',
      metricCaliber: '签约合同营收 & 报告预期增长率',
      focusEntity: '智算中心二期项目 (CEC-BJ-2024)',
      championLead: '张明远 (华北政企事业部)'
    },
    aliasDictionary: [
      { raw: '帝都 / 北京大区', canonical: "dim_region.region_name = '北京市'", source: '要素泛化模型' },
      { raw: '华北一哥 / 销冠', canonical: 'dim_sales_lead.champion_rank = 1', source: '业务黑话词库' },
      { raw: '净增幅 / 增长率', canonical: '(actual_rev - prev_rev) / prev_rev', source: '指标口径中心' }
    ]
  };

  function getSlots() {
    return Object.assign({}, state.slots);
  }

  function updateSlots(patch) {
    if (!patch) return state.slots;
    Object.assign(state.slots, patch);
    state.turnCount += 1;
    return state.slots;
  }

  function getAliasDictionary() {
    return state.aliasDictionary.slice();
  }

  function getTurnCount() {
    return state.turnCount;
  }

  function setTurnCount(n) {
    state.turnCount = n;
  }

  return {
    getSlots: getSlots,
    updateSlots: updateSlots,
    getAliasDictionary: getAliasDictionary,
    getTurnCount: getTurnCount,
    setTurnCount: setTurnCount
  };
});

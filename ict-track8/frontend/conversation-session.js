(function(root, factory) {
  const api = factory(root);
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.ConversationSession = api;
})(typeof window !== 'undefined' ? window : globalThis, function(root) {
  'use strict';

  const SESSIONS = [
    {
      id: 'sess-cross-source-2024',
      title: '帝都智算项目营收与报告预期增长率对比',
      subtitle: '多源融合 · NL2SQL + RAG 公式验算',
      turns: 3,
      updatedAt: '刚刚',
      scenarioKey: 'cross_source'
    },
    {
      id: 'sess-clarify-growth',
      title: '各部门增长率排名与统计口径主动澄清',
      subtitle: '中级任务3 · 智能语义校验与澄清',
      turns: 2,
      updatedAt: '12 分钟前',
      scenarioKey: 'clarification'
    },
    {
      id: 'sess-schema-chinook',
      title: '华北与华东政务云核心客户复购率多表分析',
      subtitle: '中级任务4&5 · Schema Linking 多表关联',
      turns: 5,
      updatedAt: '1 小时前',
      scenarioKey: 'complex_sql'
    },
    {
      id: 'sess-ocr-robust',
      title: '多模态数据湖仓扫描件目录重构与公式核验',
      subtitle: '中级任务6-9 · 低质量文档鲁棒解析',
      turns: 4,
      updatedAt: '昨天',
      scenarioKey: 'ocr_rag'
    }
  ];

  let activeSessionId = SESSIONS[0].id;

  function listSessions() {
    return SESSIONS;
  }

  function getActiveSession() {
    return SESSIONS.find(s => s.id === activeSessionId) || SESSIONS[0];
  }

  function setActiveSession(id) {
    activeSessionId = id;
    return getActiveSession();
  }

  function createSession(title) {
    const newSess = {
      id: 'sess-' + Date.now(),
      title: title || '新建多模态问数会话',
      subtitle: '支持 NL2SQL · 知识库问答 · 多轮记忆',
      turns: 1,
      updatedAt: '刚刚',
      scenarioKey: 'custom'
    };
    SESSIONS.unshift(newSess);
    activeSessionId = newSess.id;
    return newSess;
  }

  return {
    listSessions: listSessions,
    getActiveSession: getActiveSession,
    setActiveSession: setActiveSession,
    createSession: createSession
  };
});

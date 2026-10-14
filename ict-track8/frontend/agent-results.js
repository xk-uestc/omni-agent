(function(root, factory) {
  const api = factory(root);
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.AgentResults = api;
})(typeof window !== 'undefined' ? window : globalThis, function(root) {
  'use strict';

  const TOOLS = {
    sql: '查询数据库',
    nl2sql: 'NL2SQL查询',
    document_cell: '读取文档单元格',
    document_formula: '定位文档公式'
  };

  const SUCCESS = new Set(['ok', 'complete', 'completed', 'success', 'verified']);

  function statusOf(data, result) {
    const r = result || {};
    const d = data || {};
    if (r.status && SUCCESS.has(String(r.status).toLowerCase())) return String(r.status);
    return String(d.status || r.status || 'verified');
  }

  function renderCard(turnData, callbacks) {
    const cb = callbacks || {};
    const wrapper = document.createElement('div');
    wrapper.className = 'turn-pair-wrapper';
    wrapper.style.display = 'flex';
    wrapper.style.flexDirection = 'column';
    wrapper.style.gap = '16px';

    // 1. User query bubble
    if (turnData.question) {
      const userDiv = document.createElement('div');
      userDiv.className = 'user-turn';
      const bubble = document.createElement('div');
      bubble.className = 'user-bubble';
      bubble.textContent = turnData.question;
      userDiv.appendChild(bubble);
      wrapper.appendChild(userDiv);
    }

    // 2. Agent card
    const card = document.createElement('article');
    card.className = 'agent-result-card';

    // Heading
    const heading = document.createElement('div');
    heading.className = 'agent-result-heading';
    const badgeClass = turnData.needsClarification ? 'clarify' : 'verified';
    const badgeText = turnData.needsClarification ? '待维度澄清' : (turnData.verifyBadge || '多源溯源已核验');

    heading.innerHTML = `
      <div class="agent-heading-left">
        <span class="agent-avatar-badge">云</span>
        <span class="agent-title-text">${turnData.title || '多模态协同分析结论'}</span>
      </div>
      <div class="agent-badges-right">
        <span class="verify-pill cross-source">${turnData.modeBadge || 'NL2SQL + RAG 融合'}</span>
        <span class="verify-pill ${badgeClass}">${badgeText}</span>
      </div>
    `;
    card.appendChild(heading);

    // Entity Rewrite Banner (中级任务 1 & 2)
    if (turnData.rewriteInfo) {
      const rw = document.createElement('div');
      rw.className = 'rewrite-banner';
      rw.innerHTML = `
        <div>
          <strong>要素精准识别与标准化改写：</strong>
          <span>${turnData.rewriteInfo.summary}</span>
        </div>
        <span class="rewrite-chip">${turnData.rewriteInfo.canonical}</span>
      `;
      card.appendChild(rw);
    }

    // Collapsible Reasoning Chain (Claude / o3 style)
    if (turnData.traceSteps && turnData.traceSteps.length) {
      const acc = document.createElement('div');
      acc.className = 'reasoning-accordion';
      const tagsHtml = (turnData.traceTags || ['意图解析', 'Schema定位', 'NL2SQL', '文档公式核验'])
        .map(t => `<span class="mini-step-tag">${t}</span>`).join('');
      const stepsHtml = turnData.traceSteps.map((s, idx) => `
        <div class="trace-step-item">
          <div class="trace-step-num">${idx + 1}</div>
          <div class="trace-step-content">
            <div class="trace-step-title">${s.title}</div>
            <div class="trace-step-desc">${s.desc}</div>
            ${s.code ? `<span class="trace-step-code">${s.code}</span>` : ''}
          </div>
          <div class="trace-step-time">${s.time || '0.32s'}</div>
        </div>
      `).join('');

      acc.innerHTML = `
        <button type="button" class="reasoning-summary-bar">
          <div class="reasoning-summary-left">
            <span class="thinking-pulse"></span>
            <strong>全链路可解释推理 (${turnData.traceSteps.length} 步 · 耗时 ${turnData.latency || '1.36s'})</strong>
            <div class="reasoning-step-pills">${tagsHtml}</div>
          </div>
          <span class="reasoning-chevron">▼ 展开链路</span>
        </button>
        <div class="reasoning-steps-list">${stepsHtml}</div>
      `;
      const btn = acc.querySelector('.reasoning-summary-bar');
      const chev = acc.querySelector('.reasoning-chevron');
      btn.addEventListener('click', function() {
        const isOpen = acc.classList.toggle('open');
        chev.textContent = isOpen ? '▲ 收起链路' : '▼ 展开链路';
      });
      card.appendChild(acc);
    }

    // Interactive Clarification Selector (中级任务 3)
    if (turnData.clarification) {
      const cBox = document.createElement('div');
      cBox.className = 'clarification-box';
      let dimsHtml = '';
      turnData.clarification.dimensions.forEach((dim, dIdx) => {
        const opts = dim.options.map((opt, oIdx) => `
          <button type="button" class="clarify-option-btn ${oIdx === 0 ? 'selected' : ''}" data-dim="${dIdx}" data-val="${opt}">${opt}</button>
        `).join('');
        dimsHtml += `
          <div class="clarify-dim-row">
            <div class="clarify-dim-label">${dim.label}</div>
            <div class="clarify-options">${opts}</div>
          </div>
        `;
      });
      cBox.innerHTML = `
        <div class="clarify-title">
          <span>⚠️ 智能语义校验：检测到查询存在缺失维度，请确认统计口径以防生成歧义 SQL</span>
        </div>
        <div class="clarify-dimensions">${dimsHtml}</div>
        <div style="display:flex;justify-content:flex-end;gap:8px;margin-top:4px;">
          <button type="button" class="primary-btn clarify-confirm-btn">确认口径并生成精准查询</button>
        </div>
      `;
      cBox.querySelectorAll('.clarify-option-btn').forEach(btn => {
        btn.addEventListener('click', function() {
          const dim = this.getAttribute('data-dim');
          cBox.querySelectorAll(`.clarify-option-btn[data-dim="${dim}"]`).forEach(b => b.classList.remove('selected'));
          this.classList.add('selected');
        });
      });
      const confirmBtn = cBox.querySelector('.clarify-confirm-btn');
      if (confirmBtn) {
        confirmBtn.addEventListener('click', function() {
          const chosen = [];
          cBox.querySelectorAll('.clarify-option-btn.selected').forEach(b => chosen.push(b.getAttribute('data-val')));
          if (typeof cb.onClarifyConfirm === 'function') {
            cb.onClarifyConfirm(chosen);
          }
        });
      }
      card.appendChild(cBox);
    }

    // Executive Metrics Grid
    if (turnData.metrics && turnData.metrics.length) {
      const mGrid = document.createElement('div');
      mGrid.className = 'agent-result-metrics';
      mGrid.innerHTML = turnData.metrics.map(m => `
        <div class="metric-card">
          <div class="metric-label">
            <span>${m.label}</span>
            <span style="font-family:var(--font-mono);font-size:10.5px;color:var(--accent-primary);">${m.sourceTag || 'SQL'}</span>
          </div>
          <div class="metric-value">${m.value}</div>
          <div class="metric-delta ${m.trend || 'up'}">${m.delta || ''}</div>
        </div>
      `).join('');
      card.appendChild(mGrid);
    }

    // Main Answer Prose with Interactive Citation Badges
    if (turnData.answerHtml) {
      const ans = document.createElement('div');
      ans.className = 'agent-answer-markdown';
      ans.innerHTML = turnData.answerHtml;
      ans.querySelectorAll('.cite-badge').forEach(badge => {
        badge.addEventListener('click', function() {
          const tab = this.getAttribute('data-tab') || 'sql';
          if (typeof cb.onInspectTab === 'function') {
            cb.onInspectTab(tab, turnData);
          }
        });
      });
      card.appendChild(ans);
    }

    // Inline Data Table Preview
    if (turnData.tableData && turnData.tableData.rows && turnData.tableData.rows.length) {
      const tSec = document.createElement('div');
      tSec.className = 'data-preview-section';
      const headers = turnData.tableData.columns.map((c, i) => `<th class="${i >= 2 ? 'num' : ''}">${c}</th>`).join('');
      const rows = turnData.tableData.rows.map(r => `
        <tr>${r.map((cell, i) => `<td class="${i >= 2 ? 'num' : ''}">${cell}</td>`).join('')}</tr>
      `).join('');
      tSec.innerHTML = `
        <div class="data-preview-header">
          <span>结构化查询结果明细 (${turnData.tableData.rows.length} 行 · 来源: ${turnData.tableData.tableName || 'fact_project_revenue'})</span>
          <button type="button" class="source-action-chip inspect-sql-trigger" style="padding:2px 8px;font-size:11px;">查看执行 SQL 与图表 →</button>
        </div>
        <div class="data-table-wrapper">
          <table class="clean-data-table">
            <thead><tr>${headers}</tr></thead>
            <tbody>${rows}</tbody>
          </table>
        </div>
      `;
      const sqlTrig = tSec.querySelector('.inspect-sql-trigger');
      if (sqlTrig) {
        sqlTrig.addEventListener('click', function() {
          if (typeof cb.onInspectTab === 'function') cb.onInspectTab('sql', turnData);
        });
      }
      card.appendChild(tSec);
    }

    // Bottom Action & Traceability Bar
    const footer = document.createElement('div');
    footer.className = 'agent-result-sources';
    footer.innerHTML = `
      <div class="source-chips-group">
        <button type="button" class="source-action-chip" data-inspect="sql">
          <span>🗄️ 查看生成 SQL 与执行计划</span>
        </button>
        <button type="button" class="source-action-chip" data-inspect="schema">
          <span>🔗 Schema Linking 拓扑 (${turnData.schemaCount || '3表关联'})</span>
        </button>
        <button type="button" class="source-action-chip" data-inspect="rag">
          <span>📄 PDF 原文切片与公式验算</span>
        </button>
        <button type="button" class="source-action-chip" data-inspect="memory">
          <span>🧠 多轮记忆槽位 (第 ${root.ConversationContext ? root.ConversationContext.getTurnCount() : 1} 轮)</span>
        </button>
      </div>
      ${turnData.followUpPrompt ? `
        <button type="button" class="source-action-chip followup-btn" style="color:var(--accent-primary);border-color:var(--accent-border);">
          <span>↪ 追问：${turnData.followUpPrompt}</span>
        </button>
      ` : ''}
    `;

    footer.querySelectorAll('[data-inspect]').forEach(btn => {
      btn.addEventListener('click', function() {
        const tab = this.getAttribute('data-inspect');
        if (typeof cb.onInspectTab === 'function') cb.onInspectTab(tab, turnData);
      });
    });

    const followBtn = footer.querySelector('.followup-btn');
    if (followBtn && turnData.followUpPrompt) {
      followBtn.addEventListener('click', function() {
        if (typeof cb.onFollowUp === 'function') cb.onFollowUp(turnData.followUpPrompt);
      });
    }

    card.appendChild(footer);
    wrapper.appendChild(card);
    return wrapper;
  }

  return {
    render: renderCard,
    statusOf: statusOf,
    TOOLS: TOOLS
  };
});

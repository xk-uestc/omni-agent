(function(root, factory) {
  const api = factory(root);
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.SchemaSvg = api;
})(typeof window !== 'undefined' ? window : globalThis, function(root) {
  'use strict';

  function renderSchemaTopology(schemaData) {
    const data = schemaData || {
      totalTablesScanned: 24,
      distractorsFiltered: 21,
      confidence: '99.2%',
      nodes: [
        { id: 'dim_region', name: 'dim_region (区域维表)', x: 20, y: 24, pk: 'region_id', matchedField: "region_alias -> '北京市'", role: '维度过滤' },
        { id: 'fact_project_revenue', name: 'fact_project_revenue (营收事实表)', x: 20, y: 128, pk: 'project_id', matchedField: 'actual_revenue, yoy_growth', role: '核心事实表' },
        { id: 'dim_sales_lead', name: 'dim_sales_lead (销售负责人表)', x: 20, y: 232, pk: 'leader_id', matchedField: 'champion_flag = 1', role: '跨源关联键' }
      ],
      distractors: ['tmp_log_archive_2022', 'bak_regional_budget_v1', 'hr_attendance_daily']
    };

    const nodes = data.nodes || [];
    const svgHeight = Math.max(310, nodes.length * 104 + 24);

    let edgesSvg = '';
    for (let i = 0; i < nodes.length - 1; i++) {
      const y1 = nodes[i].y + 72;
      const y2 = nodes[i + 1].y;
      const midY = (y1 + y2) / 2;
      edgesSvg += `
        <path d="M 165 ${y1} C 165 ${midY}, 165 ${midY}, 165 ${y2}" stroke="#0066CC" stroke-width="2" stroke-dasharray="4 3" fill="none"/>
        <circle cx="165" cy="${midY}" r="10" fill="#EFF6FF" stroke="#0066CC" stroke-width="1.2"/>
        <text x="165" y="${midY + 3.5}" text-anchor="middle" font-size="9" font-family="monospace" fill="#0066CC" font-weight="600">JOIN</text>
      `;
    }

    let nodesSvg = '';
    nodes.forEach((n) => {
      nodesSvg += `
        <g transform="translate(${n.x}, ${n.y})">
          <rect x="0" y="0" width="290" height="72" rx="10" fill="#FFFFFF" stroke="#0066CC" stroke-width="1.5"/>
          <rect x="0" y="0" width="290" height="24" rx="10" fill="#EFF6FF"/>
          <text x="12" y="16" font-size="11.5" font-weight="600" fill="#0052A3" font-family="-apple-system, sans-serif">${n.name}</text>
          <text x="278" y="16" text-anchor="end" font-size="10" font-weight="600" fill="#059669">${n.role}</text>
          <text x="12" y="43" font-size="11" fill="#374151" font-family="monospace">PK/FK: ${n.pk}</text>
          <text x="12" y="61" font-size="11" fill="#0066CC" font-family="monospace">命中字段: ${n.matchedField}</text>
        </g>
      `;
    });

    return `
      <div class="schema-topology-wrap">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;font-size:12px;">
          <span style="color:var(--text-secondary);">扫描业务表: <strong>${data.totalTablesScanned} 张</strong> · 排除干扰表: <strong>${data.distractorsFiltered} 张</strong></span>
          <span class="verify-pill verified">Schema 置信度 ${data.confidence}</span>
        </div>
        <div style="background:var(--bg-surface);border:1px solid var(--border-hairline);border-radius:10px;padding:8px;overflow-x:auto;">
          <svg viewBox="0 0 330 ${svgHeight}" width="100%" height="${svgHeight}" xmlns="http://www.w3.org/2000/svg">
            ${edgesSvg}
            ${nodesSvg}
          </svg>
        </div>
        <div style="margin-top:10px;font-size:11.5px;color:var(--text-muted);line-height:1.5;">
          <strong>已排除高频语义干扰项：</strong>
          <span style="font-family:var(--font-mono);">${(data.distractors || []).join(', ')}</span>
        </div>
      </div>
    `;
  }

  return {
    render: renderSchemaTopology
  };
});

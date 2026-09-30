(() => {
  "use strict";
  const $ = (selector) => document.querySelector(selector);
  const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
  const icon = (name, fallback = "·") => `<i data-lucide="${name}" aria-hidden="true">${fallback}</i>`;
  const icons = () => { if (window.lucide) window.lucide.createIcons(); };
  const fmt = (value) => typeof value === "number" ? value.toLocaleString("zh-CN", { maximumFractionDigits: 1 }) : String(value);

  const scenarios = {
    rank: {
      group: "多表问数", title: "华南销售额 Top 3 产品", question: "华南地区销售额前3的产品", time: 38,
      answer: "华南地区销售额最高的是云屏 11（¥12,495），其后是星河 Pro（¥3,999）和声场 Air（¥1,398）。按产品聚合订单明细，Top 3 使用 DENSE_RANK，包含并列名次。",
      metrics: [["最高产品销售额", "¥12,495"], ["返回产品", "3 个"], ["查询范围", "华南地区"]],
      columns: ["产品", "地区", "销售额", "排名"], rows: [["云屏 11", "华南", 12495, 1], ["星河 Pro", "华南", 3999, 2], ["声场 Air", "华南", 1398, 3]],
      bars: [["云屏 11", 12495], ["星河 Pro", 3999], ["声场 Air", 1398]],
      sql: `WITH ranked AS (\n  SELECT p.product_name AS 产品, r.region_name AS 地区,\n         SUM(i.line_amount) AS 销售额,\n         DENSE_RANK() OVER (ORDER BY SUM(i.line_amount) DESC) AS 排名\n  FROM order_items i\n  JOIN products p ON i.product_id = p.product_id\n  JOIN orders o ON i.order_id = o.order_id\n  JOIN regions r ON o.region_id = r.region_id\n  WHERE r.region_name = ?\n  GROUP BY p.product_name, r.region_name\n)\nSELECT 产品, 地区, 销售额, 排名 FROM ranked\nWHERE 排名 <= ? ORDER BY 排名, 销售额 DESC LIMIT ?;`,
      params: ["华南", 3, 100], intent: "销售额 SUM · 产品分组 · 华南过滤 · Top 3", join: "order_items → products / orders → regions", plan: "聚合排序，DENSE_RANK 包含并列", used: ["order_items", "products", "orders", "regions"],
      segments: [["华南", "filter", "取值"], ["地区", "dimension", "维度"], ["销售额", "metric", "指标"], ["前3", "operator", "算子"], ["的", "", ""], ["产品", "dimension", "维度"]],
      mappings: [["华南", "取值", "regions.region_name", "—", "限定 region_name = 华南"], ["地区", "维度", "regions.region_name", "0.74", "区域维度"], ["销售额", "指标", "order_items.line_amount", "0.82", "SUM 聚合"], ["前3", "算子", "DENSE_RANK", "—", "Top 3，含并列"], ["产品", "维度", "products.product_name", "0.74", "产品维度"]],
      followups: [["按月看销售趋势", "trend"], ["看看地区环比", "compare"], ["销售额同比", "clarify"]]
    },
    trend: {
      group: "趋势分析", title: "2025 年上半年月度销售额", question: "按月统计2025年上半年销售额", time: 42,
      answer: "2025 年上半年示例销售额合计 ¥66,800。月度数值从 1 月的 ¥8,200 上升到 6 月的 ¥14,100；这里按订单日期聚合，而不是按商品创建时间。",
      metrics: [["上半年销售额", "¥66,800"], ["月份", "6 个月"], ["6 月销售额", "¥14,100"]],
      columns: ["月份", "销售额"], rows: [["2025-01", 8200], ["2025-02", 9600], ["2025-03", 10480], ["2025-04", 11520], ["2025-05", 12900], ["2025-06", 14100]],
      bars: [["1 月", 8200], ["2 月", 9600], ["3 月", 10480], ["4 月", 11520], ["5 月", 12900], ["6 月", 14100]],
      sql: `SELECT strftime('%Y-%m', o.order_date) AS 月份,\n       SUM(i.line_amount) AS 销售额\nFROM order_items i\nJOIN orders o ON i.order_id = o.order_id\nWHERE o.order_date >= ? AND o.order_date < ?\nGROUP BY strftime('%Y-%m', o.order_date)\nORDER BY 月份 LIMIT ?;`,
      params: ["2025-01-01", "2025-07-01", 100], intent: "销售额 SUM · 按月分组 · 2025 上半年", join: "order_items → orders", plan: "日期按月截取，半年窗口参数化", used: ["order_items", "orders"],
      segments: [["按月", "time", "时间"], ["统计", "operator", "算子"], ["2025年上半年", "time", "时间"], ["销售额", "metric", "指标"]],
      mappings: [["按月", "时间", "orders.order_date", "0.91", "strftime 按月分组"], ["2025年上半年", "时间", "orders.order_date", "—", "[2025-01-01, 2025-07-01)"], ["销售额", "指标", "order_items.line_amount", "0.82", "SUM 聚合"]],
      followups: [["看华南 Top 3", "rank"], ["看看地区环比", "compare"]]
    },
    compare: {
      group: "趋势分析", title: "地区销售额月环比", question: "2025年3月各地区销售额的环比", time: 51,
      answer: "示例中，2025 年 3 月各地区合计销售额 ¥44,700，较 2 月的 ¥41,800 增长约 6.9%。华东和华南增长，华北略有下降。两期窗口按同一地区维度对齐。",
      metrics: [["3 月销售额", "¥44,700"], ["2 月销售额", "¥41,800"], ["整体环比", "+6.9%"]],
      columns: ["地区", "3 月销售额", "2 月销售额", "环比"], rows: [["华东", 20500, 18400, "+11.4%"], ["华南", 14500, 13200, "+9.8%"], ["华北", 9700, 10200, "-4.9%"]],
      bars: [["华东", 20500], ["华南", 14500], ["华北", 9700]],
      sql: `WITH monthly AS (\n  SELECT r.region_name AS region_name,\n         strftime('%Y-%m', o.order_date) AS month,\n         SUM(i.line_amount) AS sales\n  FROM order_items i\n  JOIN orders o ON i.order_id = o.order_id\n  JOIN regions r ON o.region_id = r.region_id\n  WHERE o.order_date >= ? AND o.order_date < ?\n  GROUP BY r.region_name, strftime('%Y-%m', o.order_date)\n), pivoted AS (\n  SELECT region_name,\n         MAX(CASE WHEN month = ? THEN sales END) AS current_sales,\n         MAX(CASE WHEN month = ? THEN sales END) AS previous_sales\n  FROM monthly GROUP BY region_name\n)\nSELECT region_name AS 地区, current_sales AS "3月销售额",\n       previous_sales AS "2月销售额",\n       CASE WHEN previous_sales IS NULL OR previous_sales = 0 THEN NULL\n            ELSE ROUND((current_sales - previous_sales) * 100.0 / previous_sales, 1) END AS "环比(%)"\nFROM pivoted ORDER BY current_sales DESC LIMIT ?;`,
      params: ["2025-02-01", "2025-04-01", "2025-03", "2025-02", 100], intent: "销售额 SUM · 地区分组 · 3 月对比 2 月", join: "order_items → orders → regions", plan: "双窗口按地区对齐，空值不按 0 计算", used: ["order_items", "orders", "regions"],
      segments: [["2025年3月", "time", "时间"], ["各地区", "dimension", "维度"], ["销售额", "metric", "指标"], ["的环比", "operator", "算子"]],
      mappings: [["2025年3月", "时间", "orders.order_date", "—", "与 2025-02 对齐"], ["各地区", "维度", "regions.region_name", "0.74", "按地区分组"], ["销售额", "指标", "order_items.line_amount", "0.82", "SUM 聚合"], ["环比", "算子", "两期窗口", "—", "(本期-上期)/上期"]],
      followups: [["看月度趋势", "trend"], ["销售额同比", "clarify"]]
    },
    yoy: {
      group: "趋势分析", title: "2025 年销售额同比", question: "2025年销售额同比", time: 44,
      answer: "示例中，2025 年销售额 ¥372,000，2024 年为 ¥318,000，同比增长约 17.0%。两年使用相同的年度边界，避免把不完整月份直接对比。",
      metrics: [["2025 年", "¥372,000"], ["2024 年", "¥318,000"], ["同比变化", "+17.0%"]],
      columns: ["年份", "销售额"], rows: [["2024", 318000], ["2025", 372000]], bars: [["2024", 318000], ["2025", 372000]],
      sql: `SELECT strftime('%Y', o.order_date) AS 年份,\n       SUM(i.line_amount) AS 销售额\nFROM order_items i\nJOIN orders o ON i.order_id = o.order_id\nWHERE o.order_date >= ? AND o.order_date < ?\nGROUP BY strftime('%Y', o.order_date)\nORDER BY 年份 LIMIT ?;`,
      params: ["2024-01-01", "2026-01-01", 100], intent: "销售额 SUM · 2025 对比 2024", join: "order_items → orders", plan: "年度同比，对齐完整自然年", used: ["order_items", "orders"],
      segments: [["2025年", "time", "时间"], ["销售额", "metric", "指标"], ["同比", "operator", "算子"]],
      mappings: [["2025年", "时间", "orders.order_date", "—", "对比 2024 年"], ["销售额", "指标", "order_items.line_amount", "0.82", "SUM 聚合"], ["同比", "算子", "年度对比", "—", "两期窗口对齐"]],
      followups: [["看月度趋势", "trend"], ["看看地区环比", "compare"]]
    },
    clarify: {
      group: "澄清与安全", title: "销售额同比 · 待澄清", question: "销售额同比", time: 12,
      answer: "要计算销售额同比，还需要明确对比年份。当前没有足够的时间范围，我不会猜测口径，也不会生成或执行 SQL。",
      intent: "销售额 SUM · 同比 · 缺少时间范围", join: "尚未确定完整查询范围", plan: "时间窗口缺失，进入澄清", used: ["order_items"],
      segments: [["销售额", "metric", "指标"], ["同比", "operator", "算子"]],
      mappings: [["销售额", "指标", "order_items.line_amount", "0.82", "SUM 聚合"], ["同比", "算子", "年度对比", "—", "缺少年份，等待确认"]],
      options: [["2025 年同比", "yoy"], ["改看 2025 年 3 月环比", "compare"]], followups: [["看华南 Top 3", "rank"]]
    }
  };

  const app = $("#app-shell");
  let current = "rank";
  let activeTab = "overview";
  let activeStage = 0;
  let toastTimer;

  function showToast(message) {
    const toast = $("#toast");
    toast.textContent = message;
    toast.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { toast.hidden = true; }, 3500);
  }

  function renderNavigation() {
    const query = $("#scenario-search").value.trim().toLowerCase();
    const groups = ["多表问数", "趋势分析", "澄清与安全"];
    const html = groups.map((group) => {
      const entries = Object.entries(scenarios).filter(([, item]) => item.group === group && (!query || `${item.title} ${item.question}`.toLowerCase().includes(query)));
      if (!entries.length) return "";
      return `<div class="scenario-group-label">${group}</div>${entries.map(([key, item]) => `<button class="scenario-row ${current === key ? "is-selected" : ""}" data-scenario="${key}" type="button">${icon(item.options ? "circle-help" : "message-square", "◫")}<span>${escapeHtml(item.title)}</span></button>`).join("")}`;
    }).join("");
    $("#scenario-list").innerHTML = html || '<p class="search-empty">没有匹配的会话</p>';
    icons();
  }

  function stages(item) {
    const times = item.options ? [2, 3, 3, 4] : [5, 8, 7, 6, 5, item.time - 31];
    const base = [
      ["要素识别", item.intent, times[0]],
      ["表与字段定位", item.join, times[1]],
      ["查询计划", item.plan, times[2]],
      ["语义校验", item.options ? "缺少年份，等待用户确认" : "问题要素与口径已核对", times[3]]
    ];
    if (!item.options) base.push(["SQL 与安全门", "只读 SELECT · 参数化 · 行数限制", times[4]], ["结果与溯源", `返回 ${item.rows.length} 行示例记录`, times[5]]);
    return base.map(([name, detail, ms], index) => ({ name, detail, ms, pending: !!item.options && index === 3 }));
  }

  function renderReasoning(item) {
    const steps = stages(item);
    $("#reasoning-toggle-text").textContent = item.options ? "需要澄清 · 停在第 4 步" : "已完成 6 步分析";
    $("#reasoning-time").textContent = `${item.time} ms`;
    $("#reasoning-panel").innerHTML = steps.map((step) => `<div class="process-row">${icon(step.pending ? "circle-help" : "check", "✓")}<strong>${escapeHtml(step.name)}</strong><time>${step.ms} ms</time><span>${escapeHtml(step.detail)}</span></div>`).join("");
    $("#reasoning-toggle").setAttribute("aria-expanded", "false");
    $("#reasoning-panel").hidden = true;
  }

  function overview(item) {
    const max = Math.max(...item.bars.map(([, value]) => value), 1);
    return `<div class="result-panel-heading"><h3>结果概览</h3><span>${item.rows.length} 行 · 示例数据</span></div>
      <div class="metric-strip">${item.metrics.map(([label, value], index) => `<div class="metric"><span class="metric-label">${escapeHtml(label)}</span><span class="metric-value ${index === 2 && String(value).startsWith("+") ? "positive" : ""}">${escapeHtml(value)}</span></div>`).join("")}</div>
      <div class="bar-chart">${item.bars.map(([label, value]) => `<div class="bar-row"><span class="bar-label">${escapeHtml(label)}</span><span class="bar-track"><span class="bar-fill" style="--bar:${Math.max(3, Math.round(value / max * 100))}%"></span></span><span class="bar-value">${fmt(value)}</span></div>`).join("")}</div><p class="result-footnote">数值仅用于界面演示，不代表当前数据库实时结果。</p>`;
  }

  function table(item) {
    return `<div class="result-panel-heading"><h3>查询结果</h3><span>${item.rows.length} 行</span></div><div class="table-scroll"><table class="data-table"><thead><tr>${item.columns.map((column, index) => `<th class="${index > 0 ? "num" : ""}">${escapeHtml(column)}</th>`).join("")}</tr></thead><tbody>${item.rows.map((row) => `<tr>${row.map((value, index) => `<td class="${index > 0 && typeof value === "number" ? "num" : ""}">${escapeHtml(fmt(value))}</td>`).join("")}</tr>`).join("")}</tbody></table></div><p class="result-footnote">演示结果，不执行真实查询。</p>`;
  }

  function highlightedSql(sql) {
    return sql.split(/(\b(?:WITH|AS|SELECT|FROM|WHERE|JOIN|ON|GROUP|BY|ORDER|LIMIT|AND|OR|SUM|DENSE_RANK|OVER|DESC|ASC)\b|\?)/gi).map((part) => {
      if (part === "?") return '<span class="param">?</span>';
      if (/^(WITH|AS|SELECT|FROM|WHERE|JOIN|ON|GROUP|BY|ORDER|LIMIT|AND|OR|SUM|DENSE_RANK|OVER|DESC|ASC)$/i.test(part)) return `<span class="kw">${escapeHtml(part)}</span>`;
      return escapeHtml(part);
    }).join("");
  }

  function sqlView(item) {
    return `<div class="sql-toolbar"><span>参数化 SQL · SQLite 方言</span><button type="button" id="copy-sql">${icon("copy", "▢")}复制 SQL</button></div><pre class="sql-code"><code>${highlightedSql(item.sql)}</code></pre><div class="param-list">${item.params.map((value, index) => `<span class="param-chip">$${index + 1} = ${escapeHtml(value)}</span>`).join("")}</div><p class="result-footnote">SQL 为界面样例；此页面不会访问数据库。</p>`;
  }

  function elementsView(item) {
    const question = item.segments.map(([text, role, label]) => role ? `<span class="query-token role-${role}"><small>${label}</small>${escapeHtml(text)}</span>` : `<span class="query-plain">${escapeHtml(text)}</span>`).join("");
    const legend = [["metric", "指标"], ["dimension", "维度"], ["filter", "取值"], ["time", "时间"], ["operator", "算子"]].map(([role, label]) => `<span class="role-legend role-${role}"><b></b>${label}</span>`).join("");
    const rows = item.mappings.map(([text, role, field, score, note]) => `<tr><td>${escapeHtml(text)}</td><td><span class="mapping-role">${escapeHtml(role)}</span></td><td><code>${escapeHtml(field)}</code></td><td>${escapeHtml(score)}</td><td>${escapeHtml(note)}</td></tr>`).join("");
    return `<div class="result-panel-heading"><h3>要素识别与问题改写</h3><span>每个片段的业务含义</span></div><div class="annotated-question">${question}</div><div class="role-legend-list">${legend}</div><div class="rewrite-summary"><div><span>改写结果</span><strong>${escapeHtml(item.question)}</strong></div><div><span>覆盖状态</span><strong>${item.options ? "时间条件待确认" : `${item.mappings.length} 个要素已处理 · 0 个未解析`}</strong></div></div><div class="table-scroll"><table class="data-table mapping-table"><thead><tr><th>片段</th><th>角色</th><th>链接到</th><th>置信</th><th>说明</th></tr></thead><tbody>${rows}</tbody></table></div>`;
  }

  const schemaNodes = [
    { name: "order_items", count: 14, x: 16, y: 16, fields: [["item_id", "PK"], ["order_id", "FK"], ["product_id", "FK"], ["quantity", ""], ["line_amount", "metric"]] },
    { name: "orders", count: 9, x: 274, y: 16, fields: [["order_id", "PK"], ["order_date", "time"], ["channel", ""], ["customer_id", "FK"], ["region_id", "FK"]] },
    { name: "regions", count: 3, x: 539, y: 16, fields: [["region_id", "PK"], ["region_name", "filter"], ["province", ""]] },
    { name: "products", count: 5, x: 274, y: 232, fields: [["product_id", "PK"], ["product_name", "dimension"], ["category", ""], ["unit_price", ""]] },
    { name: "customers", count: 6, x: 539, y: 209, fields: [["customer_id", "PK"], ["customer_name", ""], ["customer_level", ""], ["region_id", "FK"]] },
    { name: "support_tickets", count: 6, x: 789, y: 209, fields: [["ticket_id", "PK"], ["customer_id", "FK"], ["created_date", ""], ["status", ""], ["resolution_hours", ""]] }
  ];
  const schemaEdges = [
    { from: "order_items", to: "orders", path: "M200 85 C236 85 239 85 274 85" },
    { from: "order_items", to: "products", path: "M200 126 C244 126 233 289 274 289" },
    { from: "orders", to: "regions", path: "M458 148 C493 148 504 87 539 87" },
    { from: "orders", to: "customers", path: "M458 155 C500 155 495 267 539 267" },
    { from: "customers", to: "support_tickets", path: "M723 269 C753 269 761 269 789 269" }
  ];

  function schemaView(item) {
    const used = new Set(item.used);
    const roleByField = {};
    const roleCss = { "指标": "metric", "维度": "dimension", "取值": "filter", "时间": "time" };
    item.mappings.forEach(([, role, field]) => {
      if (field.includes(".") && !roleByField[field]) roleByField[field] = roleCss[role] || "";
    });
    const edges = schemaEdges.map((edge) => `<path class="schema-edge ${used.has(edge.from) && used.has(edge.to) ? "used" : ""}" d="${edge.path}"/>`).join("");
    const nodes = schemaNodes.map((node) => `<div class="schema-node ${used.has(node.name) ? "used" : "unused"}" style="left:${node.x}px;top:${node.y}px"><div class="schema-node-head"><strong>${node.name}</strong><span>${node.count} 行</span></div>${node.fields.map(([field, role]) => { const color = roleByField[`${node.name}.${field}`]; return `<div class="schema-field ${color && used.has(node.name) ? `field-${color}` : ""}"><span>${role === "PK" || role === "FK" ? role : ""}</span><code>${field}</code></div>`; }).join("")}</div>`).join("");
    return `<div class="result-panel-heading"><h3>表与字段定位 · JOIN 路径</h3><span>基于示例外键图</span></div><div class="schema-scroll"><div class="schema-canvas"><svg class="schema-links" width="990" height="378" viewBox="0 0 990 378" aria-hidden="true">${edges}</svg>${nodes}</div></div><div class="schema-legend"><span><b class="legend-metric"></b>指标字段</span><span><b class="legend-dimension"></b>维度字段</span><span><b class="legend-filter"></b>取值字段</span><span><b class="legend-line"></b>选中关联</span><span class="muted-node">未使用的表淡化</span></div><div class="join-path">${escapeHtml(item.join).replaceAll("→", '<span>→</span>')}</div>`;
  }

  function setTab(tab) {
    if (!current || scenarios[current].options) return;
    activeTab = tab;
    const item = scenarios[current];
    document.querySelectorAll(".result-tab").forEach((button) => button.setAttribute("aria-selected", String(button.dataset.tab === tab)));
    const views = { overview, table, sql: sqlView, elements: elementsView, schema: schemaView };
    $("#result-panel").innerHTML = views[tab](item);
    icons();
  }

  function renderAnswer(item) {
    const lead = `<p class="answer-lede">${escapeHtml(item.answer)}</p><p class="answer-note">示例数据与耗时仅用于视觉演示，不是实时查询。</p>`;
    if (item.options) {
      $("#answer-area").innerHTML = lead + `<div class="clarify-block"><div class="clarify-lead">${icon("circle-help", "?")}需要确认时间范围</div><p class="clarify-question">你想比较哪个时间段？</p><div class="clarify-options">${item.options.map(([label, target]) => `<button type="button" class="clarify-option" data-scenario="${target}">${escapeHtml(label)}</button>`).join("")}</div></div>`;
      return;
    }
    $("#answer-area").innerHTML = lead + `<div class="result-block"><div class="result-tabs" role="tablist" aria-label="结果视图">${[["overview", "概览", "chart-no-axes-column"], ["table", "数据表", "table-2"], ["sql", "SQL", "code-2"], ["elements", "要素识别", "scan-text"], ["schema", "关联路径", "workflow"]].map(([key, label, glyph]) => `<button type="button" role="tab" class="result-tab" data-tab="${key}" aria-selected="${key === activeTab}">${icon(glyph, "▤")}${label}</button>`).join("")}</div><div class="result-panel" id="result-panel" role="tabpanel"></div></div>`;
    setTab(activeTab);
  }

  function renderInspector(item) {
    const steps = stages(item);
    activeStage = Math.min(activeStage, steps.length - 1);
    $("#trace-state").textContent = item.options ? "等待澄清" : "查询完成";
    $(".summary-state").classList.toggle("is-warn", !!item.options);
    $("#trace-duration").textContent = `${item.time} ms`;
    $("#step-count").textContent = `${steps.length} 个阶段`;
    $("#stage-list").innerHTML = steps.map((step, index) => `<button class="stage ${index === activeStage ? "is-active" : ""} ${step.pending ? "is-pending" : ""}" data-stage="${index}" type="button"><span class="stage-marker">${icon(step.pending ? "help-circle" : "check", step.pending ? "?" : "✓")}</span><span class="stage-name">${escapeHtml(step.name)}</span><span class="stage-duration">${step.ms} ms</span></button>`).join("");
    const step = steps[activeStage];
    $("#stage-detail").innerHTML = `<div class="detail-label">${escapeHtml(step.name)}</div><p class="detail-copy">${escapeHtml(step.detail)}</p><div class="detail-pairs"><div class="detail-pair"><span>输入问题</span><strong>${escapeHtml(item.question)}</strong></div><div class="detail-pair"><span>处理状态</span><strong>${step.pending ? "等待用户确认" : "已完成"}</strong></div></div>`;
    $("#lineage-list").innerHTML = item.mappings.filter((row) => row[2].includes(".")).slice(0, 3).map(([fragment, role, field]) => `<div class="lineage-item"><span>${escapeHtml(fragment)} · ${escapeHtml(role)}</span><code>${escapeHtml(field)}</code></div>`).join("");
    $("#inspector-foot-text").textContent = item.options ? "口径未确定 · 未生成 SQL" : "只读查询 · 参数化执行（演示）";
    icons();
  }

  function renderScenario(key) {
    current = key;
    activeTab = "overview";
    activeStage = 0;
    const item = scenarios[key];
    $("#empty-state").hidden = true;
    ["#conversation-meta", "#user-turn", "#assistant-turn", "#followups"].forEach((selector) => { $(selector).hidden = false; });
    $("#question-display").textContent = item.question;
    $("#conversation-id").textContent = `会话 ${String(Object.keys(scenarios).indexOf(key) + 1).padStart(2, "0")}`;
    $("#answer-stamp").textContent = `示例耗时 ${item.time} ms`;
    $("#download-csv").disabled = !!item.options;
    renderReasoning(item);
    renderAnswer(item);
    renderInspector(item);
    $("#followups").innerHTML = item.followups.map(([label, target]) => `<button type="button" class="followup" data-scenario="${target}">${escapeHtml(label)}${icon("arrow-up-right", "↗")}</button>`).join("");
    renderNavigation();
    $("#question-input").value = "";
    $("#conversation-scroll").scrollTop = 0;
    app.classList.remove("sidebar-open", "inspector-open");
    icons();
  }

  function renderEmpty() {
    current = null;
    ["#conversation-meta", "#user-turn", "#assistant-turn", "#followups"].forEach((selector) => { $(selector).hidden = true; });
    $("#empty-state").hidden = false;
    $("#empty-state").innerHTML = `<div class="empty-logo">问</div><h1>今天想查询什么？</h1><div class="empty-suggestions">${[["华南销售额 Top 3 产品", "rank"], ["月度销售趋势", "trend"], ["地区销售额环比", "compare"], ["销售额同比", "clarify"]].map(([label, key]) => `<button type="button" data-scenario="${key}">${escapeHtml(label)}${icon("arrow-up-right", "↗")}</button>`).join("")}</div>`;
    $("#trace-state").textContent = "等待问题";
    $("#trace-duration").textContent = "—";
    $("#step-count").textContent = "0 个阶段";
    $("#stage-list").innerHTML = "";
    $("#stage-detail").innerHTML = '<p class="detail-copy">选择一个示例问题后，这里显示执行记录。</p>';
    $("#lineage-list").innerHTML = "";
    $("#inspector-foot-text").textContent = "演示环境 · 未连接数据库";
    app.classList.remove("sidebar-open", "inspector-open");
    renderNavigation();
    $("#question-input").focus();
    icons();
  }

  async function copyText(value) {
    try {
      if (navigator.clipboard && window.isSecureContext) await navigator.clipboard.writeText(value);
      else {
        const field = document.createElement("textarea");
        field.value = value;
        field.style.position = "fixed";
        field.style.opacity = "0";
        document.body.appendChild(field);
        field.select();
        if (!document.execCommand("copy")) throw new Error("copy failed");
        field.remove();
      }
      showToast("已复制到剪贴板");
    } catch { showToast("复制失败，请手动选择内容"); }
  }

  function downloadCsv() {
    if (!current || !scenarios[current].rows) return;
    const item = scenarios[current];
    const quote = (value) => `"${String(value ?? "").replaceAll('"', '""')}"`;
    const csv = "\ufeff" + [item.columns, ...item.rows].map((row) => row.map(quote).join(",")).join("\r\n");
    const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = `nl2sql-demo-${current}.csv`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  document.addEventListener("click", (event) => {
    const scenarioButton = event.target.closest("[data-scenario]");
    if (scenarioButton && scenarios[scenarioButton.dataset.scenario]) renderScenario(scenarioButton.dataset.scenario);
    const tab = event.target.closest("[data-tab]");
    if (tab) setTab(tab.dataset.tab);
    const stage = event.target.closest("[data-stage]");
    if (stage && current) { activeStage = Number(stage.dataset.stage); renderInspector(scenarios[current]); }
    if (event.target.closest("#copy-sql") && current) copyText(scenarios[current].sql);
  });
  $("#reasoning-toggle").addEventListener("click", () => {
    const button = $("#reasoning-toggle");
    const expanded = button.getAttribute("aria-expanded") !== "true";
    button.setAttribute("aria-expanded", String(expanded));
    $("#reasoning-panel").hidden = !expanded;
  });
  $("#scenario-search").addEventListener("input", renderNavigation);
  $("#new-chat").addEventListener("click", renderEmpty);
  $("#nav-ask").addEventListener("click", () => current ? renderScenario(current) : renderEmpty());
  $("#nav-schema").addEventListener("click", () => { if (!current || scenarios[current].options) renderScenario("rank"); setTab("schema"); $("#result-panel").scrollIntoView({ block: "nearest", behavior: "smooth" }); app.classList.remove("sidebar-open"); });
  $("#copy-answer").addEventListener("click", () => { if (current) copyText(scenarios[current].answer); });
  $("#download-csv").addEventListener("click", downloadCsv);
  $("#ask-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const question = $("#question-input").value.trim().replace(/\s+/g, "");
    const match = Object.entries(scenarios).find(([, item]) => item.question.replace(/\s+/g, "") === question);
    if (match) renderScenario(match[0]);
    else if (question) showToast("当前是静态演示。请选择左侧示例会话查看完整流程。");
  });
  $("#question-input").addEventListener("keydown", (event) => { if (event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); $("#ask-form").requestSubmit(); } });
  $("#question-input").addEventListener("input", (event) => { event.target.style.height = "35px"; event.target.style.height = Math.min(120, event.target.scrollHeight) + "px"; });
  $("#collapse-sidebar").addEventListener("click", () => app.classList.add("sidebar-collapsed"));
  $("#open-sidebar").addEventListener("click", () => { if (matchMedia("(max-width: 710px)").matches) app.classList.add("sidebar-open"); else app.classList.remove("sidebar-collapsed"); });
  $("#close-sidebar").addEventListener("click", () => app.classList.remove("sidebar-open"));
  $("#inspector-toggle").addEventListener("click", () => { if (matchMedia("(max-width: 1050px)").matches) app.classList.toggle("inspector-open"); else app.classList.toggle("inspector-collapsed"); });
  $("#close-inspector").addEventListener("click", () => { if (matchMedia("(max-width: 1050px)").matches) app.classList.remove("inspector-open"); else app.classList.add("inspector-collapsed"); });
  $("#mobile-scrim").addEventListener("click", () => app.classList.remove("sidebar-open", "inspector-open"));
  $("#theme-toggle").addEventListener("click", () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem("ict8-preview-theme", next); } catch { /* local preview */ }
    $("#theme-toggle").innerHTML = icon(next === "dark" ? "sun" : "moon", "◐");
    icons();
  });
  try { document.documentElement.dataset.theme = localStorage.getItem("ict8-preview-theme") || "light"; } catch { document.documentElement.dataset.theme = "light"; }
  const previewParams = new URLSearchParams(location.search);
  const initialScenario = scenarios[previewParams.get("scenario")] ? previewParams.get("scenario") : "rank";
  renderScenario(initialScenario);
  if (["overview", "table", "sql", "elements", "schema"].includes(previewParams.get("tab"))) setTab(previewParams.get("tab"));
})();

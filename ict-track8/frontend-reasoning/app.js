/* 问数推理台 · 静态 UI 版：用 preview-data.js 中的真实引擎响应渲染全部界面，暂不接入交互与 API。 */
(function () {
  "use strict";
  const C = window.ReasoningCore;
  const P = window.PREVIEW;
  const $ = (s) => document.querySelector(s);
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = C.formatNumber;

  const r = P.result;
  const plan = r.plan;
  const spans = C.classifySpans(r);
  const roleOf = {};
  spans.forEach((s) => { if (s.column) roleOf[s.column] = s.role; });

  /* ---------- 页头 ---------- */
  $("#health").dataset.state = "ok";
  $("#health-text").textContent = "API 正常 · 规则规划器";
  $("#db-pill").hidden = false;
  $("#db-pill").textContent = "industry_demo.sqlite · 6 表";
  $("#session-label").textContent = "会话 demo-7f3a";
  $("#question").value = P.question;

  /* ---------- 工具调用链 ---------- */
  $("#chain-panel").hidden = false;
  $("#trace-id").textContent = "trace 1014bb8b9b7b0c06";
  $("#latency").textContent = "端到端 38 ms";
  const chain = [
    ["意图与上下文", "completed", "0 轮上下文", "done"],
    ["Schema 链接", "4 个字段", "规则 + 别名包", "done"],
    ["JOIN 路径", `${plan.join_path.length} 跳`, "外键图 BFS", "done"],
    ["语义校验", "通过", "覆盖率 100%", "done"],
    ["SQL 安全门", "只读", "AST + authorizer", "done"],
    ["SQLite 执行", `${r.rows.length} 行`, "参数化", "done"],
    ["知识库检索", "RAG 模块", "不在本页", "external"],
  ];
  $("#chain").innerHTML = chain.map(([n, a, b, s]) => `<div class="chain-node" data-state="${s}"><div class="name">${n}</div><div class="meta"><span>${a}</span><span>${b}</span></div></div>`).join("");

  /* ---------- 步骤导航 ---------- */
  const stepNames = ["要素识别", "表与字段定位", "查询计划", "语义校验", "SQL 与安全门", "结果与溯源"];
  $("#stepper").hidden = false;
  $("#stepper").innerHTML = stepNames.map((n, i) => `<a href="#step-${i + 1}" data-state="done"><span class="num">${i + 1}</span>${n}</a>`).join("");

  const step = (i, verb, title, why, body, state) => `
    <section class="step reveal" id="step-${i}" data-state="${state || "done"}" style="animation-delay:${i * 60}ms">
      <div class="step-rail"><div class="step-index">${state === "blocked" ? "!" : i}</div></div>
      <div class="panel step-card">
        <div class="step-head"><span class="verb">${verb}</span><h3>${title}</h3><span class="why">${why}</span></div>
        <div class="step-body">${body}</div>
      </div>
    </section>`;

  /* ① 要素识别 */
  const loc = C.locateSpans(r.question, spans);
  const line = loc.segments.map((seg) => seg.span
    ? `<span class="span" data-role="${seg.span.role}" title="${esc(seg.span.detail)}"><span class="tag">${seg.span.label}</span>${esc(seg.text)}</span>`
    : `<span class="plain">${esc(seg.text)}</span>`).join("");
  const legend = ["metric", "dimension", "filter", "time", "operator"].map((k) => `<span class="legend-item role-${k}"><span class="swatch"></span>${C.ROLE_META[k].label}</span>`).join("")
    + `<span class="legend-item"><span class="swatch unresolved"></span>未解析</span><span class="legend-item"><span class="swatch ignored"></span>已剔除</span>`;
  const spanRows = spans.map((s) => `<tr><td>${esc(s.text)}</td><td><span class="role-chip role-${s.role}">${s.label}</span></td><td><code>${esc(s.column || "—")}</code></td><td class="num">${s.score != null ? `<span class="score-bar"><span class="track"><span class="fill" style="width:${s.score * 100}%"></span></span>${s.score.toFixed(2)}</span>` : "—"}</td><td class="muted">${esc(s.detail)}</td></tr>`).join("");
  const s1 = `
    <div class="question-line">${line}</div>
    <div class="legend">${legend}</div>
    <div class="rewrite">
      <div class="row"><span class="k">改写结果</span><span>${esc(r.rewritten_question)}</span><span class="muted small">（无需改写：问题已标准化）</span></div>
      <div class="row"><span class="k">覆盖率</span><span>${plan.coverage.consumed.length} / ${plan.coverage.consumed.length} 个片段被槽位消费，0 个未解析</span></div>
    </div>
    <div class="table-wrap"><table><thead><tr><th>片段</th><th>角色</th><th>链接到</th><th class="num">置信</th><th>说明</th></tr></thead><tbody>${spanRows}</tbody></table></div>`;

  /* ② 表与字段定位 */
  const highlight = {};
  plan.links.forEach((l) => { highlight[`${l.table}.${l.column}`] = l.role === "metric" ? "metric" : "dimension"; });
  plan.filters.forEach((f) => { highlight[`${f.table}.${f.column}`] = "filter"; });
  const used = [plan.table, ...plan.join_path.map((j) => j.to_table)];
  const L = C.layoutSchema(P.schema.tables, { usedTables: used, highlight, rootTable: plan.table });
  const pad = 14;
  const onEdge = new Set(plan.join_path.map((j) => [j.from_table, j.to_table].sort().join("|")));
  const colY = (node, col) => { const row = node.rows.find((x) => x.name === col); return node.y + (row ? row.y : 15); };
  const edges = L.edges.map((e) => {
    const a = L.nodes.get(e.from), b = L.nodes.get(e.to);
    const left = a.x <= b.x ? a : b, right = a.x <= b.x ? b : a;
    const x1 = left.x + left.w, y1 = colY(left, left === a ? e.fromCol : e.toCol);
    const x2 = right.x, y2 = colY(right, right === a ? e.fromCol : e.toCol);
    const mx = (x1 + x2) / 2;
    const on = onEdge.has([e.from, e.to].sort().join("|"));
    const card = on ? `<rect class="card-bg" x="${mx - 13}" y="${(y1 + y2) / 2 - 8}" width="26" height="15" rx="3"/><text class="card" x="${mx}" y="${(y1 + y2) / 2 + 3}" text-anchor="middle">N:1</text>` : "";
    return `<path class="edge${on ? " on" : ""}" d="M${x1 + pad},${y1 + pad} C${mx + pad},${y1 + pad} ${mx + pad},${y2 + pad} ${x2 + pad},${y2 + pad}"/>${card.replace(/x="([\d.-]+)"/g, (m, v) => `x="${+v + pad}"`).replace(/y="([\d.-]+)"/g, (m, v) => `y="${+v + pad}"`)}`;
  }).join("");
  const nodes = [...L.nodes.values()].map((n) => `
    <g class="node ${n.used ? "used" : "unused"}" transform="translate(${n.x + pad},${n.y + pad})">
      <rect class="box" width="${n.w}" height="${n.h}" rx="6"/>
      <rect class="head" x="1" y="1" width="${n.w - 2}" height="26" rx="5"/>
      <text class="t-name" x="10" y="18">${esc(n.name)}</text>
      <text class="t-rows" x="${n.w - 10}" y="18" text-anchor="end">${n.rowCount ?? ""} 行</text>
      ${n.rows.map((c) => `<g class="col${c.highlight ? ` role-${c.highlight}` : ""}">
        ${c.highlight ? `<rect class="c-hl" x="1" y="${c.y - 8}" width="${n.w - 2}" height="16"/><rect class="c-bar" x="1" y="${c.y - 8}" width="3" height="16"/>` : ""}
        <text class="c-key" x="10" y="${c.y + 3}">${c.pk ? "PK" : c.fk ? "FK" : ""}</text>
        <text class="c-name" x="30" y="${c.y + 4}">${esc(c.name)}</text>
        <text class="c-type" x="${n.w - 10}" y="${c.y + 4}" text-anchor="end">${esc(c.type)}</text></g>`).join("")}
      ${n.hidden ? `<text class="c-type" x="30" y="${n.h - 8}">+${n.hidden} 列</text>` : ""}
    </g>`).join("");
  const svg = `<svg class="schema-svg" viewBox="0 0 ${L.width + pad * 2} ${L.height + pad * 2}" width="${L.width + pad * 2}" role="img" aria-label="Schema 外键图，高亮本次使用的表与字段">${edges}${nodes}</svg>`;
  const path = [plan.table, ...plan.join_path.map((j) => j.to_table)];
  const joinRows = plan.join_path.map((j) => `<tr><td><code>${esc(j.from_table)}</code> → <code>${esc(j.to_table)}</code></td><td><code>${esc(j.condition)}</code></td><td>多对一</td><td class="muted">不扇出，SUM 不会重复计数</td></tr>`).join("");
  const s2 = `
    <div class="schema-wrap">${svg}</div>
    <div class="legend">${["metric", "dimension", "filter"].map((k) => `<span class="legend-item role-${k}"><span class="swatch"></span>${C.ROLE_META[k].label}字段</span>`).join("")}<span class="legend-item"><span class="swatch line" style="background:var(--ink)"></span>选中的关联边</span><span class="legend-item"><span class="swatch" style="background:var(--surface-3)"></span>未使用的表（淡化）</span></div>
    <div class="join-path">${path.map((t, i) => `${i ? `<span class="arrow">N:1<br>→</span>` : ""}<span class="tbl">${esc(t)}</span>`).join("")}</div>
    <div class="table-wrap"><table><thead><tr><th>关联边</th><th>条件（来自外键元数据）</th><th>基数</th><th>影响</th></tr></thead><tbody>${joinRows}</tbody></table></div>
    <ul class="note-list"><li>候选路径 1 条，无需在多条路径间裁决（有歧义时会发起 <code>ambiguous_join_path</code> 澄清）。</li><li>表名、列名与外键全部来自 SQLite 元数据自省，规划器内没有写死任何表结构。</li></ul>`;

  /* ③ 查询计划 */
  const cell = (k, v, role) => `<div class="plan-cell"><div class="k">${role ? `<span class="swatch" style="--role:var(--role-${role})"></span>` : ""}${k}</div><div class="v">${v}</div></div>`;
  const conf = plan.confidence;
  const s3 = `
    <div class="plan-grid">
      ${cell("指标", `${esc(plan.metric_label)} = <code>SUM(${plan.metric_table}.${plan.metric_column})</code>`, "metric")}
      ${cell("分组维度", plan.dimensions.map((d) => `<code>${plan.dimension_tables[d] || ""}.${d}</code>`).join("、"), "dimension")}
      ${cell("过滤条件", plan.filters.map((f) => esc(f.explanation)).join("<br>"), "filter")}
      ${cell("时间范围", `<span class="muted">未指定 · 全量</span>`, "time")}
      ${cell("分析算子", `Top-${plan.top_n}（DENSE_RANK，并列不截断）`, "operator")}
      ${cell("排序 / 上限", `按指标降序 · 安全上限 ${plan.limit} 行`)}
      ${cell("规划来源", `规则规划器 <code>${plan.planner_source}</code>`)}
      ${cell("计划置信度", `<div class="meter" data-level="${conf >= 0.6 ? "good" : conf >= 0.4 ? "warning" : "critical"}"><span class="track"><span class="fill" style="width:${conf * 100}%"></span></span><span class="value">${conf.toFixed(2)}</span></div>`)}
    </div>
    <details><summary class="small muted">查看执行前意图审计 intent_audit（原始 JSON）</summary><pre class="sql">${esc(JSON.stringify(plan.intent_audit, null, 2))}</pre></details>`;

  /* ④ 语义校验 */
  const check = (lvl, t, sub) => `<li class="check" data-level="${lvl}"><span class="ico">${{ pass: "✓", warn: "!", fail: "×", info: "i" }[lvl]}</span><div><span class="lvl">${{ pass: "通过", warn: "注意", fail: "阻断", info: "说明" }[lvl]}</span>${t}${sub ? `<div class="sub">${sub}</div>` : ""}</div></li>`;
  const cl = P.clarify;
  const s4 = `
    <ul class="checks">
      ${check("pass", "指标已明确", "销售额 → order_items.line_amount，唯一匹配")}
      ${check("pass", "问题片段全部被消费", "没有未解析的词，不存在“静默丢弃条件”")}
      ${check("pass", "取值在库内存在", "“华南”命中 regions.region_name 的真实取值")}
      ${check("pass", "JOIN 路径唯一且不扇出", "3 条多对一边，聚合结果不会被放大")}
      ${check("info", "未指定时间范围", "按全量数据统计；如需限定请在问题里写明年份或月份")}
    </ul>
    <div class="clarify" aria-label="澄清示例">
      <div class="title"><span class="ico">?</span>另一种情况：问题信息不足时，系统先问你，而不是猜</div>
      <div class="hint">示例“${esc(cl.question)}” → <code>${esc(cl.clarification_code)}</code>：${esc(cl.clarification)}</div>
      <div class="options">${cl.clarification_options.map((o) => `<button type="button" class="option">${esc(o.label)}</button>`).join("")}</div>
      <div class="refine"><input type="text" placeholder="或直接补充，例如：2025年" aria-label="补充信息"><button type="button" class="btn small">补全后重新规划</button></div>
    </div>`;

  /* ⑤ SQL 与安全门 */
  const origins = C.paramOrigins(r, spans);
  const colRole = {};
  Object.entries(highlight).forEach(([k, v]) => { colRole[k] = v; });
  const tokens = C.layoutSql(C.tokenizeSql(r.sql));
  let prevIdent = null;
  const sqlHtml = tokens.map((t, i) => {
    if (t.type === "newline") return "\n" + t.text.slice(1);
    if (t.type === "keyword") return `<span class="kw">${esc(t.text)}</span>`;
    if (t.type === "func") return `<span class="fn">${esc(t.text)}</span>`;
    if (t.type === "number") return `<span class="num">${esc(t.text)}</span>`;
    if (t.type === "string") return `<span class="str">${esc(t.text)}</span>`;
    if (t.type === "param") {
      const o = origins[t.index] || {};
      const isLimit = String(o.origin || "").startsWith("安全行数");
      return `<span class="param${isLimit ? " limit" : ""}" title="${esc(o.origin)}">${esc(o.value)}<sub>$${t.index + 1}</sub></span>`;
    }
    if (t.type === "ident") {
      const name = t.text.replace(/^"|"$/g, "");
      const prev = tokens[i - 1], table = prev && prev.text === "." ? prevIdent : null;
      if (!(tokens[i + 1] && tokens[i + 1].text === ".")) prevIdent = null; else prevIdent = name;
      const role = table && colRole[`${table}.${name}`];
      return role ? `<span class="col-hl role-${role}">${esc(t.text)}</span>` : `<span class="id">${esc(t.text)}</span>`;
    }
    return esc(t.text);
  }).join("");
  const paramRows = origins.map((o, i) => `<tr><td class="num">$${i + 1}</td><td><code>${esc(o.value)}</code></td><td>${esc(o.span || "—")}</td><td class="muted">${esc(o.origin)}</td></tr>`).join("");
  const s5 = `
    <div class="sql-toolbar"><div class="seg" role="group" aria-label="SQL 视图"><button type="button" aria-pressed="true">代入参数</button><button type="button" aria-pressed="false">原始 ?</button></div><span class="grow"></span><span class="muted small mono">query_hash ${esc(r.provenance.query_hash)}</span><button type="button" class="btn ghost small">复制 SQL</button></div>
    <pre class="sql">${sqlHtml}</pre>
    <div class="table-wrap"><table><thead><tr><th class="num">参数</th><th>值</th><th>来自问题片段</th><th>来源规则</th></tr></thead><tbody>${paramRows}</tbody></table></div>
    <ul class="checks">
      ${check("pass", "单条 SELECT，sqlglot AST 解析通过", "拒绝 DML / DDL / PRAGMA / ATTACH / 注释 / 多语句")}
      ${check("pass", "全部取值参数化绑定", "用户文本不会拼接进 SQL 字符串")}
      ${check("pass", "SQLite authorizer 只读", "执行期再拦一次写操作与 load_extension")}
      ${check("pass", "资源预算", "墙钟 5 s + VM 步数上限；结果行 ≤ 100")}
    </ul>`;

  /* ⑥ 结果 */
  const chart = C.chooseChart(r);
  const summary = C.summarize(r, chart);
  const metric = chart.metric;
  const dims = chart.dims || [];
  const rows = r.rows;
  const W = 640, rowH = 34, left = 150, right = 70, top = 22;
  const { max, ticks } = C.niceTicks(Math.max(...rows.map((x) => x[metric] || 0)), 4);
  const sx = (v) => left + (v / max) * (W - left - right);
  const H = top + rows.length * rowH + 20;
  const bars = rows.map((row, i) => {
    const y = top + i * rowH, v = row[metric], w = Math.max(0, sx(v) - left), bh = 20, by = y + (rowH - bh) / 2;
    const r4 = Math.min(4, w);
    const d = `M${left},${by} h${w - r4} q${r4},0 ${r4},${r4} v${bh - 2 * r4} q0,${r4} -${r4},${r4} h-${w - r4} z`;
    return `<g class="band" tabindex="0" aria-label="${esc(C.labelOf(row, dims))} ${fmt(v)}">
      <rect class="hit" x="0" y="${y}" width="${W}" height="${rowH}"/>
      <text class="cat" x="${left - 10}" y="${y + rowH / 2 + 4}" text-anchor="end">${esc(row[dims[0]])}</text>
      <path class="s1" d="${d}"/>
      <text class="val" x="${sx(v) + 8}" y="${y + rowH / 2 + 4}">${fmt(v)}</text></g>`;
  }).join("");
  const grid = ticks.map((t) => `<line class="grid" x1="${sx(t)}" x2="${sx(t)}" y1="${top - 4}" y2="${H - 20}"/><text class="tick" x="${sx(t)}" y="${H - 4}" text-anchor="middle">${fmt(t)}</text>`).join("");
  const chartSvg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(metric)} 条形图">${grid}<line class="base" x1="${left}" x2="${left}" y1="${top - 4}" y2="${H - 20}"/>${bars}</svg>`;
  const tableHtml = `<table><thead><tr>${r.columns.map((c) => `<th${typeof rows[0][c] === "number" ? ' class="num"' : ""}>${esc(c)}</th>`).join("")}</tr></thead><tbody>${rows.map((row) => `<tr>${r.columns.map((c) => `<td${typeof row[c] === "number" ? ' class="num"' : ""}>${esc(typeof row[c] === "number" ? fmt(row[c]) : row[c])}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
  const s6 = `
    <div class="answer-line"><span class="first">${esc(summary[0])}${esc(summary[1] || "")}</span><span class="answer-foot">结论由结果行按模板生成，没有经过大模型改写；每个数字都能在下方明细里找到。</span></div>
    <div class="view-switch"><div class="seg" role="group" aria-label="结果视图"><button type="button" aria-pressed="true">图表</button><button type="button" aria-pressed="false">明细表</button></div><span class="muted small">华南 · 按${esc(metric)}降序 · 前 ${plan.top_n}（含并列）</span></div>
    <div class="chart-box">${chartSvg}</div>
    <div class="table-wrap">${tableHtml}</div>
    <div class="provenance"><span><b>数据源</b> ${esc(r.provenance.database)}</span><span><b>主表</b> ${esc(r.provenance.table)}</span><span><b>返回</b> ${r.provenance.row_count} 行</span><span><b>字段映射</b> ${r.provenance.field_links.length} 条</span><span><b>query_hash</b> <span class="mono">${esc(r.provenance.query_hash)}</span></span></div>`;

  $("#steps").innerHTML = [
    step(1, "感知", "要素识别与问题改写", "问题里的每个词被当成什么", s1),
    step(2, "定位", "表与字段定位 · JOIN 路径", "为什么是这几张表", s2),
    step(3, "规划", "结构化查询计划", "槽位 → 计划，执行前可审计", s3),
    step(4, "校验", "语义校验与澄清", "不确定就停下来问", s4),
    step(5, "生成", "SQL 与安全门", "每个参数都能追到问题片段", s5),
    step(6, "回答", "结果与溯源", "图表、明细与来源", s6),
  ].join("");

  /* ---------- 侧栏：会话 ---------- */
  const turns = [
    ["2025年华东区域的销售额", "2025年华东区域的销售额", [], "ok"],
    ["那华南呢", "2025年华南地区的销售额", [["取值", "华东", "华南"]], "ok"],
    ["看看订单数", "2025年华南地区的订单数", [["指标", "销售额", "订单数"]], "ok"],
    ["换成2024年", "2024年华南地区的订单数", [["时间", "2025年", "2024年"]], "ok"],
    [P.question, P.question, [], "ok"],
  ];
  $("#turns-empty").hidden = true;
  $("#turns").innerHTML = turns.map(([q, eff, rep, s], i) => `
    <li><button type="button" class="turn"${i === turns.length - 1 ? ' aria-current="true"' : ""}>
      <span class="n">${i + 1}</span>
      <span><span class="q">${esc(q)}</span>
        ${eff !== q ? `<span class="eff">→ ${esc(eff)}</span>` : ""}
        ${rep.length ? `<span class="chips">${rep.map(([k, a, b]) => `<span class="slot-chip">${k} <del>${esc(a)}</del> → <ins>${esc(b)}</ins></span>`).join("")}</span>` : ""}
        <span class="status" data-s="${s}"><span class="d"></span>${i === 0 ? "独立问题" : eff === q ? "独立问题（自包含）" : "槽位级改写"}</span>
      </span></button></li>`).join("");

  /* ---------- 侧栏：示例 ---------- */
  $("#samples-db").textContent = P.samples.label;
  $("#samples").innerHTML = P.samples.groups.map((g) => `
    <div class="sample-group"><h4>${esc(g.title)}<span>${esc(g.task)}</span></h4>
      ${g.items.map((it) => `<button type="button" class="sample"><span class="sq">${esc(it.q)}</span><span class="sn">${esc(it.note)}</span></button>`).join("")}
    </div>`).join("");

  /* ---------- 深浅色切换 ---------- */
  $("#theme").addEventListener("click", () => {
    const root = document.documentElement;
    const dark = root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
    root.dataset.theme = dark ? "light" : "dark";
  });
  /* 标签切换（纯视图） */
  const tabs = [["#tab-ask", "#view-ask"], ["#tab-doc", "#view-doc"]];
  tabs.forEach(([t, v]) => $(t).addEventListener("click", () => {
    tabs.forEach(([t2, v2]) => { $(t2).setAttribute("aria-selected", String(t2 === t)); $(v2).hidden = v2 !== v; });
  }));
})();

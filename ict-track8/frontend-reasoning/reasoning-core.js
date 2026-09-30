/*
 * 问数推理台的纯函数层：把 NL2SQL 响应里的真实审计字段整理成可视化所需的结构。
 * 不做任何推断性补写——每个输出都能追溯到响应中的某个字段。
 * 同时支持浏览器（window.ReasoningCore）与 node --test（module.exports）。
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.ReasoningCore = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const ROLE_META = {
    metric: { label: "指标", order: 1 },
    dimension: { label: "维度", order: 2 },
    filter: { label: "取值", order: 3 },
    time: { label: "时间", order: 4 },
    operator: { label: "算子", order: 5 },
    unresolved: { label: "未解析", order: 6 },
    ignored: { label: "已剔除", order: 7 },
  };

  // 与 backend/nl2sql/t2s.py 的 _PAIRS 保持一致（tests/test_reasoning_console_samples.py 校验）。
  // 后端在解析前做繁→简归一，前端只用它把归一后的片段定位回用户原文，不参与任何判断。
  const T2S_PAIRS = "銷销售售額额訂订單单數数據据區区華华東东門门線线產产類类別别客客戶户統统計计價价錢钱總总營营業业務务報报表表員员當当時时間间歲岁體体級级優优務务態态處处理理結结況况發发網网購购買买賣卖費费貨货運运輸输達达較较對对與与從从個个們们為为這这麼么們们還还沒没來来後后會会過过現现點点幾几開开關关長长頭头電电腦脑機机車车學学習习聽听說说讀读寫写問问題题號号碼码週周會会議议簽签約约續续紀纪錄录庫库應应風风險险評评價价標标準准億亿萬万兩两環环比比漲涨減减幣币額额預预測测趨趋勢势占占擁拥齊齐傳传專专將将歷历際际區区縣县鎮镇廣广劃划陝陕貴贵遼辽寧宁蘇苏淺浅穩稳齡龄寶宝雲云聲声場场頻频";
  const T2S = new Map();
  for (let i = 0; i + 1 < T2S_PAIRS.length; i += 2) T2S.set(T2S_PAIRS[i], T2S_PAIRS[i + 1]);
  const toSimplified = (text) => Array.from(String(text || ""), (ch) => T2S.get(ch) || ch).join("");

  const TIME_HINT = /(\d{2,4}\s*年|\d{1,2}\s*月|季度|半年|上半年|下半年|去年|今年|前年|本月|上月|上个月|本周|上周|最近|过去|近\d|至今|以来)/;
  const DATE_LIKE = /^\d{4}-\d{2}(-\d{2})?/;

  function qualified(table, column) {
    return table ? `${table}.${column}` : String(column || "");
  }

  function isDateLike(value) {
    return typeof value === "string" && DATE_LIKE.test(value);
  }

  function operatorDetail(text, plan) {
    const t = String(text || "");
    if (plan.comparison_mode && plan.comparison_mode !== "none" && /同比|环比/.test(t)) return `比较模式：${plan.comparison_mode}，本期与对比期双窗口对齐`;
    if (/排名|排行/.test(t)) return "排名：DENSE_RANK() 窗口函数";
    if (/占比|份额|比例/.test(t)) return "占比：分组值 / 窗口总量";
    if (plan.having && plan.having.source_text === t) return `聚合阈值：${plan.having.explanation}`;
    if (plan.top_n != null && /\d|前|最/.test(t)) return `Top-${plan.top_n}（DENSE_RANK，含并列）`;
    if (/不含|不包括|除了|以外|除外|排除|剔除/.test(t)) return "否定：对随后取值取反（!= / NOT IN）";
    if (/按月|每月|按年|每年|按季|每季|按周|每周|按天|每天|逐月|月度/.test(t)) return "时间粒度：按周期分组";
    return "结构化算子";
  }

  /** 把 plan.links / filters / coverage 分成带角色的问题片段。 */
  function classifySpans(structured) {
    const plan = (structured && structured.plan) || {};
    const coverage = plan.coverage || (structured && structured.provenance && structured.provenance.coverage) || {};
    const groupSet = new Set((plan.dimensions || []).map((column) => qualified((plan.dimension_tables || {})[column], column)));
    const filterCols = new Set((plan.filters || []).map((item) => qualified(item.table, item.column)));
    const spans = [];
    const seen = new Set();
    const push = (text, role, detail, extra) => {
      const value = String(text == null ? "" : text).trim();
      if (!value || seen.has(value)) return;
      seen.add(value);
      spans.push(Object.assign({ text: value, role, label: ROLE_META[role].label, detail: detail || "" }, extra || {}));
    };

    for (const item of plan.filters || []) {
      const values = Array.isArray(item.value) ? item.value : [item.value];
      const isTime = item.operator === "RANGE" || item.operator === "BETWEEN" || values.every(isDateLike);
      push(item.source_text, isTime ? "time" : "filter", item.explanation, { column: qualified(item.table, item.column), operator: item.operator });
    }
    for (const link of plan.links || []) {
      const role = link.role === "metric" ? "metric" : "dimension";
      const column = qualified(link.table, link.column);
      let detail = column;
      if (role === "dimension" && !groupSet.has(column)) {
        detail += filterCols.has(column) ? " · 修饰取值，不参与分组" : " · 仅用于定位，不参与分组";
      }
      const transform = (plan.dimension_transforms || {})[link.column];
      if (role === "dimension" && transform) detail += ` · 按 ${transform} 粒度`;
      push(link.source_text, role, detail, { column, score: link.score, alias: link.matched_alias });
    }
    for (const text of coverage.consumed || []) {
      const isTime = TIME_HINT.test(text);
      push(text, isTime ? "time" : "operator", isTime ? "时间表达式" : operatorDetail(text, plan));
    }
    for (const text of coverage.unresolved || []) push(text, "unresolved", "问题中没有对应的 Schema 字段或库内取值");
    for (const text of coverage.ignored_instruction_spans || []) push(text, "ignored", "识别为指令性文本，已从问题中剔除");
    return spans;
  }

  /**
   * 在原始问题中定位片段（忽略空白与大小写，长片段优先，互不重叠）。
   * 返回按原文顺序的 segments 与无法定位的片段（例如繁体输入被归一成简体后）。
   */
  function locateSpans(question, spans) {
    const q = String(question || "");
    const map = [];
    let stripped = "";
    for (let i = 0; i < q.length; i += 1) {
      if (/\s/.test(q[i])) continue;
      map.push(i);
      stripped += toSimplified(q[i]).toLowerCase();
    }
    const taken = new Array(stripped.length).fill(null);
    const ordered = [...(spans || [])].sort((a, b) => b.text.length - a.text.length);
    const unlocated = [];
    for (const span of ordered) {
      const needle = toSimplified(span.text.replace(/\s+/g, "")).toLowerCase();
      if (!needle) continue;
      let from = 0;
      let placed = false;
      while (from <= stripped.length - needle.length) {
        const idx = stripped.indexOf(needle, from);
        if (idx < 0) break;
        let free = true;
        for (let k = idx; k < idx + needle.length; k += 1) {
          if (taken[k]) { free = false; break; }
        }
        if (free) {
          for (let k = idx; k < idx + needle.length; k += 1) taken[k] = span;
          placed = true;
          break;
        }
        from = idx + 1;
      }
      if (!placed) unlocated.push(span);
    }
    const segments = [];
    let j = 0;
    for (let i = 0; i < q.length; i += 1) {
      const span = /\s/.test(q[i]) ? null : taken[j++];
      const last = segments[segments.length - 1];
      if (last && last.span === span) last.text += q[i];
      else segments.push({ text: q[i], span });
    }
    return { segments, unlocated };
  }

  const SQL_WORDS = new Set(
    ("SELECT FROM WHERE AND OR NOT IN IS NULL JOIN LEFT RIGHT INNER OUTER ON GROUP BY ORDER HAVING LIMIT OFFSET AS DESC ASC " +
      "DISTINCT CASE WHEN THEN ELSE END OVER PARTITION WITH BETWEEN LIKE UNION ALL EXISTS").split(" "),
  );
  const SQL_FUNCS = new Set("SUM COUNT AVG MIN MAX DENSE_RANK RANK ROW_NUMBER NULLIF STRFTIME SUBSTR CAST COALESCE ROUND DATE ABS".split(" "));
  const CLAUSE_BREAK = new Set(["FROM", "WHERE", "GROUP", "ORDER", "HAVING", "LIMIT", "JOIN", "LEFT", "INNER", "UNION"]);

  function tokenizeSql(sql) {
    const re = /("(?:[^"]|"")*")|('(?:[^']|'')*')|(\?)|(\d+(?:\.\d+)?)|([A-Za-z_][A-Za-z0-9_]*)|(\s+)|([\s\S])/g;
    const tokens = [];
    let match;
    let param = 0;
    while ((match = re.exec(String(sql || "")))) {
      if (match[1]) tokens.push({ type: "ident", text: match[1] });
      else if (match[2]) tokens.push({ type: "string", text: match[2] });
      else if (match[3]) tokens.push({ type: "param", text: "?", index: param++ });
      else if (match[4]) tokens.push({ type: "number", text: match[4] });
      else if (match[5]) {
        const upper = match[5].toUpperCase();
        tokens.push({ type: SQL_WORDS.has(upper) ? "keyword" : SQL_FUNCS.has(upper) ? "func" : "word", text: match[5] });
      } else if (match[6]) tokens.push({ type: "space", text: " " });
      else tokens.push({ type: "punct", text: match[7] });
    }
    return tokens;
  }

  /** 在每层 SELECT 的子句关键字前换行；OVER(...) 内部的 ORDER BY 不换行。 */
  function layoutSql(tokens) {
    const out = [];
    let depth = 0;
    const selectDepths = [];
    let inWhere = false;
    const newline = (extra) => {
      while (out.length && out[out.length - 1].type === "space") out.pop();
      out.push({ type: "newline", text: "\n" + "  ".repeat(Math.max(0, selectDepths.length - 1) + (extra || 0)) });
    };
    for (const token of tokens) {
      const upper = token.text.toUpperCase();
      if (token.type === "punct" && token.text === "(") depth += 1;
      if (token.type === "punct" && token.text === ")") {
        depth -= 1;
        while (selectDepths.length && selectDepths[selectDepths.length - 1] > depth) selectDepths.pop();
      }
      if (token.type === "keyword" && upper === "SELECT") {
        selectDepths.push(depth);
        if (out.length) newline(0);
        out.push(token);
        inWhere = false;
        continue;
      }
      const atClauseDepth = selectDepths.length && selectDepths[selectDepths.length - 1] === depth;
      if (token.type === "keyword" && atClauseDepth && CLAUSE_BREAK.has(upper)) {
        const previous = [...out].reverse().find((item) => item.type !== "space");
        const joinTail = previous && ["LEFT", "INNER"].includes(previous.text.toUpperCase()) && upper === "JOIN";
        if (!joinTail) newline(0);
        inWhere = upper === "WHERE" || upper === "HAVING";
        if (["GROUP", "ORDER", "LIMIT"].includes(upper)) inWhere = false;
      } else if (token.type === "keyword" && atClauseDepth && inWhere && (upper === "AND" || upper === "OR")) {
        newline(1);
      }
      if (token.type === "space" && out.length && out[out.length - 1].type === "newline") continue;
      out.push(token);
    }
    return out;
  }

  /** 为每个 SQL 参数找到它来自问题的哪个片段 / 哪条规则。 */
  function paramOrigins(structured, spans) {
    const params = (structured && structured.parameters) || [];
    const plan = (structured && structured.plan) || {};
    const audit = plan.intent_audit || {};
    const timeSpan = (spans || []).find((item) => item.role === "time");
    const same = (a, b) => String(a) === String(b);
    return params.map((value, index) => {
      for (const item of plan.filters || []) {
        const values = Array.isArray(item.value) ? item.value : [item.value];
        if (values.some((candidate) => same(candidate, value))) {
          return { value, origin: item.explanation, span: item.source_text };
        }
      }
      const window = audit.comparison_window;
      if (window) {
        if ((window.current || []).some((v) => same(v, value))) return { value, origin: `本期窗口 ${window.current.join(" ~ ")}`, span: timeSpan && timeSpan.text };
        if ((window.previous || []).some((v) => same(v, value))) return { value, origin: `对比窗口 ${window.previous.join(" ~ ")}（由本期推导）`, span: timeSpan && timeSpan.text };
      }
      if (Array.isArray(audit.time_range) && audit.time_range.some((v) => same(v, value))) {
        return { value, origin: `时间窗 ${audit.time_range.join(" ~ ")}`, span: timeSpan && timeSpan.text };
      }
      if (plan.having && same(plan.having.value, value)) return { value, origin: plan.having.explanation, span: plan.having.source_text };
      if (plan.top_n != null && same(plan.top_n, value)) return { value, origin: `Top-${plan.top_n}` };
      if (index === params.length - 1 && same(plan.limit, value)) return { value, origin: "安全行数上限（LIMIT），非用户意图" };
      return { value, origin: "规划器常量" };
    });
  }

  const PCT_COLUMN = /\(%\)$/;

  function chooseChart(structured) {
    const columns = (structured && structured.columns) || [];
    const rows = (structured && structured.rows) || [];
    const plan = (structured && structured.plan) || {};
    if (!rows.length || !columns.length) return { kind: "empty" };
    const isNumeric = (column) =>
      rows.every((row) => row[column] == null || typeof row[column] === "number") && rows.some((row) => typeof row[column] === "number");
    const nullOnly = (column) => rows.every((row) => row[column] == null);
    const numeric = columns.filter((column) => isNumeric(column) || (nullOnly(column) && columns.length <= 3 && rows.length === 1));
    const pct = columns.find((column) => PCT_COLUMN.test(column));
    const rank = columns.find((column) => column === "排名");
    const dims = columns.filter((column) => !numeric.includes(column) && column !== pct);
    const values = numeric.filter((column) => column !== pct && column !== rank);
    if (plan.comparison_mode && plan.comparison_mode !== "none" && values.length >= 2) {
      const [current, previous] = values;
      if (!dims.length && rows.length === 1) return { kind: "stat", value: current, previous, pct, row: rows[0] };
      return { kind: "compare", dims, current, previous, pct };
    }
    if (!values.length) return { kind: "table" };
    if (!dims.length && rows.length === 1) return { kind: "stat", value: values[0], row: rows[0], pct };
    const metric = values[0];
    const grains = Object.values(plan.dimension_transforms || {});
    const timeGrain = grains.some((grain) => ["day", "week", "month", "quarter", "year"].includes(grain));
    if (timeGrain && dims.length === 1 && rows.length >= 2) return { kind: "line", dim: dims[0], metric };
    if (dims.length && rows.length <= 40) return { kind: "bar", dims, metric, pct, rank };
    return { kind: "table" };
  }

  function formatNumber(value) {
    if (value == null || value === "") return "—";
    if (typeof value !== "number" || !Number.isFinite(value)) return String(value);
    const rounded = Math.abs(value) >= 100 || Number.isInteger(value) ? Math.round(value * 100) / 100 : Math.round(value * 100) / 100;
    return rounded.toLocaleString("en-US", { maximumFractionDigits: 2 });
  }

  function niceStep(raw) {
    if (!(raw > 0)) return 1;
    const exponent = Math.pow(10, Math.floor(Math.log10(raw)));
    const fraction = raw / exponent;
    const nice = fraction <= 1 ? 1 : fraction <= 2 ? 2 : fraction <= 2.5 ? 2.5 : fraction <= 5 ? 5 : 10;
    return nice * exponent;
  }

  function niceTicks(maxValue, count) {
    const target = count || 4;
    const step = niceStep((maxValue > 0 ? maxValue : 1) / target);
    const max = Math.max(step, Math.ceil((maxValue > 0 ? maxValue : 1) / step) * step);
    const ticks = [];
    for (let v = 0; v <= max + step / 2; v += step) ticks.push(Math.round(v * 1e6) / 1e6);
    return { max, ticks };
  }

  function labelOf(row, dims) {
    return dims.map((dim) => (row[dim] == null ? "（空）" : String(row[dim]))).join(" · ");
  }

  /** 由结果行生成的模板化结论，不经大模型。 */
  function summarize(structured, chart) {
    const plan = (structured && structured.plan) || {};
    const rows = (structured && structured.rows) || [];
    const lines = [];
    if (!chart || chart.kind === "empty") {
      lines.push("查询范围内没有返回数据行。");
      return lines;
    }
    if (chart.kind === "stat") {
      const value = chart.row[chart.value];
      if (value == null) {
        lines.push(`${chart.value}：无数据（范围内没有匹配的记录，聚合结果为空，不等于 0）。`);
      } else {
        lines.push(`${chart.value} = ${formatNumber(value)}`);
      }
      if (chart.previous) {
        const previous = chart.row[chart.previous];
        const pct = chart.pct ? chart.row[chart.pct] : null;
        if (previous == null) lines.push(`${chart.previous}无数据，无法计算变化率。`);
        else if (pct != null) lines.push(`${chart.previous} ${formatNumber(previous)}，变化 ${pct > 0 ? "+" : ""}${formatNumber(pct)}%。`);
      }
      return lines;
    }
    if (chart.kind === "bar" || chart.kind === "line") {
      const dims = chart.kind === "bar" ? chart.dims : [chart.dim];
      const valid = rows.filter((row) => typeof row[chart.metric] === "number");
      lines.push(`共 ${rows.length} 组${plan.top_n ? `（Top-${plan.top_n}，含并列）` : ""}。`);
      if (valid.length) {
        const max = valid.reduce((a, b) => (b[chart.metric] > a[chart.metric] ? b : a));
        const min = valid.reduce((a, b) => (b[chart.metric] < a[chart.metric] ? b : a));
        lines.push(`${chart.metric}最高：${labelOf(max, dims)}（${formatNumber(max[chart.metric])}）${valid.length > 1 ? `；最低：${labelOf(min, dims)}（${formatNumber(min[chart.metric])}）` : ""}。`);
        if (chart.pct && max[chart.pct] != null) lines.push(`${labelOf(max, dims)} 占比 ${formatNumber(max[chart.pct])}%。`);
      }
      return lines;
    }
    if (chart.kind === "compare") {
      const both = rows.filter((row) => row[chart.current] != null && row[chart.previous] != null);
      lines.push(`${rows.length} 组中 ${both.length} 组两期都有数据，其余组缺少某一期，变化率留空而不是按 0 计算。`);
      if (both.length && chart.pct) {
        const best = both.reduce((a, b) => ((b[chart.pct] || 0) > (a[chart.pct] || 0) ? b : a));
        lines.push(`变化最大：${labelOf(best, chart.dims)}（${best[chart.pct] > 0 ? "+" : ""}${formatNumber(best[chart.pct])}%）。`);
      }
      return lines;
    }
    lines.push(`返回 ${rows.length} 行，见下方明细表。`);
    return lines;
  }

  /** 基于外键图的分层布局：从事实表出发 BFS 分列，未连通的表顺延到右侧。 */
  function layoutSchema(tables, options) {
    const opts = options || {};
    const used = new Set(opts.usedTables || []);
    const highlight = opts.highlight || {};
    const nodeW = opts.nodeWidth || 188;
    const rowH = 17;
    const headH = 30;
    const gapX = 64;
    const gapY = 18;
    const maxRows = 7;
    const byName = new Map((tables || []).map((table) => [table.name, table]));
    const adjacency = new Map([...byName.keys()].map((name) => [name, new Set()]));
    const fkCols = new Map([...byName.keys()].map((name) => [name, new Set()]));
    const edges = [];
    for (const table of tables || []) {
      for (const fk of table.foreign_keys || []) {
        if (!byName.has(fk.table)) continue;
        adjacency.get(table.name).add(fk.table);
        adjacency.get(fk.table).add(table.name);
        fkCols.get(table.name).add(fk.from_column);
        edges.push({ from: table.name, fromCol: fk.from_column, to: fk.table, toCol: fk.to_column });
      }
    }
    const outDegree = (name) => (byName.get(name).foreign_keys || []).length;
    const remaining = new Set(byName.keys());
    const depthOf = new Map();
    let offset = 0;
    const pickRoot = () => {
      const pool = [...remaining];
      const preferred = opts.rootTable && remaining.has(opts.rootTable) ? opts.rootTable : null;
      if (preferred) return preferred;
      pool.sort((a, b) => (used.has(b) - used.has(a)) || outDegree(b) - outDegree(a) || a.localeCompare(b));
      return pool[0];
    };
    while (remaining.size) {
      const root = pickRoot();
      const queue = [root];
      depthOf.set(root, offset);
      remaining.delete(root);
      let maxDepth = offset;
      while (queue.length) {
        const current = queue.shift();
        for (const next of [...adjacency.get(current)].sort()) {
          if (!remaining.has(next)) continue;
          remaining.delete(next);
          depthOf.set(next, depthOf.get(current) + 1);
          maxDepth = Math.max(maxDepth, depthOf.get(next));
          queue.push(next);
        }
      }
      offset = maxDepth + 1;
    }
    const columnsByDepth = new Map();
    for (const [name, depth] of depthOf) {
      if (!columnsByDepth.has(depth)) columnsByDepth.set(depth, []);
      columnsByDepth.get(depth).push(name);
    }
    const nodes = new Map();
    let height = 0;
    for (const [depth, names] of [...columnsByDepth.entries()].sort((a, b) => a[0] - b[0])) {
      names.sort((a, b) => (used.has(b) - used.has(a)) || a.localeCompare(b));
      let y = 0;
      for (const name of names) {
        const table = byName.get(name);
        const all = table.columns || [];
        const important = (column) => column.primary_key || fkCols.get(name).has(column.name) || highlight[`${name}.${column.name}`];
        let shown = all.length <= maxRows ? all : all.filter(important);
        if (shown.length < maxRows) {
          for (const column of all) {
            if (shown.length >= maxRows) break;
            if (!shown.includes(column)) shown = [...shown, column];
          }
          shown = all.filter((column) => shown.includes(column));
        }
        const hidden = all.length - shown.length;
        const rows = shown.map((column, index) => ({
          name: column.name,
          type: column.data_type,
          pk: !!column.primary_key,
          fk: fkCols.get(name).has(column.name),
          highlight: highlight[`${name}.${column.name}`] || null,
          y: headH + index * rowH + rowH / 2,
        }));
        const h = headH + rows.length * rowH + (hidden ? rowH : 0) + 6;
        nodes.set(name, { name, x: depth * (nodeW + gapX), y, w: nodeW, h, rows, hidden, rowCount: table.row_count, used: used.has(name) });
        y += h + gapY;
      }
      height = Math.max(height, y - gapY);
    }
    const width = Math.max(nodeW, ...[...nodes.values()].map((node) => node.x + node.w));
    return { nodes, edges, width, height: Math.max(height, 40), rowH };
  }

  const STAGE_LABELS = {
    intent: "意图与上下文",
    structured_query: "结构化问数",
    clarification: "主动澄清",
    document_retrieval: "知识库检索（RAG 模块）",
    evidence_fusion: "证据融合",
  };

  return {
    ROLE_META,
    STAGE_LABELS,
    classifySpans,
    locateSpans,
    tokenizeSql,
    layoutSql,
    paramOrigins,
    chooseChart,
    summarize,
    formatNumber,
    niceTicks,
    labelOf,
    layoutSchema,
    toSimplified,
    T2S_PAIRS,
  };
});

(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.QueryJourney = api;
})(typeof window !== "undefined" ? window : globalThis, function () {
  "use strict";
  const NS = "http://www.w3.org/2000/svg";
  const ROLES = { metric: "指标", dimension: "维度", filter: "筛选", time: "时间", join: "关联", operator: "运算" };
  const arr = (value) => Array.isArray(value) ? value : [];
  const text = (value) => value == null ? "未返回" : typeof value === "object" ? JSON.stringify(value) : String(value);
  const key = (table, column) => `${table}.${column}`;
  function el(tag, cls, value) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (value !== undefined) node.textContent = text(value);
    return node;
  }
  function svg(tag, attrs, value) {
    const node = document.createElementNS(NS, tag);
    Object.entries(attrs || {}).forEach(([name, v]) => node.setAttribute(name, String(v)));
    if (value !== undefined) node.textContent = text(value);
    return node;
  }
  function button(label, cls, handler) {
    const b = el("button", cls, label); b.type = "button";
    if (handler) b.addEventListener("click", handler);
    return b;
  }

  function buildModel(data) {
    const structured = data?.structured || {}, plan = structured.plan || {};
    const fields = [], seen = new Set();
    function add(item, role, source, fallback = false) {
      const table = item.table || plan.table, column = item.column;
      if (!table || !column || column === "*") return;
      const name = key(table, column), sourceText = source ?? item.source_text ?? item.label ?? column;
      if (fallback && fields.some((f) => f.key === name && f.role === role)) return;
      const identity = `${name}\0${role}\0${sourceText}`;
      if (seen.has(identity)) return;
      seen.add(identity);
      fields.push({ key: name, table, column, role: ROLES[role] ? role : "operator", source: String(sourceText), score: item.score });
    }
    arr(structured.provenance?.field_links || plan.links).forEach((item) => add(item, item.role));
    arr(plan.filters).forEach((item) => add(item, "filter"));
    if (plan.metric_column) add({ table: plan.metric_table || plan.table, column: plan.metric_column }, "metric", plan.metric_label, true);
    arr(plan.metrics).forEach((m) => add({ table: m.table || m.metric_table, column: m.column || m.metric_column }, "metric", m.label || m.metric_label, true));
    arr(plan.dimensions).forEach((column) => add({ table: plan.dimension_tables?.[column] || plan.table, column }, "dimension", plan.dimension_labels?.[column] || column, true));
    arr(plan.join_path).forEach((j) => {
      if (j.from_column) add({ table: j.from_table, column: j.from_column }, "join");
      if (j.to_column) add({ table: j.to_table, column: j.to_column }, "join");
    });
    const tables = [...new Set([plan.table, plan.metric_table, ...arr(plan.join_tables), ...fields.map((f) => f.table)].filter(Boolean))];
    const operations = [];
    if (tables.length) operations.push({ type: "source", label: "数据表", summary: tables.join(" · "), details: tables, fields: fields.map((f) => f.key) });
    if (arr(plan.join_path).length) operations.push({ type: "join", label: "关联", summary: `${plan.join_path.length} 段关联`, details: plan.join_path.map((j) => j.condition || `${j.from_table} → ${j.to_table}`), fields: fields.filter((f) => f.role === "join").map((f) => f.key) });
    if (arr(plan.filters).length) operations.push({ type: "filter", label: "筛选", summary: `${plan.filters.length} 个条件`, details: plan.filters.map((f) => `${key(f.table || plan.table, f.column)} ${f.operator || "="} ${text(f.value)}`), fields: fields.filter((f) => f.role === "filter" || f.role === "time").map((f) => f.key) });
    if (arr(plan.dimensions).length) operations.push({ type: "dimension", label: "分组", summary: plan.dimensions.join(" · "), details: plan.dimensions.map((c) => key(plan.dimension_tables?.[c] || plan.table, c)), fields: fields.filter((f) => f.role === "dimension").map((f) => f.key) });
    const metrics = arr(plan.metrics).length ? plan.metrics.map((m) => `${m.function || m.metric_function || ""}(${key(m.table || m.metric_table || plan.table, m.column || m.metric_column || "*")})`) : plan.metric_function ? [`${plan.metric_function}(${key(plan.metric_table || plan.table, plan.metric_column || "*")})`] : [];
    if (metrics.length) operations.push({ type: "metric", label: "计算", summary: plan.metric_label || `${metrics.length} 个指标`, details: metrics.concat(arr(plan.derived_metrics).map((m) => text(m.formula || m.expression || m))), fields: fields.filter((f) => f.role === "metric").map((f) => f.key) });
    if (plan.having) operations.push({ type: "filter", label: "聚合后筛选", summary: "HAVING", details: [text(plan.having)], fields: [] });
    if (plan.analysis_mode && plan.analysis_mode !== "aggregate") operations.push({ type: "analysis", label: "分析", summary: ({ rank: "排名", share: "占比", detail: "明细" })[plan.analysis_mode] || plan.analysis_mode, details: [`分析方式：${plan.analysis_mode}`, ...(plan.comparison_mode && plan.comparison_mode !== "none" ? [`比较方式：${plan.comparison_mode}`, text(plan.comparison_period)] : [])], fields: [] });
    else if (plan.comparison_mode && plan.comparison_mode !== "none") operations.push({ type: "analysis", label: "比较", summary: plan.comparison_mode, details: [text(plan.comparison_period)], fields: [] });
    const complete = structured.provenance?.complete_result;
    if (structured.sql && complete) {
      const verified = complete.status === "complete" && complete.cursor_eof_verified === true && Number.isSafeInteger(complete.row_count);
      operations.push({ type: "limit", label: "返回范围", summary: verified ? `完整 ${complete.row_count} 行` : "完整结果未取得", details: [
        `当前预览：${arr(structured.rows).length} 行`,
        verified ? `完整结果：${complete.row_count} 行，可分页查看` : "当前仅有部分预览，不能作为完整答案",
        ...(plan.semantic_row_limit != null ? [`查询范围上限：${plan.semantic_row_limit} 行`] : []),
        ...(plan.top_n != null ? [`查询 Top-${plan.top_n}（含并列）`] : []),
      ], fields: [] });
    } else if (structured.sql && plan.limit != null) operations.push({ type: "limit", label: "返回范围", summary: `预览上限 ${plan.limit} 行`, details: [`预览上限：${plan.limit}`, `已返回：${arr(structured.rows).length} 行`, `结果范围：${structured.provenance?.result_completeness || "未返回"}`], fields: [] });
    return { structured, plan, fields, tables, operations, question: data?.effective_question || structured.rewritten_question || data?.question || "", original: data?.question || "" };
  }

  // Longest mapped phrases win overlaps. We never assign an unreported meaning to a word.
  function questionSegments(question, fields, unresolved) {
    const candidates = [];
    fields.forEach((field) => {
      if (!field.source) return;
      let at = 0;
      while ((at = question.indexOf(field.source, at)) !== -1) {
        candidates.push({ start: at, end: at + field.source.length, field }); at += field.source.length;
      }
    });
    arr(unresolved).forEach((value) => {
      const term = typeof value === "string" ? value : value?.source_text || value?.text;
      if (!term) return;
      let at = 0;
      while ((at = question.indexOf(term, at)) !== -1) { candidates.push({ start: at, end: at + term.length, unresolved: true }); at += term.length; }
    });
    candidates.sort((a, b) => (b.end - b.start) - (a.end - a.start));
    const chosen = [];
    candidates.forEach((candidate) => { if (!chosen.some((c) => candidate.start < c.end && candidate.end > c.start)) chosen.push(candidate); });
    chosen.sort((a, b) => a.start - b.start);
    const segments = []; let cursor = 0;
    chosen.forEach((c) => { if (c.start > cursor) segments.push({ text: question.slice(cursor, c.start) }); segments.push({ ...c, text: question.slice(c.start, c.end) }); cursor = c.end; });
    if (cursor < question.length) segments.push({ text: question.slice(cursor) });
    return segments;
  }

  function annotateSql(tokens, fields) {
    const byKey = new Map(fields.map((f) => [f.key, f]));
    const byColumn = new Map();
    fields.forEach((f) => { if (!byColumn.has(f.column)) byColumn.set(f.column, new Set()); byColumn.get(f.column).add(f.key); });
    const parts = tokens.map((token) => ({ ...token }));
    const indices = parts.map((t, i) => ({ t, i })).filter(({ t }) => t.text.trim());
    const identifier = (t) => {
      if (typeof t.text !== "string") return null;
      if (t.kind === "identifier") return t.text;
      if (t.text.startsWith('"') && t.text.endsWith('"')) return t.text.slice(1, -1).replace(/""/g, '"');
      if (t.text.startsWith("`") && t.text.endsWith("`")) return t.text.slice(1, -1);
      return null;
    };
    for (let n = 0; n < indices.length; n++) {
      const { t, i } = indices[n], name = identifier(t);
      if (!name) continue;
      if (indices[n + 1]?.t.text === "." && identifier(indices[n + 2]?.t || {})) {
        const qualified = key(name, identifier(indices[n + 2].t));
        if (byKey.has(qualified)) { parts[i].field = qualified; parts[indices[n + 2].i].field = qualified; }
        n += 2; continue;
      }
      if (indices[n - 1]?.t.text === "." || /^as$/i.test(indices[n - 1]?.t.text || "")) continue;
      const keys = byColumn.get(name);
      if (keys?.size === 1) parts[i].field = [...keys][0];
    }
    let parameter = 0;
    parts.forEach((t) => { if (t.kind === "parameter" && /^\?\d*$/.test(t.text)) { t.parameter = t.text.length > 1 ? Number(t.text.slice(1)) - 1 : parameter; parameter = Math.max(parameter, t.parameter + 1); } });
    return parts;
  }

  function formatSql(source) {
    const tokens = (window.LatticeSyntaxHighlight?.tokenize(source, "sql") || [])
      .filter((token) => token.text.trim());
    if (!tokens.length) return source;
    const clauses = new Set(["select", "from", "where", "having", "limit", "offset", "union", "intersect", "except", "returning"]);
    const output = [];
    const subqueries = [];
    let indent = 0, continuation = 0, caseDepth = 0, lineStart = true, pendingIndent = 0, previous = null;
    const newline = (level = indent) => {
      while (output.length && output[output.length - 1] === " ") output.pop();
      if (output.length && output[output.length - 1] !== "\n") output.push("\n");
      lineStart = true; pendingIndent = Math.max(0, level);
    };
    const emit = (value) => {
      if (lineStart) { output.push("  ".repeat(pendingIndent)); lineStart = false; }
      output.push(value);
    };
    const space = () => { if (!lineStart && output[output.length - 1] !== " ") output.push(" "); };
    tokens.forEach((token, index) => {
      const lower = token.text.toLowerCase(), prev = tokens[index - 1], next = tokens[index + 1];
      const insideExpression = subqueries.some((frame) => !frame.isSubquery);
      const clause = !insideExpression && token.kind === "keyword" && (clauses.has(lower) ||
        ((lower === "group" || lower === "order") && next?.text.toLowerCase() === "by") ||
        (["join", "left", "right", "inner", "full", "cross", "on"].includes(lower)));
      const booleanBreak = token.kind === "keyword" && ["and", "or"].includes(lower) && caseDepth === 0;
      if ((clause || booleanBreak || token.kind === "comment") && output.length) {
        continuation = 0;
        newline(booleanBreak ? indent + 1 : indent);
      }

      if (token.text === ")") {
        const frame = subqueries.pop();
        if (frame?.isSubquery) {
          indent = Math.max(0, indent - 1); continuation = frame.continuation; newline(indent);
        }
        emit(token.text);
      } else if (token.text === ",") {
        emit(token.text);
        if (!subqueries.some((frame) => !frame.isSubquery)) newline(indent + continuation);
        else space();
      } else if (token.text === ".") {
        emit(token.text);
      } else if (token.text === "(") {
        if (prev && prev.kind !== "function" && prev.text !== ".") space();
        emit(token.text);
        const isSubquery = next?.kind === "keyword" && ["select", "with"].includes(next.text.toLowerCase());
        subqueries.push({ isSubquery, continuation });
        if (isSubquery) { indent += 1; continuation = 0; newline(indent); }
      } else if (token.kind === "operator") {
        space(); emit(token.text); space();
      } else if (token.text === ";") {
        emit(token.text);
      } else {
        if (previous && previous.text !== "(" && previous.text !== "." && output[output.length - 1] !== "\n") space();
        emit(token.text);
      }
      if (clause && token.text !== "from" && token.text !== "join" && token.text !== "left" && token.text !== "right" && token.text !== "inner" && token.text !== "full" && token.text !== "cross") continuation = 1;
      if (token.kind === "keyword" && (lower === "select" || lower === "where" || lower === "having" || lower === "group" || lower === "order" || lower === "on" || lower === "set" || lower === "values")) continuation = 1;
      if ((lower === "group" || lower === "order") && next?.text.toLowerCase() === "by") continuation = 1;
      if (lower === "case" && token.kind === "keyword") caseDepth += 1;
      if (lower === "end" && token.kind === "keyword") caseDepth = Math.max(0, caseDepth - 1);
      previous = token;
      if (token.kind === "comment") newline();
    });
    return output.join("").trim();
  }

  function formatSqlWithComments(source, plan = {}, schema = {}, manifest = {}) {
    const formatted = formatSql(source);
    const tableName = (name) => window.SchemaSvg?.tableLabel(name) || name;
    const columnName = (table, column) => window.SchemaSvg?.columnLabel(table, column) || column;
    const dimensions = arr(plan.dimensions).map((item) => {
      const column = typeof item === "string" ? item : item?.column;
      const table = (typeof item === "object" && item?.table) || plan.dimension_tables?.[column] || plan.table;
      return plan.dimension_labels?.[column] || columnName(table, column);
    }).filter(Boolean);
    const metrics = arr(plan.metrics).map((item) => {
      const table = item.table || item.metric_table || plan.metric_table || plan.table;
      const column = item.column || item.metric_column;
      return metricName(table, column, item.function || item.metric_function, item.label || item.metric_label);
    });
    if (!metrics.length && plan.metric_column) {
      metrics.push(metricName(plan.metric_table || plan.table, plan.metric_column, plan.metric_function, plan.metric_label));
    }
    const outputNames = [...dimensions, ...metrics].filter(Boolean);
    const sourceTables = arr(manifest.tables).map((table) => tableName(table.name)).filter(Boolean);
    if (!sourceTables.length) sourceTables.push(...[plan.table, plan.metric_table, ...arr(plan.join_tables)].filter(Boolean).map(tableName));
    const filters = arr(plan.filters).map((filter) => columnName(filter.table || plan.table, filter.column)).filter(Boolean);
    const notes = {
      with: "先生成供后续查询复用的中间结果。",
      select: outputNames.length ? `输出${outputNames.join("、")}${plan.analysis_mode === "rank" ? "及排名" : ""}。` : "确定本段查询要返回的字段。",
      from: sourceTables.length ? `读取${sourceTables.join("、")}表。` : "确定本段查询的数据来源。",
      where: filters.length ? `按${[...new Set(filters)].join("、")}筛选记录` : "筛选满足查询条件的记录",
      group: dimensions.length ? `按${dimensions.join("、")}分组后分别计算指标。` : "按查询指定字段分组汇总。",
      having: "在分组计算后筛选汇总结果。",
      order: "按查询指定的字段和方向排列结果。",
      limit: Number.isFinite(plan.top_n) ? `仅返回排名前 ${plan.top_n} 项。` : Number.isFinite(plan.limit) ? `最多返回 ${plan.limit} 行。` : "限制本次返回的行数。",
      join: "按后续关联条件合并相关数据表。",
      on: "指定关联数据表之间的匹配条件。",
      union: "合并多段查询的结果。",
      intersect: "保留多段查询结果中的共同记录。",
      except: "保留前段查询中未出现在后段查询的记录。",
    };
    return formatted.split("\n").flatMap((line) => {
      const match = line.match(/^(\s*)(WITH|SELECT|FROM|WHERE|GROUP\s+BY|HAVING|ORDER\s+BY|LIMIT|JOIN|LEFT|RIGHT|INNER|FULL|CROSS|ON|UNION|INTERSECT|EXCEPT)\b/i);
      if (!match || match[1].length) return [line];
      const clause = match[2].toLowerCase().replace(/\s+/g, " ");
      const key = clause === "group by" ? "group" : clause === "order by" ? "order" : clause.split(" ")[0];
      let note = notes[key];
      if (!note) return [line];
      if (key === "from") {
        const source = line.match(/^\s*FROM\s+["`]([^"`]+)["`]/i)?.[1] || line.match(/^\s*FROM\s+([\w$]+)/i)?.[1];
        const physicalTables = new Set([...sourceTables, ...arr(schema.tables).map((table) => table.name)]);
        note = source && physicalTables.has(source)
          ? `读取${tableName(source)}表。`
          : source ? `读取中间结果 ${source}。` : "读取本段查询的数据来源。";
      } else if (key === "where" && filters.length) {
        note += line.includes("?") ? "；条件值通过参数绑定。" : "。";
      }
      if (key === "order") {
        const direction = /\bDESC\b/i.test(line) ? "降序" : /\bASC\b/i.test(line) ? "升序" : "指定顺序";
        const metricColumn = plan.metric_column || arr(plan.metrics)[0]?.column || arr(plan.metrics)[0]?.metric_column;
        const metric = metricColumn && line.toLowerCase().includes(String(metricColumn).toLowerCase()) ? metrics[0] : null;
        return [`-- ${metric ? `按${metric}${direction}排列结果。` : `按查询指定字段${direction}排列结果。`}`, line];
      }
      return [`-- ${note}`, line];
    }).join("\n");

    function metricName(table, column, aggregate, explicitLabel) {
      const name = explicitLabel && explicitLabel !== column ? explicitLabel : column && column !== "*" ? columnName(table, column) : "记录数";
      const suffix = ({ SUM: "求和", AVG: "平均值", COUNT: "数量", COUNT_DISTINCT: "去重计数", MIN: "最小值", MAX: "最大值" })[String(aggregate || "").toUpperCase()];
      return suffix && name && !name.includes(suffix) ? `${name}${suffix}` : name;
    }
  }


  function render(data, schema, schemaPromise) {
    const model = buildModel(data), { structured, plan, fields } = model;
    const root = el("div", "query-journey"), sections = [], header = el("div", "query-journey-head");
    const heading = el("div"); heading.append(el("span", "query-eyebrow", "QUERY JOURNEY"), el("h3", "", "看懂这一次查询"));
    const controls = el("div", "query-journey-controls"), replay = button("逐步查看", "query-replay"), closeReplay = button("结束逐步查看", "query-replay"); closeReplay.hidden = true;
    controls.append(replay, closeReplay); header.append(heading, controls); root.append(header);
    root.append(el("p", "query-caption", "点击问题词语、数据库字段或 SQL 标识，查看它们之间的对应关系。"));
    const nav = el("nav", "query-journey-nav"); nav.setAttribute("aria-label", "查询过程导航"); root.append(nav);
    const selection = el("div", "query-selection"); selection.setAttribute("aria-live", "polite");
    const selectionText = el("div", "query-selection-text", "选择词语或字段，查看问题 → 数据库 → SQL 的对应关系。");
    const clearFocus = button("清除定位", "query-text-button", () => focusFields([], "已清除字段定位", false)); clearFocus.hidden = true;
    selection.append(selectionText, clearFocus); root.append(selection);
    let activeKeys = [], schemaHost, replayIndex = -1;
    const prefersReduced = () => window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    function focusFields(keys, origin, scroll) {
      if (scroll && replayIndex >= 0) { replayIndex = 1; showPart(1, false); }
      activeKeys = keys;
      root.querySelectorAll("[data-query-field]").forEach((node) => node.classList.toggle("is-linked", keys.includes(node.dataset.queryField)));
      root.querySelectorAll(".query-sql-token[data-field]").forEach((node) => node.classList.toggle("is-linked", keys.includes(node.dataset.field)));
      let selected = fields.find((f) => keys.includes(f.key));
      if (!selected && keys.length) {
        arr(schema?.tables).some((table) => { const column = arr(table.columns).find((c) => keys.includes(key(table.name, c.name))); if (!column) return false; selected = { key: key(table.name, column.name), table: table.name, column: column.name }; return true; });
      }
      clearFocus.hidden = !keys.length;
      selectionText.replaceChildren();
      if (selected) {
        selectionText.append(el("span", `query-role-dot role-${selected.role || "operator"}`), el("b", "", selected.key));
        const counts = new Set([...root.querySelectorAll(".query-sql-token.is-linked[data-column-token]")].map((n) => n));
        selectionText.append(el("span", "query-selection-meta", [ROLES[selected.role] || "数据库字段", counts.size ? `SQL 中 ${counts.size} 处` : null, keys.length > 1 ? `共 ${keys.length} 个字段` : null].filter(Boolean).join(" · ")));
        selection.dataset.role = selected.role || "operator";
      } else { selectionText.textContent = origin || "当前步骤没有映射到具体字段"; delete selection.dataset.role; }
      const explorer = schemaHost?.querySelector(".schema-explorer");
      if (explorer) explorer.dispatchEvent(new CustomEvent("query-field-focus", { detail: { key: keys[0] || null, keys, table: selected?.table, column: selected?.column, scroll: Boolean(scroll) } }));
    }
    root.addEventListener("schema-field-focus", (event) => focusFields([event.detail.key], "数据库字段", false));
    root.addEventListener("query-result-focus", (event) => {
      const column = event.detail.column;
      const source = fields.find((f) => f.column === column || (f.role === "metric" && (plan.metric_label === column || arr(plan.metrics).some((m) => (m.label === column || m.metric_label === column) && (m.column || m.metric_column) === f.column))) || (f.role === "dimension" && plan.dimension_labels?.[f.column] === column));
      focusFields(source ? [source.key] : [], source ? `结果列 ${column}` : `结果列 ${column}：未返回具体来源字段`, false);
      root.querySelectorAll("[data-qrv-row]").forEach((node) => { const selected = Number(node.dataset.qrvRow) === event.detail.row; node.classList.toggle("is-selected", selected); node.setAttribute("aria-pressed", String(selected)); });
      root.querySelector(".query-result-viz")?.dispatchEvent(new CustomEvent("query-result-selection", { detail: event.detail }));
      root.closest(".assistant")?.querySelectorAll(".data tbody tr").forEach((row, index) => row.classList.toggle("is-result-focus", Number(row.dataset.resultRow ?? index) === event.detail.row));
    });
    function section(title, subtitle) {
      const node = el("section", "query-part"), h = el("div", "query-part-head");
      const label = el("h4", "", title);
      h.append(label); if (subtitle) h.append(el("span", "", subtitle));
      node.append(h); sections.push(node); root.append(node); return node;
    }
    const understanding = section("问题与使用条件", model.original !== model.question ? "已结合对话上下文" : "本次原问题");
    if (model.original !== model.question) understanding.append(el("p", "query-original", `原问题：${model.original}`));
    const sentence = el("p", "query-sentence");
    questionSegments(model.question, fields, plan.coverage?.unresolved).forEach((part) => {
      if (!part.field) { sentence.append(el("span", part.unresolved ? "query-unresolved" : "", part.text)); return; }
      const node = button(part.text, `query-phrase role-${part.field.role}`, () => focusFields([part.field.key], `问题词语「${part.text}」`, true));
      node.dataset.queryField = part.field.key; node.title = `${ROLES[part.field.role]} → ${part.field.key}`; sentence.append(node);
    }); understanding.append(sentence);
    const legend = el("div", "query-role-legend"); [...new Set(fields.map((f) => f.role))].forEach((role) => { const item = el("span", `role-${role}`); item.append(el("i"), document.createTextNode(ROLES[role])); legend.append(item); }); understanding.append(legend);
    if (arr(plan.coverage?.unresolved).length) understanding.append(el("p", "query-attention", `待确认内容：${plan.coverage.unresolved.map(text).join("、")}`));

    const mapping = section("数据库字段", "词语 → 数据库字段");
    const list = el("div", "query-field-list");
    fields.forEach((field) => {
      const row = button("", `query-field-row role-${field.role}`, () => focusFields([field.key], ROLES[field.role], true)); row.dataset.queryField = field.key;
      row.append(el("span", "query-field-source", field.source), el("span", "query-field-arrow", "→"), el("code", "query-field-name", field.key), el("span", "query-field-role", ROLES[field.role]));
      if (Number.isFinite(field.score)) { const score = el("span", "query-field-score", field.score.toFixed(2)); score.title = "后端返回的字段匹配分数，不代表查询准确率"; row.append(score); }
      list.append(row);
    }); mapping.append(list);
    if (!fields.length) mapping.append(el("p", "query-caption", "本次响应未提供具体字段映射。"));
    schemaHost = el("div", "query-schema-host"); mapping.append(schemaHost);
    const drawSchema = (value) => {
      if (!window.SchemaSvg) return;
      schemaHost.replaceChildren(window.SchemaSvg.render(value || {}, plan, structured.provenance?.field_links || plan.links || []));
      if (activeKeys.length) focusFields(activeKeys, "字段对应关系", false);
    };
    drawSchema(schema);
    if (!arr(schema?.tables).length && schemaPromise) schemaPromise.then(drawSchema).catch(() => {});

    const queryPlan = section("SQL执行信息", `依据后端返回的查询方案${plan.planner_source ? ` · ${plan.planner_source}` : ""}`);
    if (model.operations.length) {
      const viewport = el("div", "query-plan-viewport"), width = Math.max(630, model.operations.length * 126), graph = svg("svg", { class: "query-plan-svg", viewBox: `0 0 ${width} 146`, width, height: 146, role: "group", "aria-label": "本次查询步骤：" + model.operations.map((o) => o.label).join("、") });
      const details = el("div", "query-operation-detail"); details.setAttribute("aria-live", "polite");
      const nodes = [];
      function selectOperation(index) {
        const operation = model.operations[index];
        nodes.forEach((node, i) => { node.classList.toggle("is-selected", i === index); node.setAttribute("aria-pressed", String(i === index)); });
        graph.querySelectorAll(".query-plan-edge").forEach((edge, i) => edge.classList.toggle("is-traversed", i < index));
        details.dataset.operation = operation.type;
        details.replaceChildren(el("strong", "", `${String(index + 1).padStart(2, "0")} · ${operation.label}`));
        operation.details.forEach((line) => details.append(el("code", "", line)));
        focusFields(operation.fields, `查询步骤：${operation.label}`, false);
      }
      model.operations.forEach((operation, i) => {
        const x = 50 + i * (width - 100) / Math.max(1, model.operations.length - 1);
        if (i < model.operations.length - 1) {
          const end = 50 + (i + 1) * (width - 100) / (model.operations.length - 1);
          graph.append(svg("path", { class: "query-plan-edge", d: `M${x + 22},44 H${end - 22}` }));
          graph.append(svg("path", { class: "query-plan-chevron", d: `M${(x + end) / 2 - 3},40 l4,4 -4,4` }));
        }
        const node = svg("g", { class: `query-plan-node role-${operation.type}`, transform: `translate(${x},44)`, tabindex: 0, role: "button", "aria-label": `${operation.label}：${operation.summary}`, "aria-pressed": "false" });
        node.append(svg("title", {}, `${operation.label} · ${operation.summary}`), svg("rect", { x: -49, y: -35, width: 98, height: 130, fill: "transparent", class: "query-plan-hit" }));
        node.append(svg("circle", { r: 27, class: "query-plan-halo" }), svg("circle", { r: 20, class: "query-plan-dot" }));
        const paths = { source: "M-8,-5 C-8,-9 8,-9 8,-5 V5 C8,9 -8,9 -8,5 Z M-8,-5 C-8,-1 8,-1 8,-5 M-8,0 C-8,4 8,4 8,0", filter: "M-9,-7 H9 L3,0 V7 L-3,9 V0 Z", dimension: "M-8,-7 H-1 V0 H-8 Z M2,-7 H9 V0 H2 Z M-8,3 H-1 V10 H-8 Z M2,3 H9 V10 H2 Z", metric: "M7,-8 H-6 L1,0 -6,8 H7", analysis: "M-8,8 V2 M0,8 V-4 M8,8 V-9", join: "M-2,-5 H-5 A5,5 0 0 0 -5,5 H-2 M2,-5 H5 A5,5 0 0 1 5,5 H2 M-4,0 H4", limit: "M-8,-6 H8 M-8,0 H4 M-8,6 H0" };
        node.append(svg("path", { d: paths[operation.type] || paths.analysis, class: "query-plan-icon" }), svg("text", { x: 0, y: -29, "text-anchor": "middle", class: "query-plan-index" }, String(i + 1).padStart(2, "0")), svg("text", { x: 0, y: 48, "text-anchor": "middle", class: "query-plan-label" }, operation.label), svg("text", { x: 0, y: 69, "text-anchor": "middle", class: "query-plan-summary" }, operation.summary.length > 16 ? operation.summary.slice(0, 13) + "…" : operation.summary));
        node.addEventListener("click", () => selectOperation(i));
        node.addEventListener("keydown", (event) => {
          if (["Enter", " "].includes(event.key)) { event.preventDefault(); selectOperation(i); }
          if (["ArrowLeft", "ArrowRight"].includes(event.key)) { event.preventDefault(); const next = (i + (event.key === "ArrowRight" ? 1 : -1) + nodes.length) % nodes.length; nodes[next].focus(); selectOperation(next); }
        });
        graph.append(node); nodes.push(node);
      }); viewport.append(graph); queryPlan.append(viewport, details);
      // Show details without changing the initial field focus.
      details.append(el("strong", "", `01 · ${model.operations[0].label}`)); model.operations[0].details.forEach((line) => details.append(el("code", "", line))); nodes[0]?.classList.add("is-selected"); nodes[0]?.setAttribute("aria-pressed", "true");
    } else queryPlan.append(el("p", "query-caption", "本次没有可展示的结构化查询方案。"));
    if (arr(structured.explanation).length) {
      const rules = el("details", "query-plan-notes"); rules.append(el("summary", "", `查看查询说明 · ${structured.explanation.length} 项`));
      structured.explanation.forEach((line) => rules.append(el("p", "", line))); queryPlan.append(rules);
    }
    if (structured.status === "clarification") queryPlan.append(el("p", "query-attention", structured.clarification || "需要补充条件；本次未执行 SQL。"));

    const sqlSection = section("SQL 与参数", structured.sql ? "点击字段查看对应位置" : "尚未执行 SQL");
    if (structured.sql) {
      const toolbar = el("div", "query-sql-toolbar"), copy = button("复制 SQL", "query-text-button", async () => {
        try { await navigator.clipboard.writeText(structured.sql); copy.textContent = "已复制"; } catch { copy.textContent = "请选中代码复制"; }
      }); toolbar.append(el("span", "", "SQL · 真实执行语句"), copy); sqlSection.append(toolbar);
      const pre = el("pre", "sqlbox syntax-highlighted query-sql"); pre.dataset.language = "sql"; pre.dataset.queryVisual = "true";
      const displaySql = formatSqlWithComments(structured.sql, plan, schema || {}, structured.source_tables || {});
      const tokenized = window.LatticeSyntaxHighlight?.tokenize(displaySql, "sql") || [{ text: displaySql, kind: "plain" }];
      const schemaFields = arr(schema?.tables).filter((t) => model.tables.includes(t.name)).flatMap((t) => arr(t.columns).map((c) => ({ key: key(t.name, c.name), table: t.name, column: c.name })));
      const tokens = annotateSql(tokenized, [...schemaFields, ...fields]);
      tokens.forEach((token) => {
        const span = el("span", `tok-${token.kind} query-sql-token`, token.text);
        if (token.field) {
          span.dataset.field = token.field;
          const rawName = token.text.replace(/^["`]|["`]$/g, "");
          if (schemaFields.some((f) => f.key === token.field && f.column === rawName)) span.dataset.columnToken = "true";
          span.tabIndex = 0; span.setAttribute("role", "button"); span.title = `定位 ${token.field}`; span.addEventListener("click", () => focusFields([token.field], "SQL 字段", true)); span.addEventListener("keydown", (e) => { if (["Enter", " "].includes(e.key)) { e.preventDefault(); focusFields([token.field], "SQL 字段", true); } });
        }
        if (token.parameter !== undefined) { span.dataset.parameter = token.parameter; span.tabIndex = 0; span.setAttribute("role", "button"); span.title = `参数 ${token.parameter + 1}：${text(arr(structured.parameters)[token.parameter])}`; span.addEventListener("click", () => focusParameter(token.parameter)); span.addEventListener("keydown", (e) => { if (["Enter", " "].includes(e.key)) { e.preventDefault(); focusParameter(token.parameter); } }); }
        pre.append(span);
      }); sqlSection.append(pre);
      const parameters = el("div", "query-parameters"), parameterStatus = el("p", "query-caption", "点击参数值，高亮 SQL 中绑定的位置。"); parameterStatus.setAttribute("aria-live", "polite");
      function focusParameter(index) {
        sqlSection.querySelectorAll("[data-parameter]").forEach((node) => node.classList.toggle("is-bound", Number(node.dataset.parameter) === index));
        parameterStatus.textContent = `参数 ${index + 1} → ${text(arr(structured.parameters)[index])}（独立绑定，不拼接进 SQL）`;
      }
      arr(structured.parameters).forEach((value, index) => { const node = button("", "query-parameter", () => focusParameter(index)); node.dataset.parameter = index; node.append(el("span", "", `?${index + 1}`), el("code", "", text(value))); parameters.append(node); });
      if (arr(structured.parameters).length) sqlSection.append(parameters, parameterStatus);
      if (structured.provenance?.query_hash) sqlSection.append(el("p", "query-caption query-fingerprint", `本次查询标识 ${structured.provenance.query_hash}`));
    } else sqlSection.append(el("p", "query-caption", structured.status === "clarification" ? "补充条件后才会生成并执行 SQL。" : "本次响应没有 SQL 语句。"));
    const resultSection = section("05 · 查询结果", structured.status === "ok" ? `${arr(structured.rows).length} 行 · ${arr(structured.columns).length} 列` : "本次结果状态");
    if (window.QueryResultViz) { const visual = window.QueryResultViz.render(structured); if (visual) resultSection.append(visual); }
    resultSection.append(el("p", "query-caption", structured.status === "ok" ? "图形与下方表格使用同一份后端返回数据；点击图形可定位结果行。" : structured.status === "clarification" ? "等待补充查询条件，未展示计算结果。" : "本次未返回可用的结构化结果。"));

    const titles = ["问题", "字段", "步骤", "SQL", "结果"], navButtons = [];
    function showPart(index, scroll) {
      if (replayIndex >= 0) replay.textContent = index === sections.length - 1 ? "从头查看" : "下一步 →";
      navButtons.forEach((b, i) => { b.classList.toggle("is-current", i === index); b.setAttribute("aria-current", i === index ? "step" : "false"); });
      sections.forEach((node, i) => node.hidden = replayIndex >= 0 && i !== index);
      if (scroll) {
        const thread = root.closest(".thread");
        if (thread) thread.scrollTo({ top: thread.scrollTop + sections[index].getBoundingClientRect().top - thread.getBoundingClientRect().top - nav.getBoundingClientRect().height - 14, behavior: prefersReduced() ? "auto" : "smooth" });
        else sections[index].scrollIntoView({ behavior: prefersReduced() ? "auto" : "smooth", block: "nearest" });
      }
    }
    titles.forEach((title, index) => { const b = button("", "query-nav-step", () => { if (replayIndex >= 0) replayIndex = index; showPart(index, true); }); b.append(el("span", "", String(index + 1).padStart(2, "0")), document.createTextNode(title)); navButtons.push(b); nav.append(b); });
    const scrollHost = root.closest(".thread") || document.getElementById("thread");
    let scheduled = false;
    function followScroll() {
      if (scheduled || replayIndex >= 0) return;
      scheduled = true;
      requestAnimationFrame(() => {
        scheduled = false;
        if (!root.isConnected) { scrollHost?.removeEventListener("scroll", followScroll); return; }
        const cutoff = (scrollHost?.getBoundingClientRect().top || 0) + nav.getBoundingClientRect().height + 28;
        let current = 0; sections.forEach((node, i) => { if (node.getBoundingClientRect().top <= cutoff) current = i; });
        navButtons.forEach((b, i) => { b.classList.toggle("is-current", i === current); b.setAttribute("aria-current", i === current ? "step" : "false"); });
      });
    }
    scrollHost?.addEventListener("scroll", followScroll, { passive: true });
    replay.addEventListener("click", () => { replayIndex = replayIndex < 0 ? 0 : (replayIndex + 1) % sections.length; replay.textContent = replayIndex === sections.length - 1 ? "从头查看" : "下一步 →"; closeReplay.hidden = false; showPart(replayIndex, false); });
    closeReplay.addEventListener("click", () => { replayIndex = -1; replay.textContent = "逐步查看"; closeReplay.hidden = true; sections.forEach((node) => node.hidden = false); navButtons.forEach((b) => { b.classList.remove("is-current"); b.removeAttribute("aria-current"); }); });
    return root;
  }
  return { buildModel, questionSegments, annotateSql, formatSql, formatSqlWithComments, render };
});

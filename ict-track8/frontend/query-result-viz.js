(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.QueryResultViz = api;
})(typeof window !== "undefined" ? window : globalThis, function () {
  "use strict";

  const SVG_NS = "http://www.w3.org/2000/svg";
  const MAX_ROWS = 20;
  const AGGREGATES = new Set(["SUM", "COUNT", "AVG", "MIN", "MAX", "COUNT_DISTINCT"]);
  let sequence = 0;

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = String(text);
    return node;
  }

  function svgElement(tag, attributes, text) {
    const node = document.createElementNS(SVG_NS, tag);
    Object.entries(attributes || {}).forEach(([name, value]) => node.setAttribute(name, String(value)));
    if (text !== undefined) node.textContent = String(text);
    return node;
  }

  // Parse the entire value, never parseFloat a prefix or coerce blanks/null to zero.
  // Original strings remain the labels; units are never inferred from metric names.
  function numeric(value) {
    if (value === null || value === undefined) return { missing: true };
    if (typeof value === "number") return Number.isFinite(value) ? { value, unit: "" } : null;
    if (typeof value !== "string" || !value.trim()) return null;
    const match = value.trim().match(/^([+-]?(?:(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*(%|％|元|万元|亿元|人|次|件|个|条|吨|千克|公斤|米|平方米|秒|小时|天|USD|CNY|EUR|kg|km|kWh)?$/);
    if (!match) return null;
    const number = Number(match[1].replace(/,/g, ""));
    if (!Number.isFinite(number) || Math.abs(number) > Number.MAX_SAFE_INTEGER) return null;
    // Underflow must not turn a non-zero returned number into a fabricated zero.
    if (number === 0 && /[1-9]/.test(match[1].split(/[eE]/)[0])) return null;
    return { value: number, unit: match[2] || "" };
  }

  function valueLabel(value) {
    if (value === null || value === undefined) return "空值";
    if (typeof value === "string") return value.trim();
    return new Intl.NumberFormat("zh-CN", { maximumSignificantDigits: 12 }).format(value);
  }

  function shortText(value, budget) {
    let width = 0, text = "";
    for (const character of value) {
      width += character.codePointAt(0) > 255 ? 2 : 1;
      if (width > budget) return text + "…";
      text += character;
    }
    return text;
  }

  function dimensionName(structured, column) {
    if (!column) return column;
    const plan = structured.plan || {};
    const label = plan.dimension_labels?.[column];
    if (typeof label === "string" && label.trim()) return label.trim();
    const links = [...(Array.isArray(structured.provenance?.field_links) ? structured.provenance.field_links : []),
      ...(Array.isArray(plan.links) ? plan.links : [])];
    const names = new Set(links.filter((link) => link && link.column === column && link.role === "dimension" &&
      typeof link.source_text === "string" && link.source_text.trim()).map((link) => link.source_text.trim()));
    // Keep the result column when links disagree; never invent a business alias.
    return names.size === 1 ? [...names][0] : column;
  }

  function metricSpec(plan, column, rows, columns) {
    const metrics = Array.isArray(plan.metrics) ? plan.metrics : [];
    const match = metrics.find((item) => item && item.label === column);
    if (match) return match;
    const derived = (Array.isArray(plan.derived_metrics) ? plan.derived_metrics : []).find((item) => item?.label === column);
    if (derived) return derived;
    const unambiguousScalar = rows.length === 1 && columns.length === 1 && plan.analysis_mode === "aggregate" &&
      !(Array.isArray(plan.dimensions) && plan.dimensions.length) && !metrics.length &&
      !(Array.isArray(plan.derived_metrics) && plan.derived_metrics.length) &&
      (!plan.comparison_mode || plan.comparison_mode === "none") && !plan.top_n && !plan.metric_label &&
      AGGREGATES.has(String(plan.metric_function || "").toUpperCase());
    if (plan.metric_label === column || plan.metric_column === column || unambiguousScalar) {
      return { table: plan.metric_table || plan.table, column: plan.metric_column,
        function: plan.metric_function, label: column };
    }
    return {};
  }

  function identify(structured) {
    if (!structured || (structured.status && structured.status !== "ok") || structured.result_state === "empty") return null;
    const columns = structured.columns, rows = structured.rows;
    if (!Array.isArray(columns) || !columns.length || !columns.every((column) => typeof column === "string") ||
        new Set(columns).size !== columns.length || !Array.isArray(rows) || !rows.length ||
        !rows.every((row) => row && typeof row === "object" && !Array.isArray(row))) return null;
    const plan = structured.plan || {};
    if (plan.clarification) return null;
    const dimensions = new Set((Array.isArray(plan.dimensions) ? plan.dimensions : []).flatMap((column) =>
      [column, plan.dimension_labels?.[column]].filter((value) => typeof value === "string")));
    const metrics = [], categorical = [];
    for (const column of columns) {
      const values = rows.map((row) => row[column]);
      const parsed = values.map(numeric);
      const hasValue = parsed.some((item) => item && !item.missing);
      if (!dimensions.has(column) && hasValue && parsed.every(Boolean)) {
        const units = new Set(parsed.filter((item) => !item.missing).map((item) => item.unit));
        // Mixing explicit units or explicit/unitless values would make bar lengths ambiguous.
        if (units.size === 1) metrics.push({ column, parsed, unit: [...units][0] });
        else return null;
      } else if (values.every((value) => value == null || typeof value === "string") &&
                 values.some((value) => typeof value === "string" && value.trim())) {
        // An invalid numeric-looking field cannot silently become a category.
        if (!dimensions.has(column) && values.some((value) => numeric(value)?.value !== undefined)) return null;
        categorical.push(column);
      } else return null;
    }
    if (!metrics.length || categorical.length > 1) return null;
    metrics.forEach((metric) => { metric.spec = metricSpec(plan, metric.column, rows, columns); });
    const scalar = rows.length === 1 && categorical.length === 0 && !dimensions.size &&
      plan.analysis_mode !== "detail" && metrics.every((metric) => AGGREGATES.has(String(metric.spec.function || "").toUpperCase()));
    if (!scalar && categorical.length !== 1) return null;
    return { columns, rows, plan, metrics, dimension: categorical[0], dimensionLabel: dimensionName(structured, categorical[0]), scalar };
  }

  function scopeText(structured, count, visible, scalar) {
    const provenance = structured.provenance || {}, plan = structured.plan || {};
    const parts = [`当前返回 ${count} 行`, scalar ? "展示返回的聚合值" : `图中展示前 ${visible} 行，保持返回顺序`];
    const limit = provenance.row_limit ?? plan.preview_row_limit ?? plan.limit;
    if (Number.isSafeInteger(limit) && limit > 0) parts.push(`预览上限 ${limit} 行`);
    if (Number.isSafeInteger(plan.top_n) && plan.top_n > 0) parts.push(`查询 Top-${plan.top_n}（含并列）`);
    if (Number.isSafeInteger(plan.semantic_row_limit) && plan.semantic_row_limit > 0) parts.push(`查询范围上限 ${plan.semantic_row_limit} 行`);
    const complete = provenance.complete_result;
    if (complete?.status === "complete" && Number.isSafeInteger(complete.row_count) && complete.row_count >= count) {
      parts.push(`完整查询产物 ${complete.row_count} 行`);
    } else if (provenance.result_completeness === "limit_reached_total_unknown") {
      parts.push("已触及返回上限，总行数未知");
    } else if (provenance.result_completeness === "partial" || (complete && complete.status !== "complete")) {
      parts.push("查询产物未完整返回");
    }
    parts.push(scalar ? "口径以本次查询条件为准" : "仅表示本次返回行，不代表完整数据分布");
    return parts.join(" · ");
  }

  function sourceText(structured, metric) {
    const plan = structured.plan || {}, provenance = structured.provenance || {}, spec = metric.spec;
    const parts = [];
    if (provenance.database) parts.push(`数据库：${provenance.database}`);
    const table = spec.table || provenance.table || plan.table;
    if (table) parts.push(`来源表：${table}`);
    if (spec.column) parts.push(`来源字段：${spec.column}`);
    else parts.push(`结果列：${metric.column}`);
    if (spec.function) parts.push(`聚合：${spec.function}`);
    if (spec.expression) parts.push("派生指标：由查询计划中的公式计算");
    const unit = metric.unit || (spec.unit && spec.unit !== "unknown" ? spec.unit : "");
    if (unit) parts.push(`原始单位：${unit}`);
    if (spec.currency) parts.push(`币种：${spec.currency}`);
    if (!unit && !spec.currency) parts.push("返回值未注明单位");
    return parts.join(" · ");
  }

  function render(structured) {
    if (typeof document === "undefined") return null;
    const model = identify(structured);
    if (!model) return null;
    const id = `query-result-viz-${++sequence}`;
    const section = element("section", "query-result-viz");
    section.setAttribute("aria-labelledby", `${id}-title`);
    const header = element("div", "qrv-header");
    const heading = element("div", "qrv-heading");
    heading.append(element("span", "qrv-eyebrow", model.scalar ? "QUERY SNAPSHOT / 聚合结果" : "RETURNED ROWS / 返回结果"));
    const title = element("h4", "qrv-title", model.scalar ? "本次查询的聚合值" : `${model.dimensionLabel} · 指标对比`);
    title.id = `${id}-title`;
    heading.append(title);
    header.append(heading);
    const controls = element("div", "qrv-controls");
    controls.setAttribute("role", "group");
    controls.setAttribute("aria-label", "切换可视化指标");
    const body = element("div", "qrv-body");
    const caption = element("p", "qrv-scope", scopeText(structured, model.rows.length, Math.min(MAX_ROWS, model.rows.length), model.scalar));
    caption.id = `${id}-scope`;
    const source = element("p", "qrv-source");
    source.setAttribute("aria-live", "polite");
    const live = element("p", "qrv-sr-only");
    live.setAttribute("role", "status");
    live.setAttribute("aria-live", "polite");
    const reading = element("div", "qrv-reading"); reading.setAttribute("aria-live", "polite");
    reading.append(element("span", "", "悬停或选择一行，查看完整读数"));
    section.append(header, body, reading, caption, source, live);
    let selectedRow = -1, activeMetric = null, chartSizing = null;
    function chartLayout() {
      const narrow = window.matchMedia("(max-width: 640px)").matches;
      const total = narrow ? Math.max(300, Math.round(section.clientWidth || window.innerWidth - 43)) : 720;
      return { narrow, total, left: narrow ? 82 : 120, width: narrow ? total - 190 : 470 };
    }

    function activate(row, column, label) {
      selectedRow = row;
      body.querySelectorAll("[data-qrv-row]").forEach((node) => {
        const selected = Number(node.getAttribute("data-qrv-row")) === row;
        node.classList.toggle("is-selected", selected);
        node.setAttribute("aria-pressed", String(selected));
      });
      live.textContent = `已定位结果表第 ${row + 1} 行，${column}：${label}`;
      readRow(row, activeMetric);
      section.dispatchEvent(new CustomEvent("query-result-focus", { bubbles: true, detail: { row, column } }));
    }
    function readRow(index, metric) {
      if (!metric) return;
      const row = model.rows[index]; if (!row) return;
      reading.replaceChildren();
      reading.append(element("span", "qrv-reading-label", model.scalar ? metric.column : String(row[model.dimension] ?? "空值")),
        element("strong", "", valueLabel(row[metric.column])), element("span", "qrv-reading-detail", `${metric.column} · 返回行 ${index + 1}`));
    }

    function draw(metric) {
      activeMetric = metric;
      body.replaceChildren();
      controls.querySelectorAll("button").forEach((button) => button.setAttribute("aria-pressed", String(button.dataset.column === metric.column)));
      source.textContent = sourceText(structured, metric);
      if (selectedRow >= 0) readRow(selectedRow, metric);
      else { reading.replaceChildren(element("span", "", "悬停或选择一行，查看完整读数")); }
      if (model.scalar) {
        const scalar = element("button", "qrv-scalar");
        scalar.type = "button";
        scalar.setAttribute("data-qrv-row", "0");
        scalar.setAttribute("aria-pressed", String(selectedRow === 0));
        scalar.classList.toggle("is-selected", selectedRow === 0);
        scalar.append(element("span", "qrv-scalar-label", metric.column),
          element("span", "qrv-scalar-value", valueLabel(model.rows[0][metric.column])),
          element("span", "qrv-scalar-hint", "点击定位结果表 · 第 1 行"));
        scalar.title = String(model.rows[0][metric.column]);
        scalar.addEventListener("click", () => activate(0, metric.column, valueLabel(model.rows[0][metric.column])));
        scalar.addEventListener("focus", () => readRow(0, metric));
        scalar.addEventListener("mouseenter", () => readRow(0, metric));
        body.append(scalar);
        return;
      }
      const count = Math.min(MAX_ROWS, model.rows.length);
      const visible = metric.parsed.slice(0, count);
      // Normalize before subtracting endpoints so large signed values cannot overflow.
      const magnitude = Math.max(...visible.map((item) => item.missing ? 0 : Math.abs(item.value))) || 1;
      let lower = Math.min(0, ...visible.map((item) => item.missing ? 0 : item.value / magnitude));
      let upper = Math.max(0, ...visible.map((item) => item.missing ? 0 : item.value / magnitude));
      if (lower === upper) upper = 1;
      const { narrow, total, left, width } = chartLayout(), rowHeight = 42;
      chartSizing = total;
      const x = (value) => left + ((value / magnitude - lower) / (upper - lower)) * width;
      const zero = x(0), height = count * rowHeight + 52;
      const viewport = element("div", "qrv-chart-viewport");
      const chart = svgElement("svg", { viewBox: `0 0 ${total} ${height}`, class: "qrv-chart", role: "group",
        "aria-labelledby": `${id}-chart-title`, "aria-describedby": `${id}-scope` });
      chart.append(svgElement("title", { id: `${id}-chart-title` }, `${model.dimensionLabel}与${metric.column}，仅展示返回行`));
      const definitions = svgElement("defs", {});
      ["positive", "negative"].forEach((sign) => {
        const gradient = svgElement("linearGradient", { id: `${id}-${sign}`, x1: "0%", x2: "100%", y1: "0%", y2: "0%" });
        gradient.append(svgElement("stop", { offset: "0%", class: `qrv-gradient-${sign}-start` }), svgElement("stop", { offset: "100%", class: `qrv-gradient-${sign}-end` }));
        definitions.append(gradient);
      });
      chart.append(definitions);
      (narrow ? [0, .5, 1] : [0, .25, .5, .75, 1]).forEach((position) => {
        chart.append(svgElement("line", { x1: left + width * position, x2: left + width * position, y1: 8, y2: count * rowHeight + 8, class: "qrv-grid" }));
        const value = (lower + (upper - lower) * position) * magnitude;
        const label = new Intl.NumberFormat("zh-CN", { maximumSignificantDigits: 3, notation: Math.abs(value) >= 10000 ? "compact" : "standard" }).format(value);
        chart.append(svgElement("text", { x: left + width * position, y: count * rowHeight + 30, class: "qrv-axis", "text-anchor": "middle" }, label));
      });
      chart.append(svgElement("line", { x1: zero, x2: zero, y1: 8, y2: count * rowHeight + 8, class: "qrv-zero-line" }));
      const groups = [];
      visible.forEach((item, rowIndex) => {
        const row = model.rows[rowIndex], rawLabel = row[model.dimension];
        const label = rawLabel == null ? "（空值）" : rawLabel.trim() || "（空字符串）";
        const value = valueLabel(row[metric.column]);
        const y = 8 + rowIndex * rowHeight;
        const group = svgElement("g", { class: `qrv-row${selectedRow === rowIndex ? " is-selected" : ""}`, tabindex: 0,
          role: "button", "aria-pressed": String(selectedRow === rowIndex), "data-qrv-row": rowIndex,
          "aria-label": `${model.dimensionLabel}：${label}；${metric.column}：${value}；定位结果表第 ${rowIndex + 1} 行` });
        group.append(svgElement("title", {}, `${label} · ${metric.column}：${row[metric.column] ?? "空值"}`));
        group.append(svgElement("rect", { x: 0, y, width: total, height: rowHeight - 2, class: "qrv-row-bg" }));
        const shortLabel = shortText(label, narrow ? 6 : 16);
        group.append(svgElement("text", { x: left - 14, y: y + 25, "text-anchor": "end", class: "qrv-category" }, shortLabel));
        if (!item.missing) {
          const endpoint = x(item.value);
          if (item.value === 0) group.append(svgElement("line", { x1: zero, x2: zero, y1: y + 13, y2: y + 29, class: "qrv-zero-mark" }));
          else group.append(svgElement("rect", { x: Math.min(zero, endpoint), y: y + 11, width: Math.abs(endpoint - zero), height: 20,
            class: `qrv-bar${item.value < 0 ? " qrv-bar-negative" : ""}`, rx: 2, style: `--qrv-bar-fill:url(#${id}-${item.value < 0 ? "negative" : "positive"});--qrv-origin:${zero}px;--qrv-delay:${Math.min(rowIndex, 10) * 22}ms` }));
        }
        const shortValue = shortText(value, narrow ? 12 : 14);
        group.append(svgElement("text", { x: left + width + 16, y: y + 25, class: `qrv-value${item.missing ? " is-missing" : ""}` }, shortValue));
        group.addEventListener("click", () => activate(rowIndex, metric.column, value));
        group.addEventListener("mouseenter", () => readRow(rowIndex, metric));
        group.addEventListener("focus", () => readRow(rowIndex, metric));
        group.addEventListener("keydown", (event) => {
          if (event.key === "Enter" || event.key === " ") { event.preventDefault(); activate(rowIndex, metric.column, value); }
          const next = event.key === "ArrowDown" ? rowIndex + 1 : event.key === "ArrowUp" ? rowIndex - 1 : event.key === "Home" ? 0 : event.key === "End" ? count - 1 : null;
          if (next !== null) { event.preventDefault(); groups[Math.max(0, Math.min(count - 1, next))].focus(); }
        });
        groups.push(group);
        chart.append(group);
      });
      viewport.append(chart);
      body.append(viewport);
      const notes = ["点选条形可定位结果表行；键盘 ↑↓ 移动，Enter / 空格选择。"];
      const missing = visible.filter((item) => item.missing).length;
      if (missing) notes.push(`${missing} 个空值以“空值”标注，不按零值绘制。`);
      if (model.rows.length > MAX_ROWS) notes.push(`其余 ${model.rows.length - MAX_ROWS} 行请查看结果表。`);
      body.append(element("p", "qrv-interaction", notes.join(" ")));
    }

    model.metrics.forEach((metric) => {
      const button = element("button", "qrv-metric", metric.column);
      button.type = "button";
      button.dataset.column = metric.column;
      button.setAttribute("aria-pressed", "false");
      button.addEventListener("click", () => { if (activeMetric !== metric) draw(metric); });
      controls.append(button);
    });
    header.append(controls);
    section.addEventListener("query-result-selection", (event) => {
      const row = event.detail?.row;
      if (!Number.isInteger(row) || row < 0) return;
      if (row >= Math.min(MAX_ROWS, model.rows.length) && !model.scalar) {
        selectedRow = -1;
        body.querySelectorAll("[data-qrv-row]").forEach((node) => { node.classList.remove("is-selected"); node.setAttribute("aria-pressed", "false"); });
        reading.replaceChildren(element("span", "", `第 ${row + 1} 行不在图中，请查看完整结果表。`));
        return;
      }
      selectedRow = row;
      const metric = model.metrics.find((item) => item.column === event.detail.column) || activeMetric;
      if (metric !== activeMetric) draw(metric);
      body.querySelectorAll("[data-qrv-row]").forEach((node) => {
        const selected = Number(node.dataset.qrvRow) === row;
        node.classList.toggle("is-selected", selected);
        node.setAttribute("aria-pressed", String(selected));
      });
      readRow(row, metric);
      live.textContent = `已定位结果表第 ${row + 1} 行，${metric.column}：${valueLabel(model.rows[row][metric.column])}`;
    });
    draw(model.metrics[0]);
    if (!model.scalar && window.ResizeObserver) {
      const observer = new ResizeObserver(() => {
        if (!section.isConnected) { observer.disconnect(); return; }
        if (chartLayout().total !== chartSizing) draw(activeMetric);
      });
      observer.observe(section);
    }
    return section;
  }

  return { render };
});

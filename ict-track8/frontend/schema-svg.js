(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.SchemaSvg = api;
})(typeof window !== "undefined" ? window : globalThis, function () {
  "use strict";

  const SVG_NS = "http://www.w3.org/2000/svg";
  const ROLE_ORDER = ["metric", "filter", "dimension", "time", "operator", "join"];
  const ROLE_LABELS = {
    metric: "指标",
    dimension: "维度",
    filter: "过滤",
    time: "时间",
    operator: "算子",
    join: "关联键",
  };
  let diagramSequence = 0;

  function fieldKey(table, column) {
    return JSON.stringify([table, column]);
  }

  function addRole(roles, table, column, role) {
    if (!table || !column || !ROLE_ORDER.includes(role)) return;
    const key = fieldKey(table, column);
    if (!roles.has(key)) roles.set(key, new Set());
    roles.get(key).add(role);
  }

  function canonicalPair(left, right) {
    return [left, right].sort().join("\u0000");
  }

  function buildModel(schema, plan, links) {
    const tables = (Array.isArray(schema?.tables) ? schema.tables : [])
      .filter((table) => table && typeof table.name === "string" && table.name);
    const byName = new Map(tables.map((table) => [table.name, table]));
    const roles = new Map();
    const activeTables = new Set();
    const joinPath = Array.isArray(plan?.join_path) ? plan.join_path : [];

    function hasColumn(table, column) {
      return Boolean(byName.get(table)?.columns?.some((item) => item.name === column));
    }

    function useTable(name) {
      if (name && byName.has(name)) activeTables.add(name);
    }
    function addQualified(table, column, role) {
      if (table && column && hasColumn(table, column)) {
        addRole(roles, table, column, role);
        useTable(table);
      }
    }

    const fieldLinks = Array.isArray(links) && links.length ? links : (plan?.links || []);
    fieldLinks.forEach((link) => addQualified(link.table, link.column, link.role));
    addQualified(plan?.metric_table || plan?.table, plan?.metric_column, "metric");
    (Array.isArray(plan?.metrics) ? plan.metrics : []).forEach((metric) => {
      addQualified(metric.table || metric.metric_table, metric.column || metric.metric_column, "metric");
    });
    Object.entries(plan?.dimension_tables || {}).forEach(([column, table]) => addQualified(table, column, "dimension"));
    (Array.isArray(plan?.filters) ? plan.filters : []).forEach((filter) => {
      addQualified(filter.table || plan?.table, filter.column, "filter");
    });
    (Array.isArray(plan?.group_by) ? plan.group_by : []).forEach((item) => {
      addQualified(item.table, item.column, "dimension");
    });
    addQualified(plan?.time_table || plan?.date_table, plan?.time_column || plan?.date_column, "time");

    useTable(plan?.table);
    useTable(plan?.metric_table);
    (Array.isArray(plan?.join_tables) ? plan.join_tables : []).forEach(useTable);
    joinPath.forEach((join) => {
      useTable(join.from_table);
      useTable(join.to_table);
    });

    const edges = [];
    const foreignColumns = new Map(tables.map((table) => [table.name, new Set()]));
    tables.forEach((table) => {
      (table.foreign_keys || []).forEach((foreignKey) => {
        if (!byName.has(foreignKey.table)) return;
        foreignColumns.get(table.name).add(foreignKey.from_column);
        edges.push({
          fromTable: table.name,
          fromColumn: foreignKey.from_column,
          toTable: foreignKey.table,
          toColumn: foreignKey.to_column,
          active: false,
        });
      });
    });

    joinPath.forEach((join) => {
      const pairEdges = edges.filter((edge) => canonicalPair(edge.fromTable, edge.toTable) === canonicalPair(join.from_table, join.to_table));
      const condition = String(join.condition || "");
      const exact = pairEdges.filter((edge) => condition.includes(edge.fromColumn) && condition.includes(edge.toColumn));
      const matches = exact.length ? exact : pairEdges.length === 1 ? pairEdges : [];
      matches.forEach((edge) => {
        edge.active = true;
        addRole(roles, edge.fromTable, edge.fromColumn, "join");
        addRole(roles, edge.toTable, edge.toColumn, "join");
      });
    });

    const uniqueColumns = new Map(tables.map((table) => [table.name, new Set()]));
    tables.forEach((table) => (table.unique_keys || []).forEach((key) => {
      if (Array.isArray(key) && key.length === 1) uniqueColumns.get(table.name).add(key[0]);
    }));

    const adjacency = new Map(tables.map((table) => [table.name, new Set()]));
    edges.forEach((edge) => {
      adjacency.get(edge.fromTable).add(edge.toTable);
      adjacency.get(edge.toTable).add(edge.fromTable);
    });

    const columns = new Map(tables.map((table) => [table.name, (table.columns || []).map((column) => {
      const key = fieldKey(table.name, column.name);
      const fieldRoles = [...(roles.get(key) || [])].sort((a, b) => ROLE_ORDER.indexOf(a) - ROLE_ORDER.indexOf(b));
      return {
        ...column,
        roles: fieldRoles,
        primaryRole: fieldRoles[0] || null,
        isForeignKey: foreignColumns.get(table.name).has(column.name),
        isUnique: uniqueColumns.get(table.name).has(column.name),
        key: key,
      };
    })]));

    const firstTable = [plan?.table, plan?.metric_table, ...fieldLinks.map((link) => link.table)]
      .find((name) => name && byName.has(name));
    const remaining = new Set(byName.keys());
    const nodes = new Map();
    let componentTop = 0;
    let maxDepth = 0;
    let maxTableWidth = 300;
    for (const table of tables) {
      for (const column of columns.get(table.name)) {
        const columnWidth = Array.from(column.name).length * 7.8 + Array.from(column.data_type || "").length * 6.6 + 112;
        maxTableWidth = Math.max(maxTableWidth, columnWidth);
      }
    }
    const nodeWidth = Math.ceil(maxTableWidth);
    const headHeight = 38;
    const rowHeight = 26;
    const gapX = 76;
    const gapY = 26;
    const pad = 20;

    while (remaining.size) {
      const pool = [...remaining].sort((a, b) => {
        const used = Number(activeTables.has(b)) - Number(activeTables.has(a));
        return used || adjacency.get(b).size - adjacency.get(a).size || a.localeCompare(b);
      });
      const root = firstTable && remaining.has(firstTable) ? firstTable : pool[0];
      const queue = [root];
      const depth = new Map([[root, 0]]);
      remaining.delete(root);
      while (queue.length) {
        const current = queue.shift();
        for (const next of adjacency.get(current)) {
          if (!remaining.has(next)) continue;
          remaining.delete(next);
          depth.set(next, depth.get(current) + 1);
          queue.push(next);
        }
      }

      const byDepth = new Map();
      for (const [name, level] of depth) {
        if (!byDepth.has(level)) byDepth.set(level, []);
        byDepth.get(level).push(name);
      }
      let componentHeight = 0;
      for (const [level, names] of [...byDepth.entries()].sort((a, b) => a[0] - b[0])) {
        names.sort((a, b) => Number(activeTables.has(b)) - Number(activeTables.has(a)) || a.localeCompare(b));
        let y = componentTop;
        for (const name of names) {
          const fields = columns.get(name);
          const height = headHeight + Math.max(fields.length, 1) * rowHeight + 10;
          nodes.set(name, {
            name,
            x: level * (nodeWidth + gapX),
            y,
            width: nodeWidth,
            height,
            columns: fields.map((column, index) => ({ ...column, y: y + headHeight + index * rowHeight, id: `schema-field-${nodes.size}-${index}` })),
            rowCount: byName.get(name).row_count,
            active: activeTables.has(name),
          });
          y += height + gapY;
          componentHeight = Math.max(componentHeight, y - componentTop - gapY);
          maxDepth = Math.max(maxDepth, level);
        }
      }
      componentTop += componentHeight + gapY * 2;
    }

    const focusCandidates = [];
    nodes.forEach((node) => node.columns.forEach((column) => {
      if (column.roles.length) focusCandidates.push({ table: node.name, column: column.name, id: column.id, role: column.primaryRole });
    }));
    focusCandidates.sort((a, b) => ROLE_ORDER.indexOf(a.role) - ROLE_ORDER.indexOf(b.role));
    const width = Math.max(nodeWidth, ...[...nodes.values()].map((node) => node.x + node.width));
    const height = Math.max(60, componentTop - gapY * 2);
    return {
      tables,
      nodes,
      edges,
      width: width + pad * 2,
      height: height + pad * 2,
      nodeWidth,
      headHeight,
      rowHeight,
      pad,
      activeTables,
      fieldCount: tables.reduce((count, table) => count + (table.columns || []).length, 0),
      foreignKeyCount: edges.length,
      focusTarget: focusCandidates[0] || null,
      focusFieldCount: focusCandidates.length,
    };
  }

  function svgElement(tag, attrs, text) {
    const node = document.createElementNS(SVG_NS, tag);
    Object.entries(attrs || {}).forEach(([key, value]) => {
      if (value !== null && value !== undefined) node.setAttribute(key, String(value));
    });
    if (text !== undefined) node.textContent = String(text);
    return node;
  }

  function roleClasses(roles) {
    return (roles || []).map((role) => `role-${role}`).join(" ");
  }

  function createSvg(model) {
    const id = `schema-arrow-${++diagramSequence}`;
    const svg = svgElement("svg", {
      class: "schema-diagram-svg",
      viewBox: `0 0 ${model.width} ${model.height}`,
      width: model.width,
      height: model.height,
      role: "group",
      "aria-label": `完整数据库结构图，共 ${model.tables.length} 张表、${model.fieldCount} 个字段。突出显示本次查询涉及的字段。`,
    });
    const defs = svgElement("defs");
    const marker = svgElement("marker", { id, markerWidth: 7, markerHeight: 7, refX: 6, refY: 3.5, orient: "auto", markerUnits: "strokeWidth" });
    marker.append(svgElement("path", { d: "M0,0 L7,3.5 L0,7 Z", class: "schema-edge-arrow" }));
    defs.append(marker);
    svg.append(defs);

    const edgeLayer = svgElement("g", { class: "schema-edge-layer" });
    const fieldY = (tableName, columnName) => {
      const node = model.nodes.get(tableName);
      const column = node?.columns.find((item) => item.name === columnName);
      return column ? column.y + model.rowHeight / 2 + model.pad : null;
    };
    model.edges.forEach((edge) => {
      const from = model.nodes.get(edge.fromTable), to = model.nodes.get(edge.toTable);
      if (!from || !to) return;
      const y1 = fieldY(edge.fromTable, edge.fromColumn), y2 = fieldY(edge.toTable, edge.toColumn);
      if (y1 === null || y2 === null) return;
      let x1, x2, curveX;
      if (from.x + from.width <= to.x) {
        x1 = from.x + from.width + model.pad;
        x2 = to.x + model.pad;
        curveX = (x1 + x2) / 2;
      } else if (to.x + to.width <= from.x) {
        x1 = from.x + model.pad;
        x2 = to.x + to.width + model.pad;
        curveX = (x1 + x2) / 2;
      } else {
        x1 = from.x + from.width + model.pad;
        x2 = to.x + to.width + model.pad;
        curveX = Math.max(x1, x2) + 36;
      }
      edgeLayer.append(svgElement("path", {
        class: `schema-edge${edge.active ? " is-active" : ""}`,
        d: `M${x1},${y1} C${curveX},${y1} ${curveX},${y2} ${x2},${y2}`,
        "marker-end": `url(#${id})`,
        "aria-label": `${edge.fromTable}.${edge.fromColumn} 关联 ${edge.toTable}.${edge.toColumn}`,
      }));
    });
    svg.append(edgeLayer);

    const fieldNodes = new Map();
    model.nodes.forEach((node) => {
      const group = svgElement("g", {
        class: `schema-table-node${node.active ? " is-active" : " is-idle"}`,
        transform: `translate(${node.x + model.pad},${node.y + model.pad})`,
        "data-table": node.name,
      });
      group.append(svgElement("rect", { class: "schema-table-box", x: 0, y: 0, width: node.width, height: node.height, rx: 6 }));
      group.append(svgElement("rect", { class: "schema-table-head", x: 1, y: 1, width: node.width - 2, height: model.headHeight - 1, rx: 5 }));
      group.append(svgElement("text", { class: "schema-table-name", x: 12, y: 23 }, node.name));
      group.append(svgElement("text", { class: "schema-table-count", x: node.width - 12, y: 23, "text-anchor": "end" }, `${node.columns.length} 字段${node.rowCount == null ? "" : ` · ${node.rowCount} 行`}`));

      node.columns.forEach((column) => {
        const classes = ["schema-column", roleClasses(column.roles), column.id === model.focusTarget?.id ? "is-target" : ""].filter(Boolean).join(" ");
        const row = svgElement("g", {
          id: column.id,
          class: classes,
          role: "button",
          tabindex: 0,
          "data-field-id": column.id,
          "data-field-key": column.key,
          "data-table": node.name,
          "data-column": column.name,
          "aria-label": `${node.name}.${column.name}${column.primaryRole ? `，${ROLE_LABELS[column.primaryRole]}` : ""}`,
        });
        const rowY = column.y - node.y;
        row.append(svgElement("title", {}, `${node.name}.${column.name} · ${column.data_type || "UNKNOWN"}${column.primary_key ? " · 主键" : ""}${column.isForeignKey ? " · 外键" : ""}${column.nullable === false ? " · 非空" : ""}${column.roles.length ? ` · 本次涉及：${column.roles.map((role) => ROLE_LABELS[role]).join("、")}` : ""}`));
        row.append(svgElement("rect", { class: "schema-column-bg", x: 1, y: rowY, width: node.width - 2, height: model.rowHeight }));
        row.append(svgElement("rect", { class: "schema-column-bar", x: 1, y: rowY, width: 3, height: model.rowHeight }));
        row.append(svgElement("path", { class: "schema-focus-marker", d: `M8 ${rowY + 8} L15 ${rowY + 13} L8 ${rowY + 18} Z` }));
        const keyLabels = [column.primary_key ? "PK" : "", column.isForeignKey ? "FK" : "", column.nullable === false ? "NN" : "", column.isUnique && !column.primary_key ? "UQ" : ""]
          .filter(Boolean).join(" ");
        row.append(svgElement("text", { class: "schema-column-key", x: 20, y: rowY + 17 }, keyLabels));
        row.append(svgElement("text", { class: "schema-column-name", x: 76, y: rowY + 17 }, column.name));
        row.append(svgElement("text", { class: "schema-column-type", x: node.width - 10, y: rowY + 17, "text-anchor": "end" }, column.data_type || "UNKNOWN"));
        group.append(row);
        fieldNodes.set(column.id, row);
      });
      svg.append(group);
    });

    return { svg, fieldNodes };
  }

  function render(schema, plan, links) {
    const model = buildModel(schema, plan || {}, links || []);
    const section = document.createElement("section");
    section.className = "schema-explorer";
    const header = document.createElement("div");
    header.className = "schema-explorer-head";
    const heading = document.createElement("div");
    heading.className = "schema-explorer-title";
    const title = document.createElement("strong");
    title.textContent = "数据库字段定位";
    const count = document.createElement("span");
    count.textContent = `${model.tables.length} 张表 · ${model.fieldCount} 个字段 · ${model.foreignKeyCount} 条外键`;
    heading.append(title, count);
    const controls = document.createElement("div");
    controls.className = "schema-explorer-controls";
    const status = document.createElement("span");
    status.className = "schema-focus-status";
    status.setAttribute("aria-live", "polite");
    const jump = document.createElement("button");
    jump.type = "button";
    jump.className = "schema-jump";
    jump.textContent = "定位命中字段";
    jump.hidden = !model.focusTarget;
    controls.append(status, jump);
    header.append(heading, controls);
    section.append(header);

    const legend = document.createElement("div");
    legend.className = "schema-legend";
    ROLE_ORDER.filter((role) => role !== "operator").forEach((role) => {
      const item = document.createElement("span");
      item.className = `schema-legend-item role-${role}`;
      const swatch = document.createElement("i");
      const label = document.createElement("span");
      label.textContent = ROLE_LABELS[role];
      item.append(swatch, label);
      legend.append(item);
    });
    const primaryKeyNote = document.createElement("span");
    primaryKeyNote.className = "schema-legend-note";
    primaryKeyNote.textContent = "PK 主键 · FK 外键 · NN 非空 · UQ 唯一";
    legend.append(primaryKeyNote);
    section.append(legend);

    const viewport = document.createElement("div");
    viewport.className = "schema-viewport";
    viewport.setAttribute("role", "region");
    viewport.setAttribute("aria-label", "完整数据库字段图，可滚动查看全部表和字段");
    const { svg, fieldNodes } = createSvg(model);
    viewport.append(svg);
    section.append(viewport);
    if (!model.tables.length) {
      const empty = document.createElement("p");
      empty.className = "schema-empty";
      empty.textContent = "当前没有可显示的数据库 Schema。";
      viewport.replaceChildren(empty);
      status.textContent = "字段定位不可用";
      jump.hidden = true;
      return section;
    }

    const focusField = (fieldId, scrollPage) => {
      const row = fieldNodes.get(fieldId);
      if (!row) return;
      svg.querySelectorAll(".schema-column.is-target").forEach((item) => item.classList.remove("is-target"));
      row.classList.add("is-target");
      status.textContent = `已定位：${row.getAttribute("data-table")}.${row.getAttribute("data-column")}`;
      const bounds = row.getBBox();
      const svgBounds = svg.getBoundingClientRect();
      const scaleX = svgBounds.width / model.width || 1;
      const scaleY = svgBounds.height / model.height || 1;
      viewport.scrollTo({
        left: Math.max(0, (bounds.x + bounds.width / 2) * scaleX - viewport.clientWidth / 2),
        top: Math.max(0, (bounds.y + bounds.height / 2) * scaleY - viewport.clientHeight / 2),
        behavior: "smooth",
      });
      if (scrollPage) viewport.scrollIntoView({ block: "nearest", behavior: "smooth" });
    };

    if (model.focusTarget) {
      const target = model.focusTarget;
      status.textContent = `本次命中：${target.table}.${target.column}`;
      jump.addEventListener("click", () => focusField(target.id, true));
      requestAnimationFrame(() => focusField(target.id, true));
    } else {
      status.textContent = "本次响应未映射到具体字段；未猜测高亮目标";
    }

    svg.addEventListener("click", (event) => {
      const row = event.target.closest?.(".schema-column[data-field-id]");
      if (row) focusField(row.getAttribute("data-field-id"), false);
    });
    svg.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" && event.key !== " ") return;
      const row = event.target.closest?.(".schema-column[data-field-id]");
      if (!row) return;
      event.preventDefault();
      focusField(row.getAttribute("data-field-id"), false);
    });
    return section;
  }

  return { buildModel, render };
});

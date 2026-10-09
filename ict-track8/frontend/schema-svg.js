(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.SchemaSvg = api;
})(typeof window !== "undefined" ? window : globalThis, function () {
  "use strict";

  const SVG_NS = "http://www.w3.org/2000/svg";
  const ROLE_ORDER = ["metric", "filter", "dimension", "time", "operator", "join", "used"];
  const ROLE_LABELS = {
    used: "查询使用",
    metric: "指标",
    dimension: "维度",
    filter: "筛选",
    time: "时间",
    operator: "运算",
    join: "关联键",
  };
  const TABLE_LABELS = {
    customers: "客户档案", employees: "员工档案", inventory_snapshots: "库存快照",
    marketing_campaigns: "营销活动", operating_expenses: "运营费用", payment_receipts: "回款记录",
    payroll_records: "薪酬记录", products: "产品目录", purchase_orders: "采购订单",
    regions: "地区信息", sales_orders: "销售订单", sales_reps: "销售人员",
    sales_returns: "退货退款", sales_targets: "销售目标", shipment_records: "物流发货",
    suppliers: "供应商档案", support_tickets: "售后工单", web_traffic_daily: "每日网站流量",
  };
  const COLUMN_LABELS = {
    acquisition_channel: "获客渠道", annual_value: "年度客户价值", approval_status: "审批状态",
    assigned_rep_id: "处理人员编号", attributed_sales: "归因销售额", base_salary: "基本工资",
    bonus_amount: "奖金金额", brand: "品牌", budget: "预算金额", campaign_id: "活动编号", campaign_name: "活动名称",
    campaign_type: "活动类型", carrier: "承运商", category: "类别", channel: "渠道", city: "城市",
    clicks: "点击量", collected_amount: "实收金额", conversions: "转化量", created_date: "创建日期",
    customer_id: "客户编号", customer_level: "客户等级", customer_name: "客户名称", delivery_days: "配送天数",
    department: "部门", device_type: "设备类型", discount_amount: "优惠金额", employee_id: "员工编号",
    employee_name: "员工姓名", employee_status: "员工状态", employment_type: "用工类型", end_date: "结束日期", expense_amount: "费用金额",
    expense_category: "费用类别", expense_date: "费用日期", expense_id: "费用单号",
    first_response_minutes: "首次响应时长（分钟）", gross_profit: "毛利", hire_date: "入职日期",
    impressions: "曝光量", inbound_quantity: "在途库存量", industry: "所属行业", inventory_value: "库存金额",
    issue_type: "问题类型", launch_date: "上市日期", late_days: "延误天数", lead_time_days: "采购周期（天）",
    list_price: "标价", on_hand_quantity: "现有库存量", order_date: "下单日期", order_id: "订单编号",
    order_status: "订单状态", overtime_hours: "加班时长", page_views: "页面浏览量", payment_date: "收款日期",
    payment_method: "支付方式", payroll_date: "薪酬日期", payroll_id: "薪酬记录编号", position: "岗位",
    priority: "优先级", product_category: "产品类别", product_id: "产品编号", product_name: "产品名称",
    purchase_amount: "采购金额", purchase_date: "采购日期", purchase_id: "采购单号",
    purchase_quantity: "采购数量", purchase_status: "采购状态", quantity: "销售数量", reason: "退货原因",
    receipt_id: "回款单号", receipt_status: "回款状态", refund_amount: "退款金额", region: "地区",
    region_id: "地区编号", region_name: "地区名称", resolution_hours: "处理时长（小时）",
    return_date: "退货日期", return_id: "退货单号", safety_stock: "安全库存", sales_amount: "销售额",
    sales_rep_id: "销售人员编号", sales_rep_name: "销售人员姓名", salary_amount: "薪酬金额",
    satisfaction_score: "满意度评分", shipment_date: "发货日期", shipment_id: "发货单号",
    shipment_status: "物流状态", shipping_cost: "运费", snapshot_month: "库存快照月份", start_date: "开始日期",
    status: "状态", supplier_category: "供应商类别", supplier_id: "供应商编号", supplier_name: "供应商名称",
    supplier_rating: "供应商评分", target_id: "目标记录编号", target_month: "目标月份", target_orders: "目标订单数",
    target_sales: "目标销售额", team: "销售团队", ticket_id: "工单编号", traffic_id: "流量记录编号",
    traffic_source: "流量来源", transit_days: "运输时长（天）", unit_cost: "单位成本", unit_price: "单价",
    visit_date: "访问日期", visits: "访问次数", web_conversions: "网站转化数",
  };
  const TABLE_COLUMN_LABELS = {
    marketing_campaigns: { channel: "营销渠道", budget: "活动预算" },
    sales_orders: { channel: "销售渠道" },
    support_tickets: { created_date: "工单创建日期", status: "工单状态" },
    sales_returns: { status: "退货状态" },
    products: { category: "产品类别" },
  };
  const DATA_TYPE_LABELS = {
    BLOB: "二进制", BOOLEAN: "布尔值", DATE: "日期", DATETIME: "日期时间", DECIMAL: "小数",
    INTEGER: "整数", NUMERIC: "数值", REAL: "实数", TEXT: "文本",
  };

  function tableLabel(name) {
    return TABLE_LABELS[name] || name;
  }

  function columnLabel(table, column) {
    return TABLE_COLUMN_LABELS[table]?.[column] || COLUMN_LABELS[column] || column;
  }

  function dataTypeLabel(type) {
    const raw = String(type || "UNKNOWN").toUpperCase();
    return DATA_TYPE_LABELS[raw] || raw;
  }

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
        const columnWidth = Array.from(columnLabel(table.name, column.name)).length * 11.5 + 166;
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

    // Disconnected tables stay side by side; no relationship is invented.
    if (!edges.length && nodes.size > 1) {
      const ordered = [...nodes.values()], across = Math.min(3, ordered.length);
      let gridTop = 0;
      for (let i = 0; i < ordered.length; i += across) {
        const row = ordered.slice(i, i + across);
        row.forEach((node, index) => {
          node.x = index * (nodeWidth + 36); node.y = gridTop;
          node.columns.forEach((column, columnIndex) => { column.y = gridTop + headHeight + columnIndex * rowHeight; });
        });
        gridTop += Math.max(...row.map((node) => node.height)) + gapY;
      }
      componentTop = gridTop + gapY;
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
        "aria-label": `${tableLabel(edge.fromTable)}的${columnLabel(edge.fromTable, edge.fromColumn)}（${edge.fromTable}.${edge.fromColumn}）关联${tableLabel(edge.toTable)}的${columnLabel(edge.toTable, edge.toColumn)}（${edge.toTable}.${edge.toColumn}）`,
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
      group.append(svgElement("rect", { class: "schema-table-box", x: 0, y: 0, width: node.width, height: node.height }));
      group.append(svgElement("rect", { class: "schema-table-head", x: 1, y: 1, width: node.width - 2, height: model.headHeight - 1 }));
      group.append(svgElement("title", {}, `${tableLabel(node.name)}（${node.name}）`));
      group.append(svgElement("text", { class: "schema-table-name", x: 12, y: 23 }, tableLabel(node.name)));
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
          "aria-label": `${tableLabel(node.name)}的${columnLabel(node.name, column.name)}（${node.name}.${column.name}）${column.primaryRole ? `，${ROLE_LABELS[column.primaryRole]}` : ""}`,
        });
        const rowY = column.y - node.y;
        const typeLabel = dataTypeLabel(column.data_type);
        const constraints = [column.primary_key ? "主键" : "", column.isForeignKey ? "外键" : "", column.nullable === false ? "非空" : "", column.isUnique && !column.primary_key ? "唯一" : ""]
          .filter(Boolean);
        row.append(svgElement("title", {}, `${tableLabel(node.name)}（${node.name}） · ${columnLabel(node.name, column.name)}（${column.name}） · 类型：${typeLabel}（${column.data_type || "UNKNOWN"}）${constraints.length ? ` · ${constraints.join(" · ")}` : ""}${column.roles.length ? ` · 本次涉及：${column.roles.map((role) => ROLE_LABELS[role]).join("、")}` : ""}`));
        row.append(svgElement("rect", { class: "schema-column-bg", x: 1, y: rowY, width: node.width - 2, height: model.rowHeight }));
        row.append(svgElement("rect", { class: "schema-column-bar", x: 1, y: rowY, width: 3, height: model.rowHeight }));
        row.append(svgElement("path", { class: "schema-focus-marker", d: `M8 ${rowY + 8} L15 ${rowY + 13} L8 ${rowY + 18} Z` }));
        const keyLabels = [column.primary_key ? "主键" : "", column.isForeignKey ? "外键" : "", column.nullable === false ? "非空" : "", column.isUnique && !column.primary_key ? "唯一" : ""]
          .filter(Boolean).join(" · ");
        row.append(svgElement("text", { class: "schema-column-key", x: 20, y: rowY + 17 }, keyLabels));
        row.append(svgElement("text", { class: "schema-column-name", x: 86, y: rowY + 17 }, columnLabel(node.name, column.name)));
        row.append(svgElement("text", { class: "schema-column-type", x: node.width - 10, y: rowY + 17, "text-anchor": "end" }, typeLabel));
        group.append(row);
        fieldNodes.set(column.id, row);
      });
      svg.append(group);
    });

    return { svg, fieldNodes };
  }

  function render(schema, plan, links, options = {}) {
    const model = buildModel(schema, plan || {}, links || []);
    const section = document.createElement("section");
    section.className = `schema-explorer${options.minimal ? " is-minimal" : ""}`;
    const header = document.createElement("div");
    header.className = "schema-explorer-head";
    const heading = document.createElement("div");
    heading.className = "schema-explorer-title";
    const title = document.createElement("strong");
    title.textContent = "数据库字段定位";
    const count = document.createElement("span");
    count.textContent = `${model.tables.length} 张表 · ${model.fieldCount} 个字段 · ${model.foreignKeyCount} 条外键`;
    heading.append(title);
    if (!options.hideCount) heading.append(count);
    const controls = document.createElement("div");
    controls.className = "schema-explorer-controls";
    const fit = document.createElement("button");
    fit.type = "button";
    fit.className = "schema-jump";
    fit.textContent = "适应宽度";
    const zoomOut = document.createElement("button"), zoomIn = document.createElement("button");
    [zoomOut, zoomIn].forEach((button) => { button.type = "button"; button.className = "schema-jump schema-zoom"; });
    zoomOut.textContent = "−"; zoomIn.textContent = "+";
    zoomOut.setAttribute("aria-label", "缩小数据库字段图"); zoomIn.setAttribute("aria-label", "放大数据库字段图");
    const status = document.createElement("span");
    status.className = "schema-focus-status";
    status.setAttribute("aria-live", "polite");
    const jump = document.createElement("button");
    jump.type = "button";
    jump.className = "schema-jump";
    jump.textContent = "定位命中字段";
    jump.hidden = !model.focusTarget;
    controls.append(status, fit, zoomOut, zoomIn, jump);
    header.append(heading, controls);
    if (!options.minimal) section.append(header);

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
    if (!options.hideLegendNote) {
      const primaryKeyNote = document.createElement("span");
      primaryKeyNote.className = "schema-legend-note";
      primaryKeyNote.textContent = "主键 · 外键 · 非空 · 唯一";
      legend.append(primaryKeyNote);
    }
    section.append(legend);

    const viewport = document.createElement("div");
    viewport.className = "schema-viewport";
    viewport.setAttribute("role", "region");
    viewport.setAttribute("aria-label", "完整数据库字段图，可滚动查看全部表和字段");
    const { svg, fieldNodes } = createSvg(model);
    viewport.append(svg);
    section.append(viewport);
    const footer = document.createElement("div"); footer.className = "schema-inspector";
    footer.setAttribute("aria-live", "polite");
    const inspectorText = document.createElement("div"); inspectorText.className = "schema-inspector-text";
    const scopeToggle = document.createElement("button"); scopeToggle.type = "button"; scopeToggle.className = "schema-jump";
    scopeToggle.textContent = "突出查询字段"; scopeToggle.setAttribute("aria-pressed", "false");
    scopeToggle.hidden = !model.focusFieldCount;
    scopeToggle.addEventListener("click", () => {
      const highlighted = section.classList.toggle("is-query-focused");
      scopeToggle.setAttribute("aria-pressed", String(highlighted)); scopeToggle.textContent = highlighted ? "显示完整结构" : "突出查询字段";
    });
    inspectorText.textContent = model.focusFieldCount ? `本次涉及 ${model.focusFieldCount} 个字段 · 点击查看中文说明、原字段名与类型` : "点击字段查看中文说明、原字段名与约束";
    footer.append(inspectorText, scopeToggle);
    if (!options.minimal) section.append(footer);
    if (!model.tables.length) {
      const empty = document.createElement("p");
      empty.className = "schema-empty";
      empty.textContent = "当前没有可显示的数据库 Schema。";
      viewport.replaceChildren(empty);
      status.textContent = "字段定位不可用";
      jump.hidden = true;
      return section;
    }

    const reduceMotion = () => window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    let zoom = 1;
    const setZoom = (value) => { zoom = Math.max(.4, Math.min(1.8, value)); svg.style.width = `${model.width * zoom}px`; svg.style.height = `${model.height * zoom}px`; };
    fit.addEventListener("click", () => setZoom(Math.min(1, viewport.clientWidth / model.width)));
    zoomOut.addEventListener("click", () => setZoom(zoom - .15));
    zoomIn.addEventListener("click", () => setZoom(zoom + .15));
    const focusField = (fieldId, scrollPage, notify = true) => {
      const row = fieldNodes.get(fieldId);
      if (!row) return;
      svg.querySelectorAll(".schema-column.is-target").forEach((item) => item.classList.remove("is-target"));
      row.classList.add("is-target");
      const column = [...model.nodes.values()].flatMap((node) => node.columns).find((item) => item.id === fieldId);
      inspectorText.replaceChildren();
      const table = row.getAttribute("data-table"), field = row.getAttribute("data-column");
      const label = document.createElement("strong"); label.className = "schema-inspector-label"; label.textContent = `${tableLabel(table)} · ${columnLabel(table, field)}`;
      const name = document.createElement("code"); name.textContent = `${table}.${field}`;
      const type = document.createElement("span"); type.className = "schema-inspector-type"; type.textContent = `类型：${dataTypeLabel(column?.data_type)}（${column?.data_type || "UNKNOWN"}）`;
      const description = document.createElement("span");
      description.textContent = [column?.primary_key ? "主键" : null, column?.isForeignKey ? "外键" : null, column?.nullable === false ? "非空" : null, column?.isUnique && !column?.primary_key ? "唯一" : null, ...(column?.roles || []).map((role) => ROLE_LABELS[role])].filter(Boolean).join(" · ") || "本次未使用";
      inspectorText.append(label, name, type, description);
      status.textContent = `已定位：${tableLabel(table)} · ${columnLabel(table, field)}`;
      const bounds = row.getBoundingClientRect(), viewportBounds = viewport.getBoundingClientRect();
      viewport.scrollTo({
        left: Math.max(0, viewport.scrollLeft + bounds.left - viewportBounds.left + bounds.width / 2 - viewport.clientWidth / 2),
        top: Math.max(0, viewport.scrollTop + bounds.top - viewportBounds.top + bounds.height / 2 - viewport.clientHeight / 2),
        behavior: reduceMotion() ? "auto" : "smooth",
      });
      if (scrollPage) {
        const thread = section.closest(".thread");
        if (thread) thread.scrollTo({ top: thread.scrollTop + viewport.getBoundingClientRect().top - thread.getBoundingClientRect().top - 60, behavior: reduceMotion() ? "auto" : "smooth" });
        else viewport.scrollIntoView({ block: "nearest", behavior: reduceMotion() ? "auto" : "smooth" });
      }
      if (notify) section.dispatchEvent(new CustomEvent("schema-field-focus", { bubbles: true, detail: { key: `${row.getAttribute("data-table")}.${row.getAttribute("data-column")}`, table: row.getAttribute("data-table"), column: row.getAttribute("data-column") } }));
    };
    section.addEventListener("query-field-focus", (event) => {
      const keys = event.detail?.keys || (event.detail?.key ? [event.detail.key] : []);
      fieldNodes.forEach((row) => row.classList.toggle("is-linked", keys.includes(`${row.getAttribute("data-table")}.${row.getAttribute("data-column")}`)));
      if (!keys.length) {
        fieldNodes.forEach((row) => row.classList.remove("is-target")); status.textContent = `本次涉及 ${model.focusFieldCount} 个字段`;
        inspectorText.textContent = "点击字段查看中文说明、原字段名与类型"; return;
      }
      const found = [...fieldNodes.entries()].find(([, row]) => event.detail?.table && event.detail?.column
        ? row.getAttribute("data-table") === event.detail.table && row.getAttribute("data-column") === event.detail.column
        : `${row.getAttribute("data-table")}.${row.getAttribute("data-column")}` === event.detail?.key);
      if (found) focusField(found[0], event.detail.scroll, false);
    });

    if (model.focusTarget) {
      const target = model.focusTarget;
      status.textContent = `本次命中：${tableLabel(target.table)} · ${columnLabel(target.table, target.column)}`;
      jump.addEventListener("click", () => focusField(target.id, true));
      requestAnimationFrame(() => focusField(target.id, false, false));
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

  return { buildModel, render, tableLabel, columnLabel };
});

(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.ResultBrowser = api;
})(typeof window !== "undefined" ? window : globalThis, function () {
  "use strict";
  const digest = (value) => typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
  function receiptFrom(structured) {
    const value = structured.provenance?.complete_result;
    if (value?.status !== "complete" || value.cursor_eof_verified !== true ||
        !/^[a-f0-9]{32}$/.test(value.artifact_id || "") || !digest(value.binding_sha256) ||
        !digest(value.query_sha256) || !digest(value.data_sha256) ||
        !Number.isSafeInteger(value.row_count) || value.row_count < 0 ||
        !Array.isArray(value.columns) || !value.columns.every((c) => typeof c === "string")) return null;
    return value;
  }
  function validatePage(page, receipt, offset) {
    const length = Math.min(100, Math.max(0, receipt.row_count - offset));
    const next = offset + length < receipt.row_count ? offset + length : null;
    if (page.status !== "complete" || page.artifact_id !== receipt.artifact_id ||
        page.query_sha256 !== receipt.query_sha256 || page.data_sha256 !== receipt.data_sha256 ||
        page.row_count !== receipt.row_count || page.offset !== offset || page.page_size !== 100 ||
        JSON.stringify(page.columns) !== JSON.stringify(receipt.columns) ||
        !Array.isArray(page.rows) || page.rows.length !== length ||
        !page.rows.every((row) => Array.isArray(row) && row.length === receipt.columns.length) ||
        page.next_offset !== next) throw new Error("分页结果与本次查询不一致，请重新查询。");
    return page;
  }
  function pageUrl(api, receipt, offset) {
    const params = new URLSearchParams({ offset: String(offset), page_size: "100",
      binding_sha256: receipt.binding_sha256, query_sha256: receipt.query_sha256 });
    return `${api}/api/v1/nl2sql/results/${receipt.artifact_id}?${params}`;
  }
  function cellText(value) {
    return value === null ? "NULL" : typeof value === "object" ? JSON.stringify(value) : String(value);
  }
  function render(structured, { api = "", onFocus } = {}) {
    const receipt = receiptFrom(structured);
    if (!receipt || typeof document === "undefined") return null;
    const element = (tag, cls, text) => {
      const node = document.createElement(tag); if (cls) node.className = cls;
      if (text !== undefined) node.textContent = text; return node;
    };
    const panel = element("section", "result-browser"); panel.setAttribute("aria-label", "完整查询结果");
    const toolbar = element("div", "result-browser-toolbar"), heading = element("strong", "", `完整结果 · ${receipt.row_count.toLocaleString("zh-CN")} 行`);
    const range = element("span", "result-browser-range"), actions = element("div", "result-browser-actions");
    function button(label, action) { const node = element("button", "query-text-button", label); node.type = "button"; node.onclick = action; return node; }
    let offset = 0, rows = [], loaded = false, busy = false, failed = false;
    const previous = button("上一页", () => load(offset - 100));
    const next = button("下一页", () => load(offset + 100));
    const retry = button("重新读取", () => load(offset)); retry.hidden = true;
    const download = button("下载当前页 JSON", () => {
      if (!loaded || busy || failed) return;
      const blob = new Blob([JSON.stringify({ columns: receipt.columns, rows, offset,
        total_rows: receipt.row_count, query_sha256: receipt.query_sha256, data_sha256: receipt.data_sha256 }, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob), link = element("a");
      link.href = url; link.download = `query-result-${offset + 1}-${offset + rows.length}.json`;
      link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    });
    actions.append(previous, next, retry, download); toolbar.append(heading, range, actions);
    const status = element("p", "query-caption"); status.setAttribute("role", "status");
    const wrap = element("div", "audit-table-wrap");
    panel.append(toolbar, status, wrap);
    function controls() {
      previous.disabled = busy || failed || !loaded || offset === 0;
      next.disabled = busy || failed || !loaded || offset + rows.length >= receipt.row_count;
      download.disabled = busy || failed || !loaded;
      retry.disabled = busy; panel.setAttribute("aria-busy", String(busy));
    }
    function draw() {
      wrap.replaceChildren();
      range.textContent = rows.length ? `第 ${offset + 1}–${offset + rows.length} 行 / 每页 100 行` : "无符合条件的数据";
      const table = element("table", "data"), head = element("thead"), header = element("tr");
      const number = element("th", "result-row-number", "行"); number.scope = "col"; header.append(number);
      receipt.columns.forEach((label) => { const cell = element("th", "", label); cell.scope = "col"; header.append(cell); });
      head.append(header); table.append(head);
      const body = element("tbody"); rows.forEach((row, index) => {
        const line = element("tr"); line.dataset.resultRow = String(offset + index);
        line.append(element("td", "result-row-number", String(offset + index + 1)));
        row.forEach((value, columnIndex) => {
          const cell = element("td"), field = element("button", "query-result-cell", cellText(value)); field.type = "button";
          field.title = `查看结果列 ${receipt.columns[columnIndex]} 的字段来源`;
          field.onclick = () => onFocus?.({ row: offset + index, column: receipt.columns[columnIndex] });
          cell.append(field); line.append(cell);
        }); body.append(line);
      }); table.append(body); wrap.append(table);
    }
    async function load(target) {
      if (busy || target < 0 || (receipt.row_count && target >= receipt.row_count)) return;
      busy = true; controls(); status.textContent = "正在核对来源并读取本页…";
      try {
        const response = await fetch(pageUrl(api, receipt, target), { signal: AbortSignal.timeout(20000), cache: "no-store" });
        if (!response.ok) throw new Error([404, 409].includes(response.status)
          ? "来源或结果已变化，不能继续读取；请重新提交查询。" : "本页读取失败，可以重新读取。");
        const page = validatePage(await response.json(), receipt, target);
        if (!panel.isConnected) return;
        offset = target; rows = page.rows; loaded = true; failed = false; retry.hidden = true;
        draw(); status.textContent = "本页已通过后端来源与完整结果核验。图表仅展示初始预览，分页不会重新执行 SQL。";
      } catch (error) {
        if (!panel.isConnected) return;
        failed = true; retry.hidden = false;
        status.textContent = `${error.name === "TimeoutError" ? "读取超时，请重新读取。" : error.message} ${loaded ? "下方保留的是上一次已读取页面。" : "未展示未经核验的数据。"}`;
      } finally { busy = false; controls(); }
    }
    controls(); queueMicrotask(() => { if (panel.isConnected) load(0); });
    return panel;
  }
  return { receiptFrom, validatePage, pageUrl, cellText, render };
});

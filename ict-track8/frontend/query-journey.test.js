const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const { buildModel, questionSegments, annotateSql } = require("./query-journey.js");
const context = { window: {} };
vm.runInNewContext(fs.readFileSync(path.join(__dirname, "syntax-highlight.js"), "utf8"), context);
const tokenize = context.window.LatticeSyntaxHighlight.tokenize;

test("maps only reported phrases and preserves untouched question text", () => {
  const question = "2025年华东地区的净销售额和销售额";
  const segments = questionSegments(question, [
    { key: "sales.amount", source: "销售额", role: "metric" },
    { key: "sales.net", source: "净销售额", role: "metric" },
    { key: "sales.region", source: "华东", role: "filter" },
  ], []);
  assert.equal(segments.map((s) => s.text).join(""), question);
  assert.equal(segments.find((s) => s.text === "净销售额").field.key, "sales.net");
  assert.equal(segments.find((s) => s.text === "2025年").field, undefined);
});

test("SQL links exact qualified columns, never single-quoted values or comments", () => {
  const sql = `SELECT "orders"."amount", 'orders.amount' FROM "orders" WHERE "orders"."amount" > ? -- orders.amount`;
  const result = annotateSql(tokenize(sql, "sql"), [{ key: "orders.amount", column: "amount" }]);
  assert.equal(result.map((t) => t.text).join(""), sql);
  assert.equal(result.filter((t) => t.field).length, 4);
  assert.equal(result.find((t) => t.text === "'orders.amount'").field, undefined);
  assert.equal(result.find((t) => t.kind === "comment").field, undefined);
  assert.equal(result.find((t) => t.kind === "parameter").parameter, 0);
});

test("ambiguous bare columns and output aliases do not receive invented source mappings", () => {
  const result = annotateSql(tokenize(`SELECT amount, SUM("orders"."amount") AS "amount" FROM orders`, "sql"), [
    { key: "orders.amount", column: "amount" }, { key: "refunds.amount", column: "amount" },
  ]);
  assert.equal(result.find((t) => t.text === "amount").field, undefined);
  assert.equal(result.filter((t) => t.text === '"amount"').at(-1).field, undefined);
  assert.equal(result.filter((t) => t.field).length, 2);
});

test("numbered/repeated placeholders bind to the matching parameter without changing SQL", () => {
  const sql = "SELECT ?2, ?1, ?, ?2, ?";
  const result = annotateSql(tokenize(sql, "sql"), []);
  assert.deepEqual(Array.from(result.filter((t) => t.parameter !== undefined), (t) => t.parameter), [1, 0, 2, 1, 3]);
  assert.equal(result.map((t) => t.text).join(""), sql);
});

test("a missing query plan never produces made-up operations or fields", () => {
  const model = buildModel({ question: "销售额", structured: { status: "clarification" } });
  assert.equal(model.fields.length, 0); assert.equal(model.operations.length, 0);
  assert.equal(model.question, "销售额");
});

test("fallback plan fields do not duplicate an existing business phrase mapping", () => {
  const model = buildModel({ structured: { plan: { table: "orders", dimensions: ["region"], links: [
    { table: "orders", column: "region", role: "dimension", source_text: "地区" },
  ] } } });
  assert.equal(model.fields.length, 1); assert.equal(model.fields[0].source, "地区");
});

test("real filters join the field explanation, but absent SQL has no executed return limit", () => {
  const model = buildModel({ question: "华东销售额", structured: { status: "clarification", plan: {
    table: "orders", limit: 100, filters: [{ table: "orders", column: "region", source_text: "华东", operator: "=", value: "华东" }],
  } } });
  assert.equal(model.fields[0].key, "orders.region");
  assert.equal(model.operations.find((o) => o.type === "filter").details[0], 'orders.region = 华东');
  assert.equal(model.operations.some((o) => o.type === "limit"), false);
});

test("complete and partial query scope never mislabels the preview limit as SQL scope", () => {
  const structured = { sql: "SELECT * FROM orders", rows: [[1]], plan: { limit: 100 },
    provenance: { complete_result: { status: "complete", cursor_eof_verified: true, row_count: 244 } } };
  assert.equal(buildModel({ structured }).operations.find((o) => o.type === "limit").summary, "完整 244 行");
  structured.provenance.complete_result.status = "partial";
  assert.equal(buildModel({ structured }).operations.find((o) => o.type === "limit").summary, "完整结果未取得");
  delete structured.provenance.complete_result;
  assert.equal(buildModel({ structured }).operations.find((o) => o.type === "limit").summary, "预览上限 100 行");
});

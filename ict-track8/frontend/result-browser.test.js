const test = require("node:test");
const assert = require("node:assert/strict");
const { receiptFrom, validatePage, pageUrl, cellText } = require("./result-browser.js");
const receipt = { status: "complete", cursor_eof_verified: true, artifact_id: "a".repeat(32),
  binding_sha256: "b".repeat(64), query_sha256: "c".repeat(64), data_sha256: "d".repeat(64),
  row_count: 241, columns: ["重复列", "重复列"] };
const lastPage = () => ({ ...receipt, offset: 200, page_size: 100, next_offset: null,
  rows: Array.from({ length: 41 }, (_, i) => [i, null]) });

test("only verified complete receipts enable browsing", () => {
  assert.equal(receiptFrom({ provenance: { complete_result: receipt } }), receipt);
  for (const change of [{ status: "partial" }, { artifact_id: null }, { cursor_eof_verified: false }, { row_count: -1 }]) {
    assert.equal(receiptFrom({ provenance: { complete_result: { ...receipt, ...change } } }), null);
  }
});
test("pages preserve repeated columns, nulls and absolute row offsets", () => {
  const page = lastPage(); assert.equal(validatePage(page, receipt, 200), page);
  assert.deepEqual(page.rows[0], [0, null]);
  assert.equal(cellText(null), "NULL"); assert.equal(cellText("NULL"), "NULL");
});
test("rejects another query, wrong schema, counts, next offset and malformed cells", () => {
  for (const change of [{ query_sha256: "e".repeat(64) }, { data_sha256: "e".repeat(64) }, { offset: 0 },
    { columns: ["重复列"] }, { row_count: 242 }, { next_offset: 241 }, { rows: [[1]] }]) {
    assert.throws(() => validatePage({ ...lastPage(), ...change }, receipt, 200));
  }
});
test("pagination binds to original receipt and query", () => {
  const url = new URL(pageUrl("http://localhost:8030", receipt, 200));
  assert.equal(url.searchParams.get("binding_sha256"), receipt.binding_sha256);
  assert.equal(url.searchParams.get("query_sha256"), receipt.query_sha256);
  assert.equal(url.searchParams.get("offset"), "200");
  assert.equal(url.searchParams.get("page_size"), "100");
});
test("empty complete results are valid with an empty first page", () => {
  const empty = { ...receipt, row_count: 0 };
  assert.ok(validatePage({ ...empty, offset: 0, page_size: 100, rows: [], next_offset: null }, empty, 0));
});

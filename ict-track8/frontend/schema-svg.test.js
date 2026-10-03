const assert = require("node:assert/strict");
const test = require("node:test");
const { buildModel } = require("./schema-svg.js");

test("unrelated tables use a compact layout without invented foreign-key links", () => {
  const model = buildModel({ tables: [
    { name: "orders", columns: [{ name: "id", data_type: "TEXT" }] },
    { name: "notes", columns: [{ name: "id", data_type: "TEXT" }] },
  ] }, { table: "orders" }, []);
  assert.equal(model.edges.length, 0);
  const [first, second] = [...model.nodes.values()];
  assert.equal(first.y, second.y); assert.ok(first.x !== second.x);
});

const schema = {
  tables: [
    {
      name: "customers",
      row_count: 8,
      columns: ["customer_id", "customer_name", "customer_level", "industry"].map((name) => ({
        name,
        data_type: "TEXT",
        nullable: name !== "customer_id",
        primary_key: name === "customer_id",
      })),
      foreign_keys: [],
      unique_keys: [["customer_id"]],
    },
    {
      name: "sales_orders",
      row_count: 12,
      columns: [
        ["order_id", "TEXT"], ["order_date", "TEXT"], ["region", "TEXT"],
        ["channel", "TEXT"], ["product_category", "TEXT"], ["product_name", "TEXT"],
        ["quantity", "INTEGER"], ["unit_price", "REAL"], ["sales_amount", "REAL"],
        ["customer_id", "TEXT"],
      ].map(([name, data_type]) => ({ name, data_type, nullable: false, primary_key: name === "order_id" })),
      foreign_keys: [],
      unique_keys: [["order_id"]],
    },
  ],
};

function fields(model) {
  return [...model.nodes.values()].flatMap((table) => table.columns);
}

test("preserves the full schema and highlights only mapped query fields", () => {
  const model = buildModel(schema, {
    table: "sales_orders",
    metric_table: "sales_orders",
    metric_column: "sales_amount",
    filters: [
      { table: "sales_orders", column: "order_date" },
      { table: "sales_orders", column: "region" },
      { table: "sales_orders", column: "missing_column" },
    ],
  }, [{ table: "sales_orders", column: "sales_amount", role: "metric" }]);
  const columns = fields(model);

  assert.equal(model.fieldCount, 14);
  assert.deepEqual(columns.filter((column) => column.roles.length).map((column) => [column.name, column.primaryRole]), [
    ["order_date", "filter"],
    ["region", "filter"],
    ["sales_amount", "metric"],
  ]);
  assert.deepEqual([model.focusTarget.table, model.focusTarget.column], ["sales_orders", "sales_amount"]);
  assert.equal(model.foreignKeyCount, 0);
});

test("does not infer a focus target when the response has no valid field mapping", () => {
  const model = buildModel(schema, {
    table: "sales_orders",
    filters: [{ table: "sales_orders", column: "missing_column" }],
  }, []);

  assert.equal(model.fieldCount, 14);
  assert.equal(model.focusTarget, null);
  assert.equal(model.focusFieldCount, 0);
  assert.equal(fields(model).some((column) => column.roles.length), false);
});

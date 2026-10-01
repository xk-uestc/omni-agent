CREATE TABLE customers (
    customer_id TEXT PRIMARY KEY,
    customer_name TEXT NOT NULL,
    customer_level TEXT NOT NULL,
    industry TEXT NOT NULL
);

CREATE INDEX idx_sales_orders_date ON sales_orders(order_date);

CREATE INDEX idx_sales_orders_region ON sales_orders(region);

CREATE TABLE sales_orders (
    order_id TEXT PRIMARY KEY,
    order_date TEXT NOT NULL,
    region TEXT NOT NULL,
    channel TEXT NOT NULL,
    product_category TEXT NOT NULL,
    product_name TEXT NOT NULL,
    quantity INTEGER NOT NULL CHECK (quantity >= 0),
    unit_price REAL NOT NULL CHECK (unit_price >= 0),
    sales_amount REAL NOT NULL CHECK (sales_amount >= 0),
    customer_id TEXT NOT NULL
);
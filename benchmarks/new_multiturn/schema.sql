CREATE TABLE Orders (
  OrderId INTEGER PRIMARY KEY,
  Department TEXT NOT NULL,
  ApprovedDate TEXT NOT NULL,
  ApprovedAmount REAL NOT NULL
);
INSERT INTO Orders VALUES
  (1,'研发部','2025-02-11',900),
  (2,'研发部','2025-07-19',600),
  (3,'支持部','2025-03-13',2000),
  (4,'支持部','2025-11-25',1600),
  (5,'研发部','2024-03-01',5000),
  (6,'外联部','2025-04-08',50);

CREATE TABLE WarrantyRequests (
  RequestId INTEGER PRIMARY KEY,
  RequestDate TEXT NOT NULL,
  RefundedAmount REAL NOT NULL
);
INSERT INTO WarrantyRequests VALUES
  (11,'2025-01-15',400),
  (12,'2025-06-21',600),
  (13,'2025-10-17',800),
  (14,'2024-08-07',10000);

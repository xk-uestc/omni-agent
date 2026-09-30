"""在真实 SQLite 数据库上验收业务别名/字段标注。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.nl2sql.annotation import validate_schema_annotations
from backend.nl2sql.schema import SchemaIntrospector


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--aliases", type=Path, required=True)
    args = parser.parse_args()
    if not args.database.exists():
        raise SystemExit(f"数据库不存在: {args.database}")
    import sqlite3

    with sqlite3.connect(f"file:{args.database.resolve().as_posix()}?mode=ro", uri=True) as connection:
        tables = SchemaIntrospector().introspect(connection)
    report = validate_schema_annotations(tables, args.aliases)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["status"] == "invalid":
        raise SystemExit(1)


if __name__ == "__main__":
    main()

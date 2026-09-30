"""创建赛题八结构化问数示例数据库。"""

import sys
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from backend.nl2sql.seed import initialize_database


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    path = initialize_database(root / "data" / "demo_sales.sqlite")
    print(path)

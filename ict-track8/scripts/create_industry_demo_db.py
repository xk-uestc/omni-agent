"""创建赛题八行业级可复现实验数据库。"""

import sys
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from backend.nl2sql.industry_seed import initialize_industry_database


ROOT = Path(__file__).resolve().parents[1]


if __name__ == "__main__":
    destination = initialize_industry_database(ROOT / "data" / "industry_demo.sqlite")
    print(destination)

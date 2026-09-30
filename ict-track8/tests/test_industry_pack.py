from __future__ import annotations

from pathlib import Path

from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.industry_seed import initialize_industry_database


ROOT = Path(__file__).resolve().parents[1]
ALIASES = ROOT / "data" / "industry_aliases.json"


def industry_engine(tmp_path):
    return Nl2SqlEngine(
        initialize_industry_database(tmp_path / "industry.sqlite"),
        aliases_path=ALIASES,
    )


def test_industry_pack_resolves_multi_hop_sales_query(tmp_path):
    result = industry_engine(tmp_path).answer("2025年华东地区各品类销售额")
    assert result.status == "ok"
    assert result.plan["metric_column"] == "line_amount"
    assert result.plan["join_tables"] == ["orders", "products", "regions"]
    assert result.plan["filters"][0]["column"] == "order_date"
    assert result.plan["filters"][1]["column"] == "region_name"
    assert result.columns == ("category", "销售额")


def test_industry_pack_accepts_colloquial_monetary_metric(tmp_path):
    result = industry_engine(tmp_path).answer("2025年华东地区卖了多少钱")
    assert result.status == "ok"
    assert result.plan["metric_column"] == "line_amount"
    assert result.rows[0]["销售额"] > 0


def test_industry_pack_uses_generic_date_and_dimension_aliases(tmp_path):
    result = industry_engine(tmp_path).answer("按月统计销售额")
    assert result.status == "ok"
    assert result.plan["dimension_transforms"]["order_date"] == "month"
    assert result.plan["dimension_tables"]["order_date"] == "orders"


def test_industry_pack_supports_second_business_subject(tmp_path):
    result = industry_engine(tmp_path).answer("已解决工单的平均解决时长")
    assert result.status == "ok"
    assert result.plan["table"] == "support_tickets"
    assert result.plan["metric_function"] == "AVG"
    # 旧断言 10.5 是全部工单的平均值——即旧实现静默丢弃了“已解决”过滤。
    # 这里改为与金标 SQL 的执行结果比较，不写死数字。
    import sqlite3
    with sqlite3.connect(tmp_path / "industry.sqlite") as connection:
        gold = connection.execute(
            "SELECT AVG(resolution_hours) FROM support_tickets WHERE status = '已解决'"
        ).fetchone()[0]
    assert result.rows[0]["平均解决时长"] == gold
    assert "已解决" in result.parameters

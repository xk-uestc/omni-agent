"""从真实 Schema 推断可迁移的字段语义。

业务别名文件仍然是精度最高的领域知识来源，但不能要求每个新数据库都先
手工维护一套词典。这个模块只使用表名、列名、主外键和数据类型生成保守的
候选语义，供字段链接器排序；它不会生成 SQL，也不会读取题库文本。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from .models import TableInfo
from .date_semantics import is_date_column


_IDENTIFIER_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z]|\d|$)|[A-Z]?[a-z]+|\d+")
_NON_WORD_RE = re.compile(r"[^a-zA-Z0-9]+")
_NUMERIC_TYPES = ("INT", "REAL", "NUM", "DEC", "DOUBLE", "FLOAT", "MONEY")
_TEXT_TYPES = ("TEXT", "CHAR", "CLOB", "VARCHAR", "STRING")
_MEASURE_TOKENS = {
    "age", "amount", "bytes", "cost", "count", "discount", "distance", "duration",
    "hours", "length", "milliseconds", "price", "profit", "quantity", "qty", "rating",
    "rate", "revenue", "salary", "score", "stock", "tax", "total", "weight",
}


@dataclass(frozen=True)
class ProfileRule:
    table: str
    column: str
    aliases: tuple[str, ...]
    role: str
    confidence: float
    metric_function: str | None = None


def split_identifier(value: str) -> tuple[str, ...]:
    """把 snake_case、camelCase、连字符命名拆成稳定的小写词元。"""

    text = _NON_WORD_RE.sub(" ", str(value or ""))
    tokens: list[str] = []
    for part in text.split():
        tokens.extend(_IDENTIFIER_RE.findall(part) or [part])
    return tuple(token.lower() for token in tokens if token)


_TABLE_ALIASES: dict[str, tuple[str, ...]] = {
    "album": ("专辑", "唱片"),
    "artist": ("艺术家", "歌手", "艺人"),
    "customer": ("客户", "顾客"),
    "employee": ("员工", "雇员"),
    "genre": ("音乐类型", "流派", "曲风"),
    "invoice": ("发票", "账单", "订单"),
    "invoiceline": ("发票明细", "账单明细", "订单明细"),
    "mediatype": ("媒体类型", "格式"),
    "playlist": ("播放列表", "歌单"),
    "playlisttrack": ("播放列表曲目", "歌单曲目"),
    "product": ("产品", "商品"),
    "order": ("订单",),
    "orderitem": ("订单明细", "商品明细"),
    "region": ("地区", "区域"),
    "supportticket": ("工单", "售后工单"),
    "track": ("曲目", "歌曲"),
}

_TOKEN_ALIASES: dict[str, tuple[str, ...]] = {
    "address": ("地址",),
    "artist": ("艺术家", "歌手", "艺人"),
    "bytes": ("字节数", "文件大小"),
    "category": ("类别", "分类", "品类"),
    "city": ("城市",),
    "composer": ("作曲家", "作曲者"),
    "country": ("国家",),
    "customer": ("客户", "顾客"),
    "date": ("日期", "时间"),
    "department": ("部门",),
    "email": ("邮箱", "电子邮件"),
    "employee": ("员工",),
    "firstname": ("名", "名字"),
    "genre": ("音乐类型", "流派", "曲风"),
    "hours": ("小时", "时长"),
    "industry": ("行业",),
    "lastname": ("姓", "姓氏"),
    "level": ("等级",),
    "milliseconds": ("时长", "播放时长", "毫秒"),
    "name": ("名称", "名字"),
    "phone": ("电话",),
    "priority": ("优先级",),
    "quantity": ("数量", "件数", "销量"),
    "rating": ("评分", "评级"),
    "status": ("状态",),
    "stock": ("库存",),
    "title": ("标题",),
    "total": ("总额", "总金额"),
    "unitprice": ("单价", "价格"),
    "price": ("价格",),
    # 通用列名不能直接推断为销售业务；销售额需要显式的 sales_amount/
    # line_amount 等命名或人工别名。否则贷款 amount、费用 amount 会被误算成销售额。
    "revenue": ("收入",),
    "amount": ("金额",),
    "duration": ("时长",),
    "cost": ("成本", "费用"),
    "length": ("长度",),
    "rate": ("费率",),
    "profit": ("利润",),
    "weight": ("重量",),
    "distance": ("距离",),
    "discount": ("折扣",),
    "tax": ("税额",),
    "salary": ("薪资", "工资"),
    "score": ("分数",),
    "age": ("年龄",),
}

# Lexical translations, not database-specific business formulas. Compound
# identifiers keep their modifiers: replacement_cost is not just generic cost.
_ENTITY_ALIASES = {
    **_TABLE_ALIASES,
    "payment": ("付款", "支付"), "rental": ("租赁", "租借"),
    "film": ("电影", "影片"), "actor": ("演员",), "staff": ("职员", "工作人员"),
    "store": ("门店", "店铺"), "inventory": ("库存",), "language": ("语言",),
    "supplier": ("供应商",), "shipment": ("发货", "货运"), "warehouse": ("仓库",),
    "account": ("账户",), "transaction": ("交易",), "loan": ("贷款",),
    "patient": ("患者",), "appointment": ("预约",), "sensor": ("传感器",),
    "device": ("设备",), "book": ("图书",), "student": ("学生",),
    "course": ("课程",), "project": ("项目",), "ticket": ("工单",),
    "category": ("类别", "分类"), "city": ("城市",), "country": ("国家",),
    "address": ("地址",),
}
_MODIFIER_ALIASES = {
    **{key: values[:1] for key, values in _ENTITY_ALIASES.items()},
    "replacement": ("替换", "重置"), "shipping": ("配送",),
    "billing": ("账单",), "release": ("发行",), "return": ("归还",),
    "birth": ("出生",), "purchase": ("采购",), "unit": ("单位",),
}
_NAMESPACE_TOKENS = {"dbo", "public", "dim", "fact", "tbl"}


def _singular(token: str) -> str:
    # Only known vocabulary is singularized, never arbitrary physical names.
    if token.endswith("ies") and token[:-3] + "y" in _ENTITY_ALIASES:
        return token[:-3] + "y"
    if token.endswith("s") and token[:-1] in _ENTITY_ALIASES:
        return token[:-1]
    return token


def _compound_aliases(tokens: tuple[str, ...]) -> tuple[str, ...]:
    tokens = tuple(_singular(token) for token in tokens)
    if len(tokens) < 2 or len(tokens) > 4:
        return ()
    modifiers = [_MODIFIER_ALIASES.get(token) for token in tokens[:-1]]
    heads = _TOKEN_ALIASES.get(tokens[-1]) or _ENTITY_ALIASES.get(tokens[-1])
    if not heads or any(not item for item in modifiers):
        return ()
    from itertools import product
    return tuple("".join(parts) for parts in product(*modifiers, heads))

_SALES_CONTEXTS = {"订单", "订单明细", "发票", "账单", "发票明细", "销售", "商品", "商品明细"}


def _canonical(tokens: Iterable[str]) -> str:
    return "".join(tokens)


def _table_context(table: str) -> tuple[str, ...]:
    tokens = tuple(_singular(token) for token in split_identifier(table))
    while tokens and tokens[0] in _NAMESPACE_TOKENS:
        tokens = tokens[1:]
    key = _canonical(tokens)
    aliases: list[str] = list(_ENTITY_ALIASES.get(key, ()))
    if not aliases:
        aliases.extend(_compound_aliases(tokens))
    return tuple(dict.fromkeys(aliases))


def table_aliases(table: str) -> tuple[str, ...]:
    """Existing Schema-derived table names; these do not translate row values."""
    return (table, *_table_context(table))


def _is_numeric(data_type: str) -> bool:
    upper = str(data_type or "").upper()
    return any(token in upper for token in _NUMERIC_TYPES)


def _is_text(data_type: str) -> bool:
    upper = str(data_type or "").upper()
    return not upper or any(token in upper for token in _TEXT_TYPES)


def _is_measure(tokens: tuple[str, ...], data_type: str) -> bool:
    return _is_numeric(data_type) and any(token in _MEASURE_TOKENS for token in tokens)


def _default_metric_function(tokens: tuple[str, ...], primary_key: bool) -> str | None:
    if primary_key:
        return "COUNT_DISTINCT"
    if any(token in {"age", "duration", "hours", "length", "milliseconds", "price", "rate", "rating", "salary", "score"} for token in tokens):
        return "AVG"
    if any(token in {"amount", "bytes", "count", "discount", "profit", "quantity", "qty", "revenue", "stock", "tax", "total", "weight"} for token in tokens):
        return "SUM"
    return None


def _count_aliases(table: str, column: str, tokens: tuple[str, ...], primary_key: bool) -> tuple[str, ...]:
    if not primary_key:
        return ()
    subject = _canonical(tokens[:-1]) if tokens and tokens[-1] == "id" else ""
    if not subject:
        subject = _canonical(split_identifier(table))
    context = _table_context(table)
    if subject:
        aliases = list(_ENTITY_ALIASES.get(_singular(subject), ()))
        if not aliases:
            aliases.extend(context)
    else:
        aliases = list(context)
    return tuple(dict.fromkeys(f"{item}数" for item in aliases if item))


def infer_rules(tables: Iterable[TableInfo], *, infer_entity_counts: bool = True) -> tuple[ProfileRule, ...]:
    """根据 Schema 生成保守候选，不覆盖显式领域词典。"""

    result: list[ProfileRule] = []
    for table in tables:
        table_context = _table_context(table.name)
        foreign_keys = {item.from_column for item in table.foreign_keys}
        single_primary_key = sum(column.primary_key for column in table.columns) == 1
        for column in table.columns:
            tokens = split_identifier(column.name)
            if not tokens and not is_date_column(column.name, column.data_type):
                continue
            tokens = tokens or (column.name,)
            joined = _canonical(tokens)
            aliases: list[str] = []
            role = "dimension"
            confidence = 0.56
            metric_function = None

            date_column = is_date_column(column.name, column.data_type)
            numeric = _is_numeric(column.data_type)
            measure = _is_measure(tokens, column.data_type)
            id_column = joined.endswith("id") or column.primary_key or column.name in foreign_keys
            if date_column:
                aliases.extend((column.name, '日期', '时间'))
                for context in table_context:
                    aliases.append(f"{context}日期")
                role = "dimension"
                confidence = 0.66
            elif id_column:
                # 外键只是关联键，不能把订单明细中的 order_id 自动误判成订单指标。
                # 主键才有稳定的“实体数”语义；跨表去重由显式领域词典或模型契约声明。
                if infer_entity_counts:
                    aliases.extend(_count_aliases(table.name, column.name, tokens, column.primary_key and single_primary_key))
                # ID 本身不是维度；只有出现“X数”语义时才允许作为指标候选。
                if aliases:
                    role = "metric"
                    confidence = 0.62
                    metric_function = _default_metric_function(tokens, column.primary_key and single_primary_key)
            elif measure:
                role = "metric"
                confidence = 0.6
                metric_function = _default_metric_function(tokens, False)
            elif numeric:
                role = "dimension"
                confidence = 0.52
            elif _is_text(column.data_type):
                role = "dimension"
                confidence = 0.55

            if not id_column:
                aliases.extend(_compound_aliases(tokens))
                aliases.extend(_TOKEN_ALIASES.get(joined, ()))
                for token in tokens:
                    aliases.extend(_TOKEN_ALIASES.get(token, ()))
            if not id_column and tokens[-1] in {"name", "title"}:
                aliases.extend(table_context)
            if joined == "unitprice":
                aliases.extend(("单价", "价格"))
            if joined in {"total", "amount"}:
                aliases.extend(("金额", "总额", "总金额") if joined == "total" else ("金额",))
                if _SALES_CONTEXTS.intersection(table_context):
                    aliases.extend(("销售额", "收入"))
            if joined == "revenue":
                aliases.append("收入")
            if joined in {"salesamount", "lineamount", "orderamount", "invoiceamount", "transactionamount"}:
                metric_aliases = ("金额", "销售额", "收入")
                aliases.extend(metric_aliases)
                aliases.extend(f"{context}{alias}" for context in table_context for alias in metric_aliases)

            aliases = list(dict.fromkeys(alias for alias in aliases if alias and (date_column or alias != column.name)))
            if aliases:
                result.append(ProfileRule(table.name, column.name, tuple(aliases), role, confidence, metric_function))
    return tuple(result)


def suggested_alias(table: str, column: str) -> str:
    """为审计/澄清提供稳定的人类可读列名。"""

    rules = infer_rules((TableInfo(table, (), None, ()),))
    for rule in rules:
        if rule.column == column and rule.aliases:
            return rule.aliases[0]
    return column

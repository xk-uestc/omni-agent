"""Schema-grounded, local business concept graph for colloquial NL2SQL inputs.

The graph translates phrases, never invents columns, formulas or filters.  A
concept -> metric -> real column path is required for every edit.  Ambiguous
paths produce a clarification instead.  Quoted strings, native identifiers and
known database values remain byte-for-byte unchanged.  Building graph snapshots
is bounded and cached; normalization does not access SQLite, a network or an LLM.
"""
from __future__ import annotations

import re
import threading
from collections import OrderedDict
from dataclasses import asdict, dataclass
from functools import lru_cache
from typing import Iterable, Mapping

from .models import TableInfo
from .schema import normalize_text
from .schema_profile import infer_rules, split_identifier, table_aliases
from .t2s import to_simplified


@dataclass(frozen=True)
class SemanticRewrite:
    source_text: str
    replacement: str
    start: int
    end: int
    concept: str
    table: str
    column: str
    confidence: float
    reason: str
    metric_id: str | None = None
    source_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class SemanticAmbiguity:
    source_text: str
    start: int
    end: int
    concept: str
    candidates: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class SemanticNormalization:
    original_question: str
    normalized_question: str
    rewrites: tuple[SemanticRewrite, ...] = ()
    ambiguities: tuple[SemanticAmbiguity, ...] = ()
    clarification: str | None = None
    skipped_reason: str | None = None

    @property
    def changed(self) -> bool:
        return self.original_question != self.normalized_question

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class _Binding:
    table: str
    column: str
    aliases: tuple[str, ...]
    replacement: str
    concepts: tuple[str, ...]
    metric_id: str | None = None
    source_fields: tuple[str, ...] = ()


# These express business concepts, not a specific database's table names.
_PHRASES = (
    (r"(?:卖了|卖出|卖出去|售出)(?:了)?多少(?:钱|金额)|卖了多少钱|进账(?:了)?多少(?:钱)?", "revenue"),
    (r"(?:赚了|赚到|挣了|挣到|盈利了|赚)(?:了)?多少(?:钱)?|挣多少钱", "profit"),
    (r"(?:花了|花掉|花出去|支出)(?:了)?多少(?:钱)?|花费了多少钱", "spend"),
    (r"(?<![A-Za-z0-9_])(?:(?:贷款|借款|存款|投资|理财|银行|债券|销售|卖货|进货|拿货|采购|生产))?(?:本金|本钱)(?![A-Za-z0-9_])", "principal_or_cost"),
    (r"(?:进货|采购|买进|买入)(?:花了|花费了|用了|花|用了多少)?(?:多少(?:钱)?|多少钱)", "purchase"),
    (r"(?:运货|送货|配送|运输)(?:花了|花费了|用了|花)?(?:多少(?:钱)?|多少钱)", "shipping"),
    (r"(?:工资|薪水|薪酬)(?:发了|支付了|发|给了)?(?:多少(?:钱)?|多少钱)", "salary"),
    (r"(?:卖了|卖出|售出)(?:了)?多少(?:件|个)|卖出去多少件", "quantity"),
    (r"(?:页面|网页)(?:被)?(?:看了|浏览了|打开了)多少(?:次|回)|(?:看了|浏览了)多少(?:次|回)(?:网页|页面)", "page_views"),
    (r"(?:网站|站点)(?:被)?(?:访问了|访问|来了)多少(?:次|回)|来了多少次(?:网站|站点)", "visits"),
    (r"(?:有|下了|产生了)多少(?:笔|个|张)订单|订单有几(?:笔|个|张)", "order_count"),
    (r"(?:有|来了)多少(?:个|位)客户|客户有几(?:个|位)", "customer_count"),
    (r"(?:客人|顾客)数", "customer_count"),
    (r"(?<![A-Za-z0-9_])PV(?![A-Za-z0-9_])|页面被看了几次|网页被看了几次", "page_views"),
    (r"(?:卖了|卖出|售出)几(?:件|个)", "quantity"),
    (r"营收入|营收|营业额|成交金额|营业收入", "revenue"),
    (r"赚头|获利|盈利金额|(?:卖货|销售)?赚的钱|(?:卖货|销售)赚(?:了)?多少(?:钱)?", "profit"),
    (r"开销|花销|(?:花|花费)(?:了)?多少(?:钱)?|花钱多少", "spend"),
    (r"(?:卖货|销售|生产|投入|拿货|进货)成本", "cost"),
    (r"(?:进货|拿货|采购)(?:钱|花的钱)", "procurement_spend"),
    (r"销售数量|售出数量", "quantity"),
    (r"(?:采购|进货|买)(?:了)?多少(?:件|个)|(?:采购|进货|买)(?:了)?几(?:件|个)", "purchase_quantity"),
    (r"(?:实收|到账|到帐|回款|收回)(?:了)?多少(?:钱|金额)?|(?:收了|收到|收回了)多少钱", "collected"),
    (r"(?:发了|支付了|发)多少(?:工资|薪水|薪酬)", "salary"),
    (r"奖金(?:发了|支付了|发)?多少(?:钱)?|(?:发了|支付了|发)多少奖金", "bonus"),
    (r"(?:物流|运输|运货|配送|送货)(?:花|花费|花钱|支出)(?:了)?多少(?:钱)?", "shipping"),
    (r"(?:运营|报销)(?:花|花费|花钱|支出)(?:了)?多少(?:钱)?", "expense"),
    (r"(?:工资|薪水|薪酬)(?:发|支付)(?:了)?多少(?:钱)?", "salary"),
    (r"(?:实收|实际收|实际收到|实际收回)(?:了)?多少(?:钱|金额)?", "collected"),
    (r"(?:网站|站点)来了多少次(?:访问)?", "visits"),
    (r"(?:物流|运输|运货|配送|送货)(?:花|支付)(?:了)?的钱", "shipping"),
    (r"(?:发出去|发出|发放|支付)(?:了)?的?(?:工资|薪水|薪酬)", "salary"),
    (r"(?:回款|实收|到账|到帐)(?:到账|到帐)?(?:金额|钱)", "collected"),
    (r"(?:配送|物流|运输|运货)(?:成本|费用)", "shipping"),
    (r"(?:退回来|退回来的|退回的|退还的)(?:钱|金额|款项)", "refund"),
    (r"(?:总)?成本(?:是|为|有)?多少(?:钱)?", "cost"),
    (r"(?:总)?利润(?:是|为|有)?多少(?:钱)?", "profit"),
    (r"(?:基本工资|底薪)(?:(?:发|支付)(?:了)?多少(?:钱)?)?", "base_salary"),
    (r"(?:净利润|利润净额|净利|纯利润|纯利|税后利润)(?!率|比例|百分比)(?:是|为|有)?(?:多少(?:钱)?)?", "net_profit"),
    (r"(?:毛利润|毛利)(?!率|比例|百分比)(?:是|为|有)?(?:多少(?:钱)?)?", "gross_profit"),
    (r"税前利润(?!率|比例|百分比)(?:是|为|有)?(?:多少(?:钱)?)?", "pre_tax_profit"),
    (r"(?:目标|计划|预算)(?:营收|销售额|营业额|收入)(?:是|为|有)?(?:多少(?:钱)?)?", "revenue_target"),
    (r"(?:归因|营销归因)(?:营收|销售额|收入)(?:是|为|有)?(?:多少(?:钱)?)?", "revenue_attributed"),
)
_PHRASE_RE = tuple((re.compile(pattern, re.IGNORECASE), concept) for pattern, concept in _PHRASES)
_FINANCE_CUE = re.compile(r"贷款|借款|还款|借贷|利息|存款|定存|理财|金融|投资|证券|债券|银行")
_COST_CUE = re.compile(r"销售|卖|商品|货品|产品|进货|采购|成本|生产|制造|原料|毛利")
_COST_SCOPE_CUE = re.compile(r"销售|卖货?|商品|货品|产品|订单|进货|采购|投入|原料|材料|人工|生产|制造|运营|经营|报销|运费|物流|运输|配送")
_PROFIT_SCOPE_CUE = re.compile(r"销售|卖货?|经营|业务|毛利|净利|纯利|税前|税后|投资|贷款")
_UNIT_CUE = re.compile(r"单件|每件|单个|单位|单价")
_TOPIC_SWITCH = re.compile(r"换(?:一个|个)?(?:主题|话题|问题)|改查|改看|另一个(?:主题|问题)|^\s*(?:新问题|重新查询)")
_QUOTE_RE = re.compile(r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|`[^`]*`|“[^”]*”|‘[^’]*’|「[^」]*」|『[^』]*』")
_LITERAL_CUE = re.compile(r"(?:名叫|叫做|名称(?:为|是)|名字(?:为|是)|内容(?:为|是)|包含|含有|等于|值为|取值(?:为|是))\s*[=:：]?\s*$")
_NUMERIC_TYPES = re.compile(r"INT|REAL|NUM|DEC|DOUBLE|FLOAT|MONEY", re.IGNORECASE)


def needs_normalization(question: str) -> bool:
    """Lexical fast path; callers can skip schema loading on old inputs."""
    if len(question) > 12_000:
        return False
    return _needs_normalization_cached(question)


@lru_cache(maxsize=1024)
def _needs_normalization_cached(question: str) -> bool:
    text = to_simplified(question)
    return any(pattern.search(text) for pattern, _ in _PHRASE_RE)


def _concepts(table: str, column: str, aliases: tuple[str, ...]) -> tuple[str, ...]:
    tokens = set(split_identifier(column))
    native = normalize_text(column)
    labels = "|".join(normalize_text(alias) for alias in aliases)
    exact_labels = {normalize_text(alias) for alias in aliases}
    result = []
    unit = ("unit" in tokens and "cost" in tokens) or "单位成本" in labels or "单件成本" in labels
    if "principal" in tokens or native in {"本金", "借款本金", "贷款本金", "投资本金"} or "本金" in labels:
        result.append("principal")
    if ("cost" in tokens or "成本" in labels or "成本" in native) and not tokens.intersection({"shipping", "transport", "freight"}):
        result.append("unit_cost" if unit else "cost")
    if ("profit" in tokens or "利润" in labels or "毛利" in labels or "净利" in labels) and not tokens.intersection({"rate", "margin", "ratio"}) and not any(word in labels for word in ("利润率", "毛利率", "净利率")):
        profit_kind = "profit_target" if tokens.intersection({"target", "goal", "budget", "forecast"}) or any(word in labels for word in ("目标利润", "计划利润", "预测利润", "预计利润")) else "profit"
        result.append(profit_kind)
        if profit_kind == "profit":
            declared_net = any(word in labels for word in ("净利润", "净利", "净收益", "纯利润", "税后利润"))
            declared_gross = any(word in labels for word in ("毛利", "毛利润"))
            if declared_net and not declared_gross or (not declared_gross and "net" in tokens):
                result.append("net_profit")
            if declared_gross and not declared_net or (not declared_net and "gross" in tokens):
                result.append("gross_profit")
            if any(word in labels for word in ("税前利润", "税前利得")) or "pretax" in tokens:
                result.append("pre_tax_profit")
    if tokens.intersection({"revenue", "turnover"}) or native in {"salesamount", "sales_amount", "line_amount", "营业额", "销售额"} or any(word in labels for word in ("销售额", "销售金额", "营业额", "销售收入", "营业收入", "目标收入", "计划收入", "预算收入", "归因收入", "每件收入", "每单收入", "单位收入")):
        if tokens.intersection({"target", "goal", "budget", "forecast"}) or any(word in labels for word in ("目标", "计划", "预算", "预测")):
            result.append("revenue_target")
        elif tokens.intersection({"attributed", "attribution"}) or "归因" in labels:
            result.append("revenue_attributed")
        elif tokens.intersection({"price", "rate", "ratio", "percent"}) or any(word in labels for word in ("收入率", "收入比例", "收入百分比")):
            result.append("revenue_ratio")
        elif "unit" in tokens or "per" in tokens or any(word in labels for word in ("每件", "每单", "单均", "单位收入", "平均收入")):
            result.append("revenue_unit")
        else:
            result.append("revenue")
    if any(word in labels for word in ("采购金额", "采购额", "进货金额")) or (tokens.intersection({"purchase", "procurement"}) and tokens.intersection({"amount", "cost", "total"})):
        result.append("purchase")
    if any(word in labels for word in ("运费", "物流费用", "运输费用")) or (tokens.intersection({"shipping", "freight", "transport"}) and tokens.intersection({"cost", "amount", "fee"})):
        result.append("shipping")
    if (tokens.intersection({"refund", "refunded"}) or native in {"refund_amount", "return_amount"}
            or any(word in labels for word in ("退款金额", "退回金额", "退款总额", "退还金额"))):
        if not tokens.intersection({"count", "quantity", "qty", "number"}) and not any(
                word in labels for word in ("退款单数", "退货单数", "退款笔数", "退货数量")):
            result.append("refund")
    if "expense" in tokens or any(word in labels for word in ("费用金额", "运营费用", "报销金额")):
        result.append("expense")
    if tokens.intersection({"salary", "payroll", "wage"}) or exact_labels.intersection({"工资", "薪酬", "薪水", "底薪"}) or any(word in labels for word in ("工资总额", "薪酬总额", "工资金额", "基本工资", "基础工资")):
        result.append("salary")
        if tokens.intersection({"base", "basic"}) or any(word in labels for word in ("基本工资", "底薪", "基础工资")):
            result.append("base_salary")
    if "bonus" in tokens or "奖金" in exact_labels or any(word in labels for word in ("奖金金额", "奖金总额")):
        result.append("bonus")
    if exact_labels.intersection({"回款", "实收", "到账金额", "到帐金额"}) or any(word in labels for word in ("回款金额", "实收金额", "已收款金额")) or tokens.intersection({"collected", "collection", "receipt"}) and tokens.intersection({"amount", "total"}):
        result.append("collected")
    if any(word in labels for word in ("采购数量", "采购件数", "进货数量")) or tokens.intersection({"purchase", "procurement"}) and tokens.intersection({"quantity", "qty"}):
        result.append("purchase_quantity")
    if native in {"page_views", "pageviews", "网页浏览量", "页面浏览量"} or "网页浏览量" in labels or "页面浏览量" in labels:
        result.append("page_views")
    if native in {"visits", "网站访问量", "访问次数"} or any(word in labels for word in ("网站访问量", "访问次数")):
        result.append("visits")
    nonsales_quantity = any(word in labels for word in ("库存", "采购数量", "采购件数", "进货数量", "在途")) or bool(set(split_identifier(table)).intersection({"purchase", "purchases", "procurement", "inventory", "stock", "stocks"}))
    if not nonsales_quantity and ("销量" in labels or "销售数量" in labels or (tokens.intersection({"quantity", "qty"}) and ("销售" in labels or any(word in normalize_text(table) for word in ("order", "sale"))))):
        result.append("quantity")
    if any(word in labels for word in ("订单数", "订单数量", "订单笔数")):
        result.append("order_count")
    if any(word in labels for word in ("客户数", "客户数量", "客户人数", "顾客数")):
        result.append("customer_count")
    return tuple(dict.fromkeys(result))


class SemanticGraph:
    """Small concept graph with at most eight schema snapshots by default.

    ``linker_rules`` should be ``SchemaLinker.rules_for(tables)`` so explicit
    business annotations take precedence.  ``protected_values`` are actual
    database values already available in the value index.  ``history_hint`` is
    an optional trusted previous plan: table, metric_column and question.
    """

    def __init__(self, metric_catalog=None, *, max_snapshots: int = 8):
        self.metric_catalog = metric_catalog
        self.max_snapshots = max(1, min(int(max_snapshots), 32))
        self._cache: OrderedDict = OrderedDict()
        self._lock = threading.RLock()

    @property
    def snapshot_count(self) -> int:
        with self._lock:
            return len(self._cache)

    needs_normalization = staticmethod(needs_normalization)

    def _snapshot(self, tables, rules):
        signature = (tuple((t.name, tuple((c.name, c.data_type, c.primary_key) for c in t.columns),
                            tuple((fk.table, fk.from_column, fk.to_column) for fk in t.foreign_keys)) for t in tables),
                     tuple((r.table, r.column, tuple(r.aliases), r.role, getattr(r, "metric_function", None)) for r in rules),
                     getattr(self.metric_catalog, "digest", None))
        with self._lock:
            if signature in self._cache:
                self._cache.move_to_end(signature)
                return self._cache[signature]
        available = {(t.name, c.name): c for t in tables for c in t.columns}
        by_pair = {}
        explicit_pairs = {(r.table, r.column) for r in rules}
        active_rules = (*rules, *(r for r in infer_rules(tables) if (r.table, r.column) not in explicit_pairs))
        for rule in active_rules:
            pair = (rule.table, rule.column)
            if pair in available and rule.role == "metric" and _NUMERIC_TYPES.search(available[pair].data_type):
                aliases = tuple(dict.fromkeys(rule.aliases))
                by_pair[pair] = _Binding(*pair, aliases, aliases[0] if aliases else rule.column, _concepts(*pair, aliases))
        # Native numeric fields are graph nodes even without a curated alias.
        for pair, column in available.items():
            identifier = bool(split_identifier(column.name) and split_identifier(column.name)[-1] == "id")
            foreign_key = any(fk.from_column == column.name for t in tables if t.name == pair[0] for fk in t.foreign_keys)
            if pair not in by_pair and _NUMERIC_TYPES.search(column.data_type) and not column.primary_key and not identifier and not foreign_key:
                aliases = (column.name,)
                by_pair[pair] = _Binding(*pair, aliases, column.name, _concepts(*pair, aliases))
        # A catalog metric may count a TEXT identifier; that is an explicit
        # semantic contract rather than a guessed numeric measure.
        extra_catalog_bindings = []
        if self.metric_catalog:
            catalog_by_pair = {}
            for metric_id, metric in self.metric_catalog.sources.items():
                pair = (metric.table, metric.column)
                if pair not in available:
                    continue
                previous = by_pair.get(pair)
                aliases = tuple(dict.fromkeys((metric.label, *self.metric_catalog.aliases.get(metric_id, ()), *(previous.aliases if previous else ()))))
                replacement = self.metric_catalog.query_aliases.get(metric_id) or metric.label
                declared_aliases = (metric.label, *self.metric_catalog.aliases.get(metric_id, ()))
                catalog_by_pair.setdefault(pair, []).append(_Binding(*pair, aliases, replacement, _concepts(*pair, declared_aliases), metric_id))
            for pair, catalog_bindings in catalog_by_pair.items():
                if len(catalog_bindings) == 1:
                    by_pair[pair] = catalog_bindings[0]
                else:
                    by_pair.pop(pair, None)
                    extra_catalog_bindings.extend(catalog_bindings)
        # Rule-annotated counts of TEXT IDs are also valid. A numeric check
        # protects other accidental semantic assignments to text dimensions.
        for rule in active_rules:
            pair = (rule.table, rule.column)
            count_annotated = str(getattr(rule, "metric_function", "")).upper() in {"COUNT", "COUNT_DISTINCT"} or (pair in available and available[pair].primary_key and any(c in _concepts(*pair, tuple(rule.aliases)) for c in ("order_count", "customer_count")))
            if pair not in by_pair and pair in available and rule.role == "metric" and count_annotated:
                aliases = tuple(rule.aliases)
                by_pair[pair] = _Binding(*pair, aliases, aliases[0] if aliases else rule.column, _concepts(*pair, aliases))
        derived_bindings = []
        if self.metric_catalog:
            for metric_id, metric in self.metric_catalog.derived.items():
                sources, _ = self.metric_catalog.dependencies(metric_id)
                dependencies = [self.metric_catalog.sources[source] for source in sources]
                if not dependencies or not all((source.table, source.column) in available for source in dependencies):
                    continue
                aliases = tuple(dict.fromkeys((metric.label, *self.metric_catalog.aliases.get(metric_id, ()))))
                # The main real field locates the graph node, but its native
                # name must not assign its revenue concept to a derived cost.
                source = dependencies[0]
                derived_bindings.append(_Binding(source.table, source.column, aliases, metric.label,
                    _concepts(source.table, metric_id, aliases), metric_id,
                    tuple(f"{item.table}.{item.column}" for item in dependencies)))
        result = tuple(binding for binding in (*by_pair.values(), *extra_catalog_bindings, *derived_bindings) if binding.concepts)
        with self._lock:
            self._cache[signature] = result
            self._cache.move_to_end(signature)
            while len(self._cache) > self.max_snapshots:
                self._cache.popitem(last=False)
        return result

    @staticmethod
    def _protected(question, tables, rules, values, *, include_identifiers=True):
        spans = [m.span() for m in _QUOTE_RE.finditer(question)]
        names = ({c.name for t in tables for c in t.columns} | {t.name for t in tables}) if include_identifiers else set()
        # Exact annotated phrases already have a meaning; do not reinterpret
        # "本金余额" or a field specifically annotated as "赚了多少".
        names.update(alias for rule in rules for alias in rule.aliases)
        folded_question = question.casefold()
        for name in names:
            if not name:
                continue
            name = to_simplified(name)
            if name.casefold() not in folded_question:
                continue
            boundary = re.escape(name)
            if re.fullmatch(r"[A-Za-z0-9_]+", name):
                boundary = r"(?<![A-Za-z0-9_])" + boundary + r"(?![A-Za-z0-9_])"
            spans.extend(m.span() for m in re.finditer(boundary, question, re.IGNORECASE))
        for value in values:
            if value:
                simplified = to_simplified(str(value))
                if simplified in question:
                    spans.extend(m.span() for m in re.finditer(re.escape(simplified), question))
        return spans

    @staticmethod
    def _metric_scope(binding):
        """Infer declared whole/component relations, independently of names.

        Basic salary is a component even when an alias says "基本工资总额";
        gross/net profit are alternative definitions and never components.
        """
        if not set(binding.concepts).intersection({"salary", "cost", "expense"}):
            return "unspecified"
        tokens = set(split_identifier(binding.column))
        labels = "|".join(normalize_text(alias) for alias in binding.aliases)
        if ("salary" in binding.concepts and (tokens.intersection({"base", "basic"}) or
                any(word in labels for word in ("基本工资", "基础工资", "底薪", "基薪")))):
            return "component"
        if "cost" in binding.concepts and (tokens.intersection({"material", "labor", "packaging"}) or
                any(word in labels for word in ("原料成本", "材料成本", "人工成本", "包装成本"))):
            return "component"
        if tokens.intersection({"total", "overall"}) or any(word in labels for word in ("总额", "总成本", "总工资", "总薪酬", "整体成本", "全部费用")):
            return "whole"
        return "unspecified"

    @staticmethod
    def _select(candidates, question, tables, history_hint):
        if len(candidates) <= 1:
            return candidates
        text = normalize_text(to_simplified(question))
        if re.search(r"基本工资|基础工资|底薪|基薪", text):
            basic = [binding for binding in candidates if "base_salary" in binding.concepts]
            if basic:
                candidates = basic
        owner_words = {"sales": ("销售", "卖货"), "sale": ("销售", "卖货"), "purchase": ("采购", "进货", "拿货"),
                       "procurement": ("采购", "进货", "拿货"), "shipment": ("物流", "运输", "配送"),
                       "shipping": ("物流", "运输", "配送"), "expense": ("运营", "报销"),
                       "expenses": ("运营", "报销"), "payroll": ("薪酬", "工资"), "loan": ("贷款", "借款"),
                       "loans": ("贷款", "借款")}
        owners = {t.name: (*table_aliases(t.name), *(term for token in split_identifier(t.name)
                   for term in owner_words.get(token, ()))) for t in tables}
        scores = {}
        for binding in candidates:
            owner_score = max((len(normalize_text(alias)) + 8 for alias in owners[binding.table]
                               if len(normalize_text(alias)) >= 2 and normalize_text(alias) in text), default=0)
            # Specific metric modifiers (人工成本/原料成本/毛利) resolve same
            # concept nodes without letting generic "成本" defeat ambiguity.
            alias_score = max((len(normalize_text(alias)) for alias in binding.aliases
                               if len(normalize_text(alias)) >= 3 and normalize_text(alias) in text), default=0)
            scores[binding] = owner_score * 10 + alias_score * 3
        top = max(scores.values())
        chosen = [b for b in candidates if scores[b] == top]
        if len(chosen) == 1 and top:
            return chosen
        whole = [b for b in chosen if SemanticGraph._metric_scope(b) == "whole"]
        if len(whole) == 1 and all(b == whole[0] or SemanticGraph._metric_scope(b) == "component" for b in chosen):
            # One explicitly declared total and its explicitly recognized
            # components form a semantic relation, not a score tie-break.
            return whole
        if not top and history_hint and not _TOPIC_SWITCH.search(text):
            table = history_hint.get("table") or history_hint.get("metric_table")
            column = history_hint.get("metric_column")
            contextual = [b for b in chosen if b.table == table]
            if column and len(contextual) > 1:
                specific = [b for b in contextual if b.column == column]
                if specific:
                    return specific
            if contextual:
                return contextual
        return chosen

    def normalize(self, question: str, tables: Iterable[TableInfo], linker_rules=(),
                  history_hint: Mapping | None = None, *, protected_values: Iterable[str] = ()) -> SemanticNormalization:
        tables, rules = tuple(tables), tuple(linker_rules)
        if len(question) > 12_000 or sum(len(t.columns) for t in tables) > 6_000 or len(rules) > 12_000:
            return SemanticNormalization(question, question, skipped_reason="semantic_graph_input_budget")
        values = []
        for index, value in enumerate(protected_values):
            if index >= 20_000:
                return SemanticNormalization(question, question, skipped_reason="semantic_graph_value_budget")
            values.append(value)
        text = to_simplified(question)
        if len(text) != len(question):
            return SemanticNormalization(question, question, skipped_reason="semantic_graph_span_alignment")
        matches = [(m.start(), m.end(), concept) for pattern, concept in _PHRASE_RE for m in pattern.finditer(text)]
        if not matches:
            return SemanticNormalization(question, question)
        bindings = self._snapshot(tables, rules)
        protected = self._protected(text, tables, rules, values)
        literal_protected = self._protected(text, tables, (), values, include_identifiers=False)
        rewrites, ambiguities, occupied = [], [], []
        hint = history_hint if isinstance(history_hint, Mapping) and not _TOPIC_SWITCH.search(text) else {}
        for start, end, concept in sorted(matches, key=lambda item: (item[0], -(item[1] - item[0]))):
            source = question[start:end]
            selected_binding = None
            selection = hint.get("selected_metric")
            if (isinstance(selection, Mapping) and hint.get("selection_span") == start
                    and isinstance(selection.get("table"), str)
                    and isinstance(selection.get("column"), str)
                    and isinstance(selection.get("label"), str)):
                label = normalize_text(selection["label"])
                candidates = [binding for binding in bindings
                    if (binding.table, binding.column) == (selection["table"], selection["column"])
                    and (not selection.get("metric_id") or binding.metric_id == selection["metric_id"])
                    and label in {normalize_text(binding.replacement), *(normalize_text(alias) for alias in binding.aliases)}]
                if len(candidates) == 1:
                    selected_binding = candidates[0]
            if start > 0 and text[start - 1] in {".", "．"}:
                continue
            overlap = [(a, b) for a, b in protected if start < b and end > a]
            # A short schema alias such as "工资" or "金额" is evidence
            # inside a complete business phrase, not a reason to ignore its
            # verb. An exact annotation or an enclosing native identifier is
            # already canonical and stays unchanged. Literal values always
            # win, including ones coinciding with a financial word.
            partial_schema_alias = (overlap and all(start <= a and b <= end and (a, b) != (start, end) for a, b in overlap)
                and not any(start < b and end > a for a, b in literal_protected))
            # Bare natural-language "本金" is contextual even if a finance
            # table also declares that alias. Explicit per-item business
            # language can select a real unit-cost field; qualified/quoted
            # financial identifiers and actual values remain protected.
            unit_principal_context = (concept == "principal_or_cost" and text[start:end] in {"本金", "本钱"}
                and overlap and all((a, b) == (start, end) for a, b in overlap)
                and bool(_UNIT_CUE.search(text)) and bool(_COST_CUE.search(text))
                and not _FINANCE_CUE.search(text) and (start == 0 or text[start - 1] not in {".", "．"})
                and any("unit_cost" in b.concepts for b in bindings)
                and not any(start < b and end > a for a, b in literal_protected))
            if (overlap and not partial_schema_alias and not unit_principal_context) or any(start < b and end > a for a, b in occupied):
                continue
            if _LITERAL_CUE.search(text[max(0, start - 24):start]):
                continue
            occupied.append((start, end))
            surrounding = text[max(0, start - 24):start] + text[end:min(len(text), end + 24)]
            if (selected_binding is None and concept == "cost" and "成本" in source
                    and not _COST_SCOPE_CUE.search(source + surrounding)):
                candidates = tuple(dict.fromkeys(
                    f"{binding.table}.{binding.column}（{binding.replacement}）"
                    for binding in bindings
                    if set(binding.concepts).intersection({"cost", "unit_cost", "purchase", "expense", "shipping"})
                ))
                ambiguities.append(SemanticAmbiguity(source, start, end, concept, candidates,
                    "成本未说明销售、采购、运营等业务口径，需要确认统计范围"))
                continue
            if (selected_binding is None and concept == "profit" and "利润" in source
                    and not _PROFIT_SCOPE_CUE.search(source + surrounding)):
                candidates = tuple(dict.fromkeys(
                    f"{binding.table}.{binding.column}（{binding.replacement}）"
                    for binding in bindings
                    if set(binding.concepts).intersection({"profit", "gross_profit", "net_profit", "pre_tax_profit"})
                ))
                ambiguities.append(SemanticAmbiguity(source, start, end, concept, candidates,
                    "利润未说明毛利、净利或其他利润口径，需要确认统计范围"))
                continue
            concepts = set(selected_binding.concepts) if selected_binding else {concept}
            reason = ("用户在当前澄清中明确选择的 Schema 指标" if selected_binding
                      else "真实 Schema 的业务概念与字段路径")
            if concept == "principal_or_cost":
                principal_available = any("principal" in binding.concepts for binding in bindings)
                cost_available = any(set(binding.concepts).intersection({"cost", "unit_cost"}) for binding in bindings)
                explicit_business_context = bool(_COST_CUE.search(text))
                explicit_financial_context = bool(_FINANCE_CUE.search(text[max(0, start - 16):min(len(text), end + 16)]))
                if principal_available and cost_available and not explicit_financial_context and not explicit_business_context:
                    concepts = {"principal", "cost"}
                    reason = "金融本金与业务成本均存在，需要根据明确上下文确定口径"
                elif principal_available and (explicit_financial_context or not explicit_business_context or not cost_available):
                    # An unannotated real principal column can be normalized
                    # to its native name; it must never be treated as cost.
                    concepts = {"principal"}
                    reason = "本金绑定实际金融本金字段，保留金融含义"
                elif re.search(r"(?:本金|本钱).{0,4}(?:不是|不代表|不等于)|(?:不要|不能|别).{0,5}(?:本金|本钱).{0,8}成本", text[max(0, start - 12):min(len(text), end + 16)]):
                    ambiguities.append(SemanticAmbiguity(source, start, end, concept, (), "用户明确排除了成本解释，需要提供真正本金字段"))
                    continue
                elif _FINANCE_CUE.search(text[max(0, start - 16):min(len(text), end + 16)]):
                    ambiguities.append(SemanticAmbiguity(source, start, end, concept, (), "金融语境的本金没有对应字段，不能当作成本"))
                    continue
                else:
                    context = text + to_simplified(str(hint.get("question", "")))
                    owner = str(hint.get("table", ""))
                    schema_cost_context = any("cost" in binding.concepts and (
                        any(cue in "|".join(table_aliases(binding.table))
                            for cue in ("销售", "商品", "订单", "产品", "生产"))
                        or set(split_identifier(binding.table)).intersection(
                            {"sale", "sales", "order", "orders", "product", "products", "production", "manufacturing"})
                        or any(other.table == binding.table and "revenue" in other.concepts for other in bindings)
                    ) for binding in bindings)
                    scoped_request = bool(re.search(r"(?:19|20)\d{2}|各|每个|每一|按|地区|区域|渠道|品类|月份|季度", text))
                    if (not _COST_CUE.search(context)
                            and not _COST_CUE.search("|".join(table_aliases(owner)))
                            and not (schema_cost_context and scoped_request)):
                        ambiguities.append(SemanticAmbiguity(source, start, end, concept, (), "本金可能是金融本金或业务成本，需要确认业务含义"))
                        continue
                    concepts = {"unit_cost"} if _UNIT_CUE.search(text) else {"cost"}
                    reason = "商品或销售语境的口语本金绑定成本字段"
            elif concept == "spend":
                concepts = {"cost", "expense", "purchase", "shipping", "salary"}
                vicinity = text[max(0, start - 18):min(len(text), end + 8)]
                for cue, narrowed in ((r"进货|采购|买进|买入|供应商", "purchase"), (r"运输|运货|运费|物流|配送|发货", "shipping"),
                                      (r"工资|薪酬|薪水|员工", "salary"), (r"报销|运营|经营|费用", "expense")):
                    if re.search(cue, vicinity):
                        concepts = {narrowed}
                        break
                if _UNIT_CUE.search(vicinity):
                    concepts = {"unit_cost"}
            elif concept == "procurement_spend":
                concepts = {"purchase"} if any("purchase" in binding.concepts for binding in bindings) else {"cost"}
            elif concept == "purchase" and not any("purchase" in binding.concepts for binding in bindings):
                # Some merchant datasets declare only the purchase cost, not
                # a distinct procurement ledger. A real cost binding is still
                # required; neither unit cost nor an arbitrary amount qualifies.
                concepts = {"cost"}
            candidates = [binding for binding in bindings if concepts.intersection(binding.concepts)]
            if selected_binding is not None:
                candidates = [binding for binding in candidates if binding == selected_binding]
            selected = self._select(candidates, text, tables, hint)
            if len(selected) != 1:
                if concept == "principal_or_cost" and not selected and any("unit_cost" in b.concepts for b in bindings):
                    message = "只有单件成本字段；请确认询问单件成本还是订单总成本"
                elif selected:
                    message = "同一个口语概念对应多个真实指标，请确认具体口径"
                else:
                    message = "当前 Schema 没有该口语概念的可靠字段绑定"
                ambiguities.append(SemanticAmbiguity(source, start, end, concept,
                                  tuple(f"{b.table}.{b.column}（{b.replacement}）" for b in selected[:8]), message))
                continue
            binding = selected[0]
            # If another field shares this alias, qualify the real identifier
            # rather than sending an ambiguous generic alias to the linker.
            replacement = binding.replacement
            if any(other != binding and replacement in other.aliases for other in bindings):
                if binding.source_fields:
                    ambiguities.append(SemanticAmbiguity(source, start, end, concept,
                        tuple(f"{b.table}.{b.column}（{b.replacement}）" for b in bindings if replacement in b.aliases),
                        "派生指标与其他字段共用别名，需要确认指标口径"))
                    continue
                replacement = f"{binding.table}.{binding.column}"
            if question[:start].rstrip().endswith(binding.replacement):
                replacement = "多少"
            rewrites.append(SemanticRewrite(source, replacement, start, end, concept,
                            binding.table, binding.column, 0.95, reason, binding.metric_id,
                            binding.source_fields or (f"{binding.table}.{binding.column}",)))
        normalized = question
        for rewrite in sorted(rewrites, key=lambda item: item.start, reverse=True):
            normalized = normalized[:rewrite.start] + rewrite.replacement + normalized[rewrite.end:]
        clarification = None
        if ambiguities:
            first = ambiguities[0]
            choices = "、".join(first.candidates)
            clarification = f"“{first.source_text}”的含义需要确认：{first.reason}。" + (f"可选字段：{choices}。" if choices else "")
        return SemanticNormalization(question, normalized, tuple(rewrites), tuple(ambiguities), clarification)

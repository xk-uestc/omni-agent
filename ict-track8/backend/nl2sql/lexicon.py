"""问数语义词法层：中文数值、时间表达、比较阈值、Top-N 与否定词。

设计原则（对应赛题中级任务 1/3）：
- 所有解析在 ``normalize_text`` 之后的文本上进行，并返回被消费的原文片段
  (``span``)，供规划器做"覆盖率守卫"——未被任何槽位消费的时间词、数字、
  否定词会触发澄清，而不是被静默丢弃。
- 时间统一输出半开区间 ``[start, end)``（TIMEX3 风格的归一化），相对时间
  必须依赖显式参考日期，并把参考日期写入假设，保证可审计、可复现。
- 这里没有任何题目级特判：规则只描述语言现象（季度、区间、单位、极性）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta

_CN_DIGITS = {"零": 0, "〇": 0, "○": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_CN_UNITS = {"十": 10, "百": 100, "千": 1000, "万": 10_000, "亿": 100_000_000}
_CN_NUM = "零〇○一二两三四五六七八九十百千万亿"


def cn_to_int(text: str) -> int | None:
    """把 "三" "十二" "二十" "一万五千" 等中文整数转成 int；无法解析返回 None。"""

    if not text:
        return None
    if text.isdigit():
        return int(text)
    if any(ch not in _CN_DIGITS and ch not in _CN_UNITS for ch in text):
        return None
    total, section, number = 0, 0, 0
    for ch in text:
        if ch in _CN_DIGITS:
            number = _CN_DIGITS[ch]
        else:
            unit = _CN_UNITS[ch]
            if unit >= 10_000:
                section = (section + number) * unit
                total += section
                section = 0
            else:
                section += (number or 1) * unit
            number = 0
    return total + section + number


def _year_digits_cn(text: str) -> str:
    """"二〇二五年" -> "2025年"：只转换紧邻"年"的 4 位中文数字年份。"""

    def repl(match: re.Match[str]) -> str:
        digits = "".join(str(_CN_DIGITS[ch]) for ch in match.group(1))
        return digits + "年"

    return re.sub(r"([零〇○一二三四五六七八九]{4})年", repl, text)


_MONTH = r"(1[0-2]|0?[1-9]|十[一二]?|[一二三四五六七八九])"
_YEAR = r"((?:19|20)\d{2})"


def _month(token: str) -> int:
    value = cn_to_int(token)
    if value is None or not 1 <= value <= 12:
        raise ValueError(f"非法月份: {token}")
    return value


def _add_months(day: date, months: int) -> date:
    index = day.year * 12 + (day.month - 1) + months
    year, month = index // 12, index % 12 + 1
    # 保留日号；目标月没有该日时钳制到月末。
    if day.day == 1:
        return date(year, month, 1)
    import calendar
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


@dataclass
class TimeRange:
    start: date
    end: date  # 半开区间
    granularity: str  # year|half|quarter|month|range|days
    span: str
    relative: bool = False

    def iso(self) -> tuple[str, str]:
        return self.start.isoformat(), self.end.isoformat()


@dataclass
class TimeParse:
    ranges: list[TimeRange] = field(default_factory=list)
    spans: list[str] = field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None
    assumptions: list[str] = field(default_factory=list)

    @property
    def single(self) -> TimeRange | None:
        return self.ranges[0] if len(self.ranges) == 1 else None


def parse_time(normalized: str, reference: date | None) -> TimeParse:
    """解析归一化问题中的时间表达。多个互不重叠的时间范围返回澄清错误。"""

    text = _year_digits_cn(normalized)
    result = TimeParse()
    consumed: list[tuple[int, int]] = []

    def free(match: re.Match[str]) -> bool:
        return all(match.end() <= a or match.start() >= b for a, b in consumed)

    def take(match: re.Match[str], item: TimeRange) -> None:
        consumed.append((match.start(), match.end()))
        result.ranges.append(item)
        # 中文年份替换是等长的，按位置从原文切片，保证覆盖率守卫能在原文中定位
        result.spans.append(normalized[match.start():match.end()])
        item.span = normalized[match.start():match.end()]

    patterns: list[tuple[str, callable]] = []

    def pattern(regex: str):
        def deco(fn):
            patterns.append((regex, fn))
            return fn
        return deco

    @pattern(_YEAR + r"年" + _MONTH + r"月份?(?:到|至|~|—|－|-)" + r"(?:" + _YEAR + r"年)?" + _MONTH + r"月份?")
    def _month_range(m):
        y1, m1 = int(m.group(1)), _month(m.group(2))
        y2 = int(m.group(3)) if m.group(3) else y1
        m2 = _month(m.group(4))
        start, end = date(y1, m1, 1), _add_months(date(y2, m2, 1), 1)
        return TimeRange(start, end, "range", m.group(0)) if start < end else None

    @pattern(_YEAR + r"年" + _MONTH + r"(?:到|至|~|—|－|-)" + _MONTH + r"月份?")
    def _month_range_short(m):
        y, m1, m2 = int(m.group(1)), _month(m.group(2)), _month(m.group(3))
        start, end = date(y, m1, 1), _add_months(date(y, m2, 1), 1)
        return TimeRange(start, end, "range", m.group(0)) if start < end else None

    @pattern(_YEAR + r"年?(?:第)?([1-4一二三四])季度")
    def _quarter(m):
        y, q = int(m.group(1)), cn_to_int(m.group(2))
        start = date(y, 3 * (q - 1) + 1, 1)
        return TimeRange(start, _add_months(start, 3), "quarter", m.group(0))

    @pattern(_YEAR + r"年?q([1-4])(?![0-9])")
    def _quarter_q(m):
        y, q = int(m.group(1)), int(m.group(2))
        start = date(y, 3 * (q - 1) + 1, 1)
        return TimeRange(start, _add_months(start, 3), "quarter", m.group(0))

    @pattern(_YEAR + r"年(上|下)半年")
    def _half(m):
        y = int(m.group(1))
        start = date(y, 1 if m.group(2) == "上" else 7, 1)
        return TimeRange(start, _add_months(start, 6), "half", m.group(0))

    @pattern(_YEAR + r"年前([0-9]{1,2}|[一二三四五六七八九十]{1,2})个?月")
    def _first_months(m):
        y, n = int(m.group(1)), cn_to_int(m.group(2))
        if not n or not 1 <= n <= 12:
            return None
        return TimeRange(date(y, 1, 1), _add_months(date(y, 1, 1), n), "range", m.group(0))

    @pattern(_YEAR + r"年" + _MONTH + r"月份?")
    def _year_month(m):
        start = date(int(m.group(1)), _month(m.group(2)), 1)
        return TimeRange(start, _add_months(start, 1), "month", m.group(0))

    @pattern(_YEAR + r"[-/.](1[0-2]|0?[1-9])(?![0-9./-])")
    def _iso_month(m):
        start = date(int(m.group(1)), int(m.group(2)), 1)
        return TimeRange(start, _add_months(start, 1), "month", m.group(0))

    @pattern(_YEAR + r"(?:年度|年全年|年)")
    def _year(m):
        y = int(m.group(1))
        return TimeRange(date(y, 1, 1), date(y + 1, 1, 1), "year", m.group(0))

    for regex, builder in patterns:
        for match in re.finditer(regex, text):
            if not free(match):
                continue
            try:
                item = builder(match)
            except ValueError:
                item = None
            if item is not None:
                take(match, item)

    relative = _relative(text, reference, consumed)
    for span, item in relative:
        result.ranges.append(item)
        result.spans.append(span)
    if relative and reference is None:
        result.error_code = "missing_reference_date"
        result.error_message = "问题包含相对时间（如去年、上个月），但系统未配置参考日期，请给出具体年份或月份。"
    elif relative:
        result.assumptions.append(f"相对时间按参考日期 {reference.isoformat()} 解析")

    distinct = {(item.start, item.end) for item in result.ranges}
    if len(distinct) > 1:
        result.error_code = "multiple_time_ranges"
        result.error_message = "问题包含多个时间范围，请选择其中一个，或明确说明需要对比的口径（如同比、环比）。"
    return result


def _relative(text: str, reference: date | None, consumed: list[tuple[int, int]]) -> list[tuple[str, TimeRange]]:
    ref = reference or date(2000, 1, 1)  # 仅用于构造结构，无参考日期时会被报错拦截
    found: list[tuple[str, TimeRange]] = []

    def free(match: re.Match[str]) -> bool:
        return all(match.end() <= a or match.start() >= b for a, b in consumed)

    year_words = {"今年": 0, "本年": 0, "去年": -1, "上一年": -1, "前年": -2}
    for word, delta in year_words.items():
        for match in re.finditer(word, text):
            if free(match):
                y = ref.year + delta
                found.append((word, TimeRange(date(y, 1, 1), date(y + 1, 1, 1), "year", word, True)))
                consumed.append((match.start(), match.end()))
    month_words = {"本月": 0, "这个月": 0, "当月": 0, "上个月": -1, "上月": -1, "下个月": 1, "下月": 1}
    for word, delta in month_words.items():
        for match in re.finditer(word, text):
            if free(match):
                start = _add_months(date(ref.year, ref.month, 1), delta)
                found.append((word, TimeRange(start, _add_months(start, 1), "month", word, True)))
                consumed.append((match.start(), match.end()))

    # 日历日和自然周必须以参考日期为锚点，不能把“今天”等词留给覆盖率守卫
    # 以外的路径，否则会出现“查询成功但没有时间过滤”的静默错误。
    day_words = {"今天": 0, "昨日": -1, "昨天": -1, "前天": -2, "明天": 1}
    for word, delta in day_words.items():
        for match in re.finditer(word, text):
            if free(match):
                start = ref + timedelta(days=delta)
                found.append((word, TimeRange(start, start + timedelta(days=1), "days", word, True)))
                consumed.append((match.start(), match.end()))

    # 采用周一为自然周起点，与 ISO 周定义一致；“下周”允许提前查询，
    # 但仍保留相对日期假设，方便在审计 trace 中复现。
    week_words = {"本周": 0, "这周": 0, "上周": -1, "上一周": -1, "下周": 1, "下一周": 1}
    for word, delta in week_words.items():
        for match in re.finditer(word, text):
            if free(match):
                monday = ref - timedelta(days=ref.weekday()) + timedelta(days=7 * delta)
                found.append((word, TimeRange(monday, monday + timedelta(days=7), "days", word, True)))
                consumed.append((match.start(), match.end()))
    quarter_words = {"本季度": 0, "这个季度": 0, "上个季度": -1, "上季度": -1}
    for word, delta in quarter_words.items():
        for match in re.finditer(word, text):
            if free(match):
                q_start = date(ref.year, 3 * ((ref.month - 1) // 3) + 1, 1)
                start = _add_months(q_start, 3 * delta)
                found.append((word, TimeRange(start, _add_months(start, 3), "quarter", word, True)))
                consumed.append((match.start(), match.end()))
    for match in re.finditer(r"(?:近|最近|过去)([0-9]{1,3}|[一二两三四五六七八九十]{1,3})(天|日|周|个月|月|年)", text):
        if not free(match):
            continue
        n = cn_to_int(match.group(1)) or 0
        unit = match.group(2)
        # 统一为“截至参考日期（含当天）”的滚动窗口，半开区间右端为次日；
        # 月/年不再扩展到未来自然周期。
        end = ref + timedelta(days=1)
        if unit in {"天", "日"}:
            start = end - timedelta(days=n)
            item = TimeRange(start, end, "days", match.group(0), True)
        elif unit == "周":
            item = TimeRange(end - timedelta(days=7 * n), end, "days", match.group(0), True)
        elif unit in {"个月", "月"}:
            start = _add_months(ref, -n)
            item = TimeRange(start, end, "range", match.group(0), True)
        else:
            start = date(ref.year - n, ref.month, ref.day)
            item = TimeRange(start, end, "range", match.group(0), True)
        found.append((match.group(0), item))
        consumed.append((match.start(), match.end()))
    return found


# ---------------------------------------------------------------- 阈值 / 单位

_NUMBER = r"([0-9]+(?:\.[0-9]+)?|[" + _CN_NUM + r"]+)"
_UNIT = r"(万|亿|千|k|w)?"
_TAIL = r"(?:元|件|笔|个|单|人|台|小时|次)?"
_OP_WORDS = [
    # 先匹配带否定的长词，避免"不超过"被"超过"截获（原实现的极性缺陷）
    ("不超过", "<="), ("不高于", "<="), ("不大于", "<="), ("不多于", "<="), ("至多", "<="), ("最多", "<="),
    ("不低于", ">="), ("不少于", ">="), ("不小于", ">="), ("至少", ">="), ("最少", ">="), ("达到", ">="),
    ("超过", ">"), ("大于", ">"), ("高于", ">"), ("多于", ">"), (">=", ">="), ("<=", "<="),
    ("低于", "<"), ("小于", "<"), ("少于", "<"), (">", ">"), ("<", "<"),
]
_OP_RE = "|".join(re.escape(word) for word, _ in _OP_WORDS)
_OP_MAP = dict(_OP_WORDS)
AVERAGE_THRESHOLD_RE = re.compile(
    r"(?P<operator>" + _OP_RE + r")(?:(?:整体|全部|总体)?(?:的)?平均(?:值|水平)?"
    r"|(?:所有|全部|各个|每个|各)(?P<reference>[^,;。?!？；<>=]{1,80}?)(?:的)?(?:平均值|平均水平|均值))"
)
_MULTIPLIER = {"万": 10_000, "w": 10_000, "亿": 100_000_000, "千": 1000, "k": 1000, None: 1, "": 1}


def parse_number(token: str, unit: str | None) -> float | None:
    if re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", token):
        value = float(token)
    else:
        parsed = cn_to_int(token)
        if parsed is None:
            return None
        value = float(parsed)
    return value * _MULTIPLIER.get(unit, 1)


@dataclass(frozen=True)
class Threshold:
    operator: str
    value: float | None  # None 表示与平均值比较
    mode: str  # literal | scalar_avg
    span: str
    # An explicit average-of-groups subject must be bound to the actual
    # selected dimension and metric before this span may be consumed.
    reference: str | None = None


def parse_threshold(normalized: str) -> Threshold | None:
    avg = AVERAGE_THRESHOLD_RE.search(normalized)
    if avg:
        return Threshold(_OP_MAP[avg.group('operator')], None, "scalar_avg", avg.group(0), avg.group('reference'))
    match = re.search(r"(" + _OP_RE + r")" + _NUMBER + _UNIT + _TAIL, normalized)
    if match:
        value = parse_number(match.group(2), match.group(3))
        if value is not None:
            return Threshold(_OP_MAP[match.group(1)], value, "literal", match.group(0))
    suffix = re.search(_NUMBER + _UNIT + _TAIL + r"(以上|以下|及以上|及以下)", normalized)
    if suffix:
        value = parse_number(suffix.group(1), suffix.group(2))
        if value is not None:
            op = ">=" if "以上" in suffix.group(3) else "<="
            return Threshold(op, value, "literal", suffix.group(0))
    return None


# ---------------------------------------------------------------- Top-N

_TIME_UNIT_AFTER = r"(?!个?(?:月|天|日|周|季度|年|小时|分钟))"


@dataclass(frozen=True)
class TopN:
    n: int
    descending: bool
    span: str


@dataclass(frozen=True)
class OrdinalRank:
    """An exact rank request; rank three never means the first three ranks."""

    n: int
    descending: bool
    span: str


def parse_ordinal_ranks(normalized: str) -> list[OrdinalRank]:
    # Require a ranking verb, rather than interpreting 第一季度/第一产品
    # or an entity whose name contains an ordinal as a selection predicate.
    number = r"([0-9]+|[零〇一二两三四五六七八九十百千]+)"
    pattern = re.compile(
        r"(?:排名|排行|名列|位居)(?:为|在|是)?"
        r"(?:(倒数)?第" + number + r"(?:名|位)?|首位)"
        r"(?![0-9零〇一二两三四五六七八九十百千]|季度|个月|月|年|天|周)"
    )
    result = []
    for match in pattern.finditer(normalized):
        value = cn_to_int(match.group(2)) if match.group(2) else 1
        if value is not None:
            result.append(OrdinalRank(value, not bool(match.group(1)), match.group(0)))
    return result


def parse_top_n(normalized: str) -> TopN | None:
    ordinals = parse_ordinal_ranks(normalized)
    if len(ordinals) == 1 and ordinals[0].n == 1:
        ordinal = ordinals[0]
        return TopN(1, ordinal.descending, ordinal.span)
    patterns = (
        (r"(?:排名)?(?:前|top)([0-9]+|[一二两三四五六七八九十]{1,3})" + _TIME_UNIT_AFTER + r"(?:名|个|位|家|款|条|种)?", True),
        (r"(?:最高|最多|最大)的?([0-9]+|[一二两三四五六七八九十]{1,3})" + _TIME_UNIT_AFTER + r"(?:名|个|位|家|款|条|种)?", True),
        (r"(?:倒数|最低|最少|最小|后)的?([0-9]+|[一二两三四五六七八九十]{1,3})" + _TIME_UNIT_AFTER + r"(?:名|个|位|家|款|条|种)?", False),
    )
    for regex, descending in patterns:
        match = re.search(regex, normalized)
        if match:
            value = cn_to_int(match.group(1))
            if value:
                return TopN(value, descending, match.group(0))
    return None


# ---------------------------------------------------------------- 否定 / 残留线索

NEGATION_BEFORE = re.compile(r"(除了|除去|除|不含|不包括|不包含|排除|剔除|非)$")
NEGATION_AFTER = re.compile(r"^(以外|之外|外)")
# 覆盖率守卫：这些线索若未被任何槽位消费，说明问题里有系统没理解的约束。
UNRESOLVED_CUES = re.compile(
    r"除了?|以外|之外|不含|不包括|排除|剔除|季度|半年|去年|今年|前年|本月|上个?月|下个?月|本周|上周|"
    r"这周|下周|下一周|昨日|前天|最近|近[0-9一二三四五六七八九十]|过去|昨天|今天|明天|"
    r"按周|按天|按日|每天|每日|每周|按季度|每季度|[0-9]|到|至|~|同期|上期"
)
FILLER_WORDS = (
    "那么", "那", "呢", "吗", "么", "是多少", "有多少", "多少", "怎么样", "如何", "情况下", "请问", "帮我", "查一下",
    "查询", "看看", "看一下", "给我", "一下", "换成", "改成", "如果是", "的话", "？", "?", "。", "，", ",", "！", "!",
)

"""Responses API 的真实规划适配器；模型只提出计划，不获得 SQL 执行权。"""
from __future__ import annotations

import time
from datetime import date

from .model_contract import HttpModelPlanProvider, ModelPlanError
from ..responses_client import StructuredResponses, GenerationError


def obj(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


STRING = {"type": "string"}
NULLABLE_STRING = {"type": ["string", "null"]}
LOGICAL_ID = {"type": "string", "pattern": r"^[A-Za-z_][A-Za-z0-9_]*$", "maxLength": 64}
SCALAR = {"anyOf": [{"type": "string"}, {"type": "number"}, {"type": "null"}]}
FILTER = obj({"table": STRING, "column": STRING,
              "operator": {"type": "string", "enum": ["=", "!=", ">", ">=", "<", "<=", "LIKE", "RANGE", "BETWEEN", "IN", "NOT IN"]},
              "value": {"anyOf": [SCALAR, {"type": "array", "items": SCALAR}]}, "source_text": STRING})
DIMENSION = obj({"table": STRING, "column": STRING, "transform": {"type": "string", "enum": ["raw", "month", "year"]}, "label": STRING})
METRIC = obj({"id": LOGICAL_ID, "table": STRING, "column": STRING,
              "function": {"type": "string", "enum": ["SUM", "AVG", "COUNT", "COUNT_DISTINCT", "MIN", "MAX"]},
              "label": STRING, "unit": {"type": "string", "minLength": 1, "maxLength": 40},
              "currency": {"type": ["string", "null"], "maxLength": 10},
              "missing": {"type": "string", "enum": ["null", "zero"]}, "filters": {"type": "array", "items": FILTER}})
EXPRESSION = {"anyOf": [obj({"ref": STRING}), obj({"constant": {"type": "number"}}),
                       obj({"op": {"type": "string", "enum": ["add", "subtract", "multiply", "divide"]},
                            "left": {"$ref": "#/$defs/expression"}, "right": {"$ref": "#/$defs/expression"}})]}
COMPARISON = obj({"mode": {"type": "string", "enum": ["同比", "环比"]}, "date_table": STRING, "date_column": STRING,
                  **{k: STRING for k in ("current_start", "current_end", "previous_start", "previous_end")}})
HAVING = obj({"operator": {"type": "string", "enum": [">", ">=", "<", "<="]},
              "mode": {"type": "string", "enum": ["literal", "scalar_avg"]}, "value": {"type": ["number", "null"]}})
PLAN_SCHEMA = obj({"plan": obj({
    "version": {"type": "integer", "enum": [2]},
    "metrics": {"type": "array", "items": METRIC},
    "derived_metrics": {"type": "array", "items": obj({"id": LOGICAL_ID, "label": STRING, "expression": {"$ref": "#/$defs/expression"}})},
    "dimensions": {"type": "array", "items": DIMENSION}, "filters": {"type": "array", "items": FILTER},
    "analysis_mode": {"type": "string", "enum": ["aggregate", "rank", "share"]},
    "having": {"anyOf": [HAVING, {"type": "null"}]},
    "comparison": {"anyOf": [COMPARISON, {"type": "null"}]},
    "top_n": {"type": ["integer", "null"]}, "order_desc": {"type": "boolean"},
    "order_metric": NULLABLE_STRING, "output_metrics": {"type": "array", "items": STRING},
    "limit": {"type": "integer"}, "confidence": {"type": "number"}, "rewritten_question": STRING,
})})
PLAN_SCHEMA["$defs"] = {"expression": EXPRESSION}

INSTRUCTIONS = """你是企业 NL2SQL 规划器。只输出给定 JSON Schema 的 plan，禁止输出 SQL。
所有表、字段、JOIN 只能来自传入 Schema。保留每个明确过滤条件、时间、分组、Top-N、排序和阈值。
每个指标独立聚合；不要把两个明细事实表先 JOIN 后求和。COUNT 是事实行数，COUNT_DISTINCT 是实体去重。
RANGE 是半开区间[start,end)，BETWEEN 是闭区间。日期按 reference_date 解释。
业务公式必须使用 metric_catalog 的源指标ID、公式、单位、币种、固定条件和版本；不得猜造成本或退款。
派生指标 output_metrics 仅含用户要求的指标，依赖源仍列在 metrics 内。
无依据的口径、未定义的指标或多义字段应保留不确定性，不要发明字段、过滤值、公式或 JOIN。
标签应与业务指标词典一致；一般维度使用字段名，日期月/年分别用月份/年份。
unit必须是1至40字符的非空字符串。源Schema/指标词典未声明单位时用unknown；未声明币种时currency为null，不能猜币种或用空unit。
结果 limit 最大100；同一事实分配到多个子维度需要业务分摊规则，不能使用 SUM(DISTINCT amount)。
为每个过滤保留准确 source_text。SQL执行和语义审查由服务器完成。"""
INSTRUCTIONS += """
内部metrics/derived_metrics的id仅为唯一ASCII标识符，例如m0/m1/d0；不是展示标签或SQL表名。
order_metric、output_metrics和expression.ref必须准确引用这些id，所有输出label不得重名。
JOIN关系中的字段只用于验证关系，不能因出现ID字段就新增统计指标或分组。
验证反馈仅允许修正结构与遗漏的原始约束，不能更改问题、发明新指标或绕过澄清。
"""

INSTRUCTIONS += """
verified_intent 是服务端从原始用户问题、真实值索引和业务词典独立提取的显式约束，不是可执行计划。
你仍须根据问题和Schema独立提出完整plan，且服务器会再次验证字段、安全、语义和这些约束。
明确metrics的table/column/function/label必须保留；COUNT不能擅自改成COUNT_DISTINCT，不能改业务展示标签。
verified_intent.metrics明确提供的missing、unit、currency及指标局部filters同样属于来源契约，必须保留。
missing=null表示遵循SQL原生空值语义，不能擅自改成zero；只有明确零填充业务口径才使用missing=zero。
明确filters必须完整保留真实value和operator，RANGE必须保持[start,end)半开边界，不能换BETWEEN。
dimensions为空表示未确认分组，不得把日期过滤误当日期分组；保留已确认时间粒度。
verified_intent的analysis_mode、top_n、order_desc、having、comparison_mode及comparison_period必须完整保留；排名数量是DENSE_RANK名次范围（包含并列），不能遗漏或用返回行数limit替代。
having.mode=scalar_avg表示当前同一过滤范围内各分组聚合结果的平均，编译器会执行第二层AVG；不要为它增加一个行级AVG指标，也不要改变原指标的SUM/COUNT/AVG等口径。
只引用有依据的槽位，不得从clarification_code中猜测缺失值；口径缺失需保持不确定性。
"""


class ResponsesModelPlanProvider(HttpModelPlanProvider):
    supports_complex_queries = True

    def __init__(self, base_url, token, *, model, reasoning_effort="medium", metric_catalog=None, reference_date=None,
                 timeout=45.0, max_retries=1, session=None, http_headers=None):
        if not model or not isinstance(model, str):
            raise ValueError("必须明确配置模型名称")
        if reasoning_effort not in {"low", "medium", "high", "xhigh"}:
            raise ValueError("不支持的模型思考强度")
        super().__init__(base_url.rstrip("/") + "/responses", token, timeout=timeout, max_retries=max_retries, session=session)
        self.model, self.reasoning_effort = model, reasoning_effort
        self.catalog, self.reference_date = metric_catalog, reference_date or date.today()
        self.client = StructuredResponses(base_url, token, model=model, reasoning=reasoning_effort,
                                          timeout=timeout, session=self.session, http_headers=http_headers)

    @property
    def audit(self):
        audit = self.client.audit
        if audit:
            audit['reasoning_effort'] = self.reasoning_effort
            audit['response_status'] = audit['status']
        return audit

    @property
    def audit_history(self):
        return self.client.audit_history

    @property
    def audit_dropped_count(self):
        return self.client.audit_dropped_count

    def reset_audit(self):
        self.client.reset_audit()

    def __call__(self, question, tables):
        return self.propose(question, tables, None)

    def propose(self, question, tables, verified_intent, *, validation_feedback=None):
        # Request-local context: concurrent questions must never overwrite a
        # shared provider's verified intent, catalogue or reference date.
        context = {"question": question, "schema": [t.to_dict() for t in tables],
                   "reference_date": self.reference_date.isoformat(),
                   "metric_catalog": self.catalog.model_context() if self.catalog else None}
        if verified_intent is not None:
            context["verified_intent"] = verified_intent
        if validation_feedback is not None:
            context['validation_feedback'] = validation_feedback
        retry_budget = self.max_retries if validation_feedback is None else 0
        for attempt in range(retry_budget + 1):
            try:
                parsed = self.client.generate(INSTRUCTIONS, context, PLAN_SCHEMA, name='nl2sql_plan', max_tokens=6000)
                break
            except GenerationError as exc:
                status = exc.status
                # Retry transport errors and temporary HTTP failures only.
                # Completed but invalid output and 401 are not retried.
                retryable = status == 429 or status is not None and status >= 500 or self.client.audit.get('http_status') is None
                if attempt >= retry_budget or not retryable:
                    raise ModelPlanError("真实模型规划服务暂不可用") from exc
                time.sleep(0.05 * (2**attempt))
        if not isinstance(parsed.get("plan"), dict):
            raise ModelPlanError("模型响应缺少计划对象")
        return parsed["plan"]

    def repair(self, question, tables, verified_intent, feedback):
        return self.propose(question, tables, verified_intent, validation_feedback=feedback)

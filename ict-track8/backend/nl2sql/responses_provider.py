"""Responses API 的真实规划适配器；模型只提出计划，不获得 SQL 执行权。"""
from __future__ import annotations

import json
import threading
from datetime import date

from .model_contract import HttpModelPlanProvider, ModelPlanError, parse_model_json


def obj(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


STRING = {"type": "string"}
NULLABLE_STRING = {"type": ["string", "null"]}
SCALAR = {"anyOf": [{"type": "string"}, {"type": "number"}, {"type": "null"}]}
FILTER = obj({"table": STRING, "column": STRING,
              "operator": {"type": "string", "enum": ["=", "!=", ">", ">=", "<", "<=", "LIKE", "RANGE", "BETWEEN", "IN", "NOT IN"]},
              "value": {"anyOf": [SCALAR, {"type": "array", "items": SCALAR}]}, "source_text": STRING})
DIMENSION = obj({"table": STRING, "column": STRING, "transform": {"type": "string", "enum": ["raw", "month", "year"]}, "label": STRING})
METRIC = obj({"id": STRING, "table": STRING, "column": STRING,
              "function": {"type": "string", "enum": ["SUM", "AVG", "COUNT", "COUNT_DISTINCT", "MIN", "MAX"]},
              "label": STRING, "unit": STRING, "currency": NULLABLE_STRING,
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
    "derived_metrics": {"type": "array", "items": obj({"id": STRING, "label": STRING, "expression": {"$ref": "#/$defs/expression"}})},
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
结果 limit 最大100；同一事实分配到多个子维度需要业务分摊规则，不能使用 SUM(DISTINCT amount)。
为每个过滤保留准确 source_text。SQL执行和语义审查由服务器完成。"""


class ResponsesModelPlanProvider(HttpModelPlanProvider):
    def __init__(self, base_url, token, *, model, reasoning_effort="medium", metric_catalog=None, reference_date=None,
                 timeout=45.0, max_retries=1, session=None):
        if not model or not isinstance(model, str):
            raise ValueError("必须明确配置模型名称")
        if reasoning_effort not in {"low", "medium", "high", "xhigh"}:
            raise ValueError("不支持的模型思考强度")
        super().__init__(base_url.rstrip("/") + "/responses", token, timeout=timeout, max_retries=max_retries, session=session)
        self.model, self.reasoning_effort = model, reasoning_effort
        self.catalog, self.reference_date = metric_catalog, reference_date or date.today()
        self._local = threading.local()

    @property
    def audit(self):
        return getattr(self._local, "audit", {})

    def __call__(self, question, tables):
        import requests
        self._local.audit = {}
        payload = {"model": self.model, "store": False, "reasoning": {"effort": self.reasoning_effort},
                   "max_output_tokens": 6000, "instructions": INSTRUCTIONS,
                   "input": json.dumps({"question": question, "schema": [t.to_dict() for t in tables],
                                        "reference_date": self.reference_date.isoformat(),
                                        "metric_catalog": self.catalog.model_context() if self.catalog else None}, ensure_ascii=False),
                   "text": {"format": {"type": "json_schema", "name": "nl2sql_plan", "strict": True, "schema": PLAN_SCHEMA}}}
        headers = {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json", "Accept": "application/json"}
        for attempt in range(self.max_retries + 1):
            try:
                response = self.session.post(self.url, json=payload, headers=headers, timeout=self.timeout)
                response.raise_for_status()
                break
            except requests.RequestException as exc:
                status = getattr(getattr(exc, "response", None), "status_code", None)
                if attempt >= self.max_retries or status is not None and status != 429 and status < 500:
                    raise ModelPlanError("真实模型规划服务暂不可用") from exc
        try:
            data = response.json()
        except (ValueError, TypeError) as exc:
            raise ModelPlanError("模型响应 JSON 无效") from exc
        if not isinstance(data, dict) or data.get("status") != "completed":
            raise ModelPlanError("模型响应未完成，不执行部分计划")
        text = []
        for output in data.get("output", []):
            if output.get("type") != "message":
                continue
            for content in output.get("content", []):
                if content.get("type") == "refusal":
                    raise ModelPlanError("模型拒绝生成查询计划")
                if content.get("type") == "output_text":
                    text.append(content.get("text", ""))
        parsed = parse_model_json("".join(text))
        if not isinstance(parsed.get("plan"), dict):
            raise ModelPlanError("模型响应缺少计划对象")
        usage = data.get("usage") or {}
        self._local.audit = {"provider": "responses", "model": self.model, "reasoning_effort": self.reasoning_effort,
                             "input_tokens": usage.get("input_tokens"), "output_tokens": usage.get("output_tokens"),
                             "response_status": "completed"}
        return parsed["plan"]

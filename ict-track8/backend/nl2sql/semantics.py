"""版本化业务指标配置：公式及单位由业务配置声明，不由模型猜测。"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path

from .models import DerivedMetricSpec, FilterSpec, MetricSpec
from .schema import normalize_text


class MetricCatalog:
    def __init__(self, payload):
        if not isinstance(payload, dict) or not isinstance(payload.get("version"), str):
            raise ValueError("指标配置必须包含字符串 version")
        self.payload = copy.deepcopy(payload)
        self.version = payload["version"]
        self.digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        self.sources, self.derived, self.aliases = {}, {}, {}
        for item in payload.get("metrics", []):
            metric_id = self._id(item.get("id"))
            self.sources[metric_id] = MetricSpec(metric_id, item["table"], item["column"], item["function"],
                item["label"], item["unit"], item.get("currency"), item.get("missing", "null"),
                [FilterSpec(f["column"], f["operator"], f.get("value"), "业务指标配置", f.get("explanation", "固定业务口径"), f.get("table", item["table"])) for f in item.get("filters", [])])
            self.aliases[metric_id] = self._aliases(item)
        for item in payload.get("derived_metrics", []):
            metric_id = self._id(item.get("id"))
            if metric_id in self.sources or metric_id in self.derived:
                raise ValueError("指标配置 ID 重复")
            self.derived[metric_id] = DerivedMetricSpec(metric_id, item["label"], item["expression"])
            self.aliases[metric_id] = self._aliases(item)
        self.query_aliases = {item["id"]: item["query_alias"] for item in payload.get("metrics", [])}
        if len(self.sources) != len(payload.get("metrics", [])) or not self.sources:
            raise ValueError("源指标为空或 ID 重复")
        for metric_id in self.derived:
            self.dependencies(metric_id)

    @staticmethod
    def _id(value):
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", value):
            raise ValueError("指标配置 ID 非法")
        return value

    @staticmethod
    def _aliases(item):
        values = item.get("aliases")
        if not isinstance(values, list) or not values or any(not isinstance(a, str) or not a.strip() for a in values):
            raise ValueError("指标需要非空 aliases")
        return tuple(normalize_text(a) for a in values)

    @classmethod
    def from_file(cls, path):
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    def dependencies(self, metric_id, stack=()):
        if metric_id in stack or len(stack) > 8:
            raise ValueError("指标公式循环引用或过深")
        if metric_id in self.sources:
            return [metric_id], []
        if metric_id not in self.derived:
            raise ValueError("指标公式引用未定义指标")
        refs = []

        def visit(node, depth=0):
            if depth > 8 or not isinstance(node, dict):
                raise ValueError("指标公式过深或无效")
            if set(node) == {"ref"}:
                refs.append(node["ref"])
            elif set(node) == {"constant"}:
                return
            elif set(node) == {"op", "left", "right"} and node["op"] in {"add", "subtract", "multiply", "divide"}:
                visit(node["left"], depth + 1)
                visit(node["right"], depth + 1)
            else:
                raise ValueError("指标公式结构不合法")
        visit(self.derived[metric_id].expression)
        sources, calculations = [], []
        for ref in refs:
            s, d = self.dependencies(ref, (*stack, metric_id))
            sources.extend(s)
            calculations.extend(d)
        return list(dict.fromkeys(sources)), list(dict.fromkeys([*calculations, metric_id]))

    def expand(self, question, tables):
        normalized = normalize_text(question)
        available = {(t.name, c.name) for t in tables for c in t.columns}
        applicable = all((m.table, m.column) in available for m in self.sources.values())
        if not applicable:
            return question, None
        matched = [(normalized.find(alias), metric_id, alias) for metric_id, aliases in self.aliases.items()
                   for alias in aliases if alias in normalized]
        matched.sort(key=lambda x: (x[0], -len(x[2])))
        selected, occupied = [], []
        for pos, metric_id, alias in matched:
            if any(pos < end and pos + len(alias) > start for start, end in occupied):
                continue
            selected.append(metric_id)
            occupied.append((pos, pos + len(alias)))
        if not any(m in self.derived for m in selected):
            return question, None
        sources, calculations = [], []
        for metric_id in selected:
            s, d = self.dependencies(metric_id)
            sources.extend(s)
            calculations.extend(d)
        sources, calculations = list(dict.fromkeys(sources)), list(dict.fromkeys(calculations))
        expanded = normalized
        for _, metric_id, alias in sorted(matched, key=lambda x: -len(x[2])):
            if metric_id in self.derived:
                s, _ = self.dependencies(metric_id)
                expanded = expanded.replace(alias, "和".join(self.query_aliases[m] for m in s))
        return expanded, {"sources": sources, "calculations": calculations, "outputs": list(dict.fromkeys(selected)),
                          "original_question": question, "expanded_question": expanded}

    def apply(self, plan, expansion):
        if plan.clarification:
            return plan
        if expansion:
            plan.metrics = [copy.deepcopy(self.sources[m]) for m in expansion["sources"]]
            plan.derived_metrics = [copy.deepcopy(self.derived[m]) for m in expansion["calculations"]]
            plan.output_metrics = expansion["outputs"]
            plan.semantic_audit = {"catalog_version": self.version, "catalog_sha256": self.digest, **expansion}
            first = plan.metrics[0]
            plan.metric_table, plan.metric_column, plan.metric_function, plan.metric_label = first.table, first.column, first.function, first.label
            plan.assumptions.append(f"业务口径版本 {self.version}；派生指标使用配置公式，除数为零返回未知")
        else:
            bindings = []
            for metric in plan.metrics:
                match = next((m for m in self.sources.values() if (m.table, m.column, m.function) == (metric.table, metric.column, metric.function)), None)
                if match:
                    metric.unit, metric.currency = match.unit, match.currency
                    bindings.append({"metric_id": metric.id, "definition_id": match.id})
            if bindings:
                plan.semantic_audit = {"catalog_version": self.version, "catalog_sha256": self.digest, "bindings": bindings}
        return plan

    def model_context(self):
        return copy.deepcopy(self.payload)

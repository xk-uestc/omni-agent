"""Add outcome, repeated-failure, and process-resource analysis to a stress run."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta
import json
import math
from pathlib import Path
import statistics


TECHNICAL_REASONS = {
    'TimeoutError', 'URLError', 'ConnectionError', 'ConnectionResetError',
    'RemoteDisconnected', 'http_error', 'worker', 'HTTPError',
}


def percentile(values, p):
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, math.ceil(p * len(ordered)) - 1)], 2)


def outcome(record):
    if record.get('pass') and record.get('status') == 'clarification':
        return 'expected_clarification'
    if record.get('pass'):
        return 'correct_execution'
    if (record.get('reason') in TECHNICAL_REASONS or
            (record.get('http_status') is not None and record.get('http_status') != 200) or
            record.get('response_status') == 'error'):
        return 'technical_failure'
    if record.get('status') == 'clarification':
        return 'clarification_unanswered'
    if record.get('status') == 'ok':
        return 'incorrect_execution'
    return 'other_failure'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, required=True)
    args = parser.parse_args()
    run_dir = args.run_dir.resolve()
    records = [json.loads(line) for line in (run_dir / 'results.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]
    health_path = run_dir / 'health.jsonl'
    health = [json.loads(line) for line in health_path.read_text(encoding='utf-8').splitlines() if line.strip()] if health_path.exists() else []
    resource_path = run_dir / 'resource.jsonl'
    resources = [json.loads(line) for line in resource_path.read_text(encoding='utf-8-sig').splitlines() if line.strip()] if resource_path.exists() else []
    report_path = run_dir / 'report.json'
    manifest_path = run_dir / 'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8')) if manifest_path.exists() else {}
    report = json.loads(report_path.read_text(encoding='utf-8')) if report_path.exists() else dict(manifest)

    counts = Counter(outcome(r) for r in records)
    first_by_case = {}
    by_case = defaultdict(list)
    fail_cases = defaultdict(list)
    by_category = defaultdict(Counter)
    by_difficulty = defaultdict(Counter)
    difficulty_latencies = defaultdict(list)
    by_stage = defaultdict(lambda: {'latencies': [], 'outcomes': Counter()})
    model_metrics = defaultdict(lambda: {'calls': 0, 'latencies': [], 'winners': Counter()})
    provider_attempt_metrics = defaultdict(lambda: {'attempts': 0, 'latencies': []})
    difficulty_order_violations = []
    previous_difficulty_by_cycle = {}
    local_rule_calls = 0
    max_concurrency = 1
    for record in records:
        result = outcome(record)
        case_id = record.get('case_id', 'unknown')
        first_by_case.setdefault(case_id, record)
        by_case[case_id].append(record)
        by_category[record.get('category', 'unknown')][result] += 1
        difficulty = str(record.get('difficulty', 'unknown'))
        by_difficulty[difficulty][result] += 1
        difficulty_latencies[difficulty].append(record.get('wall_ms', 0))
        try:
            numeric_difficulty = int(difficulty)
            cycle = record.get('cycle', 1)
            previous = previous_difficulty_by_cycle.get(cycle)
            if previous is not None and numeric_difficulty < previous:
                difficulty_order_violations.append({'cycle': cycle,
                    'case_id': case_id, 'previous': previous, 'current': numeric_difficulty})
            previous_difficulty_by_cycle[cycle] = numeric_difficulty
        except (TypeError, ValueError):
            pass
        max_concurrency = max(max_concurrency, int(record.get('concurrency') or 1))
        audit = record.get('planner_audit') or {}
        provider = audit.get('provider') or {}
        model = provider.get('response_model') or provider.get('model')
        if model:
            metrics = model_metrics[model]
            metrics['calls'] += 1
            if isinstance(provider.get('latency_ms'), (int, float)):
                metrics['latencies'].append(provider['latency_ms'])
            if provider.get('winning_provider'):
                metrics['winners'][provider['winning_provider']] += 1
        elif audit.get('model_called') is False or audit.get('final_source') == 'server_verified_fast_rules':
            local_rule_calls += 1
        for attempt in provider.get('provider_attempts') or []:
            name = attempt.get('provider')
            if not name:
                continue
            metrics = provider_attempt_metrics[name]
            metrics['attempts'] += 1
            if isinstance(attempt.get('latency_ms'), (int, float)):
                metrics['latencies'].append(attempt['latency_ms'])
        stage = record.get('stage', 'unknown')
        by_stage[stage]['latencies'].append(record.get('wall_ms', 0))
        by_stage[stage]['outcomes'][result] += 1
        if not record.get('pass'):
            fail_cases[case_id].append(record)

    repeated = []
    for case_id, rows in fail_cases.items():
        sample = rows[0]
        repeated.append({'case_id': case_id, 'question': sample.get('question'),
            'category': sample.get('category'), 'difficulty': sample.get('difficulty'),
            'failed_attempts': len(rows), 'outcomes': dict(Counter(outcome(r) for r in rows)),
            'mean_ms': round(statistics.mean(r.get('wall_ms', 0) for r in rows), 2),
            'max_ms': max(r.get('wall_ms', 0) for r in rows),
            'clarification': sample.get('clarification'), 'last_error': sample.get('reason')})
    repeated.sort(key=lambda x: (-x['failed_attempts'], -x['max_ms'], x['case_id']))

    first_counts = Counter(outcome(r) for r in first_by_case.values())
    accepted = counts.get('correct_execution', 0) + counts.get('expected_clarification', 0)
    all_latencies = [r.get('wall_ms', 0) for r in records]
    if records and not report.get('finished_utc'):
        final_record = records[-1]
        request_started = datetime.fromisoformat(final_record['started_utc'])
        finished = request_started + timedelta(milliseconds=final_record.get('wall_ms', 0))
        report['finished_utc'] = finished.isoformat()
        if manifest.get('started_utc'):
            run_started = datetime.fromisoformat(manifest['started_utc'])
            report['elapsed_seconds'] = round((finished - run_started).total_seconds(), 1)
    report.update({
        'total_requests': len(records),
        'passed': accepted,
        'failed': len(records) - accepted,
        'pass_rate': round(accepted / max(1, len(records)), 6),
        'latency_ms': {'p50': percentile(all_latencies, .5), 'p95': percentile(all_latencies, .95),
                       'p99': percentile(all_latencies, .99),
                       'max': round(max(all_latencies), 2) if all_latencies else None},
        'health_checks': {'passed': sum(1 for x in health if x.get('http_status') == 200),
                          'failed': sum(1 for x in health if x.get('http_status') != 200)},
    })
    invalid_cycles = {item['cycle'] for item in difficulty_order_violations}
    classification = {
        'attempt_outcomes': dict(counts),
        'first_attempt_unique_cases': len(first_by_case),
        'first_attempt_outcomes': dict(first_counts),
        'unique_cases_seen': len(by_case),
        'difficulty_order_by_cycle': {str(k): ('invalid' if k in invalid_cycles else 'valid')
                                      for k in previous_difficulty_by_cycle},
        'difficulty_order_violations': difficulty_order_violations,
        'max_observed_concurrency': max_concurrency,
        'failed_unique_case_count': len(repeated),
        'repeated_failure_case_count': sum(1 for rows in fail_cases.values() if len(rows) > 1),
        'top_failed_cases': repeated[:40],
        'by_category': {k: dict(v) for k, v in by_category.items()},
        'by_difficulty': {k: dict(v) for k, v in by_difficulty.items()},
        'by_difficulty_latency_ms': {k: {'p50': percentile(v, .5), 'p95': percentile(v, .95),
                                         'p99': percentile(v, .99)}
                                     for k, v in difficulty_latencies.items()},
        'model_planning': {'local_rule_calls': local_rule_calls,
            'by_model': {k: {'calls': v['calls'], 'p50_ms': percentile(v['latencies'], .5),
                'p95_ms': percentile(v['latencies'], .95), 'winning_providers': dict(v['winners'])}
                for k, v in model_metrics.items()},
            'provider_attempts': {k: {'attempts': v['attempts'],
                'p50_ms': percentile(v['latencies'], .5), 'p95_ms': percentile(v['latencies'], .95)}
                for k, v in provider_attempt_metrics.items()}},
        'by_load_stage': {k: {'requests': len(v['latencies']),
            'outcomes': dict(v['outcomes']), 'p50_ms': percentile(v['latencies'], .5),
            'p95_ms': percentile(v['latencies'], .95), 'p99_ms': percentile(v['latencies'], .99),
            'max_ms': round(max(v['latencies']), 2) if v['latencies'] else None}
            for k, v in by_stage.items()},
    }
    if resources:
        cpu = [x['cpu_seconds'] for x in resources if isinstance(x.get('cpu_seconds'), (int, float))]
        working = [x['working_set_mb'] for x in resources if isinstance(x.get('working_set_mb'), (int, float))]
        private = [x['private_memory_mb'] for x in resources if isinstance(x.get('private_memory_mb'), (int, float))]
        threads = [x['thread_count'] for x in resources if isinstance(x.get('thread_count'), int)]
        resource_health = Counter(str(x.get('health_status')) for x in resources)
        cpu_delta = round(cpu[-1] - cpu[0], 3) if len(cpu) > 1 else 0
        elapsed = report.get('elapsed_seconds') or 0
        classification['process_resources'] = {
            'sample_count': len(resources), 'sample_interval_seconds': 15,
            'listener_pids': sorted({x.get('listener_pid') for x in resources if x.get('listener_pid') is not None}),
            'cpu_seconds_delta': cpu_delta,
            'average_cpu_cores_used': round(cpu_delta / elapsed, 4) if elapsed else None,
            'working_set_mb_max': round(max(working), 2) if working else None,
            'private_memory_mb_max': round(max(private), 2) if private else None,
            'thread_count_max': max(threads) if threads else None,
            'health_status_samples': dict(resource_health),
        }
    classification['health_checks'] = {
        'sample_count': len(health),
        'passed': sum(1 for x in health if x.get('http_status') == 200),
        'failed': sum(1 for x in health if x.get('http_status') != 200),
    }
    report['acceptance_classification'] = classification
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    md_path = run_dir / 'report.md'
    base_md = md_path.read_text(encoding='utf-8') if md_path.exists() else '# NL2SQL 持续压力测试报告\n'
    marker = '\n## 结果分类与重复失败\n'
    if marker in base_md:
        base_md = base_md.split(marker, 1)[0]
    total = len(records)
    started_utc = report.get('started_utc', 'unknown')
    finished_utc = report.get('finished_utc', 'in progress')
    latency = report['latency_ms']
    lines = [marker.rstrip(), '', f'- 测试窗口：{started_utc} 至 {finished_utc}（UTC）。',
             f'- 请求：{len(records)}；符合预期：{accepted}；未通过：{len(records)-accepted}；正确率：{report["pass_rate"]:.2%}。',
             f'- 端到端延迟：P50 {latency["p50"]} ms，P95 {latency["p95"]} ms，P99 {latency["p99"]} ms，最大 {latency["max"]} ms。',
             f'- 范围：本项目演示数据库 HTTP 接口；gold SQL 仅本地评分，未发送给服务。',
             '', '| 结果 | 次数 | 占比 |', '|---|---:|---:|']
    labels = {'correct_execution': 'SQL 正确执行', 'expected_clarification': '按预期澄清',
              'clarification_unanswered': '非预期澄清',
              'incorrect_execution': '错误 SQL/结果', 'technical_failure': '超时/传输/服务错误',
              'other_failure': '其他失败'}
    for key in ('correct_execution', 'expected_clarification', 'clarification_unanswered', 'incorrect_execution', 'technical_failure', 'other_failure'):
        n = counts.get(key, 0)
        lines.append(f'| {labels[key]} | {n} | {n/max(total, 1):.2%} |')
    lines += ['', f'- 首次覆盖唯一用例：{len(first_by_case)}；首次结果分类：`{dict(first_counts)}`。',
              f'- 未通过的唯一题目：{len(repeated)}；跨轮重复失败题：{classification["repeated_failure_case_count"]}。',
              f'- 每轮难度顺序：`{"符合递增" if not difficulty_order_violations else "存在倒序"}`；观测到的最大问答并发：{max_concurrency}。',
              '', '### 按难度统计', '', '| 难度 | SQL 正确 | 预期澄清 | 非预期澄清 | 错误结果 | 技术失败 | 总数 | 符合预期率 | P50(ms) | P95(ms) |', '|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for difficulty in sorted(by_difficulty, key=lambda value: (not value.isdigit(), int(value) if value.isdigit() else 0)):
        outcomes = by_difficulty[difficulty]
        n = sum(outcomes.values())
        latencies = difficulty_latencies[difficulty]
        expected_count = outcomes.get('correct_execution', 0) + outcomes.get('expected_clarification', 0)
        lines.append(f'| {difficulty} | {outcomes.get("correct_execution", 0)} | {outcomes.get("expected_clarification", 0)} | {outcomes.get("clarification_unanswered", 0)} | {outcomes.get("incorrect_execution", 0)} | {outcomes.get("technical_failure", 0)} | {n} | {expected_count/max(n, 1):.2%} | {percentile(latencies, .5)} | {percentile(latencies, .95)} |')
    model_report = classification['model_planning']
    lines += ['', '### 规划耗时', '', f'- 本地规则路径：{local_rule_calls} 次。']
    if model_report['by_model']:
        lines += ['', '| 模型 | 调用数 | P50(ms) | P95(ms) |', '|---|---:|---:|---:|']
        for model, values in sorted(model_report['by_model'].items()):
            lines.append(f'| {model} | {values["calls"]} | {values["p50_ms"]} | {values["p95_ms"]} |')
    if model_report['provider_attempts']:
        lines += ['', '| API 来源 | 尝试数 | P50(ms) | P95(ms) |', '|---|---:|---:|---:|']
        for provider_name, values in sorted(model_report['provider_attempts'].items()):
            lines.append(f'| {provider_name} | {values["attempts"]} | {values["p50_ms"]} | {values["p95_ms"]} |')
    lines += ['', '### 高频未通过题', '', '| ID | 问题 | 类别 | 失败次数 | P50等待(ms) | 最长等待(ms) |', '|---|---|---|---:|---:|---:|']
    for item in repeated[:30]:
        lat = [r.get('wall_ms', 0) for r in fail_cases[item['case_id']]]
        lines.append(f'| {item["case_id"]} | {item["question"]} | {item["category"]} | {item["failed_attempts"]} | {percentile(lat, .5)} | {item["max_ms"]} |')
    lines += ['', '### 服务资源', '']
    resources_report = classification.get('process_resources')
    if resources_report:
        lines += [f'- 采样点：{resources_report["sample_count"]}，CPU 增量 {resources_report["cpu_seconds_delta"]} 秒，平均占用 {resources_report["average_cpu_cores_used"]} 个核心。',
                  f'- 工作集峰值 {resources_report["working_set_mb_max"]} MB，私有内存峰值 {resources_report["private_memory_mb_max"]} MB，线程峰值 {resources_report["thread_count_max"]}。',
                  f'- 资源采样健康状态：`{resources_report["health_status_samples"]}`。']
    else:
        lines.append('- 本次未采集进程资源样本。')
    lines += ['', '“预期澄清”按题目语义要求提问确认；“非预期澄清”指评分器预期直接执行却返回澄清。两者都与执行错误 SQL 分开统计。']
    md_path.write_text(base_md.rstrip() + '\n' + '\n'.join(lines) + '\n', encoding='utf-8')
    print(json.dumps({'report_json': str(report_path), 'report_markdown': str(md_path),
                      'attempts': total, 'outcomes': dict(counts),
                      'unique_cases': len(first_by_case)}, ensure_ascii=False))


if __name__ == '__main__':
    main()

"""Offline source/reference and meaningful scorer contract validation. No APIs."""
from __future__ import annotations

import argparse
import ast
from collections import Counter
from contextlib import closing, redirect_stdout
from copy import deepcopy
import json
import io
from pathlib import Path
import socket
import sqlite3
import tempfile
import threading
import urllib.request
from unittest.mock import patch

from cross_schema_round5_common import (DEFAULT, EVALUATION_SUBDIR, READINESS_FILE, MODEL, ROOT, digest, forbidden_payload_fields,
    implementation_hashes, load, now, read_sql, rows_equal, value_digest, verify_public, write_new)
from run_cross_schema_round5 import PayloadLedger, execute_partitions, partition_cases
from score_cross_schema_round5 import load_oracles, main as score_main, run_integrity_supported, score_case


def contract_checks():
    passed = []
    def check(name, condition):
        if not condition:
            raise ValueError('scorer_contract_check_failed:' + name)
        passed.append(name)
    integer = {'type':'integer'}
    text = {'type':'text'}
    money = {'type':'money','absolute_tolerance':'0.0000001'}
    bag = {'mode':'bag','columns':[integer]}
    check('duplicate_multiplicity', not rows_equal([[1]],[[1],[1]],bag))
    check('bag_preserves_count', rows_equal([[2],[1],[1]],[[1],[2],[1]],bag))
    check('ordered_reverse_rejected', not rows_equal([[2],[1]],[[1],[2]],{'mode':'ordered','columns':[integer]}))
    check('null_not_zero', not rows_equal([[0]],[[None]],bag))
    check('null_not_empty_string', not rows_equal([['']],[[None]],{'mode':'bag','columns':[text]}))
    check('null_not_zero_rows', not rows_equal([],[[None]],bag))
    check('one_null_row_valid', rows_equal([[None]],[[None]],bag))
    check('integer_type_exact', not rows_equal([[1.0]],[[1]],bag))
    check('money_float_storage_tolerance', rows_equal([[1.10000000001]],[[1.1]],{'mode':'bag','columns':[money]}))
    check('money_real_error_rejected', not rows_equal([[1.101]],[[1.1]],{'mode':'bag','columns':[money]}))
    check('non_finite_rejected', not rows_equal([[float('nan')],[1]],[[2],[1]],bag))
    check('all_columns_required', not rows_equal([[1]],[[1,'x']],{'mode':'bag','columns':[integer,text]}))
    check('private_nested_payload_rejected', forbidden_payload_fields({'nested':[{'reference_sql':'SELECT 1'}]}))
    class FakeClient:
        def generate(self,*args,**kwargs): return {'ok':True}
    ledger = PayloadLedger([{'case_id':'a','question':'current independent question'},
                            {'case_id':'b','question':'future secret question'}])
    fake = FakeClient(); ledger.attach(fake); ledger.current='a'
    check('current_payload_allowed',fake.generate('x',{'question':'current independent question'}, {}) == {'ok':True})
    try:
        fake.generate('x',{'history':['future secret question']},{})
        raise AssertionError('future payload accepted')
    except ValueError:
        passed.append('future_payload_rejected')
    try:
        fake.generate('x',{'reference_sql':'SELECT 1'},{})
        raise AssertionError('private payload accepted')
    except ValueError:
        passed.append('gold_payload_rejected')
    try:
        fake.generate('x',{'question':'current independent question'},{'reference_sql':'SELECT 1'})
        raise AssertionError('private schema accepted')
    except ValueError:
        passed.append('gold_schema_payload_rejected')
    try:
        fake.generate('future secret question',{'question':'current independent question'}, {})
        raise AssertionError('future instructions accepted')
    except ValueError:
        passed.append('future_instruction_payload_rejected')
    public={key:'fixture-'+key for key in ('source_manifest_sha256','database_sha256','schema_sha256','system_input_sha256')}
    hashes=implementation_hashes()
    manifest={**public,'mode':'real_model','implementation_stable':True,'source_stable':True,
        'implementation_file_sha256':hashes,'implementation_file_sha256_end':hashes,
        'planned_total':56,'reference_opened':False,'few_shot_examples':0,'domain_alias_rules':0,
        'metric_catalog':None,'limits':{'max_rows':100,'max_seconds':5.0}}
    check('complete_recorded_run_integrity_supported',run_integrity_supported(manifest,public))
    changed=deepcopy(manifest);changed['implementation_file_sha256_end']={}
    check('manifest_stable_flag_cannot_replace_hashes',not run_integrity_supported(changed,public))
    changed=deepcopy(manifest);changed['database_sha256']='wrong-source'
    check('wrong_database_manifest_rejected',not run_integrity_supported(changed,public))
    changed=deepcopy(manifest);changed['limits']['max_rows']=200000
    check('product_row_limit_cannot_be_increased',not run_integrity_supported(changed,public))
    with tempfile.TemporaryDirectory(prefix='cross-schema-contract-') as directory:
        database=Path(directory)/'fixture.sqlite'
        with closing(sqlite3.connect(database)) as connection:
            connection.executescript("CREATE TABLE t(item_id INTEGER PRIMARY KEY,segment TEXT,value NUMERIC); INSERT INTO t VALUES(1,'A',1.1),(2,'A',NULL),(3,'B',2.2),(4,'C',NULL);")
        reference='SELECT segment,COUNT(*) FROM t GROUP BY segment ORDER BY segment'
        names,expected,reads=read_sql(database,reference)
        oracle={'case_id':'fixture','kind':'single','domain':'fixture','family':'fixture','session_id':None,'turn_index':None,
            'expected_history_turns':0,'forbidden_filter_columns':[],
            'expected_output_roles':[{'kind':'dimension','table':'t','column':'segment','transform':'raw'},
                                     {'kind':'metric','table':'t','column':'item_id','function':'COUNT'}],
            'comparison_contract':{'mode':'ordered','columns':[text,integer]},
            'full_expected_rows':expected,'expected_row_count':3,'reference_read_sources':reads,
            'requires_complete_unbounded_result':True,'tags':[]}
        sql='SELECT segment,COUNT(item_id) AS "friendly business label" FROM t GROUP BY segment ORDER BY segment'
        cols,actual,_=read_sql(database,sql)
        result={'status':'ok','sql':sql,'parameters':[],'columns':cols,'rows':[dict(zip(cols,r)) for r in actual],
            'provenance':{'result_completeness':'within_return_limit'},'plan':{'table':'t','dimensions':['segment'],
                'dimension_tables':{'segment':'t'},'dimension_labels':{},'metrics':[{'table':'t','column':'item_id',
                    'function':'COUNT','label':'friendly business label'}],'planner_source':'model_validated'}}
        record={'case_id':'fixture','history_before_count':0,'response':result,
            'api_audits':[{'status':'completed','model_verified':True,'model':MODEL}], 'api_audit_dropped':0,
            'payload_audit':[{'case_id':'fixture','private_fields_absent':True,'future_question_absent':True}]}
        check('different_valid_sql_and_labels_pass',score_case(oracle,record,database)['pass'])
        missing=score_case(oracle,None,database)
        check('not_run_failure_fixed_case',not missing['pass'] and missing['failure_reason']=='not_run')
        changed=deepcopy(record); changed['response']['status']='clarification'
        check('clarification_not_answer',not score_case(oracle,changed,database)['pass'])
        changed=deepcopy(record); changed['response']['status']='unsupported'
        check('unsupported_not_answer',not score_case(oracle,changed,database)['pass'])
        changed=deepcopy(record); changed['response']['plan']['planner_source']='rules'
        check('rules_fallback_not_real_model',not score_case(oracle,changed,database)['pass'])
        changed=deepcopy(record); changed['api_audits'][0]['status']='failed'
        check('failed_api_not_success',not score_case(oracle,changed,database)['pass'])
        changed=deepcopy(record); changed['payload_audit']=[]
        check('missing_isolation_audit_rejected',not score_case(oracle,changed,database)['pass'])
        changed=deepcopy(record); changed['history_before_count']=1
        check('wrong_history_count_rejected',not score_case(oracle,changed,database)['pass'])
        changed=deepcopy(record); changed['response']['rows'][0]['friendly business label']=99
        check('returned_cells_must_match_actual_sql',not score_case(oracle,changed,database)['checks']['unchanged_sql_replay_matches_returned_rows'])
        changed=deepcopy(record); changed['response']['sql'] += ' LIMIT 2'
        changed['response']['rows']=changed['response']['rows'][:2]
        changed['response']['provenance']['result_completeness']='limit_reached_total_unknown'
        scored=score_case(oracle,changed,database)
        check('limit_kept_in_replay',scored['checks']['unchanged_sql_replay_matches_returned_rows'])
        check('correct_prefix_not_complete_answer',not scored['pass'] and not scored['checks']['complete_result'])
        changed=deepcopy(record); changed['response']['provenance']['result_completeness']='limit_reached_total_unknown'
        check('unbounded_unknown_completeness_rejected',not score_case(oracle,changed,database)['pass'])
        changed=deepcopy(record); changed['response']['rows'].reverse()
        check('requested_order_not_ignored',not score_case(oracle,changed,database)['pass'])
        changed_oracle=deepcopy(oracle); changed_oracle['forbidden_filter_columns']=[['old_table','old_date']]
        changed=deepcopy(record); changed['response']['plan']['filters']=[{'table':'old_table','column':'old_date'}]
        check('old_topic_filter_rejected',not score_case(changed_oracle,changed,database)['pass'])
        try:
            read_sql(database,'WITH x AS (SELECT 1) DELETE FROM t')
            raise AssertionError('write SQL accepted')
        except sqlite3.Error:
            passed.append('with_write_authorizer_rejected')
        check('write_attempt_left_data_intact',read_sql(database,'SELECT COUNT(*) FROM t')[1]==[[4]])
        try:
            read_sql(database,"SELECT load_extension('not_a_real_extension')")
            raise AssertionError('extension loading accepted')
        except sqlite3.Error:
            passed.append('extension_loading_rejected')
    return passed


def fixed_denominator_cli_check(source_directory):
    # Intentionally empty offline fixture: no model execution or fake success.
    with tempfile.TemporaryDirectory(prefix='cross-schema-empty-run-') as directory:
        run=Path(directory)
        observed=run/'observed.jsonl'
        with observed.open('x',encoding='utf-8'):
            pass
        write_new(run/'RUN_MANIFEST.json',{'mode':'offline_contract_fixture','observed_sha256':digest(observed)})
        with patch('sys.argv',['score_cross_schema_round5.py','--directory',str(source_directory),
                '--run-directory',str(run)]),redirect_stdout(io.StringIO()):
            code=score_main()
        report=load(run/'SCORE.json')
        if (code!=1 or report['stable'] or report['executed_total']!=0 or report['passed']!=0
                or report['planned_total']!=56 or report['standalone_total']!=36
                or report['session_turns_total']!=20 or report['sessions_total']!=4
                or len(report['not_run'])!=56 or len(report['cases'])!=56
                or any(row['pass'] for row in report['cases'])
                or any(len(session['turn_passes'])!=5 or session['all_pass'] for session in report['sessions'].values())):
            raise ValueError('empty_cli_fixture_changed_fixed_denominators')
    return 'empty_offline_cli_preserves_all_56_failures_and_four_five_turn_sessions'


def concurrency_checks():
    cases=[{'case_id':'fixture-single-'+str(i),'question':'isolated single fixture '+str(i),
            'kind':'single','session_id':None,'turn_index':None} for i in range(6)]
    cases += [{'case_id':'fixture-session-'+str(s)+'-'+str(t),
               'question':'isolated session fixture '+str(s)+' turn '+str(t),
               'kind':'session','session_id':'fixture-'+str(s),'turn_index':t}
              for s in range(2) for t in range(1,6)]
    passed=[]
    for workers in (1,2,3):
        partitions=partition_cases(cases,workers)
        ids=[c['case_id'] for units in partitions for unit in units for c in unit]
        if len(ids)!=len(cases) or set(ids)!={c['case_id'] for c in cases}:
            raise ValueError('concurrent_partition_lost_or_duplicated_case')
        if any(len(unit)!=5 or [c['turn_index'] for c in unit]!=[1,2,3,4,5]
               for units in partitions for unit in units if unit[0]['kind']=='session'):
            raise ValueError('concurrent_session_split_or_reordered')
        passed.append('workers_'+str(workers)+'_all_cases_once_sessions_ordered')
    barrier=threading.Barrier(3,timeout=5)
    class FakeClient:
        def generate(self,*args,**kwargs): return {'ok':True}
    def execute(index,units):
        client=FakeClient()
        ledger=PayloadLedger(cases)
        ledger.attach(client)
        barrier.wait()
        seen=[]
        for unit in units:
            for case in unit:
                ledger.current=case['case_id']
                client.generate('fixture',{'question':case['question']},{})
                ledger.completed_ids.add(case['case_id'])
                seen.append(case['case_id'])
        return {'thread':threading.get_ident(),'ledger':ledger,'client':client,'seen':seen}
    results,errors=execute_partitions(partition_cases(cases,3),execute)
    if (errors or len(results)!=3 or len({r['thread'] for r in results.values()})!=3
            or len({id(r['ledger']) for r in results.values()})!=3
            or len({id(r['client']) for r in results.values()})!=3
            or any([a['case_id'] for a in r['ledger'].records]!=r['seen'] for r in results.values())):
        raise ValueError('concurrent_workers_shared_or_mixed_audit')
    passed.append('three_simultaneous_threads_with_exclusive_clients_ledgers_audits')
    def failed(index,units):
        if index==1: raise RuntimeError('synthetic worker failure')
        return index
    results,errors=execute_partitions([[],[],[]],failed)
    if results!={0:0,2:2} or errors!={1:'RuntimeError'}:
        raise ValueError('worker_failure_lost_successful_peers')
    passed.append('worker_error_retains_successful_peer_results')
    try:
        partition_cases(cases,4)
        raise AssertionError('too many workers accepted')
    except ValueError:
        passed.append('more_than_three_workers_rejected')
    broken=[dict(c) for c in cases]
    broken[-1]['turn_index']=4
    try:
        partition_cases(broken,2)
        raise AssertionError('bad session ordering accepted')
    except ValueError:
        passed.append('broken_five_turn_sequence_rejected_before_execution')
    return passed


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--directory',type=Path,default=DEFAULT)
    parser.add_argument('--write-readiness',action='store_true')
    args=parser.parse_args()
    def deny_network(*args,**kwargs): raise RuntimeError('offline_validation_network_forbidden')
    socket.create_connection=deny_network
    urllib.request.urlopen=deny_network
    source,database,public,bundle=verify_public(args.directory)
    references=load_oracles(args.directory,public)
    if {c['case_id'] for c in references}!={c['case_id'] for c in bundle['cases']}:
        raise ValueError('public_private_ids_mismatch')
    singles=[c for c in references if c['kind']=='single']
    if (len(singles)!=36 or set(Counter(c['family'] for c in singles).values())!={6}
            or set(Counter(c['domain'] for c in singles).values())!={12}):
        raise ValueError('frozen_difficulty_or_business_denominator_changed')
    sessions={c['session_id']:[] for c in references if c['kind']=='session'}
    for c in references:
        if c['kind']=='session':sessions[c['session_id']].append(c['turn_index'])
    if len(sessions)!=4 or any(v!=[1,2,3,4,5] for v in sessions.values()):
        raise ValueError('five_turn_sequence_changed')
    replayed=0
    for c in references:
        names,rows,reads=read_sql(database,c['reference_sql'],c['reference_parameters'])
        if (names!=c['reference_columns'] or value_digest(rows)!=c['expected_rows_sha256']
                or value_digest([c['reference_sql'],c['reference_parameters']])!=c['reference_sql_sha256']
                or len(rows)!=c['expected_row_count'] or reads!=c['reference_read_sources']
                or not rows_equal(rows,c['full_expected_rows'],c['comparison_contract'])):
            raise ValueError('frozen_reference_replay_mismatch')
        replayed+=1
    checks=contract_checks()
    checks.append(fixed_denominator_cli_check(args.directory))
    checks.extend(concurrency_checks())
    tool_names=('acquire_sakila_round5.py','revise_cross_schema_round5_reference.py','cross_schema_round5_common.py','run_cross_schema_round5.py',
                'score_cross_schema_round5.py','validate_cross_schema_round5.py')
    for name in tool_names:
        ast.parse((ROOT/'tools'/name).read_text(encoding='utf-8'),filename=name)
    readiness={'created_at':now(),'readiness_version':'workers-v1','runner_workers_supported':[1,2,3],
        'baseline_ready':True,'model_api_calls':0,'reference_replays':replayed,
        'scorer_contract_checks_passed':len(checks),'scorer_contract_checks':checks,'planned_total':56,
        'standalone_total':36,'session_turns_total':20,'sessions_total':4,
        'source_manifest_sha256':public['source_manifest_sha256'],'database_sha256':public['database_sha256'],
        'schema_sha256':public['schema_sha256'],'system_input_sha256':public['system_input_sha256'],
        'oracle_sha256':public['oracle_sha256'],'tool_hashes':{name:digest(ROOT/'tools'/name) for name in tool_names},
        'validation_scope':'Offline source/reference replay and independent synthetic scorer-contract checks only; not model performance.',
        'network_forbidden_during_validation':True,'backend_imported':False,'official_benchmark_score':False}
    if args.write_readiness:
        write_new(Path(args.directory)/EVALUATION_SUBDIR/READINESS_FILE,readiness)
    print(json.dumps({k:readiness[k] for k in ('baseline_ready','planned_total','standalone_total',
        'session_turns_total','sessions_total','reference_replays','scorer_contract_checks_passed','model_api_calls',
        'network_forbidden_during_validation','backend_imported')}))
    return 0


if __name__=='__main__':
    raise SystemExit(main())

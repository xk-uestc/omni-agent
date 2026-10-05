"""Observe one retained SQL failure; no reference, no replacement score."""
import argparse
from datetime import date
import json
import os
from pathlib import Path
import sys
import uuid

from cross_schema_round5_common import ROOT, DEFAULT, implementation_hashes, verify_public, now
from run_cross_schema_round5 import PayloadLedger
from model_runtime import enable_local_model, local_model_headers

sys.path.insert(0, str(ROOT/'ict-track8'))
from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.responses_provider import ResponsesModelPlanProvider
from evaluate_ohr_bench import safe_audits


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case-id',required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--recover-observations',type=Path,
        help='Recover an incomplete local report without another model request')
    args=parser.parse_args()
    if args.output.exists() or args.output.resolve().parent!=(ROOT/'docs').resolve():
        parser.error('New report directly in docs required')
    if args.recover_observations:
        source=args.recover_observations.resolve()
        if source.parent!=(ROOT/'docs').resolve() or source.suffix!='.txt':
            parser.error('Preserved incomplete diagnostic text in docs required')
        raw=source.read_text(encoding='utf-8')
        prefix,tail=raw.rsplit('"result":',1)
        if tail.strip():
            parser.error('Expected serialization failure before result object')
        report=json.loads(prefix.rstrip().rstrip(',')+'}')
        if report['case_id']!=args.case_id or report['reference_opened'] is not False:
            parser.error('Recovered case does not match isolated diagnostic')
        report.update(scope='recovered_model_proposals_from_incomplete_diagnostic_not_accuracy',
            result=None,api_audits=[],implementation_stable=None,
            report_failure='QueryResult serialization failed; observed proposals preserved',
            recovery_source=str(source),recovery_model_calls=0)
        payload=json.dumps(report,ensure_ascii=False,indent=2)+'\n'
        with args.output.open('x',encoding='utf-8') as f:f.write(payload)
        print(json.dumps({'recovered_operations':len(report['observed']),'model_calls':0}))
        return
    _,database,public,bundle=verify_public(DEFAULT)
    case=next(c for c in bundle['cases'] if c['case_id']==args.case_id)
    if case['kind']!='single':
        parser.error('Standalone diagnostic only; sessions need their original history')
    config=enable_local_model('gpt-6-luna')
    before=implementation_hashes()
    work=ROOT/'runtime'/('cross-schema-probe-'+uuid.uuid4().hex)
    work.mkdir()
    provider=ResponsesModelPlanProvider(os.environ['ICT8_OPENAI_BASE_URL'],os.environ['ICT8_OPENAI_API_KEY'],
        model='gpt-6-luna',reasoning_effort=config['reasoning'],
        reference_date=date.fromisoformat(bundle['reference_date']),http_headers=local_model_headers())
    ledger=PayloadLedger(bundle['cases']);ledger.attach(provider.client);ledger.current=case['case_id']
    generate=provider.client.generate;observed=[]
    def observe(*a,**kw):
        value=generate(*a,**kw)
        observed.append({'operation':kw.get('name'),'returned_proposal_or_review':value})
        return value
    provider.client.generate=observe
    engine=Nl2SqlEngine(database,model_plan_provider=provider,reference_date=date.fromisoformat(bundle['reference_date']),
        max_seconds=5.0,metric_catalog_path=work/'absent_catalog.json')
    if engine.metric_catalog is not None or engine.value_aliases_path is not None:
        raise ValueError('domain_hints_not_allowed')
    result=engine.answer(case['question']).to_dict()
    after=implementation_hashes()
    report={'created_at':now(),'scope':'single_production_SQL_protocol_diagnosis_not_accuracy',
        'case_id':case['case_id'],'question':case['question'],'reference_opened':False,
        'model':'gpt-6-luna','reasoning':'medium','system_input_sha256':public['system_input_sha256'],
        'observed':observed,'result':result,'api_audits':safe_audits(provider.client),
        'payload_ledger':ledger.records,'implementation_stable':before==after,
        'implementation_file_sha256_start':before,'implementation_file_sha256_end':after}
    payload=json.dumps(report,ensure_ascii=False,indent=2)+'\n'
    with args.output.open('x',encoding='utf-8') as f:f.write(payload)
    print(json.dumps({'status':result['status'],'stable':before==after,'calls':len(observed)}))


if __name__=='__main__':
    main()

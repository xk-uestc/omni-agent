"""Exercise the real HTTP ingest/document/query path using only an original PDF."""
import argparse
import base64
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'ict-track8'))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--pdf',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    from fastapi.testclient import TestClient
    import backend.app as module
    from backend.dependency_agent import DependencyAgent,DependencyPlanError
    raw=args.pdf.read_bytes();sha=hashlib.sha256(raw).hexdigest()
    with TestClient(module.app) as client:
        result=client.post('/api/v1/knowledge/ingest',json={'document_id':'real-pdf-proof',
            'title':'Real document integration proof','modality':'pdf','filename':args.pdf.name,
            'file_base64':base64.b64encode(raw).decode()})
        result.raise_for_status()
        document=client.get('/api/v1/knowledge/documents/real-pdf-proof').json()
        assert document['sha256']==sha and document.get('elements')
        formulas=module.knowledge_store.formula_records('real-pdf-proof',expected_sha256=sha)
        assert formulas and all(not e['calculator_input_eligible'] for e in formulas)
        tables=[e for e in document['elements'] if e['kind']=='table']
        assert tables and all(e['cells'] for e in tables)
        response=client.post('/api/v1/knowledge/query',json={'question':'Hazard Quotient Exposure Dose RfD',
                             'document_id':'real-pdf-proof','top_k':4})
        response.raise_for_status()
        answer=response.json()
        hits=module.knowledge_store.search('Hazard Quotient Exposure Dose RfD',document_id='real-pdf-proof',top_k=20)
        structured_hits=[h for h in hits if h.metadata.get('structured_element_id')]
        assert structured_hits
        for f in formulas:
            crop=module.knowledge_store.visual_asset('real-pdf-proof',page_no=f['page_no'],
                    expected_source_sha256=sha,crop_display_pt=f['bbox'])
            assert crop.png_bytes.startswith(b'\x89PNG')
            f['visual_region_png_sha256']=hashlib.sha256(crop.png_bytes).hexdigest()
        refused=False
        try:DependencyAgent(None,module.knowledge_store).execute('document_formula',
                {'document_id':'real-pdf-proof','label':'HazardQuotient'},{},{})
        except DependencyPlanError as exc:refused=exc.code=='formula_review_required'
        assert refused
        assert client.get('/api/v1/knowledge/documents/real-pdf-proof/original').content==raw
        report={'original_sha256':sha,'http_ingest':result.status_code,'http_document':200,'http_query':response.status_code,
                'source_replay':True,'parser':document['structured_document']['parser'],
                'elements':len(document['elements']),'tables':len(tables),'cells':sum(len(e['cells']) for e in tables),
                'independent_formulas':len(formulas),'structured_search_hits':len(structured_hits),
                'recognized_formula_calculation_refused':refused,'recognized_formula_execution_count':0,
                'generation_answer_accuracy':'not_run','gold_loaded':False,'cost':document['stats']['structured_backend_cost'],
                'formula_regions':[{'element_id':e['element_id'],'page_no':e['page_no'],'bbox':e['bbox'],
                                    'visual_region_png_sha256':e['visual_region_png_sha256']} for e in formulas],
                'answer_mode':answer.get('answer_mode'),'answer_status':answer.get('status')}
        args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps(report),flush=True)


if __name__=='__main__':main()

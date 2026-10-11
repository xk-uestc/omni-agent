"""Post-hoc bounded diagnostics on unchanged OmniDocBench v1.0 annotations.

Not the official leaderboard: IoU>=0.5 greedy one-to-one matching, whitespace
normalized edit similarity for LaTeX, exact text/spans at table cell coordinates.
Failures stay in denominators. Gold never enters parser or KnowledgeStore.
"""
import argparse
import hashlib
import json
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'ict-track8'))
from backend.structured_document import normalize_mineru, normalize_docling, html_cells


def norm(text):
    return ''.join(unicodedata.normalize('NFKC', str(text)).split())


def similarity(a, b):
    a, b = norm(a), norm(b)
    if max(len(a), len(b)) > 20000:
        raise ValueError('scoring_text_budget')
    if not a and not b:
        return 1.0
    previous = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        current = [i]
        for j, y in enumerate(b, 1):
            current.append(min(current[-1] + 1, previous[j] + 1, previous[j-1] + (x != y)))
        previous = current
    return 1 - previous[-1]/max(len(a), len(b), 1)


def iou(a, b):
    overlap = max(0, min(a[2], b[2])-max(a[0], b[0])) * max(0, min(a[3], b[3])-max(a[1], b[1]))
    union = (a[2]-a[0])*(a[3]-a[1])+(b[2]-b[0])*(b[3]-b[1])-overlap
    return overlap/union if union else 0


def matching(gold, predicted):
    candidates = sorted([(iou(g['bbox'], p['bbox']), gi, pi) for gi, g in enumerate(gold)
                         for pi, p in enumerate(predicted)], reverse=True)
    gs, ps, matches = set(), set(), []
    for score, gi, pi in candidates:
        if score >= .5 and gi not in gs and pi not in ps:
            gs.add(gi); ps.add(pi); matches.append((gi, pi))
    return matches


def observations(record, trial, data):
    import fitz
    source = record['source']
    raw = (data/source['pdf_path']).read_bytes()
    if hashlib.sha256(raw).hexdigest() != source['pdf_sha256']:
        raise ValueError('source_changed')
    with fitz.open(stream=raw, filetype='pdf') as pdf:
        geometry = [(float(p.rect.width), float(p.rect.height)) for p in pdf]
    if record['status'] != 'ok':
        return [], geometry
    if record['backend'] == 'native':
        files = list(trial.glob('*'+Path(source['pdf_path']).stem+'/native.json'))
        document = json.loads(files[0].read_text())
        elements = []
        for n, c in enumerate(document['chunks']):
            box = c['metadata'].get('coordinate_evidence', {}).get('source_bbox_fitz_display_pt')
            box = box or c['metadata'].get('source_bbox_fitz_display_pt')
            if box:
                elements.append({'kind':'text', 'text':c['text'], 'bbox':box, 'page_no':c['page_no'], 'order':n})
        return elements, geometry
    f = trial/record['raw_files'][0]
    parsed = json.loads(f.read_text())
    if record['backend'] == 'mineru':
        structure = normalize_mineru(parsed, geometry, source['pdf_sha256'])
    else:
        structure = normalize_docling(parsed, geometry, source['pdf_sha256'], record['versions']['docling'])
    return structure['elements'], geometry


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--trial', type=Path, required=True)
    p.add_argument('--data-root', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    if args.output.exists():
        p.error('Do not overwrite results')
    run_raw = (args.trial/'run.json').read_bytes()
    run = json.loads(run_raw)
    if run.get('planned_input_count',len(run['records'])) != len(run['records']) or run.get('finished') is False:
        raise ValueError('parser_run_incomplete_do_not_score')
    # All prediction artifacts already exist before this opens reference data.
    manifest = json.loads((args.data_root/'odb-manifest.json').read_text())
    gold_raw = (args.data_root/'gold/OmniDocBench-v1_0.json').read_bytes()
    if hashlib.sha256(gold_raw).hexdigest() != manifest['gold_sha256']:
        raise ValueError('gold_changed')
    annotations = {Path(x['page_info']['image_path']).name:x for x in json.loads(gold_raw)}
    results = []
    for record in run['records']:
        ref = annotations[record['source']['image_name']]
        error = None
        try:
            elements, geometry = observations(record, args.trial, args.data_root)
        except Exception as exc:
            error = str(exc)
            elements, geometry = [], [(1, 1)]
        w, h = geometry[0]
        rw, rh = ref['page_info']['width'], ref['page_info']['height']
        gold = []
        for a in ref['layout_dets']:
            if a.get('ignore'):
                continue
            poly = a['poly']; xs, ys = poly[0::2], poly[1::2]
            gold.append({**a, 'bbox':[min(xs)*w/rw,min(ys)*h/rh,max(xs)*w/rw,max(ys)*h/rh]})
        formulas = [g for g in gold if g['category_type']=='equation_isolated']
        fp = [e for e in elements if e['kind']=='formula']
        fm = matching(formulas, fp)
        latex = lambda s: norm(s).strip('$')
        exact = sum(latex(formulas[g]['latex']) == latex(fp[k]['text']) for g,k in fm)
        ned = sum(similarity(latex(formulas[g]['latex']), latex(fp[k]['text'])) for g,k in fm)
        tables = [g for g in gold if g['category_type']=='table']
        tp = [e for e in elements if e['kind']=='table']
        tm = matching(tables, tp)
        cell_count, correct, span_correct = 0, 0, 0
        for gi, table in enumerate(tables):
            gc = html_cells(table['html']); cell_count += len(gc)
            pair = next((pi for g,pi in tm if g==gi), None)
            pc = {(c['row'],c['column']):c for c in tp[pair]['cells']} if pair is not None else {}
            for c in gc:
                candidate = pc.get((c['row'],c['column']))
                if candidate:
                    span_correct += c['row_span']==candidate['row_span'] and c['column_span']==candidate['column_span']
                    correct += (norm(c['text'])==norm(candidate['text']) and c['row_span']==candidate['row_span']
                                and c['column_span']==candidate['column_span'])
        text = [g for g in gold if g.get('text') and g.get('order') is not None]
        text_similarity = 0
        mapped = []
        for g in text:
            overlapping = [e for e in elements if e['kind'] not in {'table','formula','image','chart'} and iou(g['bbox'],e['bbox'])>=.1]
            observed = '\n'.join(e['text'] for e in sorted(overlapping,key=lambda e:e['order']))
            text_similarity += similarity(g['text'], observed)
            if overlapping:
                mapped.append((g['order'], min(e['order'] for e in overlapping)))
        pairs = [(a,b) for i,a in enumerate(mapped) for b in mapped[i+1:] if a[0]!=b[0]]
        order_correct = sum((a[0]-b[0])*(a[1]-b[1])>0 for a,b in pairs)
        result = {'source':record['source']['image_name'],'source_sha256':record['source']['pdf_sha256'],
            'parser_status':record['status'],'normalization_error':error, 'seconds':record['seconds'],
            'gold_formulas':len(formulas),'detected_formulas':len(fp),'matched_formulas':len(fm),
            'latex_exact':exact,'latex_similarity_sum':ned,'gold_tables':len(tables),'matched_tables':len(tm),
            'gold_cells':cell_count,'correct_cells':correct,'correct_cell_spans':span_correct,
            'text_blocks':len(text),'text_similarity_sum':text_similarity,
            'reading_order_pairs':len(pairs),'correct_order_pairs':order_correct,
            'executable_recognized_formulas':0,'generation_answer_accuracy':'not_run'}
        results.append(result)
    summed = lambda k:sum(r[k] for r in results)
    ratio = lambda a,b:summed(a)/summed(b) if summed(b) else None
    summary = {'files':len(results),'successful_files':sum(r['parser_status']=='ok' and not r['normalization_error'] for r in results),
        'formula_detection_recall':ratio('matched_formulas','gold_formulas'),
        'formula_detection_precision':ratio('matched_formulas','detected_formulas'),
        'latex_exact_per_gold':ratio('latex_exact','gold_formulas'),
        'latex_edit_similarity_per_gold':ratio('latex_similarity_sum','gold_formulas'),
        'table_detection_recall':ratio('matched_tables','gold_tables'),
        'table_cell_exact_with_spans':ratio('correct_cells','gold_cells'),
        'table_cell_span_correct':ratio('correct_cell_spans','gold_cells'),
        'text_block_edit_similarity':ratio('text_similarity_sum','text_blocks'),
        'matched_reading_order_accuracy':ratio('correct_order_pairs','reading_order_pairs'),
        'executable_recognized_formulas':0,'generation_answer_accuracy':'not_run'}
    args.output.write_text(json.dumps({'protocol':__doc__,'run_sha256':hashlib.sha256(run_raw).hexdigest(),
        'annotation_sha256':manifest['gold_sha256'],'summary':summary,'files':results},ensure_ascii=False,indent=2))
    print(json.dumps(summary),flush=True)


if __name__=='__main__':
    main()

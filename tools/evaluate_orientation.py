"""Real local OCR orientation/deskew audit, with frozen synthetic rotations."""
import hashlib
import io
import json
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ict-track8'))


def cases():
    image = Image.open(ROOT/'samples/documents/service-scan.png').convert('RGB')
    shapes = Image.new('RGB',image.size,'white')
    draw = ImageDraw.Draw(shapes)
    draw.ellipse((60,50,400,300),outline='black',width=5)
    draw.polygon([(650,40),(800,300),(500,300)],outline='black',width=4)
    return [
        ('upright',image,0,0),
        ('counterclockwise90',image.rotate(90,expand=True),90,0),
        ('upside_down',image.rotate(180,expand=True),180,0),
        ('clockwise90',image.rotate(270,expand=True),270,0),
        ('skew_ccw7',image.rotate(7,expand=True,fillcolor='white'),0,7),
        ('skew_cw7',image.rotate(-7,expand=True,fillcolor='white'),0,-7),
        ('upside_down_skew7',image.rotate(187,expand=True,fillcolor='white'),180,7),
        ('blank',Image.new('RGB',image.size,'white'),None,None),
        ('illustration_without_text',shapes,None,None),
    ]


def audit():
    from backend.ocr import OcrPipeline, RapidOcrExecutor
    from backend.knowledge_store import KnowledgeStore
    executor = RapidOcrExecutor()
    pipeline = OcrPipeline(executor)
    compact = lambda text: re.sub(r'\s|[，。:：（）()；;]','',text)
    gold = ['紧急工单首次响应时间为2小时','一般工单首次响应时间为24小时','生效日期2025年1月1日']
    records = []
    for name,image,rotation,skew in cases():
        buffer = io.BytesIO()
        image.save(buffer,format='PNG')
        raw = buffer.getvalue()
        result = pipeline.run(raw,language='chi_sim+eng').to_dict()
        orientation = result.get('metadata',{}).get('orientation',{})
        recognition = all(phrase in compact(result['text']) for phrase in gold) if rotation is not None else not result['text'].strip()
        reading_order = (result['text'].splitlines()[0].startswith('售后响应通知')
                         and result['text'].splitlines()[-1].startswith('本通知仅用于OCR')) if rotation is not None and result['text'] else rotation is None
        detected = (orientation.get('status')=='undetermined') if rotation is None else (
            orientation.get('status')=='estimated' and orientation.get('rotation_ccw_degrees')==rotation
            and abs(orientation.get('skew_ccw_degrees',999)-skew)<2.0)
        alerts = set(result.get('warnings',[]))
        alerted = rotation in (None,0) or 'page_orientation_detected' in alerts
        alerted = alerted and (skew is None or abs(skew)<2 or 'page_skew_detected' in alerts)
        records.append({'id':name,'expected_rotation_ccw':rotation,'expected_skew_ccw':skew,
            'passed':detected and recognition and alerted and reading_order,'detection_passed':detected,'recognition_passed':recognition,
            'reading_order_passed':reading_order,
            'alert_passed':alerted,'original_sha256':hashlib.sha256(raw).hexdigest(),
            'orientation':orientation,'warnings':result.get('warnings',[]),'text':result['text']})
    # Ingest an actual upside-down source and ensure both source bytes and alerts survive.
    with tempfile.TemporaryDirectory(prefix='ict8-orientation-ingest-') as directory:
        buffer = io.BytesIO()
        cases()[2][1].save(buffer,format='PNG')
        raw = buffer.getvalue()
        store = KnowledgeStore(Path(directory),ocr_pipeline=pipeline)
        record = store.ingest(raw,document_id='rotated',title='颠倒扫描件',modality='image',filename='rotated.png',language='chi_sim+eng')
        path,_ = store.original('rotated')
        detail = store.document('rotated')
        orientation = next((chunk['metadata'].get('ocr_metadata',{}).get('orientation',{})
                            for chunk in detail['chunks'] if chunk['content_type']=='ocr_region'),{})
        records.append({'id':'ingest_preserves_source_and_orientation_alert',
            'passed':path.read_bytes()==raw and record['routing']['review_required']
                and 'page_orientation_detected' in record['warnings'] and orientation.get('rotation_ccw_degrees')==180,
            'source_preserved':path.read_bytes()==raw,'review_required':record['routing']['review_required'],
            'warnings':record['warnings'],'orientation':orientation})
        from docx import Document
        import fitz
        word = Document()
        word.add_picture(io.BytesIO(raw))
        word_bytes = io.BytesIO()
        word.save(word_bytes)
        pdf = fitz.open()
        page = pdf.new_page(width=1120,height=520)
        page.insert_image(page.rect,stream=raw)
        pdf_bytes = pdf.tobytes()
        pdf.close()
        for modality,content in (('docx',word_bytes.getvalue()),('pdf',pdf_bytes)):
            record = store.ingest(content,document_id='rotated-'+modality,title='颠倒扫描'+modality,
                modality=modality,filename='rotated.'+modality,language='chi_sim+eng')
            detail = store.document('rotated-'+modality)
            source,_ = store.original('rotated-'+modality)
            orientation = next((chunk['metadata']['ocr_metadata']['orientation'] for chunk in detail['chunks']
                                if chunk['metadata'].get('ocr_metadata',{}).get('orientation')), {})
            records.append({'id':modality+'_scan_preserves_orientation_evidence',
                'passed':source.read_bytes()==content and record['routing']['review_required']
                    and any('page_orientation_detected' in warning for warning in record['warnings'])
                    and orientation.get('rotation_ccw_degrees')==180,
                'source_preserved':source.read_bytes()==content,'warnings':record['warnings'],'orientation':orientation})
        from backend.image_quality import ImageEnhancer
        preview = ImageEnhancer().enhance(raw,transforms=('rotate_to_upright',),rotation_degrees=180)
        preview_ocr = pipeline.run(preview.image_bytes,language='chi_sim+eng').to_dict()
        preview_orientation = preview_ocr['metadata']['orientation']
        records.append({'id':'corrected_preview_is_upright_without_overwriting_source',
            'passed':preview_orientation.get('status')=='estimated' and preview_orientation.get('rotation_ccw_degrees')==0
                and all(phrase in compact(preview_ocr['text']) for phrase in gold) and path.read_bytes()==raw,
            'source_preserved':path.read_bytes()==raw,'preview_orientation':preview_orientation})
    return records


def main():
    records = audit()
    report = {'created_at':datetime.now(timezone.utc).isoformat(),
        'scope':'actual_local_ocr_synthetic_orientation_development_audit_not_blind_accuracy',
        'external_model_called':False,'passed':sum(row['passed'] for row in records),'total':len(records),'cases':records}
    first = ROOT/'docs/ORIENTATION_FIRST_RUN.json'
    if not first.exists():
        first.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (ROOT/'docs/ORIENTATION_REPORT.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False),flush=True)
    return 0 if report['passed']==report['total'] else 1


if __name__=='__main__':
    raise SystemExit(main())

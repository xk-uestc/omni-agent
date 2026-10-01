"""Actual local OCR paired corruption checks, plus structured-query perturbations."""
import io
import json
import platform
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from PIL import Image, ImageEnhance, ImageFilter

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ict-track8'))


def main():
    from backend.ocr import RapidOcrExecutor, OcrPipeline
    from backend.nl2sql.engine import Nl2SqlEngine
    from backend.image_quality import ImageQualityAnalyzer
    executor=RapidOcrExecutor()
    pipeline=OcrPipeline(executor)
    image=Image.open(ROOT/'samples/documents/service-scan.png').convert('RGB')
    variants={'clean':image,'rotate180':image.rotate(180),'rotate90':image.rotate(90,expand=True),
              'blur1.5':image.filter(ImageFilter.GaussianBlur(1.5)),
              'low_contrast':ImageEnhance.Contrast(image).enhance(0.25),
              'downsample':image.resize((560,260),Image.Resampling.LANCZOS),
              'small_text':image.resize((280,130),Image.Resampling.LANCZOS),
              'dark':ImageEnhance.Brightness(image).enhance(0.12),
              'washed_out':ImageEnhance.Contrast(image).enhance(0.04),
              'blur3':image.filter(ImageFilter.GaussianBlur(3))}
    records=[]
    compact=lambda text:re.sub(r'\s|[，。:：（）()；;]', '', text)
    expected=['紧急工单首次响应时间为2小时','一般工单首次响应时间为24小时','生效日期2025年1月1日']
    for name,variant in variants.items():
        buffer=io.BytesIO();variant.save(buffer,format='PNG');raw=buffer.getvalue()
        started=time.perf_counter()
        baseline=executor.execute(raw,language='chi_sim+eng')
        recovered=pipeline.run(raw,language='chi_sim+eng').to_dict()
        row={'variant':name,'baseline_correct':all(word in compact(baseline.text) for word in expected),
             'pipeline_correct':all(word in compact(recovered['text']) for word in expected),
             'baseline_text':baseline.text,'pipeline':recovered,'quality':ImageQualityAnalyzer().analyze(raw).to_dict(),
             'latency_ms':round((time.perf_counter()-started)*1000,3)}
        records.append(row)
        print(json.dumps({'variant':name,'baseline':row['baseline_correct'],'pipeline':row['pipeline_correct']},ensure_ascii=False),flush=True)
    engine=Nl2SqlEngine(ROOT/'ict-track8/data/demo_sales.sqlite')
    gold=engine.answer('2025年华东地区销售额').rows
    queries=['二〇二五年华东地区销售额','２０２５年华东地区销售额','2025 年 华东 地区 销售额','2025年華東地區銷售額','2025年华东地区销受额']
    structured=[]
    for question in queries:
        result=engine.answer(question)
        structured.append({'question':question,'pass':result.status=='ok' and result.rows==gold,'status':result.status,'rows':result.rows})
    report={'created_at':datetime.now(timezone.utc).isoformat(),'scope':'synthetic_pair_corruption_not_OHR_benchmark',
            'environment':{'python':platform.python_version(),'platform':platform.platform()},'ocr':records,'query_pairs':structured,
            'summary':{'ocr_baseline_correct':sum(row['baseline_correct'] for row in records),'ocr_pipeline_correct':sum(row['pipeline_correct'] for row in records),'ocr_total':len(records),'structured_correct':sum(row['pass'] for row in structured),'structured_total':len(structured)}}
    (ROOT/'docs/ROBUSTNESS_REPORT.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report['summary'],ensure_ascii=False))


if __name__=='__main__':
    main()

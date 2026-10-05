"""Recover visible text in empty observed grid cells without inventing blank values."""
from copy import deepcopy
from io import BytesIO
import hashlib
import math
import time
import unicodedata
from PIL import Image,ImageOps,ImageEnhance
from .amount_cell_review import AmountCellReviewAgent
from .table_arithmetic import TableArithmeticAgent
from .cell_crop_quality import suppress_disconnected_top_edge


class BlankCellReviewAgent:
    def run(self,raw,structure,recognize,*,max_cells=4,max_seconds=15):
        if type(max_cells) is not int or not 1<=max_cells<=4 or type(max_seconds) not in (int,float) or not math.isfinite(max_seconds) or not 0<max_seconds<=60:raise ValueError('空白单元格复核预算无效')
        result={'agent':'BlankCellReviewAgent','reviews':[],'status':'not_needed','source_sha256':hashlib.sha256(raw).hexdigest(),
            'calls':0,'original_observations_replaced':False,'independent_recognizer':False,
            'warning':'空白格仅在原图可见文字且多预处理一致时给候选；同一OCR模型可能重复犯错，空白不填零。'}
        if structure.get('status')!='structure_observed':return {**result,'status':'unavailable'}
        cells=[cell for row in structure.get('body_cells',[]) for cell in row if cell.get('status')!='covered_by_merged_cell'
            and cell.get('text')=='' and cell.get('rowspan')==cell.get('colspan')==1]
        if not cells:return result
        if recognize is None:return {**result,'status':'unavailable','reason':'line_recognizer_unavailable'}
        preview=deepcopy(structure);started=time.monotonic();accepted=0
        with Image.open(BytesIO(raw)) as source:
            if source.width*source.height>10_000_000:return {**result,'status':'unavailable','reason':'pixel_budget_exceeded'}
            image=source.convert('RGB')
            for cell in cells[:max_cells]:
                if time.monotonic()-started>=max_seconds:break
                mapping=cell.get('original_geometry') or {};points=mapping.get('polygon_px')
                review={'row':cell['row'],'column':cell['column'],'original_text':'','accepted':False,'attempts':[]};result['reviews'].append(review)
                if mapping.get('source_sha256')!=result['source_sha256'] or mapping.get('original_bbox_eligible') is not True or not AmountCellReviewAgent.valid_polygon(points,image.size):
                    review['reason']='source_or_geometry_invalid';continue
                width=round(math.dist(points[0],points[1]));height=round(math.dist(points[0],points[3]))
                if width<12 or height<12 or width*height>250_000:review['reason']='cell_size_invalid';continue
                crop=image.transform((width,height),Image.Transform.QUAD,tuple(v for p in (points[0],points[3],points[2],points[1]) for v in p),Image.Resampling.BICUBIC)
                crop=crop.crop((2,2,width-2,height-2))
                crop,edge_evidence=suppress_disconnected_top_edge(crop)
                review['crop_edge_quality']=edge_evidence
                ink=ImageOps.grayscale(crop).point(lambda v:255 if v<210 else 0).getbbox()
                if ink is None:review['reason']='no_visible_ink';continue
                content=(max(0,ink[0]-3),max(0,ink[1]-3),min(crop.width,ink[2]+3),min(crop.height,ink[3]+3));crop=crop.crop(content)
                variants=[('crop_rgb',crop),('upscale2',crop.resize((crop.width*2,crop.height*2),Image.Resampling.LANCZOS)),
                    ('contrast',ImageEnhance.Contrast(ImageOps.grayscale(crop)).enhance(1.8))]
                values=[];seen=set();complete=True
                for name,variant in variants:
                    if time.monotonic()-started>=max_seconds:complete=False;break
                    output=BytesIO();variant.save(output,format='PNG');data=output.getvalue();sha=hashlib.sha256(data).hexdigest()
                    if sha in seen:continue
                    seen.add(sha);attempt={'preprocessing':name,'crop_sha256':sha,'source_polygon_px':deepcopy(points),
                        'inset_pixels':2,'content_bbox_px':list(content),'output_size_px':list(variant.size)};review['attempts'].append(attempt);result['calls']+=1
                    try:
                        observations=recognize(data)
                        if not isinstance(observations,list) or len(observations)!=1:complete=False;attempt['status']='ambiguous';continue
                        text=observations[0].get('text');confidence=observations[0].get('confidence')
                        if not isinstance(text,str) or not text.strip() or len(text)>200 or type(confidence) not in (int,float) or not math.isfinite(confidence) or not .9<=confidence<=1:
                            complete=False;attempt['status']='unreliable';continue
                        attempt.update(text=text,confidence=confidence,status='observed');values.append(text)
                    except (ValueError,RuntimeError,TypeError,KeyError):complete=False;attempt['status']='failed'
                if complete and len(values)>=2 and len({unicodedata.normalize('NFKC',v).strip() for v in values})==1:
                    review.update(accepted=True,candidate_text=values[0],source_sha256=result['source_sha256'],original_geometry=deepcopy(mapping));accepted+=1
                    from .pdf_amount_verification import amount
                    import re
                    review['candidate_kind']='ambiguous_amount_text' if re.search(r'[$€£¥￥]|USD|EUR|GBP|CNY|RMB',values[0],re.I) and amount(values[0]) is None else 'observed_text'
                    for row in preview['body_cells']:
                        for target in row:
                            if (target['row'],target['column'])==(cell['row'],cell['column']):
                                target['original_ocr_text']='';target['text']=values[0];target.pop('amount_check',None)
                else:review['reason']='no_consistent_reliable_candidate'
        result.update(status='candidates_available' if accepted else 'needs_review',accepted_count=accepted,
            suspect_count=len(cells),max_cells=max_cells,scheduling_budget_seconds=max_seconds,elapsed_seconds=round(time.monotonic()-started,3))
        if accepted:result['revised_preview']={'body_cells':preview['body_cells'],'arithmetic_check':TableArithmeticAgent().run(preview),
            'cell_values_verified':False,'original_observations_replaced':False}
        return result

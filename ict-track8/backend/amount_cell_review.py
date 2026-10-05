"""Bounded source-bound OCR rechecks; publish candidates, never alter observations."""
from copy import deepcopy
from io import BytesIO
import hashlib
import math
import re
import time
from PIL import Image,ImageEnhance,ImageOps
from .pdf_amount_verification import amount
from .table_arithmetic import TableArithmeticAgent
from .cell_crop_quality import suppress_disconnected_top_edge


class AmountCellReviewAgent:
    def run(self,raw,structure,recognize,*,max_cells=4,max_seconds=20):
        if type(max_cells) is not int or not 1<=max_cells<=4 or type(max_seconds) not in (int,float) or not math.isfinite(max_seconds) or not 0<max_seconds<=60:
            raise ValueError('局部金额复核预算无效')
        result={'agent':'AmountCellReviewAgent','status':'not_needed','reviews':[],
            'source_sha256':hashlib.sha256(raw).hexdigest(),'calls':0,'ocr_values_replaced':False,
            'independent_recognizer':False,'warning':'局部复核仍使用同一OCR模型；多预处理一致只是候选证据，不代表金额真值。'}
        if structure.get('status')!='structure_observed':return {**result,'status':'unavailable'}
        suspects=[cell for row in structure.get('body_cells',[]) for cell in row
            if cell.get('status')!='covered_by_merged_cell' and isinstance(cell.get('text'),str)
            and re.search(r'[$€£¥￥]|USD|EUR|GBP|CNY|RMB',cell['text'],re.I) and amount(cell['text']) is None]
        if not suspects:return result
        if recognize is None:return {**result,'status':'unavailable','reason':'line_recognizer_unavailable'}
        started=time.monotonic();preview=deepcopy(structure);accepted=0
        with Image.open(BytesIO(raw)) as source:
            if source.width*source.height>10_000_000:return {**result,'status':'unavailable','reason':'source_pixel_budget_exceeded'}
            image=source.convert('RGB')
            for cell in suspects[:max_cells]:
                if time.monotonic()-started>=max_seconds:break
                review={'row':cell['row'],'column':cell['column'],'original_text':cell['text'],'accepted':False,'attempts':[]}
                result['reviews'].append(review)
                mapping=cell.get('original_geometry') or {};points=mapping.get('polygon_px')
                if (mapping.get('source_sha256')!=result['source_sha256'] or mapping.get('original_bbox_eligible') is not True
                    or not self.valid_polygon(points,image.size)):
                    review['reason']='source_or_geometry_invalid';continue
                width=round((math.dist(points[0],points[1])+math.dist(points[2],points[3]))/2)
                height=round((math.dist(points[0],points[3])+math.dist(points[1],points[2]))/2)
                if width<12 or height<8 or width*height>1_000_000:
                    review['reason']='cell_crop_size_invalid';continue
                quad=tuple(v for p in (points[0],points[3],points[2],points[1]) for v in p)
                crop=image.transform((width,height),Image.Transform.QUAD,quad,Image.Resampling.BICUBIC)
                inset=2
                crop=crop.crop((inset,inset,width-inset,height-inset))
                crop,edge_evidence=suppress_disconnected_top_edge(crop)
                review['crop_edge_quality']=edge_evidence
                ink=ImageOps.grayscale(crop).point(lambda value:255 if value<230 else 0).getbbox()
                if ink is None:review['reason']='cell_contains_no_visible_ink';continue
                content=(max(0,ink[0]-3),max(0,ink[1]-3),min(crop.width,ink[2]+3),min(crop.height,ink[3]+3))
                crop=crop.crop(content)
                variants=[('crop_rgb',crop),('crop_upscale2',crop.resize((crop.width*2,crop.height*2),Image.Resampling.LANCZOS)),
                    ('crop_contrast',ImageEnhance.Contrast(ImageOps.grayscale(crop)).enhance(1.8))]
                values=[];seen=set();complete=True
                for name,variant in variants:
                    if time.monotonic()-started>=max_seconds:complete=False;break
                    stream=BytesIO();variant.save(stream,format='PNG');data=stream.getvalue();sha=hashlib.sha256(data).hexdigest()
                    if sha in seen:continue
                    seen.add(sha);attempt={'preprocessing':name,'crop_sha256':sha,'source_polygon_px':deepcopy(points),
                        'inset_pixels':inset,'output_size_px':list(variant.size)}
                    attempt['content_bbox_in_rectified_inset_cell_px']=list(content)
                    review['attempts'].append(attempt);result['calls']+=1
                    try:
                        observations=recognize(data)
                        if not isinstance(observations,list) or len(observations)!=1:
                            attempt['status']='not_one_amount';complete=False;continue
                        observation=observations[0];text=observation.get('text');confidence=observation.get('confidence')
                        if isinstance(text,str) and len(text)<=200:attempt['text']=text
                        if type(confidence) in (int,float) and math.isfinite(confidence):attempt['confidence']=confidence
                        if not isinstance(text,str) or len(text)>200 or type(confidence) not in (int,float) or not math.isfinite(confidence) or not .85<=confidence<=1:
                            attempt['status']='unreliable_observation';complete=False;continue
                        parsed=amount(text);attempt.update({'text':text,'confidence':confidence,'amount':parsed})
                        if parsed is None:attempt['status']='ambiguous_amount';complete=False;continue
                        attempt['status']='observed';values.append((parsed,text))
                    except (ValueError,RuntimeError,TypeError,KeyError):
                        attempt['status']='recognition_failed';complete=False
                from decimal import Decimal
                identities={(value['currency'],Decimal(value['value'])) for value,_ in values}
                if complete and len(values)>=2 and len(identities)==1:
                    review.update({'accepted':True,'reason':'distinct_preprocessing_amount_agreement',
                        'candidate_text':values[0][1],'candidate_amount':values[0][0],
                        'source_sha256':result['source_sha256'],'original_geometry':deepcopy(mapping)})
                    for row in preview['body_cells']:
                        for target in row:
                            if (target['row'],target['column'])==(cell['row'],cell['column']):
                                target['original_ocr_text']=target['text'];target['text']=values[0][1]
                                target.pop('amount_check',None)
                    accepted+=1
                else:review['reason']='conflicting_candidates' if len(identities)>1 else 'insufficient_consistent_evidence'
        result.update({'status':'candidates_available' if accepted else 'needs_review','accepted_count':accepted,
            'suspect_count':len(suspects),'max_cells':max_cells,'scheduling_budget_seconds':max_seconds,
            'elapsed_seconds':round(time.monotonic()-started,3)})
        if accepted:
            preview['arithmetic_check']=TableArithmeticAgent().run(preview)
            result['revised_preview']={'body_cells':preview['body_cells'],'arithmetic_check':preview['arithmetic_check'],
                'cell_values_verified':False,'original_observations_replaced':False}
        return result

    @staticmethod
    def valid_polygon(points,size):
        if not isinstance(points,list) or len(points)!=4:return False
        if any(not isinstance(p,list) or len(p)!=2 or any(type(v) not in (int,float) or not math.isfinite(v) for v in p)
            or not 0<=p[0]<=size[0] or not 0<=p[1]<=size[1] for p in points):return False
        turns=[]
        for i in range(4):
            a,b,c=points[i],points[(i+1)%4],points[(i+2)%4]
            turns.append((b[0]-a[0])*(c[1]-b[1])-(b[1]-a[1])*(c[0]-b[0]))
        return all(v>0 for v in turns) or all(v<0 for v in turns)

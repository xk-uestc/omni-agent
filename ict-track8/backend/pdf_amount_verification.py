"""Compare OCR amounts to separately extracted, source-bound native PDF words."""
from copy import deepcopy
from decimal import Decimal,InvalidOperation,localcontext
import hashlib
import math
import re
import unicodedata


def amount(text):
    if not isinstance(text,str) or len(text)>200:return None
    text=unicodedata.normalize('NFKC',text).strip()
    negative=text.startswith('(') and text.endswith(')')
    if negative:text=text[1:-1].strip()
    match=re.fullmatch(r'([+-]?)\s*(\$|€|£|¥|USD|EUR|GBP|CNY|RMB)\s*([+-]?\d[\d,.]*)',text,re.I)
    if not match:return None
    sign,currency,number=match.groups()
    # Dollar/yen signs alone do not identify USD/CAD/AUD or CNY/JPY.
    currency={'€':'EUR','£':'GBP','RMB':'CNY'}.get(currency.upper(),currency.upper())
    if sign and (negative or number.startswith(('+','-'))):return None
    if negative and number.startswith(('+','-')):return None
    number=sign+number
    if not re.fullmatch(r'[+-]?(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d{1,2})?',number):return None
    try:
        with localcontext() as context:
            context.prec=256
            value=Decimal(number.replace(',',''))*(-1 if negative else 1)
    except InvalidOperation:return None
    return {'currency':currency,'value':format(value,'f')}


def _inside(point,polygon):
    crosses=[]
    for index,left in enumerate(polygon):
        right=polygon[(index+1)%len(polygon)]
        crosses.append((right[0]-left[0])*(point[1]-left[1])-(right[1]-left[1])*(point[0]-left[0]))
    return all(v>=-0.001 for v in crosses) or all(v<=0.001 for v in crosses)


def _within_rounding(point,polygon):
    if _inside(point,polygon):return True
    distances=[]
    for index,left in enumerate(polygon):
        right=polygon[(index+1)%len(polygon)];dx,dy=right[0]-left[0],right[1]-left[1]
        length=dx*dx+dy*dy
        if not length:continue
        t=max(0,min(1,((point[0]-left[0])*dx+(point[1]-left[1])*dy)/length))
        distances.append(math.hypot(point[0]-left[0]-t*dx,point[1]-left[1]-t*dy))
    return bool(distances) and min(distances)<=.5


class PdfAmountVerificationAgent:
    def run(self,raw,structures):
        import fitz
        sha=hashlib.sha256(raw).hexdigest();output=deepcopy(structures)
        with fitz.open(stream=raw,filetype='pdf') as document:
            cache={}
            for structure in output:
                if structure.get('status')!='structure_observed':continue
                page_no=structure.get('page_no')
                if type(page_no) is not int or not 1<=page_no<=len(document):continue
                if page_no not in cache:cache[page_no]=document[page_no-1].get_text('words')
                words=cache[page_no];counts={}
                for row in structure['body_cells']:
                    for cell in row:
                        if cell.get('status')=='covered_by_merged_cell':continue
                        check=self.check(cell,words,sha,page_no,structure.get('source_pdf_sha256'))
                        cell['amount_check']=check;counts[check['status']]=counts.get(check['status'],0)+1
                structure['amount_verification']={'agent':'PdfAmountVerificationAgent','counts':counts,
                    'basis':'independent_native_pdf_text_at_original_cell_coordinates',
                    'native_layer_is_ground_truth':False,'ocr_values_replaced':False,
                    'warning':'仅核对OCR与原PDF文字层是否一致；原文字层可能有错误，扫描件无独立文字时仍未核验。'}
        return output

    @staticmethod
    def check(cell,words,sha,page_no,structure_sha):
        result={'status':'unavailable','reason':'native_reference_unavailable','source_pdf_sha256':sha,'page_no':page_no}
        mapping=(cell.get('original_geometry') or {}).get('pdf_geometry') or {}
        polygon=mapping.get('fitz_unrotated_polygon_pt')
        if (structure_sha!=sha or mapping.get('source_pdf_sha256')!=sha or mapping.get('page_no')!=page_no
            or mapping.get('pdf_highlight_eligible') is not True):
            return {**result,'reason':'source_or_page_mismatch'}
        if (not isinstance(polygon,list) or len(polygon)!=4 or any(not isinstance(p,list) or len(p)!=2
            or any(type(v) not in (int,float) or not math.isfinite(v) for v in p) for p in polygon)):
            return {**result,'reason':'invalid_cell_polygon'}
        area=sum(polygon[i][0]*polygon[(i+1)%4][1]-polygon[(i+1)%4][0]*polygon[i][1] for i in range(4))
        turns=[(polygon[(i+1)%4][0]-polygon[i][0])*(polygon[(i+2)%4][1]-polygon[(i+1)%4][1])
            -(polygon[(i+1)%4][1]-polygon[i][1])*(polygon[(i+2)%4][0]-polygon[(i+1)%4][0]) for i in range(4)]
        if abs(area)<.01 or not (all(v>0 for v in turns) or all(v<0 for v in turns)):
            return {**result,'reason':'invalid_cell_polygon'}
        if len(words)>20000:return {**result,'reason':'native_word_budget_exceeded'}
        selected=[];crossing=False
        for word in words:
            if len(word)<5:continue
            x0,y0,x1,y1,text=word[:5]
            if (any(type(v) not in (int,float) or not math.isfinite(v) for v in (x0,y0,x1,y1))
                or not isinstance(text,str) or x0>=x1 or y0>=y1):continue
            corners=[(x0,y0),(x1,y0),(x1,y1),(x0,y1)]
            if _inside(((x0+x1)/2,(y0+y1)/2),polygon):
                if not all(_within_rounding(p,polygon) for p in corners):crossing=True
                selected.append({'text':text,'bbox_pt':[x0,y0,x1,y1]})
        if crossing:return {**result,'reason':'native_word_crosses_cell_boundary'}
        if len(selected)>32:return {**result,'reason':'ambiguous_multiple_native_words'}
        selected.sort(key=lambda w:(round(w['bbox_pt'][1],1),w['bbox_pt'][0]))
        native=' '.join(item['text'] for item in selected)
        observed=cell.get('text','');left,right=amount(observed),amount(native)
        result.update({'ocr_text':observed,'native_text':native,'native_words':selected,
            'cell_polygon_pt':polygon,'native_bbox_rounding_tolerance_pt':.5,'native_value':right,'ocr_value':left})
        if not selected:return result
        if not left and not right:
            if re.search(r'[$€£¥]|USD|EUR|GBP|CNY|RMB',str(observed)+' '+native,re.I):
                return {**result,'reason':'native_amount_ambiguous'}
            return {**result,'status':'not_applicable','reason':'no_unambiguous_currency_amount'}
        if not right:return {**result,'reason':'native_amount_ambiguous'}
        if not left:return {**result,'status':'needs_review','reason':'ocr_amount_ambiguous_or_missing'}
        if left['currency']!=right['currency']:
            if left['currency'] in {'$','¥'} or right['currency'] in {'$','¥'}:
                return {**result,'status':'needs_review','reason':'currency_identity_unspecified'}
            return {**result,'status':'conflict','reason':'currency_mismatch'}
        matched=Decimal(left['value'])==Decimal(right['value'])
        return {**result,'status':'matched' if matched else 'conflict',
            'reason':'native_ocr_amount_agreement' if matched else 'amount_value_mismatch'}

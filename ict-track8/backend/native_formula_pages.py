"""Explicit mathematical continuation over adjacent original PDF pages.

Only a visibly open formula at one page's bottom and the next page's first
body line can join. No parameter values, units or semantic bindings are
inferred. The ordinary calculator and source-contract gates still apply.
"""
import hashlib
import re

import fitz

from .document_analysis import DocumentAnalyzer, SafeFormulaEvaluator

_START=re.compile(r'^([A-Za-z\u3400-\u9fff][A-Za-z0-9_ \u3400-\u9fff-]{0,48})\s*[=＝]\s*(.{1,250})$')
_MATH=re.compile(r'[A-Za-z0-9_\u3400-\u9fff\s.+*/%()^×÷−-]{1,250}')


def extract_cross_page_formulas(raw, *, label, expected_sha256):
    if (not isinstance(raw,bytes) or not raw or len(raw)>20*1024*1024
            or hashlib.sha256(raw).hexdigest()!=expected_sha256):
        raise ValueError('cross_page_formula_source_invalid')
    found=[]
    with fitz.open(stream=raw,filetype='pdf') as document:
        if document.needs_pass or len(document)>1000:
            raise ValueError('cross_page_formula_source_budget')
        def lines(page):
            # Rotation and multiple-column ambiguity are unsupported here.
            if page.rotation:return []
            result=[]
            for block in page.get_text('dict').get('blocks',[]):
                if block.get('type')!=0:continue
                for line in block.get('lines',[]):
                    text=''.join(span['text'] for span in line['spans']).strip()
                    if not text or re.fullmatch(r'(?:Page\s*)?\d+',text,re.I):continue
                    if tuple(line.get('dir',()))!=(1.0,0.0):return []
                    result.append({'text':text,'bbox':list(line['bbox']),
                                   'styles':sorted({(span['font'],round(span['size'],3)) for span in line['spans']})})
            if len(result)>20000:raise ValueError('cross_page_formula_word_budget')
            return sorted(result,key=lambda entry:(entry['bbox'][1],entry['bbox'][0]))
        previous=lines(document[0]) if len(document) else []
        for index in range(1,len(document)):
            current=lines(document[index])
            if previous and current:
                left,right=previous[-1],current[0]
                match=_START.fullmatch(left['text'])
                if (match and match[1].strip()==label and _MATH.fullmatch(match[2])
                        and _MATH.fullmatch(right['text']) and '=' not in right['text']
                        and left['bbox'][1]>=document[index-1].rect.height*.65
                        and right['bbox'][3]<=document[index].rect.height*.35
                        and not any(other['bbox'][1]<left['bbox'][3] and other['bbox'][3]>left['bbox'][1] for other in previous[:-1])
                        and not any(other['bbox'][1]<right['bbox'][3] and other['bbox'][3]>right['bbox'][1] for other in current[1:])
                        and abs(left['bbox'][0]-right['bbox'][0])<=12
                        and left['styles']==right['styles']):
                    head=DocumentAnalyzer._normalize_formula(match[2])
                    # Never join two already valid independent expressions.
                    open_tail=bool(re.search(r'[+*/%-]\s*$',head) or head.count('(')>head.count(')'))
                    if open_tail:
                        source=match[2]+' '+right['text']
                        # Normalization removes spaces; that must not merge
                        # unrelated prose words into a fabricated parameter.
                        if re.search(r'[A-Za-z0-9_\u3400-\u9fff]\s+[A-Za-z0-9_\u3400-\u9fff]',source):
                            previous=current
                            continue
                        expression=DocumentAnalyzer._normalize_formula(source)
                        try:parameters=SafeFormulaEvaluator().parameters(expression)
                        except (ValueError,SyntaxError):parameters=None
                        if parameters is not None:
                            parts=[{'page_no':index,'text':left['text'],'bbox_fitz_unrotated_pt':left['bbox']},
                                   {'page_no':index+1,'text':right['text'],'bbox_fitz_unrotated_pt':right['bbox']}]
                            found.append({'expression':expression,'parameters':list(parameters),'label':label,
                                          'sha256':expected_sha256,'locator':f'page:{index}-{index+1}:native-formula',
                                          'cross_page_literal_proof':{'policy':'explicit_open_math_adjacent_page_v1',
                                                                      'parts':parts,'calculator_input_eligible':False}})
            previous=current
    return found

"""Explain explicit observed total rows without certifying OCR or completeness."""
from copy import deepcopy
from decimal import Decimal,localcontext
import re
import unicodedata
from .pdf_amount_verification import amount


class TableArithmeticAgent:
    TOTAL={'total','grand total','合计','总计','总额'}
    SUBTOTAL={'subtotal','sub-total','sub total','小计','分计','累计','balance brought forward','carried forward','结转','承前'}

    @staticmethod
    def label(text):
        return unicodedata.normalize('NFKC',text).strip().rstrip(':：').strip().casefold() if isinstance(text,str) else ''

    def run(self,structure):
        output={'agent':'TableArithmeticAgent','status':'unavailable','checks':[],
            'cell_values_verified':False,'table_completeness_verified':False,
            'warning':'合计检查只说明观察值的算术关系，不证明OCR、业务口径或表格完整性。'}
        rows=structure.get('body_cells',[]);cols=structure.get('column_count')
        if (not isinstance(rows,list) or not 2<=len(rows)<=400 or type(cols) is not int or not 2<=cols<=64
            or len(rows)*cols>2000 or any(not isinstance(row,list) or len(row)!=cols or any(not isinstance(cell,dict) for cell in row) for row in rows)):
            return {**output,'reason':'invalid_or_over_budget_table'}
        totals=[i for i,row in enumerate(rows) if self.label(row[0].get('text')) in self.TOTAL]
        if not totals:return {**output,'status':'not_applicable','reason':'no_explicit_total_row'}
        if len(totals)!=1 or totals[0]!=len(rows)-1:return {**output,'reason':'ambiguous_total_scope'}
        if any(self.label(row[0].get('text')) in self.SUBTOTAL or re.search(r'\b(?:sub.?total|grand total|total)\b|小计|累计|合计|总计|结转|承前',self.label(row[0].get('text'))) for row in rows[:-1]):
            return {**output,'reason':'subtotal_or_carry_forward_present'}
        if any(cell.get('status')=='covered_by_merged_cell' or cell.get('rowspan',1)!=1 or cell.get('colspan',1)!=1 for row in rows for cell in row):
            return {**output,'reason':'merged_body_cells_require_review'}
        if any(not self.label(row[0].get('text')) for row in rows[:-1]):return {**output,'reason':'missing_detail_label'}
        headers=' '.join(str(part) for column in structure.get('columns',[]) for part in column.get('header_path',[]))
        if re.search(r'万元|千元|百万元|亿元|thousand|million|billion|\b000s?\b|%',headers,re.I):
            return {**output,'reason':'unit_scale_requires_confirmation'}
        for column in range(1,cols):
            parsed=[amount(row[column].get('text')) for row in rows]
            reference=lambda row_index:{'row_index':row_index,'original_row':rows[row_index][column].get('row'),
                'column':column,'text':rows[row_index][column].get('text'),
                'original_geometry':deepcopy(rows[row_index][column].get('original_geometry'))}
            base={'column':column,'total_cell':reference(len(rows)-1)}
            if not all(parsed):
                problems=[i for i,value in enumerate(parsed) if value is None]
                check={**base,'status':'unavailable','reason':'amount_missing_or_ambiguous',
                    'problem_cells':[reference(i) for i in problems]}
                known=[value for value in parsed[:-1] if value is not None]
                if len(problems)==1 and problems[0]<len(rows)-1 and parsed[-1] and known and len({value['currency'] for value in [*known,parsed[-1]]})==1:
                    with localcontext() as context:
                        context.prec=512
                        subtotal=sum((Decimal(value['value']) for value in known),Decimal(0))
                        remainder=Decimal(parsed[-1]['value'])-subtotal
                    check['review_candidate']={'cell':reference(problems[0]),'currency':parsed[-1]['currency'],
                        'candidate_amount':format(remainder,'f'),'known_detail_sum':format(subtotal,'f'),
                        'observed_total':parsed[-1]['value'],'basis':'observed_total_minus_other_observed_details',
                        'confirmed':False,'ocr_value_replaced':False,
                        'warning':'此值只是合计约束推得的候选，其他金额或总计可能错误；请对照原图确认。'}
                output['checks'].append(check);continue
            currencies={value['currency'] for value in parsed}
            if len(currencies)!=1:
                output['checks'].append({**base,'status':'unavailable','reason':'mixed_or_unspecified_currency_identity'});continue
            with localcontext() as context:
                context.prec=512
                calculated=sum((Decimal(value['value']) for value in parsed[:-1]),Decimal(0))
                observed=Decimal(parsed[-1]['value']);difference=observed-calculated
            output['checks'].append({**base,'status':'arithmetic_consistent' if difference==0 else 'conflict',
                'reason':'detail_sum_matches_observed_total' if difference==0 else 'detail_sum_differs_from_observed_total',
                'detail_cells':[reference(i) for i in range(len(rows)-1)],'currency':parsed[0]['currency'],
                'detail_count':len(rows)-1,'computed_total':format(calculated,'f'),
                'observed_total':format(observed,'f'),'difference':format(difference,'f')})
        statuses=[check['status'] for check in output['checks']]
        output['status']='conflict' if 'conflict' in statuses else 'unavailable' if 'unavailable' in statuses else 'arithmetic_consistent'
        output['reason']='observed_amount_arithmetic_only'
        return output

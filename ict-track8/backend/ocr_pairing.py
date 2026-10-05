"""Bounded geometry observations for tightly spaced labels and currency values."""
from copy import deepcopy
import re


class CloseLabelAmountAgent:
    """Observe a pair without asserting that it is a table or a verified total."""
    def run(self,valid,number_groups):
        currency=re.compile(r'^[$€£¥₹]\s*[-+]?\d[\d,]*(?:\.\d+)?\s*(?:m|bn|%)?$',re.I)
        result=[]
        numbers=[cell for group in number_groups for cell in group]
        for group in number_groups:
            if len(group)<3:continue
            for amount in group:
                if len(result)>=16:return result
                if not currency.fullmatch(amount['text']) or amount['confidence']<.8:continue
                x0,y0,x1,y1=amount['bbox'];height=y1-y0
                labels=[]
                for cell in valid:
                    if cell in numbers or cell['confidence']<.8:continue
                    left,top,right,bottom=cell['bbox'];label_height=bottom-top
                    gap=x0-right
                    if not max(2,min(height,label_height)*.12)<=gap<=12:continue
                    if abs((top+bottom-y0-y1)/2)>min(height,label_height)*.3:continue
                    if min(bottom,y1)-max(top,y0)<min(height,label_height)*.7:continue
                    labels.append(cell)
                if len(labels)!=1:continue
                label=labels[0]
                # Another numeric observation in the corridor makes the pair
                # ambiguous; do not reinterpret it as part of a label.
                if any(cell is not amount and cell['bbox'][0]<x0 and cell['bbox'][2]>label['bbox'][0]
                    and min(cell['bbox'][3],y1)>max(cell['bbox'][1],y0) for cell in numbers):continue
                result.append({'status':'candidate','is_table':False,'scope':'close_same_line_label_amount_pair',
                    'verification':'ocr_box_geometry_only','cell_values_verified':False,
                    'row_count':1,'column_count':2,'rows':[[deepcopy(label),deepcopy(amount)]],
                    'observed_gap_px':x0-label['bbox'][2],'numeric_column_observation_count':len(group),
                    'limitations':['semantic_total_not_verified','table_membership_not_verified']})
        return result

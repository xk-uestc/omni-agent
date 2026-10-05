"""Conservative page orientation evidence from local OCR lines, not EXIF guesses.

RapidOCR rectifies text quadrilaterals and rotates tall crops 90 degrees CCW.
Its 0/180 classifier therefore needs both the source axis and crop convention.
All angles exposed here use PIL's counterclockwise convention.
"""
from __future__ import annotations

import math
from statistics import median


def _fold(angle):
    return (angle+90)%180-90


def assess_text_orientation(boxes, classifications, recognized):
    report = {'status':'undetermined','method':'ocr_line_geometry_and_local_0_180_classifier',
              'scope':'horizontal_printed_text_page_estimate_not_semantic_truth',
              'rotation_ccw_degrees':None,'skew_ccw_degrees':None,'correction_ccw_degrees':None,
              'eligible_lines':0,'confidence':None,
              'confidence_kind':'mean_line_classification_not_page_accuracy',
              'source_image_modified':False,'correction_scope':'ocr_text_regions_only'}
    evidence = []
    for box, classification, recognition in zip(boxes or [],classifications or [],recognized or []):
        try:
            points = [[float(x),float(y)] for x,y in box]
            label,confidence = str(classification[0]),float(classification[1])
            text,recognition_confidence = str(recognition[0]),float(recognition[1])
            if len(points)!=4 or not all(math.isfinite(v) for p in points for v in p):
                continue
            if label not in {'0','180'} or not .9<=confidence<=1 or not .75<=recognition_confidence<=1 or len(text.strip())<4:
                continue
            width = sum(math.dist(points[a],points[b]) for a,b in ((0,1),(3,2)))/2
            height = sum(math.dist(points[a],points[b]) for a,b in ((0,3),(1,2)))/2
            if min(width,height)<=0 or max(width,height)<20 or max(width,height)/min(width,height)<2:
                continue
            edges = ((0,1),(3,2)) if width>=height else ((0,3),(1,2))
            dx = sum(points[b][0]-points[a][0] for a,b in edges)
            dy = sum(points[b][1]-points[a][1] for a,b in edges)
            angle = _fold(math.degrees(math.atan2(dy,dx)))
            vertical = abs(angle)>60
            if not vertical and abs(angle)>=30:
                continue
            residual = _fold(angle-90) if vertical else angle
            if abs(residual)>15:
                continue
            rotation = (90 if label=='180' else 270) if vertical else (180 if label=='180' else 0)
            evidence.append({'rotation':rotation,'residual':residual,'confidence':confidence})
        except (TypeError,ValueError,IndexError,OverflowError):
            continue
    report['eligible_lines'] = len(evidence)
    if len(evidence)<2:
        return {**report,'reason':'insufficient_reliable_text_lines'}
    counts = {rotation:sum(row['rotation']==rotation for row in evidence) for rotation in (0,90,180,270)}
    dominant = max(counts,key=counts.get)
    agreement = counts[dominant]/len(evidence)
    report['line_agreement'] = round(agreement,4)
    if agreement<.8 or counts[dominant]<2:
        return {**report,'reason':'mixed_text_directions'}
    selected = [row for row in evidence if row['rotation']==dominant]
    residual = median(row['residual'] for row in selected)
    deviation = median(abs(row['residual']-residual) for row in selected)
    report['angle_mad_degrees'] = round(deviation,3)
    if deviation>2:
        return {**report,'reason':'inconsistent_text_line_angles'}
    correction = (residual-dominant+180)%360-180
    return {**report,'status':'estimated','reason':None,'rotation_ccw_degrees':dominant,
            'skew_ccw_degrees':round(-residual,3),'correction_ccw_degrees':round(correction,3),
            'confidence':round(sum(row['confidence'] for row in selected)/len(selected),4)}


def orientation_warnings(report):
    if report.get('status')!='estimated':
        return ('page_orientation_undetermined',)
    warnings = []
    if report.get('rotation_ccw_degrees'):
        warnings.append('page_orientation_detected')
    if abs(report.get('skew_ccw_degrees',0))>=2:
        warnings.append('page_skew_detected')
    return tuple(warnings)


def upright_reading_order(regions, size, orientation):
    """Sort corrected text lines while keeping original executor-input boxes.

    This is geometric line order, not a multicolumn/table layout model.
    Undetermined pages keep the OCR engine's order.
    """
    if orientation.get('status')!='estimated':
        return regions
    angle = math.radians(orientation['correction_ccw_degrees'])
    cosine,sine = math.cos(angle),math.sin(angle)
    center_x,center_y = size[0]/2,size[1]/2
    projections = {}
    def bounds(region):
        projected = [(center_x+cosine*(x-center_x)+sine*(y-center_y),
                      center_y-sine*(x-center_x)+cosine*(y-center_y)) for x,y in region['bbox']]
        projections[id(region)] = projected
        return min(x for x,y in projected),min(y for x,y in projected),max(x for x,y in projected),max(y for x,y in projected)
    def line_order(items):
        if not items:
            return []
        anchor = min(box[0] for _,box in items)
        aligned = []
        for region,box in items:
            points = sorted(projections[id(region)])
            left,right = points[:2],points[2:]
            lx,rx = sum(p[0] for p in left)/2,sum(p[0] for p in right)/2
            ly,ry = sum(p[1] for p in left)/2,sum(p[1] for p in right)/2
            slope = (ry-ly)/(rx-lx) if rx-lx>1e-6 else 0.
            height = (abs(left[1][1]-left[0][1])+abs(right[1][1]-right[0][1]))/2
            if (height<=0 or abs(slope)>math.tan(math.radians(15))
                    or abs(anchor-lx)>max(rx-lx,3*height)):
                center,height = (box[1]+box[3])/2,max(1.,box[3]-box[1])
            else:
                # Compare local line heights at the same column x. Min-y
                # favours long tilted lines over preceding short lines.
                center = ly+slope*(anchor-lx)
            aligned.append((region,box,center,height))
        aligned.sort(key=lambda item:(item[2],item[1][0]))
        rows=[]
        for item in aligned:
            _,box,center,height=item
            row=rows[-1] if rows else []
            compatible = row and all(
                abs(center-other[2])<=.4*min(height,other[3])
                and min(box[2],other[1][2])-max(box[0],other[1][0])<=0
                for other in row)
            if compatible:row.append(item)
            else:rows.append([item])
        return [item[0] for row in rows for item in sorted(row,key=lambda item:item[1][0])]
    boxes = [(region, bounds(region)) for region in regions]
    ordered = sorted(boxes, key=lambda item: (item[1][1], item[1][0]))
    if len(boxes) < 16:
        return line_order(boxes)
    # Detect a clear central gutter between substantial prose lines. Short
    # numeric/table cells cannot nominate a prose-column split. This remains
    # a geometry heuristic, never a semantic document-layout guarantee.
    page_width = max(box[2] for _, box in boxes) - min(box[0] for _, box in boxes)
    origin = min(box[0] for _, box in boxes)
    prose = [(region, box) for region, box in boxes
             if .24 * page_width <= box[2]-box[0] <= .58 * page_width
             and len(region.get('text', '').strip()) >= 20]
    candidates = []
    for fraction in (.4, .45, .5, .55, .6):
        split = origin + page_width * fraction
        left = [item for item in prose if item[1][2] < split]
        right = [item for item in prose if item[1][0] > split]
        if len(left) < 4 or len(right) < 4 or len(left)+len(right) < .8 * len(prose):
            continue
        gutter = min(box[0] for _, box in right) - max(box[2] for _, box in left)
        if gutter > page_width * .012:
            candidates.append((len(left)+len(right), gutter, split))
    if not candidates:
        return line_order(boxes)
    split = max(candidates)[2]
    result, band = [], []
    def emit():
        result.extend(line_order([item for item in band if item[1][0]<split]))
        result.extend(line_order([item for item in band if item[1][0]>=split]))
        band.clear()
    for region, box in ordered:
        if box[0] < split < box[2]:
            emit()
            result.append(region)
        else:
            band.append((region, box))
    emit()
    return result

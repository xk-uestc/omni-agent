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
    def position(region):
        projected = [(center_x+cosine*(x-center_x)+sine*(y-center_y),
                      center_y-sine*(x-center_x)+cosine*(y-center_y)) for x,y in region['bbox']]
        return min(y for x,y in projected),min(x for x,y in projected)
    return sorted(regions,key=position)

"""Hash-bound affine image coordinates, using pixel edges rather than indices.

Six coefficients [a,b,c,d,e,f] mean x'=a*x+b*y+c, y'=d*x+e*y+f.
These are deliberately NOT the ordering of a PyMuPDF Matrix.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import math
import re

IDENTITY = [1., 0., 0., 0., 1., 0.]


def point(matrix, xy):
    if len(matrix)==9:
        x,y=xy
        weight=matrix[6]*x+matrix[7]*y+matrix[8]
        if not math.isfinite(weight) or abs(weight)<1e-10:raise ValueError('projective horizon')
        return [(matrix[0]*x+matrix[1]*y+matrix[2])/weight,(matrix[3]*x+matrix[4]*y+matrix[5])/weight]
    a,b,c,d,e,f = matrix
    x,y = xy
    return [a*x+b*y+c, d*x+e*y+f]


def compose(after, before):
    if len(after)==9 or len(before)==9:
        a=projective(after);b=projective(before)
        result=[sum(a[row*3+k]*b[k*3+column] for k in range(3)) for row in range(3) for column in range(3)]
        return normalize_projective(result)
    a,b,c,d,e,f = after
    g,h,i,j,k,l = before
    return [a*g+b*j, a*h+b*k, a*i+b*l+c,
            d*g+e*j, d*h+e*k, d*i+e*l+f]


def inverse(matrix):
    if len(matrix)==9:
        a,b,c,d,e,f,g,h,i=projective(matrix)
        cofactors=[e*i-f*h,c*h-b*i,b*f-c*e,f*g-d*i,a*i-c*g,c*d-a*f,d*h-e*g,b*g-a*h,a*e-b*d]
        determinant=a*cofactors[0]+b*cofactors[3]+c*cofactors[6]
        if not math.isfinite(determinant) or abs(determinant)<1e-12:raise ValueError('singular projective matrix')
        return normalize_projective([v/determinant for v in cofactors])
    if len(matrix) != 6 or not all(type(x) in (int,float) and math.isfinite(x) for x in matrix):
        raise ValueError('invalid affine matrix')
    a,b,c,d,e,f = matrix
    det = a*e-b*d
    if not math.isfinite(det) or abs(det) < 1e-12:
        raise ValueError('singular affine matrix')
    return [e/det, -b/det, (b*f-e*c)/det,
            -d/det, a/det, (d*c-a*f)/det]


def projective(matrix):
    if len(matrix) not in (6,9) or not all(type(v) in (int,float) and math.isfinite(v) for v in matrix):
        raise ValueError('invalid image matrix')
    return list(matrix) if len(matrix)==9 else [*matrix,0.,0.,1.]


def normalize_projective(matrix):
    values=projective(matrix)
    if abs(values[8])<1e-12:raise ValueError('projective origin at horizon')
    result=[v/values[8] for v in values]
    if not all(math.isfinite(v) for v in result):raise ValueError('invalid projective scale')
    return result


def exif_affine(orientation, width, height):
    return {2:[-1.,0.,width,0.,1.,0.], 3:[-1.,0.,width,0.,-1.,height],
            4:[1.,0.,0.,0.,-1.,height], 5:[0.,1.,0.,1.,0.,0.],
            6:[0.,-1.,height,1.,0.,0.], 7:[0.,-1.,height,-1.,0.,width],
            8:[0.,1.,0.,-1.,0.,width]}.get(orientation, IDENTITY.copy())


def rotation_affine(ccw_degrees, before_size, after_size):
    # Match Pillow's default centre and expand=True translation, including
    # its actual rounded output size. Image y coordinates increase downward.
    angle = math.radians(ccw_degrees % 360)
    a,b = round(math.cos(angle),15),round(math.sin(angle),15)
    d,e = -b,a
    w,h = before_size
    nw,nh = after_size
    return [a,b,nw/2-a*w/2-b*h/2, d,e,nh/2-d*w/2-e*h/2]


def polygon(box):
    if not isinstance(box,(list,tuple)) or len(box) != 4:
        raise ValueError('invalid region')
    if all(type(v) in (int,float) and math.isfinite(v) for v in box):
        left,top,right,bottom = box
        if left >= right or top >= bottom:
            raise ValueError('empty region')
        return [[left,top],[right,top],[right,bottom],[left,bottom]]
    if not all(isinstance(p,(list,tuple)) and len(p)==2
               and all(type(v) in (int,float) and math.isfinite(v) for v in p) for p in box):
        raise ValueError('invalid polygon')
    points = [list(p) for p in box]
    area = sum(points[i][0]*points[(i+1)%4][1]-points[(i+1)%4][0]*points[i][1] for i in range(4))
    if abs(area) <= 1e-9:
        raise ValueError('degenerate polygon')
    turns = [(points[(i+1)%4][0]-points[i][0])*(points[(i+2)%4][1]-points[(i+1)%4][1])
             -(points[(i+1)%4][1]-points[i][1])*(points[(i+2)%4][0]-points[(i+1)%4][0]) for i in range(4)]
    if not (all(v>1e-9 for v in turns) or all(v< -1e-9 for v in turns)):
        raise ValueError('nonconvex polygon')
    return points


def map_region(box, geometry):
    try:
        corners = polygon(box)
        output = geometry['output']['size_px']
        source = geometry['source']['size_px']
        inside_output = all(-1e-6<=p[0]<=output[0]+1e-6 and -1e-6<=p[1]<=output[1]+1e-6 for p in corners)
        reverse=geometry['output_to_source']
        if len(reverse)==9:
            weights=[reverse[6]*p[0]+reverse[7]*p[1]+reverse[8] for p in corners]
            if not (all(v>1e-10 for v in weights) or all(v< -1e-10 for v in weights)):raise ValueError('region crosses projective horizon')
        mapped = [point(geometry['output_to_source'], p) for p in corners]
        if not all(math.isfinite(v) for p in mapped for v in p):
            raise ValueError('nonfinite mapped polygon')
        inside_source = all(-1e-6<=p[0]<=source[0]+1e-6 and -1e-6<=p[1]<=source[1]+1e-6 for p in mapped)
        eligible = inside_output and inside_source
        return {'status':'inside_source' if eligible else 'outside_executor' if not inside_output else 'intersects_source_padding',
                'coordinate_scope':'source_image_stored_pixel_edges',
                'source_sha256':geometry['source']['sha256'],
                'polygon_px':mapped,
                'bbox_px':[min(p[0] for p in mapped),min(p[1] for p in mapped),
                           max(p[0] for p in mapped),max(p[1] for p in mapped)],
                'original_bbox_eligible':eligible}
    except (ValueError,TypeError,KeyError,IndexError):
        return {'status':'invalid_region','original_bbox_eligible':False}


def attach_original_coordinates(metadata, geometry, source_bytes, executor_bytes):
    """Annotate only when the executor declares the actual supplied frame.

    Bboxes and OCR facts remain in their original executor frame. Added source
    polygons describe geometry only, never independently verified cell values.
    """
    result = deepcopy(metadata)
    unavailable = {'status':'unavailable','original_bbox_eligible':False}
    frame = metadata.get('coordinate_frame',{})
    if not isinstance(frame,dict):frame={}
    if not isinstance(geometry,dict) or not geometry:
        result['original_pixel_mapping'] = {**unavailable,'reason':'enhancer_geometry_missing'}
        return result
    result['image_geometry'] = deepcopy(geometry)
    try:
        valid = (geometry['version'] in {'image-affine-chain-v1','image-projective-chain-v1'}
                 and geometry['source']['sha256']==hashlib.sha256(source_bytes).hexdigest()
                 and geometry['output']['sha256']==hashlib.sha256(executor_bytes).hexdigest()
                 and frame.get('scope')=='ocr_executor_input'
                 and frame.get('sha256')==geometry['output']['sha256']
                 and frame.get('size_px')==geometry['output']['size_px'])
        expected = inverse(geometry['source_to_output'])
        matrix_length=9 if geometry['version']=='image-projective-chain-v1' else 6
        valid = valid and len(expected)==matrix_length and len(geometry['output_to_source'])==matrix_length and all(abs(x-y)<1e-8 for x,y in zip(expected,geometry['output_to_source']))
    except (ValueError,TypeError,KeyError):
        valid = False
    if not valid:
        result['original_pixel_mapping'] = {**unavailable,'reason':'coordinate_frame_binding_mismatch'}
        return result
    regions = result.get('regions',[])
    grid_layout = result.get('scanned_grids')
    table_layout = result.get('table_layout')
    grids = grid_layout.get('tables',[]) if isinstance(grid_layout,dict) else []
    regional=table_layout.get('regional_candidates',[]) if isinstance(table_layout,dict) else []
    if not isinstance(regional,list) or len(regional)>16:
        result['original_pixel_mapping']={**unavailable,'reason':'regional_layout_budget_or_type_invalid'}
        return result
    sparse=table_layout.get('sparse_observations',[]) if isinstance(table_layout,dict) else []
    if not isinstance(sparse,list) or len(sparse)>32:
        result['original_pixel_mapping']={**unavailable,'reason':'sparse_layout_budget_or_type_invalid'}
        return result
    layouts=[table_layout,*regional,*sparse] if isinstance(table_layout,dict) else []
    rows = [row for layout in layouts if isinstance(layout,dict) for row in layout.get('rows',[])]
    if (not isinstance(regions,list) or len(regions)>2000 or not isinstance(grids,list) or len(grids)>16
            or not isinstance(rows,list) or sum(len(row) for row in rows if isinstance(row,list))>2000):
        result['original_pixel_mapping'] = {**unavailable,'reason':'region_budget_exceeded'}
        return result
    count = 0
    eligible = 0
    def annotate(item,key='bbox',target='original_geometry'):
        nonlocal count,eligible
        if isinstance(item,dict) and key in item:
            item[target] = map_region(item[key],geometry)
            count+=1
            eligible+=int(item[target]['original_bbox_eligible'])
    for region in regions:
        annotate(region)
    for row in rows:
        if isinstance(row,list):
            for cell in row:
                annotate(cell)
                annotate(cell,'crop_bbox_px','original_crop_geometry')
    for layout in layouts:
        if isinstance(layout,dict):
            for bridge in layout.get('continuity_evidence',[])[:200]:
                if isinstance(bridge,dict):
                    for observation in bridge.get('observations',[])[:12]:annotate(observation)
    for table in grids:
        if not isinstance(table,dict):continue
        annotate(table,'bbox_px')
        cells = table.get('cells',[])
        if not isinstance(cells,list) or sum(len(row) for row in cells if isinstance(row,list))>1000:
            continue
        for row in cells:
            if not isinstance(row,list):continue
            for cell in row:
                annotate(cell,'bbox_px')
                if not isinstance(cell,dict):continue
                observations = cell.get('observations',[])
                for observation in observations[:2000] if isinstance(observations,list) else []:annotate(observation,'bbox_px')
                crops = cell.get('crop_observations',[])
                for observation in crops[:8] if isinstance(crops,list) else []:
                    annotate(observation,'crop_bbox_px','original_crop_geometry')
    result['original_pixel_mapping'] = {'status':'mapped','source':deepcopy(geometry['source']),
        'coordinate_convention':geometry['coordinate_convention'],
        'mapped_region_count':count,'eligible_region_count':eligible,
        'ocr_values_independently_verified':False}
    return result


def bind_pdf_coordinates(metadata, page_geometry, render_sha256, render_size, pdf_sha256, page_no):
    """Extend selected image polygons through a matching PDF render chain.

    A scan remains an OCR observation. This adds locations, not semantic or
    numerical verification, and never changes executor/source-image bboxes.
    """
    result = deepcopy(metadata)
    # Rebinding replaces earlier PDF locations, including on failure.
    def clear_pdf_locations(value):
        if isinstance(value,dict):
            value.pop('pdf_geometry',None)
            value.pop('pdf_coordinate_mapping',None)
            for item in value.values():clear_pdf_locations(item)
        elif isinstance(value,list):
            for item in value:clear_pdf_locations(item)
    clear_pdf_locations(result)
    for key in ('scanned_table_evidence','borderless_table_evidence'):
        candidate=result.get(key)
        if isinstance(candidate,dict):
            candidate.pop('scanned_table_evidence',None)
            candidate.pop('borderless_table_evidence',None)
            result[key]=bind_pdf_coordinates(candidate,page_geometry,
                render_sha256,render_size,pdf_sha256,page_no)
    unavailable = {'status':'unavailable','reason':'pdf_render_binding_mismatch'}
    try:
        if (not isinstance(pdf_sha256,str) or re.fullmatch(r'[a-f0-9]{64}',pdf_sha256) is None
            or not isinstance(render_sha256,str) or re.fullmatch(r'[a-f0-9]{64}',render_sha256) is None
            or type(page_no) is not int or page_no<1
            or not isinstance(render_size,list) or len(render_size)!=2
            or any(type(value) is not int or value<=0 for value in render_size)):
            raise ValueError('invalid PDF identity')
        binding = result['original_pixel_mapping']
        if (binding['status']!='mapped' or binding['source']['sha256']!=render_sha256
                or binding['source']['size_px']!=render_size
                or page_geometry['mapping_status']!='complete'):
            result['pdf_coordinate_mapping']=unavailable
            return result
        mappings = page_geometry['mappings']
        def row_affine(values):
            if len(values)!=6 or any(type(value) not in (int,float) or not math.isfinite(value) for value in values):
                raise ValueError('invalid PDF affine')
            a,b,c,d,e,f=values
            return [a,c,e,b,d,f]
        user_to_unrotated = row_affine(mappings['pdf_user_to_fitz_unrotated'])
        unrotated_to_display = row_affine(mappings['fitz_unrotated_to_display'])
        display_to_render = row_affine(mappings['fitz_display_to_render_pixel'])
        render_to_display = inverse(display_to_render)
        render_to_unrotated = compose(inverse(unrotated_to_display),render_to_display)
        render_to_user = compose(inverse(user_to_unrotated),render_to_unrotated)
        display_rect = page_geometry['display_rect_fitz_pt']
        if (len(display_rect)!=4 or any(type(value) not in (int,float) or not math.isfinite(value) for value in display_rect)
            or display_rect[0]>=display_rect[2] or display_rect[1]>=display_rect[3]):
            raise ValueError('invalid PDF display rectangle')
    except (ValueError,KeyError,TypeError):
        result['pdf_coordinate_mapping']=unavailable
        return result
    count=0
    def extend(item,target='original_geometry'):
        nonlocal count
        location=item.get(target) if isinstance(item,dict) else None
        if not isinstance(location,dict) or not location.get('original_bbox_eligible'):
            return
        pixels=location.get('polygon_px')
        if (location.get('source_sha256')!=render_sha256
            or location.get('coordinate_scope')!='source_image_stored_pixel_edges'
            or not isinstance(pixels,list) or len(pixels)!=4
            or any(not isinstance(p,list) or len(p)!=2
                or any(type(v) not in (int,float) or not math.isfinite(v) for v in p) for p in pixels)
            or not all(0<=p[0]<=render_size[0] and 0<=p[1]<=render_size[1] for p in pixels)):
            return
        display=[point(render_to_display,p) for p in pixels]
        inside=all(display_rect[0]-1e-6<=p[0]<=display_rect[2]+1e-6
                   and display_rect[1]-1e-6<=p[1]<=display_rect[3]+1e-6 for p in display)
        location['pdf_geometry']={'status':'mapped' if inside else 'render_rounding_padding',
            'source_pdf_sha256':pdf_sha256,'page_no':page_no,
            'pdf_user_polygon_pt':[point(render_to_user,p) for p in pixels],
            'fitz_unrotated_polygon_pt':[point(render_to_unrotated,p) for p in pixels],
            'pdf_highlight_eligible':inside}
        count+=int(inside)
    for item in result.get('regions',[])[:2000]:extend(item)
    layout=result.get('table_layout')
    regional=layout.get('regional_candidates',[]) if isinstance(layout,dict) else []
    sparse=layout.get('sparse_observations',[]) if isinstance(layout,dict) else []
    layouts=[layout,*(regional[:16] if isinstance(regional,list) else []),
             *(sparse[:32] if isinstance(sparse,list) else [])] if isinstance(layout,dict) else []
    for row in [row for candidate in layouts if isinstance(candidate,dict) for row in candidate.get('rows',[])]:
        if isinstance(row,list):
            for item in row[:2000]:
                extend(item)
                extend(item,'original_crop_geometry')
    for candidate in layouts:
        if isinstance(candidate,dict):
            for bridge in candidate.get('continuity_evidence',[])[:200]:
                if isinstance(bridge,dict):
                    for observation in bridge.get('observations',[])[:12]:extend(observation)
    grids=result.get('scanned_grids')
    for table in grids.get('tables',[])[:16] if isinstance(grids,dict) else []:
        if not isinstance(table,dict):continue
        extend(table)
        for row in table.get('cells',[]):
            if not isinstance(row,list):continue
            for cell in row:
                if not isinstance(cell,dict):continue
                extend(cell)
                observations=cell.get('observations',[])
                for item in observations[:2000] if isinstance(observations,list) else []:extend(item)
                crops=cell.get('crop_observations',[])
                for item in crops[:8] if isinstance(crops,list) else []:extend(item,'original_crop_geometry')
    result['pdf_coordinate_mapping']={'status':'mapped','source_pdf_sha256':pdf_sha256,
        'page_no':page_no,'render_sha256':render_sha256,'eligible_region_count':count,
        'ocr_values_independently_verified':False}
    return result

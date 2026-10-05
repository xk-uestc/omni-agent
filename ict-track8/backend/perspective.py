"""Explicit observed page-corner rectification; no inferred text or table facts."""
import math
from PIL import Image


class PageBoundaryDetectionAgent:
    """Find a contrast-supported page outline; ambiguity remains uncorrected."""
    def _refine_edges(self,gray,points):
        import cv2
        import numpy as np
        lines=[]
        for k in range(4):
            a,b=points[k],points[(k+1)%4]
            tangent=b-a;normal=np.array([-tangent[1],tangent[0]])/np.linalg.norm(tangent)
            observed=[]
            for t in np.linspace(.12,.88,13):
                edge=a+t*tangent
                offsets=np.linspace(-6,6,49)
                values=[float(cv2.getRectSubPix(gray,(1,1),tuple((edge+normal*offset).astype(float)))[0,0]) for offset in offsets]
                if values[-1]-values[0]<255*.12:continue
                midpoint=(values[0]+values[-1])/2
                crossings=[i for i in range(len(values)-1) if values[i]<=midpoint<values[i+1]]
                if not crossings:continue
                index=min(crossings,key=lambda i:abs(offsets[i]))
                offset=offsets[index]+(midpoint-values[index])/(values[index+1]-values[index])*(offsets[index+1]-offsets[index])
                observed.append(edge+normal*offset)
            if len(observed)<8:return points,False
            points_on_edge=np.array(observed,np.float32)
            vx,vy,x,y=cv2.fitLine(points_on_edge,cv2.DIST_L2,0,.01,.01).reshape(-1)
            direction=np.array([vx,vy],float);origin=np.array([x,y],float)
            delta=points_on_edge-origin
            residuals=np.abs(delta[:,0]*direction[1]-delta[:,1]*direction[0])
            if float(np.max(residuals))>2:return points,False
            lines.append((origin,direction))
        refined=[]
        for k in range(4):
            previous,previous_direction=lines[(k-1)%4];current,current_direction=lines[k]
            matrix=np.column_stack([previous_direction,-current_direction])
            if abs(np.linalg.det(matrix))<.05:return points,False
            parameters=np.linalg.solve(matrix,current-previous)
            corner=previous+parameters[0]*previous_direction
            if np.linalg.norm(corner-points[k])>8:return points,False
            refined.append(corner)
        return np.array(refined),True

    def detect(self, image):
        import cv2
        import numpy as np
        from hashlib import sha256
        if min(image.size)<32 or image.width*image.height>40_000_000:
            return {'agent':'PageBoundaryDetectionAgent','status':'unavailable','reason':'page_detection_pixel_budget'}
        rgb=np.asarray(image.convert('RGB'))
        source_size=[image.width,image.height]
        base={'agent':'PageBoundaryDetectionAgent','coordinate_scope':'exif_normalized_pixel_edges',
            'size_px':source_size,'pixel_sha256':sha256(rgb.tobytes()).hexdigest(),
            'semantic_values_verified':False}
        ratio=min(1.,1600/max(image.size))
        sample=cv2.resize(rgb,(max(1,round(image.width*ratio)),max(1,round(image.height*ratio))))
        gray=cv2.cvtColor(sample,cv2.COLOR_RGB2GRAY)
        gray=cv2.GaussianBlur(gray,(5,5),0)
        height,width=gray.shape
        lab=cv2.cvtColor(sample,cv2.COLOR_RGB2LAB).astype(float)
        margin=max(1,round(min(width,height)*.025))
        border=np.concatenate([lab[:margin].reshape(-1,3),lab[-margin:].reshape(-1,3),
            lab[:,:margin].reshape(-1,3),lab[:,-margin:].reshape(-1,3)])
        background=np.median(border,axis=0)
        color_distance=np.minimum(np.linalg.norm(lab-background,axis=2),255).astype(np.uint8)
        color_distance=cv2.GaussianBlur(color_distance,(5,5),0)
        contours=[]
        for signal,basis in [(gray,'grayscale_luminance'),(color_distance,'distance_from_border_median_lab')]:
            _,mask=cv2.threshold(signal,0,255,cv2.THRESH_BINARY+cv2.THRESH_OTSU)
            found,_=cv2.findContours(mask,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
            contours.extend((contour,signal,basis,mask) for contour in sorted(found,key=cv2.contourArea,reverse=True)[:16])
        candidates=[]
        for contour,signal,basis,mask in contours:
            area=cv2.contourArea(contour)
            if not .25*width*height<=area<=.97*width*height:continue
            perimeter=cv2.arcLength(contour,True)
            quad=cv2.approxPolyDP(contour,.015*perimeter,True)
            if len(quad)!=4 or not cv2.isContourConvex(quad):continue
            points=quad.reshape(4,2).astype(float)
            center=points.mean(axis=0)
            points=points[np.argsort(np.arctan2(points[:,1]-center[1],points[:,0]-center[0]))]
            points=np.roll(points,-int(np.argmin(points.sum(axis=1))),axis=0)
            polygon_area=cv2.contourArea(points.astype(np.float32))
            if area/max(1.,polygon_area)<.97:continue
            interior=np.zeros_like(mask)
            cv2.fillConvexPoly(interior,points.astype(np.int32),255)
            foreground_fill=float(np.mean(mask[interior>0]>0))
            if foreground_fill<.7:continue
            if any(x<3 or y<3 or x>width-4 or y>height-4 for x,y in points):continue
            lengths=[math.dist(points[k],points[(k+1)%4]) for k in range(4)]
            if min(lengths)<min(width,height)*.15 or not .4<=max(lengths[0],lengths[2])/max(lengths[1],lengths[3])<=2.5:continue
            contrasts=[]
            for k in range(4):
                a,b=points[k],points[(k+1)%4]
                tangent=b-a
                normal=np.array([-tangent[1],tangent[0]])/np.linalg.norm(tangent)
                inside=[];outside=[]
                for t in np.linspace(.15,.85,9):
                    edge=a+(b-a)*t
                    for distance in (3.,5.):
                        ip=edge+normal*distance;op=edge-normal*distance
                        for location,target in [(ip,inside),(op,outside)]:
                            x,y=np.rint(location).astype(int)
                            if 0<=x<width and 0<=y<height:target.append(float(signal[y,x]))
                contrasts.append((float(np.median(inside))-float(np.median(outside)))/255 if inside and outside else 0.)
            if min(contrasts)<.12:continue
            points,refined=self._refine_edges(signal,points)
            if any(x<0 or y<0 or x>width-1 or y>height-1 for x,y in points):continue
            # Correct centre-index coordinates to pixel edges and undo the
            # actual resize ratio separately along each axis.
            original=[[(float(x)+.5)*image.width/width,(float(y)+.5)*image.height/height] for x,y in points]
            candidate={'quad_px':original,'area_fraction':area/(width*height),
                'minimum_edge_contrast':min(contrasts),'edge_contrasts':contrasts,
                'contrast_basis':basis,
                'foreground_fill_fraction':foreground_fill,
                'vertex_source':'local_edge_midpoint_line_intersections' if refined else 'threshold_contour_vertices',
                'contour_fill':area/max(1.,polygon_area)}
            duplicate=None
            current=np.array(original,np.float32)
            for position,existing in enumerate(candidates):
                previous=np.array(existing['quad_px'],np.float32)
                overlap,_=cv2.intersectConvexConvex(current,previous)
                union=cv2.contourArea(current)+cv2.contourArea(previous)-overlap
                if overlap/max(1.,union)>=.95:duplicate=position;break
            if duplicate is None:candidates.append(candidate)
            elif candidate['minimum_edge_contrast']>candidates[duplicate]['minimum_edge_contrast']:
                candidates[duplicate]=candidate
        if not candidates:return {**base,'status':'unavailable','reason':'no_unambiguous_contrast_supported_page'}
        candidates.sort(key=lambda candidate:candidate['area_fraction'],reverse=True)
        if len(candidates)>1 and candidates[1]['area_fraction']>=candidates[0]['area_fraction']*.5:
            return {**base,'status':'ambiguous','reason':'multiple_page_candidates','candidate_count':len(candidates)}
        return {**base,'status':'candidate','candidate_count':len(candidates),**candidates[0],
            'selection':'observed_contrast_supported_page_boundary'}

    def retains_observed_text(self, detection, metadata):
        """A geometric outline cannot discard OCR already seen outside it."""
        import cv2
        import numpy as np
        from .image_geometry import point,polygon,inverse
        geometry=metadata.get('image_geometry',{})
        steps=geometry.get('steps',[])
        normalization=steps[0].get('forward_affine') if steps else None
        if detection.get('status')!='candidate' or normalization is None:return False
        source=detection.get('source_image_sha256')
        if not source or geometry.get('source',{}).get('sha256')!=source:return False
        try:
            inverse(normalization)
            quad=np.array(polygon(detection['quad_px']),dtype=np.float32)
        except (ValueError,TypeError,KeyError):return False
        observed=0
        for region in metadata.get('regions',[]):
            if not isinstance(region,dict):return False
            if not region.get('text'):continue
            confidence=region.get('confidence')
            if type(confidence) not in (int,float) or not math.isfinite(confidence) or not 0<=confidence<=1:return False
            observed+=1
            location=region.get('original_geometry',{})
            if (not location.get('original_bbox_eligible') or location.get('source_sha256')!=source
                    or location.get('coordinate_scope')!='source_image_stored_pixel_edges'):return False
            try:
                corners=polygon(location.get('polygon_px'))
                for stored in corners:
                    normalized=point(normalization,stored)
                    if not all(math.isfinite(value) for value in normalized):return False
                    if cv2.pointPolygonTest(quad,tuple(normalized),True)<-2:return False
            except (ValueError,TypeError):return False
        return observed>0


class PerspectiveRectificationAgent:
    def rectify(self,image,quad,*,selection='explicit_page_corners'):
        import cv2
        import numpy as np
        from .image_geometry import polygon,compose,inverse,point
        if selection not in {'explicit_page_corners','observed_page_boundary'}:raise ValueError('invalid page corner source')
        points=polygon(quad)
        if any(not 0<=x<=image.width or not 0<=y<=image.height for x,y in points):raise ValueError('page corners outside normalized image')
        # Explicit order: top-left, top-right, bottom-right, bottom-left.
        area=sum(points[k][0]*points[(k+1)%4][1]-points[(k+1)%4][0]*points[k][1] for k in range(4))/2
        if area<=0 or area<image.width*image.height*.05:raise ValueError('invalid page corner order or area')
        lengths=[math.dist(points[k],points[(k+1)%4]) for k in range(4)]
        width=round(max(lengths[0],lengths[2]));height=round(max(lengths[1],lengths[3]))
        native_size=[width,height]
        if min(width,height)<32:raise ValueError('perspective output too small')
        output_scale=min(1.,6000/max(width,height),math.sqrt(6_000_000/(width*height)))
        width=max(1,math.floor(width*output_scale));height=max(1,math.floor(height*output_scale))
        transform=cv2.getPerspectiveTransform(np.array(points,dtype=np.float32),
            np.array([[0,0],[width,0],[width,height],[0,height]],dtype=np.float32)).reshape(-1).tolist()
        reverse=inverse(transform)
        # Coordinates describe pixel edges. OpenCV samples integer pixel centres.
        raster_transform=compose([1.,0.,-.5,0.,1.,-.5],compose(transform,[1.,0.,.5,0.,1.,.5]))
        for corner in [[0,0],[width,0],[width,height],[0,height]]:
            mapped=point(reverse,corner)
            if not all(math.isfinite(v) for v in mapped):raise ValueError('invalid perspective mapping')
        pixels=cv2.warpPerspective(np.asarray(image.convert('RGB')),np.array(raster_transform).reshape(3,3),
            (width,height),flags=cv2.INTER_CUBIC,borderMode=cv2.BORDER_CONSTANT,borderValue=(255,255,255))
        return Image.fromarray(pixels),transform,{'agent':'PerspectiveRectificationAgent',
            'corner_scope':'exif_normalized_pixel_edges','page_quad_px':points,
            'native_rectified_size_px':native_size,'output_resolution_scale':output_scale,
            'semantic_values_verified':False,'selection':selection}

"""Prefer a measured upright grid frame before counting OCR-filled cells."""


class ScannedGridSelectionAgent:
    @staticmethod
    def correction(orientation,grids):
        correction=orientation['correction_ccw_degrees']
        rotation=orientation.get('rotation_ccw_degrees')
        # A complete ruled grid observed BEFORE correction has axis-aligned
        # separators. Do not destroy that geometry with noisy OCR box skew.
        cardinal=(grids.get('status')=='observed' and grids.get('tables')
                  and rotation in (90,180,270) and abs(orientation.get('skew_ccw_degrees',99))<=3)
        if cardinal:
            correction=(-rotation+180)%360-180
        return correction,'observed_axis_aligned_grid_and_text_direction' if cardinal else 'text_direction_and_skew'

    @staticmethod
    def orientation_priority(metadata, applied_correction=None):
        orientation=metadata.get('orientation',{})
        if orientation.get('status')=='estimated':
            return (2,'measured_upright_text') if orientation.get('rotation_ccw_degrees')==0 else (0,'measured_sideways_or_inverted_text')
        if applied_correction and applied_correction.get('triggered'):
            return 1,'corrected_from_reliable_prior_text_direction'
        return 0,'direction_undetermined'

    def score(self,metadata,anchors,confidence,applied_correction=None):
        priority,basis=self.orientation_priority(metadata,applied_correction)
        observed=sum(bool(cell.get('text')) for cell in anchors)
        return (priority,observed/max(1,len(anchors)),observed,confidence or 0.),basis

"""Report original rubric points on 0-10, independently from binary gates."""
from __future__ import annotations
import math

def score(record,rubric):
    entries=rubric.get('criteria',rubric.get('units'))
    maxima={row['id']:row['points'] for row in entries}
    if len(maxima)!=len(entries) or sum(maxima.values())!=rubric['maximum_score'] or rubric['maximum_score']!=10:
        raise ValueError('original_ten_point_rubric_required')
    points=record['criterion_points']
    if set(points)!=set(maxima):raise ValueError('all_original_rubric_criteria_required')
    lower=[];upper=[];unknown=[]
    def valid(v,maximum):return type(v) in (int,float) and math.isfinite(v) and v==int(v) and 0<=v<=maximum
    for key,maximum in maxima.items():
        value=points[key]
        if value is None:
            bounds=record.get('criterion_score_bounds',{}).get(key,[0,maximum])
            if (not isinstance(bounds,list) or len(bounds)!=2 or not all(valid(v,maximum) for v in bounds)
                or bounds[0]>bounds[1]):raise ValueError('unknown_score_bounds_invalid:'+key)
            lower.append(bounds[0]);upper.append(bounds[1]);unknown.append(key)
        else:
            if not valid(value,maximum):raise ValueError('criterion_points_invalid:'+key)
            lower.append(value);upper.append(value)
    gate=record.get('source_grounded_success')
    if gate is not None and type(gate) is not bool:raise ValueError('original_gate_boolean_or_null_required')
    low=sum(lower);high=sum(upper)
    return {'score':None if unknown else low,'score_lower':low,'score_upper':high,
        'score_scale':10,'score_percent':None if unknown else low*10,
        'unknown_criteria':unknown,'criterion_points':dict(points),
        'source_grounded_success':gate,'binary_gate_converted_to_points':False}

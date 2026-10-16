"""Evaluate approved assertions on actual Keploy replay observations, never on recordings."""
import json
from urllib.parse import urlsplit,parse_qsl
from tda.runner import json_pointer,equal,contains


def verify_replay(case,observations):
    if case.cleanup:return 'blocked','Chưa có mapping cleanup trong replay report.'
    oracles={s.step_index:s.assertions for s in case.oracle.steps};used=set()
    for index,step in enumerate(case.steps):
        if step.auth!='none':return 'blocked','Replay chưa có mapping xác minh credential test đã duyệt.'
        candidates=[]
        for n,row in enumerate(observations):
            if n in used:continue
            request=row.get('request') or {};url=urlsplit(str(request.get('url','')))
            if request.get('method')!=step.method or url.path!=step.path:continue
            if sorted(parse_qsl(url.query,keep_blank_values=True))!=sorted((q.name,q.value) for q in step.query):continue
            body=request.get('body') or ''
            if step.body_json is not None:
                try:
                    if not equal(json.loads(body),json.loads(step.body_json)):continue
                except (ValueError,TypeError):continue
            elif body:continue
            candidates.append((n,row))
        if len(candidates)!=1:return 'blocked','Không xác định duy nhất replay observation cho từng bước đã duyệt.'
        n,row=candidates[0];used.add(n)
        if row.get('status') not in ('PASSED','FAILED') or type(row.get('status_code')) is not int or not 100<=row['status_code']<=599:return 'blocked','Replay chưa có actual response hợp lệ.'
        for assertion in oracles.get(index,[]):
            expected=json.loads(assertion.expected_json);kind=assertion.kind
            if kind=='status':passed=row['status_code']==expected
            elif kind in ('text_equals','text_contains'):
                if not isinstance(row.get('body'),str):return 'blocked','Thiếu actual response body.'
                passed=row['body']==expected if kind=='text_equals' else expected in row['body']
            elif kind in ('json_equals','json_contains'):
                try:actual=json_pointer(json.loads(row['body']),assertion.pointer)
                except (ValueError,TypeError,KeyError,IndexError):return 'defect','Actual JSON không khớp expected result đã duyệt.'
                passed=equal(actual,expected) if kind=='json_equals' else contains(actual,expected)
            else:return 'blocked','Replay chưa cung cấp bằng chứng cho assertion '+kind
            if not passed:return 'defect','Replay vi phạm assertion đã duyệt tại bước '+str(index+1)
    return 'pass','Đã kiểm chứng assertions trên actual replay observations.'

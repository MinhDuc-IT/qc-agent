"""Pre-execution shape feedback and lossless string-oracle encoding."""
import json

from ..domain.contracts import validate_json


STRING_ASSERTIONS={'text_equals','text_contains','raises','exception_message',
                   'dom_text_equals','dom_text_contains','dom_value_equals'}


def normalize_string_oracles(oracles):
    for oracle in oracles:
        for step in oracle.steps:
            for assertion in step.assertions:
                if assertion.kind not in STRING_ASSERTIONS:continue
                try:value=validate_json(assertion.expected_json)
                except ValueError:value=None
                if not isinstance(value,str):
                    # This assertion kind requires text. Preserve its literal text,
                    # including numbers, empty strings and partial JSON snippets.
                    assertion.expected_json=json.dumps(assertion.expected_json,ensure_ascii=False)


def generated_shape_feedback(case):
    errors=[]
    if case.runner in {'http','ai','k6'}:
        if not case.steps:errors.append('Runner HTTP/AI/k6 cần ít nhất một request cụ thể trong steps.')
        if case.unit or case.browser:errors.append('Runner HTTP/AI/k6 không được có cấu hình unit/browser.')
        if case.runner=='k6' and (not case.load or case.cleanup or any(step.method not in {'GET','HEAD'} for step in case.steps)):
            errors.append('k6 cần load, request chỉ đọc và không có cleanup.')
    elif case.runner=='unit':
        if not case.unit or case.steps or case.cleanup or case.browser or case.load or case.grading:
            errors.append('Unit cần unit; steps và cleanup phải rỗng, cấu hình khác phải null.')
        if case.unit:
            try:
                if not isinstance(validate_json(case.unit.args_json),list) or not isinstance(validate_json(case.unit.kwargs_json),dict):
                    raise ValueError('invalid arguments')
            except ValueError:errors.append('Unit cần args_json là danh sách JSON và kwargs_json là object JSON.')
    elif case.runner=='browser':
        if not case.browser or case.steps or case.cleanup or case.unit or case.load or case.grading:
            errors.append('Browser cần browser.actions; steps và cleanup phải rỗng.')
        elif case.browser.actions[0].action!='goto':errors.append('Thao tác browser đầu tiên phải là goto.')
        if case.browser:
            for index,action in enumerate(case.browser.actions):
                if action.action=='goto' and not action.path or action.action!='goto' and not action.selector or action.action in {'fill','select'} and action.value is None:
                    errors.append('Thao tác browser '+str(index)+' thiếu path, selector hoặc value phù hợp.')
    return errors


def assertion_fingerprints(oracles):
    from collections import Counter
    result={}
    for oracle in oracles:
        claims=[]
        for step in oracle.steps:
            for assertion in step.assertions:
                try:validate_json(assertion.expected_json)
                except ValueError:continue
                claims.append(json.dumps(assertion.model_dump(),sort_keys=True,ensure_ascii=False))
        result[oracle.case_id]=Counter(claims)
    return result


def preserves_browser_inputs(original,corrected):
    if original is None:return True
    if corrected is None or original.auth!=corrected.auth:return False
    actions=corrected.actions
    if original.actions[0].action!='goto' and len(actions)==len(original.actions)+1 and actions[0].action=='goto':
        actions=actions[1:]
    if len(actions)!=len(original.actions):return False
    return all(old.action==new.action and all(getattr(old,field) is None or getattr(old,field)==getattr(new,field)
        for field in ('path','selector','value')) for old,new in zip(original.actions,actions))

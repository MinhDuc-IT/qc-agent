"""Translate unambiguous JSONPath notation into the executor's JSON Pointer format."""
import json
import re


def json_pointer(pointer):
    if pointer in ('','$'):return ''
    if pointer.startswith('/'):
        if re.search(r'~(?![01])',pointer):raise ValueError('Invalid JSON Pointer escape')
        return pointer
    if not pointer.startswith('$'):raise ValueError('Use JSON Pointer or a simple $.field[index] path')
    tokens=[];position=1
    while position<len(pointer):
        match=re.match(r'\.([A-Za-z_][A-Za-z0-9_-]*)|\[(0|[1-9][0-9]*)\]',pointer[position:])
        if not match:raise ValueError('Unsupported JSONPath expression; wildcards and filters require explicit review')
        tokens.append(match.group(1) if match.group(1) is not None else match.group(2));position+=match.end()
    return ''.join('/'+token.replace('~','~0').replace('/','~1') for token in tokens)


def expected_constraint(expected):
    """Preserve the entire approved JSON value, including legacy wrapped-body expectations."""
    pointer=expected.pointer
    if expected.assertion_kind in ('json_equals','json_contains'):
        pointer=json_pointer(pointer)
        value=json.loads(expected.expected_json) if expected.expected_json is not None else None
        if pointer and expected.pointer.startswith('$') and isinstance(value,(dict,list)):
            candidate=value
            try:
                for token in pointer.split('/')[1:]:
                    token=token.replace('~1','/').replace('~0','~')
                    candidate=candidate[int(token)] if isinstance(candidate,list) else candidate[token]
            except (KeyError,IndexError,TypeError,ValueError):pass
            else:pointer=''  # Full approved object remains the assertion, never reduce it to a scalar.
    elif expected.assertion_kind in ('status','latency_ms','return_equals','raises','exception_message','error_rate','latency_p95_ms','ai_score','text_equals','text_contains'):
        pointer=''
    return pointer


def matches_expected(expected,assertions):
    value=json.loads(expected.expected_json)
    pointer=expected_constraint(expected)
    return any((not expected.assertion_kind or a.kind==expected.assertion_kind and a.pointer==pointer)
        and type(json.loads(a.expected_json)) is type(value) and json.loads(a.expected_json)==value for a in assertions)

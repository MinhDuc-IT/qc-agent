from pathlib import PurePosixPath
import json

from .contracts import ValidationIssue, validate_json
from .rules import check_expectation, check_evidence


def validate_native(case, sources):
    issues = []
    def issue(code, message):
        issues.append(ValidationIssue(case_id=case.id, code=code, message=message, repairable=False))
    if case.runner == 'unit':
        count = 1
        allowed = {'return_equals', 'raises', 'exception_message'}
        if not case.unit or case.steps or case.cleanup or case.browser or case.load or case.grading:
            issue('UNIT_SHAPE', 'Test hàm cần thông tin hàm gọi và không được có bước HTTP.')
        else:
            path = PurePosixPath(case.unit.module_path)
            if path.is_absolute() or '..' in path.parts or ':' in str(path) or '\\' in str(path):
                issue('UNIT_PATH', 'Module phải nằm trong kho mã nguồn.')
            if not any(doc.path == str(path) and doc.kind in {'code', 'existing_tests'} for doc in sources.documents):
                issue('UNIT_SOURCE', 'Không có nguồn cho module được gọi.')
            if case.unit.language=='python' and path.suffix!='.py' or case.unit.language=='javascript' and path.suffix not in {'.js','.mjs','.cjs'}:
                issue('UNIT_LANGUAGE', 'Đuôi file không phù hợp với ngôn ngữ runner.')
            try:
                if not isinstance(validate_json(case.unit.args_json), list) or not isinstance(validate_json(case.unit.kwargs_json), dict):
                    raise ValueError()
                if case.unit.language == 'javascript' and validate_json(case.unit.kwargs_json):
                    raise ValueError()
            except (TypeError, ValueError):
                issue('UNIT_ARGS', 'Tham số hàm phải là JSON hợp lệ; JavaScript chỉ dùng danh sách tham số.')
    elif case.runner == 'browser':
        count = len(case.browser.actions) if case.browser else 0
        allowed = {'status', 'dom_visible', 'dom_text_equals', 'dom_text_contains', 'dom_value_equals'}
        if not case.browser or case.steps or case.cleanup or case.unit or case.load or case.grading:
            issue('BROWSER_SHAPE', 'Test trình duyệt cần danh sách thao tác trình duyệt.')
        else:
            if case.browser.actions[0].action != 'goto':
                issue('BROWSER_START', 'Test trình duyệt phải bắt đầu bằng điều hướng trong ứng dụng.')
            for action in case.browser.actions:
                if action.action == 'goto':
                    if not action.path or not action.path.startswith('/') or action.path.startswith('//') or '\\' in action.path:
                        issue('BROWSER_PATH', 'Chỉ được điều hướng trong ứng dụng đang kiểm thử.')
                elif not action.selector or action.action in {'fill', 'select'} and action.value is None:
                    issue('BROWSER_ACTION', 'Thao tác cần selector và giá trị phù hợp.')
    else:
        count = len(case.steps)
        allowed = {'status', 'json_equals', 'json_contains', 'text_equals', 'text_contains'}
        if not count or case.unit or case.browser:
            issue('REQUEST_SHAPE', 'Runner cần request HTTP hợp lệ.')
        for request in case.steps + case.cleanup:
            if not request.path.startswith('/') or request.path.startswith('//') or any(c in request.path for c in ('\\', '?', '#', '{', '}', '\n', '\r')):
                issue('REQUEST_PATH', 'Request phải dùng đường dẫn tương đối cụ thể.')
            if request.body_json is not None:
                try:
                    validate_json(request.body_json)
                except (TypeError, ValueError):
                    issue('REQUEST_JSON', 'Dữ liệu request không phải JSON hợp lệ.')
        if case.runner == 'k6':
            allowed |= {'latency_p95_ms', 'error_rate'}
            if case.kind != 'performance' or not case.load or case.grading or case.cleanup or any(r.method not in {'GET', 'HEAD'} for r in case.steps):
                issue('LOAD_SHAPE', 'Kiểm thử tải cần cấu hình tải giới hạn và request chỉ đọc.')
        elif case.runner == 'ai':
            allowed.add('ai_score')
            if case.kind != 'ai_evaluation' or case.load:
                issue('AI_SHAPE', 'Runner AI chỉ dùng cho đánh giá AI.')
            if case.grading:
                try:
                    check_evidence(case.grading.evidence, sources)
                except ValueError as error:
                    issue('RUBRIC_EVIDENCE', str(error))
            if not any(a.kind in {'json_equals','json_contains','text_equals','text_contains','ai_score'}
                       for step in case.oracle.steps for a in step.assertions):
                issue('GROUND_TRUTH_REQUIRED', 'Đánh giá AI cần kiểm tra nội dung hoặc chấm theo rubric.')
        else:
            issue('RUNNER', 'Runner chưa được hỗ trợ.')
    if sorted(step.step_index for step in case.oracle.steps) != list(range(count)):
        issue('ORACLE_STEPS', 'Mỗi bước cần đúng một nhóm điều kiện kiểm tra.')
    for step in case.oracle.steps:
        kinds = [a.kind for a in step.assertions]
        if case.runner in {'k6', 'ai'} and kinds.count('status') != 1:
            issue('STATUS_ORACLE', 'Mỗi request cần một mã HTTP mong đợi.')
        if case.runner == 'k6' and 'latency_p95_ms' not in kinds:
            issue('SLO_STEPS', 'Mỗi bước tải cần ngưỡng p95 có bằng chứng.')
        if case.runner == 'unit' and not ({'return_equals', 'raises'} & set(kinds)):
            issue('UNIT_ORACLE', 'Test hàm cần kết quả trả về hoặc ngoại lệ mong đợi.')
        if case.runner == 'unit' and ('return_equals' in kinds and ('raises' in kinds or 'exception_message' in kinds) or 'exception_message' in kinds and 'raises' not in kinds):
            issue('UNIT_ORACLE', 'Kỳ vọng trả về và ném ngoại lệ phải nhất quán; thông báo lỗi cần loại ngoại lệ.')
        for assertion in step.assertions:
            try:
                check_expectation(assertion, sources)
                expected = validate_json(assertion.expected_json)
                if assertion.kind not in allowed:
                    raise ValueError('Điều kiện kiểm tra không phù hợp với runner.')
                if assertion.kind == 'status' and (type(expected) is not int or not 100 <= expected <= 599):
                    raise ValueError('Mã HTTP phải là số nguyên hợp lệ.')
                if assertion.kind in {'latency_p95_ms'} and (type(expected) not in {float, int} or expected <= 0):
                    raise ValueError('Ngưỡng thời gian phải là số dương.')
                if assertion.kind in {'error_rate', 'ai_score'} and (type(expected) not in {float, int} or not 0 <= expected <= 1):
                    raise ValueError('Tỷ lệ hoặc điểm phải trong khoảng 0 đến 1.')
                if assertion.kind == 'ai_score' and not case.grading:
                    raise ValueError('Chấm điểm AI cần rubric có bằng chứng.')
                if assertion.kind == 'dom_visible' and type(expected) is not bool:
                    raise ValueError('Điều kiện hiển thị phải là true hoặc false.')
                if assertion.kind.startswith('dom_') and not assertion.pointer:
                    raise ValueError('Điều kiện DOM cần selector.')
                if assertion.kind in {'raises', 'exception_message', 'text_equals', 'text_contains', 'dom_text_equals', 'dom_text_contains', 'dom_value_equals'} and not isinstance(expected, str):
                    raise ValueError('Kết quả mong đợi phải là chuỗi.')
            except (ValueError, TypeError) as error:
                issue('ORACLE_EVIDENCE', 'Kết quả mong đợi chưa được mã hóa JSON hợp lệ.' if isinstance(error,json.JSONDecodeError) else str(error))
    if case.repetitions != 1:
        issue('REPETITIONS', 'Runner chuyên biệt tự quản lý số lượt chạy.')
    return issues

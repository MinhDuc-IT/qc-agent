"""Explicit external routes; tools are not interchangeable with assertion runners."""
from pathlib import PurePosixPath


def route_issues(binding):
    worker=binding.worker;params=binding.worker_parameters;case=binding.executable
    if worker=='auto':return ['Default runner không nhận external worker parameters.'] if params else []
    if case.depends_on:return ['External worker case chưa hỗ trợ dependency giữa các runner.']
    if worker=='hercules':
        return [] if case.runner=='browser' and not params else ['Hercules chỉ nhận browser plan; không nhận feature/oracle thay thế.']
    if worker=='keploy':
        if case.kind!='integration' or case.runner!='http':return ['Keploy yêu cầu integration HTTP plan và bộ recording đã review.']
        path=params.get('keploy_path');command=params.get('sut_command')
        if set(params)!={'keploy_path','sut_command','recording_sha256'} or not isinstance(path,str) or not isinstance(command,str) or not command.strip():return ['Keploy cần keploy_path, sut_command và recording_sha256 đã được maintainer review.']
        parsed=PurePosixPath(path)
        if parsed.is_absolute() or '..' in parsed.parts or ':' in path or '\\' in path:return ['Keploy recording path phải nằm trong repository.']
        import re
        hashes=params['recording_sha256']
        if not isinstance(hashes,dict) or not hashes or any(not isinstance(p,str) or PurePosixPath(p).is_absolute() or '..' in PurePosixPath(p).parts or ':' in p or '\\' in p or not isinstance(h,str) or not re.fullmatch('[0-9a-f]{64}',h) for p,h in hashes.items()):return ['Keploy cần hash cho tất cả recording/fixture YAML đã review.']
        return []
    if worker=='strix':
        if case.kind!='security' or case.runner!='http':return ['Strix nhận security HTTP case trong scheduled run.']
        budget=params.get('max_budget_usd')
        if set(params)!={'max_budget_usd'} or type(budget) not in (int,float) or not 0<budget<=5:return ['Strix cần budget > 0 và <= 5 USD.']
        return []
    return ['Worker route không được hỗ trợ.']

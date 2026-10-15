"""Fixed HTTP executor: no generated commands, no oracle repair."""
import json
import os
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def check_case(case, base_url, allow_mutations=False):
    parts = urlsplit(base_url)
    if parts.scheme not in {"http", "https"} or not parts.netloc or parts.query or parts.fragment:
        raise ValueError("base URL must be an explicit HTTP(S) service URL")
    if case["method"] not in {"GET", "HEAD"} and not allow_mutations:
        return {"case_id": case["id"], "status": "blocked", "reason": "mutation requires explicit opt-in"}
    url = base_url.rstrip("/") + case["path"]
    if case["query"]:
        url += "?" + urlencode(case["query"])
    body = None if case["body"] is None else json.dumps(case["body"]).encode()
    headers = {"Content-Type": "application/json"}
    if os.getenv("TDA_AUTH_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["TDA_AUTH_TOKEN"]
    request = Request(url, data=body, headers=headers, method=case["method"])
    try:
        try:
            response = build_opener(NoRedirect).open(request, timeout=10)
        except HTTPError as error:
            response = error
        with response:
            status = response.code
            raw = response.read(1_048_577)
        if len(raw) > 1_048_576:
            return {"case_id": case["id"], "status": "environment_error", "reason": "response exceeds limit"}
    except (URLError, TimeoutError, OSError):
        return {"case_id": case["id"], "status": "environment_error", "reason": "HTTP transport failed"}
    mismatches = []
    oracle = case["oracle"]
    if status != oracle["status"]:
        mismatches.append(f"status: expected {oracle['status']}, got {status}")
    if oracle["json_equals"]:
        try:
            actual = json.loads(raw)
        except (ValueError, UnicodeError):
            actual = None
        for key, expected in oracle["json_equals"].items():
            if not isinstance(actual, dict) or key not in actual or actual[key] != expected:
                mismatches.append(f"JSON field {key!r} does not match oracle")
    return {"case_id": case["id"], "status": "defect" if mismatches else "pass", "mismatches": mismatches}

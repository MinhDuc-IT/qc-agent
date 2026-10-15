"""Portable declarative HTTP suite executor; no dependency on the application package."""
import json
import os
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

INVALID_JSON = object()
DEFAULT_AUTH = object()


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def json_pointer(value, pointer):
    if not pointer:
        return value
    for part in pointer[1:].split("/"):
        key = part.replace("~1", "/").replace("~0", "~")
        value = value[int(key)] if isinstance(value, list) else value[key]
    return value


def equal(actual, expected):
    return json.dumps(actual, sort_keys=True, ensure_ascii=False) == json.dumps(expected, sort_keys=True, ensure_ascii=False)


def contains(actual, expected):
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(key in actual and contains(actual[key], value)
                                               for key, value in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and all(any(contains(value, item) for value in actual) for item in expected)
    return equal(actual, expected)


class HttpSuiteExecutor:
    def __init__(self, cancellation=None, auth_token=DEFAULT_AUTH):
        self.cancellation = cancellation
        self.auth_token = os.getenv("TDA_AUTH_TOKEN") if auth_token is DEFAULT_AUTH else auth_token

    def execute(self, cases, base_url, allow_mutations=False):
        parts = urlsplit(base_url)
        if parts.scheme not in {"http", "https"} or not parts.netloc or parts.query or parts.fragment or parts.username is not None:
            raise ValueError("base URL must be an explicit HTTP(S) service URL without credentials")
        self.base_url = base_url.rstrip("/")
        self.allow_mutations = allow_mutations
        self.deadline = time.monotonic() + 120
        self.requests_left = 200
        self.cases = {case["id"]: case for case in cases}
        self.results, self.visiting = {}, set()
        if len(self.cases) != len(cases):
            raise ValueError("duplicate case IDs")
        for case in cases:
            self._run(case["id"])
        results = [self.results[case["id"]] for case in cases]
        return {"results": results, "passed": bool(results) and all(r["status"] == "pass" for r in results),
                "runner": "http-declarative-v2", "limitations": ["Sequential latency smoke sampling; no load/concurrency measurement."]}

    def _request(self, spec):
        if self.cancellation is not None and self.cancellation.is_set():
            raise TimeoutError("suite cancelled")
        if time.monotonic() >= self.deadline or self.requests_left <= 0:
            raise TimeoutError("suite execution budget exhausted")
        self.requests_left -= 1
        headers = {"Content-Type": "application/json"}
        if spec["auth"] == "bearer_env":
            headers["Authorization"] = "Bearer " + self.auth_token
        url = self.base_url + spec["path"]
        if spec["query"]:
            url += "?" + urlencode([(parameter["name"], parameter["value"]) for parameter in spec["query"]])
        payload = None if spec["body_json"] is None else spec["body_json"].encode("utf-8")
        request = Request(url, data=payload, headers=headers, method=spec["method"])
        started = time.monotonic()
        try:
            response = build_opener(NoRedirect).open(request, timeout=min(10, max(0.1, self.deadline - started)))
        except HTTPError as error:
            response = error
        with response:
            status = response.code
            raw = response.read(1_048_577)
        latency = (time.monotonic() - started) * 1000
        if len(raw) > 1_048_576:
            raise OSError("response exceeds limit")
        self.last_text = raw.decode('utf-8', errors='replace')
        try:
            body = json.loads(raw)
        except (ValueError, UnicodeError):
            body = INVALID_JSON
        return status, body, latency

    def _run(self, identifier):
        if identifier in self.results:
            return self.results[identifier]
        if identifier not in self.cases or identifier in self.visiting:
            return {"case_id": identifier, "status": "invalid", "reason": "unknown or cyclic dependency"}
        self.visiting.add(identifier)
        case = self.cases[identifier]
        result = {"case_id": identifier, "status": "pass", "steps": [], "cleanup_errors": []}
        for dependency in case["depends_on"]:
            if self._run(dependency)["status"] != "pass":
                result.update(status="blocked", reason="dependency did not pass")
        requests = case["steps"] + case["cleanup"]
        if any(req["method"] not in {"GET", "HEAD"} for req in requests) and not self.allow_mutations:
            result.update(status="blocked", reason="mutation requires explicit opt-in")
        if any(req["auth"] == "bearer_env" for req in requests) and not self.auth_token:
            result.update(status="blocked", reason="TDA_AUTH_TOKEN required for this case")
        began = False
        if result["status"] == "pass":
            oracles = {step["step_index"]: step["assertions"] for step in case["oracle"]["steps"]}
            try:
                for sample in range(case["repetitions"]):
                    for index, request in enumerate(case["steps"]):
                        began = True
                        status, body, latency = self._request(request)
                        failures = []
                        for assertion in oracles[index]:
                            expected = json.loads(assertion["expected_json"])
                            kind = assertion["kind"]
                            if kind == "status":
                                passed = status == expected
                            elif kind == "latency_ms":
                                passed = latency <= expected
                            elif kind == 'text_equals':
                                passed = self.last_text == expected
                            elif kind == 'text_contains':
                                passed = expected in self.last_text
                            else:
                                try:
                                    actual = json_pointer(body, assertion["pointer"])
                                    passed = equal(actual, expected) if kind == "json_equals" else contains(actual, expected)
                                except (KeyError, IndexError, TypeError, ValueError):
                                    passed = False
                            if not passed:
                                failures.append({"kind": kind, "pointer": assertion["pointer"]})
                        result["steps"].append({"index": index, "sample": sample, "status_code": status,
                                                "latency_ms": round(latency, 3), "failed_assertions": failures})
                        if failures:
                            result["status"] = "defect"
                            break
                    if result["status"] != "pass":
                        break
            except (URLError, TimeoutError, OSError):
                result.update(status="environment_error", reason="HTTP transport or execution budget failure")
            except (KeyError, ValueError, TypeError):
                result.update(status="invalid", reason="invalid case or oracle contract")
            finally:
                if began:
                    for cleanup in case["cleanup"]:
                        try:
                            cleanup_status, _, _ = self._request(cleanup)
                            if cleanup_status >= 400:
                                result["cleanup_errors"].append("cleanup returned error status")
                        except (URLError, TimeoutError, OSError, KeyError):
                            result["cleanup_errors"].append("cleanup transport or execution budget failure")
                    if result["cleanup_errors"] and result["status"] == "pass":
                        result.update(status="environment_error", reason="cleanup failed")
        self.visiting.remove(identifier)
        self.results[identifier] = result
        return result

"""Send a signed GitHub-like webhook for the local sample repository."""
import argparse
import hashlib
import hmac
import json
import subprocess
import time
import urllib.request
from pathlib import Path
from uuid import uuid4


def request_json(url: str, *, data: bytes | None = None, headers: dict | None = None):
    request = urllib.request.Request(url, data=data, headers=headers or {})
    with urllib.request.urlopen(request) as response:
        return json.load(response)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default="../sample-app")
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--secret", default="change-me")
    args = parser.parse_args()
    repo = Path(args.repo).resolve()
    sha = subprocess.check_output(
        ["git", "-c", f"safe.directory={repo.as_posix()}", "rev-parse", "HEAD"],
        cwd=repo,
        text=True,
    ).strip()
    payload = {
        "action": "opened",
        "installation": {"id": 1},
        "sender": {"id": 1, "login": "local-demo"},
        "repository": {
            "name": repo.name, "full_name": f"local/{repo.name}",
            "clone_url": str(repo), "default_branch": "main",
            "owner": {"login": "local"},
        },
        "pull_request": {
            "number": 1, "html_url": "http://localhost/demo/pull/1",
            "head": {"sha": sha, "ref": "demo"},
            "base": {"sha": sha, "ref": "main"},
        },
    }
    raw = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(args.secret.encode(), raw, hashlib.sha256).hexdigest()
    result = request_json(
        f"{args.url}/webhooks/github", data=raw,
        headers={"Content-Type": "application/json", "X-GitHub-Event": "pull_request",
                 "X-GitHub-Delivery": str(uuid4()), "X-Hub-Signature-256": signature},
    )
    print(json.dumps(result, indent=2))
    run_id = result["run_id"]
    while True:
        run = request_json(f"{args.url}/api/v1/runs/{run_id}")
        print(f"status={run['status']} verdict={run.get('verdict')}")
        if run["status"] in {"completed", "failed"}:
            print(json.dumps(run, indent=2))
            break
        time.sleep(0.5)


if __name__ == "__main__":
    main()

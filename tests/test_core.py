import hashlib
import hmac

from app.contracts import normalize_pull_request
from app.security import verify_webhook_signature


def payload():
    return {
        "action": "opened", "installation": {"id": 7},
        "sender": {"id": 1, "login": "demo"},
        "repository": {"name": "sample-app", "full_name": "demo/sample-app",
                       "clone_url": "https://github.com/demo/sample-app.git",
                       "default_branch": "main", "owner": {"login": "demo"}},
        "pull_request": {"number": 3, "html_url": "https://github.com/demo/sample-app/pull/3",
                         "head": {"sha": "abc", "ref": "feature"},
                         "base": {"sha": "def", "ref": "main"}},
    }


def test_signature():
    body, secret = b"hello", "secret"
    signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert verify_webhook_signature(body, signature, secret)
    assert not verify_webhook_signature(body + b"!", signature, secret)


def test_normalize_pull_request():
    context = normalize_pull_request(payload(), "delivery-1")
    assert context.repository.full_name == "demo/sample-app"
    assert context.revision.head_sha == "abc"
    assert context.installation.id == 7


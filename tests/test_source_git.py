import asyncio
from pathlib import Path
from types import SimpleNamespace

from app.infrastructure.source.git import GitRepositoryManager


class TokenProvider:
    async def installation_token(self, installation_id: int) -> str:
        return "unused"


def test_prepare_replaces_stale_workspace(monkeypatch, tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    workspace_root = tmp_path / "workspaces"
    stale = workspace_root / "run_test"
    stale.mkdir(parents=True)
    (stale / "partial-clone.txt").write_text("stale", encoding="utf-8")

    def run(argv, **kwargs):
        if "clone" in argv:
            destination = Path(argv[-1])
            assert not destination.exists()
            destination.mkdir()
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr("app.infrastructure.source.git.subprocess.run", run)
    context = SimpleNamespace(
        repository=SimpleNamespace(clone_url=str(source)),
        installation=SimpleNamespace(id=1),
        revision=SimpleNamespace(head_sha="abc123"),
    )
    run_record = SimpleNamespace(run_id="run_test", trigger_context=context)
    manager = GitRepositoryManager(workspace_root, TokenProvider(), dry_run=True)

    prepared = asyncio.run(manager.prepare(run_record))

    assert prepared == stale.resolve()
    assert not (prepared / "partial-clone.txt").exists()


def test_prepare_rejects_workspace_escape(tmp_path):
    manager = GitRepositoryManager(tmp_path / "workspaces", TokenProvider(), dry_run=True)
    run_record = SimpleNamespace(run_id="../escape", trigger_context=None)

    try:
        asyncio.run(manager.prepare(run_record))
    except RuntimeError as exc:
        assert "invalid run workspace" in str(exc)
    else:
        raise AssertionError("workspace traversal must be rejected")

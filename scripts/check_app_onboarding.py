"""Read-only GitHub App diagnostics. Never reads repository source or invokes a model."""
import asyncio
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.infrastructure.github.client import GitHubClient
from app.infrastructure.settings import Settings


async def main():
    settings = Settings(_env_file=ROOT / ".env")
    github = GitHubClient(settings.github_app_id, settings.github_private_key_path, settings.agent_qc_dry_run)
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get("https://api.github.com/app", headers={
            "Authorization": "Bearer " + github._app_jwt(), "Accept": "application/vnd.github+json"})
        response.raise_for_status()
        app = response.json()
        print("app_slug=" + app["slug"])
        print("configured_slug_matches=" + str(app["slug"] == settings.github_app_slug))
        print("oauth_client_matches=" + (str(app["client_id"] == settings.github_client_id) if app.get("client_id") else "unknown"))
        print("subscribed_events=" + str(app.get("events", [])))
    for installation in await github.app_installations():
        repositories = await github.installation_repositories(installation["id"])
        print("installation=" + str(installation["id"]) + " repositories=" + str([repo["full_name"] for repo in repositories]))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as error:
        print("diagnostic_failed=" + type(error).__name__, file=sys.stderr)
        raise SystemExit(1)

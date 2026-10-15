import httpx


class GitHubBrowserAuth:
    def __init__(self, settings):
        self.settings = settings

    @property
    def callback_url(self):
        return self.settings.agent_qc_public_url.rstrip("/") + "/auth/github/callback"

    async def identity(self, code, verifier):
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post("https://github.com/login/oauth/access_token",
                                         headers={"Accept": "application/json"}, data={
                                             "client_id": self.settings.github_client_id,
                                             "client_secret": self.settings.github_client_secret,
                                             "code": code, "redirect_uri": self.callback_url,
                                             "code_verifier": verifier})
            response.raise_for_status()
            token = response.json().get("access_token")
            if not token:
                raise ValueError("GitHub user authorization failed")
            response = await client.get("https://api.github.com/user", headers={
                "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"})
            response.raise_for_status()
            user = response.json()
            # User access tokens are discarded, not stored in browser cookies or the database.
            return {"id": int(user["id"]), "login": str(user["login"])}

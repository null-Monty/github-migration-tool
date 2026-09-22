"""Minimal GitHub REST client and token lookup (standard library only)."""

import json
import os
import subprocess
import urllib.error
import urllib.request

API_URL = "https://api.github.com"
TOKEN_ENV_VARS = ("GH_MIGRATE_TOKEN", "GITHUB_TOKEN", "GH_TOKEN")


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status
        self.message = message


class GitHubClient:
    def __init__(self, token, base_url=API_URL):
        self._token = token
        self._base_url = base_url

    def get(self, path):
        """Return the decoded JSON body, or None if GitHub answers 404."""
        try:
            return self._request("GET", path)
        except ApiError as err:
            if err.status == 404:
                return None
            raise

    def post(self, path, body):
        return self._request("POST", path, body)

    def _request(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            self._base_url + path,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "gh-migrate",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = response.read()
        except urllib.error.HTTPError as err:
            raise ApiError(err.code, _error_message(err)) from None
        except urllib.error.URLError as err:
            raise ApiError(0, str(err.reason)) from None
        return json.loads(payload) if payload else None


def _error_message(err):
    """GitHub's headline plus the specifics it puts in `errors` (e.g. why a 422 failed validation)."""
    with err:
        raw = err.read().decode(errors="replace")
    try:
        body = json.loads(raw)
        message = body["message"]
    except (ValueError, KeyError, TypeError):
        return raw or err.reason
    details = "; ".join(_describe(item) for item in body.get("errors") or [])
    return f"{message}: {details}" if details else message


def _describe(item):
    if isinstance(item, str):
        return item
    if item.get("message"):
        return item["message"]
    fields = " ".join(str(item[key]) for key in ("resource", "field", "code") if item.get(key))
    return fields or json.dumps(item)


def resolve_token(source):
    """Find a token and say where it came from, as (token, origin); None if there isn't one.

    Environment first, then the gh CLI: the source account's login if gh has one, else whichever
    account is active.
    """
    for var in TOKEN_ENV_VARS:
        if os.environ.get(var):
            return os.environ[var], f"${var}"
    attempts = (
        (["gh", "auth", "token", "--user", source], f"gh CLI login for {source}"),
        (["gh", "auth", "token"], "gh CLI active account"),
    )
    for command, origin in attempts:
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.TimeoutExpired):
            return None
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip(), origin
    return None

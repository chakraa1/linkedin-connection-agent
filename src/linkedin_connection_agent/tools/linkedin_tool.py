"""
LinkedIn API Tool — OAuth 2.0 authentication for the developer app.
App: https://www.linkedin.com/developers/apps/249660102/

Used for identity verification only. Profile search and connection sending
are handled by browser automation (browser_tool.py) since the standard
developer API does not support those operations.
"""
import json
import os
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import requests

TOKEN_FILE = Path("outputs/linkedin_tokens.json")
TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)

AUTHORIZATION_URL = "https://www.linkedin.com/oauth/v2/authorization"
TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
REDIRECT_URI = "http://localhost:8080/callback"
SCOPES = ["openid", "profile", "email", "w_member_social"]


class _CallbackHandler(BaseHTTPRequestHandler):
    code: str | None = None

    def do_GET(self):
        params = parse_qs(urlparse(self.path).query)
        _CallbackHandler.code = params.get("code", [None])[0]
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"<h2>Authentication complete. Return to terminal.</h2>")

    def log_message(self, *args):
        pass


class LinkedInAPITool:
    def __init__(self):
        self._client_id = os.getenv("LINKEDIN_CLIENT_ID", "")
        self._client_secret = os.getenv("LINKEDIN_CLIENT_SECRET", "")
        self._access_token = os.getenv("LINKEDIN_ACCESS_TOKEN", "")
        self._person_urn = os.getenv("LINKEDIN_PERSON_URN", "")

        if not self._access_token and TOKEN_FILE.exists():
            tokens = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
            self._access_token = tokens.get("access_token", "")
            self._person_urn = tokens.get("person_urn", "")

    def authenticate(self) -> bool:
        params = {
            "response_type": "code",
            "client_id": self._client_id,
            "redirect_uri": REDIRECT_URI,
            "scope": " ".join(SCOPES),
            "state": "linkedin_connection_agent",
        }
        auth_url = f"{AUTHORIZATION_URL}?{urlencode(params)}"
        print(f"\nOpening browser for LinkedIn OAuth...\n{auth_url}\n")
        webbrowser.open(auth_url)

        server = HTTPServer(("localhost", 8080), _CallbackHandler)
        server.handle_request()
        code = _CallbackHandler.code
        if not code:
            return False

        resp = requests.post(
            TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT_URI,
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            },
        )
        resp.raise_for_status()
        tokens = resp.json()
        self._access_token = tokens["access_token"]

        profile = requests.get(
            "https://api.linkedin.com/v2/userinfo",
            headers={"Authorization": f"Bearer {self._access_token}"},
        ).json()
        self._person_urn = f"urn:li:person:{profile.get('sub', '')}"

        TOKEN_FILE.write_text(
            json.dumps(
                {"access_token": self._access_token, "person_urn": self._person_urn},
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"Authenticated as {profile.get('name')} ({self._person_urn})")
        return True

    def get_profile(self) -> dict:
        resp = requests.get(
            "https://api.linkedin.com/v2/userinfo",
            headers={"Authorization": f"Bearer {self._access_token}"},
        )
        resp.raise_for_status()
        return resp.json()

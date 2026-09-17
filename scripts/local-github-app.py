#!/usr/bin/env python3
"""Create the local GitHub App through GitHub's App Manifest flow and write its
credentials into .env.

The manual path in GETTING_STARTED is a ten-field form where a wrong permission
dropdown surfaces much later as a confusing 403. The manifest flow sends the
same settings as JSON, and the conversion endpoint hands back the app id, the
OAuth client pair and the private key in one response, so nothing has to be
copied by hand.

Usage:
    python3 scripts/local-github-app.py                 # create, then wait for install
    python3 scripts/local-github-app.py --install-only  # app exists; just find its install

No webhook is configured: GitHub rejects a hook URL it cannot reach, and the
github-bot service has no public URL yet. Pass --hook-url once it does.

The manifest flow accepts repository and organization permissions only, so the
Email addresses ACCOUNT permission cannot be set here and must be added in the
UI afterwards. Sign-in calls /user/emails unconditionally and 500s without it.
The script prints the two URLs when it finishes.

Stdlib plus the openssl binary. No new dependencies.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

REPO = Path(__file__).resolve().parent.parent
ENV = REPO / ".env"
PORT = 8788
REDIRECT = f"http://localhost:{PORT}/callback"
API = "https://api.github.com"


# --------------------------------------------------------------------------- env


def read_env() -> dict[str, str]:
    out: dict[str, str] = {}
    for line in ENV.read_text().splitlines():
        if line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v
    return out


def write_env(values: dict[str, str]) -> None:
    """Replace each key in place. A key absent from .env.example is a bug, not a
    value to append, so a missing key raises rather than silently landing
    somewhere the host never reads."""
    text = ENV.read_text()
    for key, value in values.items():
        pattern = re.compile(rf"(?m)^{re.escape(key)}=.*$")
        if not pattern.search(text):
            raise SystemExit(f".env has no {key} line; refusing to append one")
        text = pattern.sub(lambda _m, v=value: f"{key}={v}", text, count=1)
    ENV.write_text(text)


# ------------------------------------------------------------------------ github


def api(path: str, *, token: str | None = None, method: str = "GET") -> object:
    req = urllib.request.Request(f"{API}{path}", method=method)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def app_jwt(app_id: str, pem: str) -> str:
    """Sign a short-lived app JWT. Python has no RSA in the stdlib, so openssl
    does the RS256 signature."""
    now = int(time.time())
    header = b64url(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    payload = b64url(
        json.dumps({"iat": now - 60, "exp": now + 540, "iss": app_id}).encode()
    )
    signing_input = f"{header}.{payload}".encode()
    # openssl cannot read the key and the message from one stream, so the key
    # goes through a temporary file and the message through stdin.
    with tempfile.NamedTemporaryFile("w", suffix=".pem", delete=True) as kf:
        kf.write(pem)
        kf.flush()
        signed = subprocess.run(
            ["openssl", "dgst", "-sha256", "-sign", kf.name, "-binary"],
            input=signing_input,
            capture_output=True,
            check=True,
        )
    return f"{header}.{payload}.{b64url(signed.stdout)}"


def manifest(name: str, hook_url: str = "") -> dict:
    man = {
        "name": name,
        "url": "http://localhost:3000",
        "redirect_url": REDIRECT,
        "callback_urls": ["http://localhost:3000/api/auth/callback/github"],
        "public": False,
        "request_oauth_on_install": False,
        "default_permissions": {
            "contents": "write",
            "pull_requests": "write",
            "issues": "write",
            "actions": "read",
            "checks": "read",
            "metadata": "read",
        },
        "default_events": [],
    }
    # GitHub validates hook_attributes.url even when active is false and refuses
    # anything it cannot reach, localhost included. The webhook only matters once
    # the github-bot service is deployed behind a public URL, so leave the key out
    # until there is one to give.
    if hook_url:
        man["hook_attributes"] = {"url": hook_url, "active": True}
    return man


# ------------------------------------------------------------------------- serve

PAGE = """<!doctype html><meta charset=utf-8><title>Create GitHub App</title>
<style>body{{font:16px system-ui;margin:4rem auto;max-width:34rem;line-height:1.5}}
button{{font:inherit;padding:.6rem 1.1rem;border-radius:6px;border:1px solid #888;cursor:pointer}}</style>
<h2>Create the Open-Inspect GitHub App</h2>
<p>Permissions and the OAuth callback URL are pre-filled. No webhook is set;
add one later when the github-bot service has a public URL. GitHub will ask you
to confirm, then send the credentials straight back here.</p>
<form id=f method=post action="{action}">
<input type=hidden name=manifest value='{manifest}'>
<button type=submit>Create GitHub App on GitHub &rarr;</button></form>
"""

DONE = """<!doctype html><meta charset=utf-8><title>Done</title>
<style>body{{font:16px system-ui;margin:4rem auto;max-width:34rem;line-height:1.5}}</style>
<h2>{heading}</h2><p>{body}</p>
"""


class Handler(BaseHTTPRequestHandler):
    result: dict | None = None
    app_name = ""
    owner = ""
    hook_url = ""

    def log_message(self, *_args):  # quiet
        pass

    def _send(self, html: str) -> None:
        body = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        url = urlparse(self.path)
        if url.path == "/":
            action = "https://github.com/settings/apps/new"
            if Handler.owner:
                action = f"https://github.com/organizations/{Handler.owner}/settings/apps/new"
            self._send(
                PAGE.format(
                    action=action,
                    manifest=json.dumps(manifest(Handler.app_name, Handler.hook_url)).replace("'", "&apos;"),
                )
            )
            return

        if url.path == "/callback":
            code = parse_qs(url.query).get("code", [""])[0]
            if not code:
                self._send(DONE.format(heading="No code returned", body="Retry from /."))
                return
            try:
                Handler.result = api(f"/app-manifests/{code}/conversions", method="POST")
            except urllib.error.HTTPError as exc:
                Handler.result = {"error": f"{exc.code} {exc.read().decode()[:300]}"}
                self._send(DONE.format(heading="Conversion failed", body=Handler.result["error"]))
                return
            self._send(
                DONE.format(
                    heading="App created",
                    body="Credentials written to .env. Back to the terminal.",
                )
            )
            return

        self.send_error(404)


def create_app(name: str, owner: str, hook_url: str) -> dict:
    Handler.app_name = name
    Handler.owner = owner
    Handler.hook_url = hook_url
    server = HTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://localhost:{PORT}/"
    print(f"Open {url} and click the button (opening it now).")
    webbrowser.open(url)
    while Handler.result is None:
        server.handle_request()
    server.server_close()
    if "error" in Handler.result:
        raise SystemExit(Handler.result["error"])
    return Handler.result


# -------------------------------------------------------------------- install id


def wait_for_install(app_id: str, pem: str, slug: str) -> str:
    install_url = f"https://github.com/apps/{slug}/installations/new"
    print(f"\nInstall the app on the repositories it may touch:\n  {install_url}")
    webbrowser.open(install_url)
    print("Waiting for the installation to appear (Ctrl-C to stop)...")
    while True:
        try:
            installs = api("/app/installations", token=app_jwt(app_id, pem))
        except urllib.error.HTTPError as exc:
            raise SystemExit(f"listing installations failed: {exc.code} {exc.reason}")
        if installs:
            first = installs[0]
            account = first.get("account") or {}
            print(f"Installed on {account.get('login', '?')} (id {first['id']})")
            return str(first["id"])
        time.sleep(3)


# -------------------------------------------------------------------------- main


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default="open-inspect-rranjan14", help="globally unique app name")
    parser.add_argument("--owner", default="", help="org to create the app under (default: your account)")
    parser.add_argument("--hook-url", default="", help="public webhook URL; omitted entirely when unset, because GitHub rejects unreachable hook URLs")
    parser.add_argument("--install-only", action="store_true", help="app exists; only resolve the installation id")
    args = parser.parse_args()

    env = read_env()

    if args.install_only:
        app_id = env.get("GITHUB_APP_ID", "")
        pem = env.get("GITHUB_APP_PRIVATE_KEY", "").strip('"').replace("\\n", "\n")
        slug = env.get("GITHUB_APP_SLUG_LOCAL", "") or args.name
        if not app_id or not pem:
            raise SystemExit("GITHUB_APP_ID / GITHUB_APP_PRIVATE_KEY are not set in .env")
    else:
        created = create_app(args.name, args.owner, args.hook_url)
        app_id = str(created["id"])
        pem = created["pem"]
        slug = created["slug"]
        write_env(
            {
                "GITHUB_APP_ID": app_id,
                "GITHUB_CLIENT_ID": created["client_id"],
                "GITHUB_CLIENT_SECRET": created["client_secret"],
                "GITHUB_APP_PRIVATE_KEY": pem.replace("\n", "\\n"),
                "GITHUB_BOT_USERNAME": f"{slug}[bot]",
            }
        )
        print(f"Wrote app id, OAuth client pair and private key for {slug} to .env")
        print(
            "\nOne permission the manifest cannot carry. Sign-in calls /user/emails on every\n"
            "attempt and returns a bare 500 without it, so set it now:\n"
            f"  https://github.com/settings/apps/{slug}/permissions\n"
            "  Account permissions -> Email addresses -> Read-only -> Save changes\n"
            "(it is a separate section below Repository permissions, with its own save)"
        )

    write_env({"GITHUB_APP_INSTALLATION_ID": wait_for_install(app_id, pem, slug)})
    print("\nDone. Restart the control plane so it reads the new values:")
    print("  docker compose up -d app")


if __name__ == "__main__":
    sys.exit(main())

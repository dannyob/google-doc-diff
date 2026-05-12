"""OAuth credential and token management for gdoc.

Default storage paths follow the spec: `~/.config/gdoc-diff/credentials.json`
holds the OAuth client (downloaded from Google Cloud Console) and
`~/.config/gdoc-diff/token.json` holds the cached refresh token.

For users who already have `gog` configured, `import_gog_token` translates
gog's stored refresh-token blob into the standard Google authorized-user
JSON shape that `google.oauth2.credentials.Credentials` expects.
"""

from __future__ import annotations

import json
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

DEFAULT_CONFIG_DIR = Path.home() / ".config" / "gdoc-diff"
DEFAULT_CREDS_PATH = DEFAULT_CONFIG_DIR / "credentials.json"
DEFAULT_TOKEN_PATH = DEFAULT_CONFIG_DIR / "token.json"

REQUIRED_SCOPES = [
    "https://www.googleapis.com/auth/documents.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]


def _is_useful_scope(scope: str) -> bool:
    """Filter out OIDC / identity scopes we don't use. Keeps anything under
    /auth/drive*, /auth/documents*, /auth/spreadsheets*, /auth/presentations*."""
    return any(s in scope for s in (
        "/auth/drive", "/auth/documents",
        "/auth/spreadsheets", "/auth/presentations",
    ))


class AuthError(RuntimeError):
    """Raised for any auth setup or refresh failure."""


def load_credentials(
    creds_path: Path | None = None,
    token_path: Path | None = None,
) -> Credentials:
    """Load and refresh OAuth credentials.

    Reads the OAuth client (`client_id`, `client_secret`) from `creds_path`
    and the refresh token from `token_path`. Refreshes the access token if
    needed.
    """
    creds_path = creds_path or DEFAULT_CREDS_PATH
    token_path = token_path or DEFAULT_TOKEN_PATH
    if not creds_path.exists():
        raise AuthError(
            f"OAuth client credentials not found at {creds_path}. Create a "
            "Google Cloud project, enable the Docs and Drive APIs, download "
            "credentials.json, and place it at the path above. See README "
            "for details."
        )
    if not token_path.exists():
        raise AuthError(
            f"OAuth token not found at {token_path}. Run `gdoc auth login` "
            "to create one (or `gdoc auth login --import-gog-token` if you "
            "already have gog configured)."
        )

    client = json.loads(creds_path.read_text())
    if "client_id" in client:
        client_id = client["client_id"]
        client_secret = client["client_secret"]
    elif "installed" in client:
        client_id = client["installed"]["client_id"]
        client_secret = client["installed"]["client_secret"]
    elif "web" in client:
        client_id = client["web"]["client_id"]
        client_secret = client["web"]["client_secret"]
    else:
        raise AuthError(f"Unrecognized credentials.json shape at {creds_path}")

    token = json.loads(token_path.read_text())
    info = {
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": token["refresh_token"],
        "token_uri": "https://oauth2.googleapis.com/token",
    }
    # Drop scopes we don't actually use (e.g. gog stores 'email', 'openid',
    # 'userinfo.email' alongside drive/docs; passing those triggers a
    # 'missing scopes email' warning from google-auth on every refresh).
    scopes = [s for s in (token.get("scopes") or []) if _is_useful_scope(s)]
    creds = Credentials.from_authorized_user_info(info, scopes=scopes or None)
    if not creds.valid:
        try:
            creds.refresh(Request())
        except Exception as e:
            raise AuthError(f"Failed to refresh access token: {e}") from e
    return creds


def run_oauth_flow(
    creds_path: Path | None = None,
    token_path: Path | None = None,
    scopes: list[str] | None = None,
) -> None:
    """Run the local-server OAuth flow and cache the resulting refresh token."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds_path = creds_path or DEFAULT_CREDS_PATH
    token_path = token_path or DEFAULT_TOKEN_PATH
    if not creds_path.exists():
        raise AuthError(
            f"OAuth client credentials not found at {creds_path}. Create one "
            "in the Google Cloud Console (OAuth client of type 'Desktop app') "
            "and download as JSON."
        )
    flow = InstalledAppFlow.from_client_secrets_file(
        str(creds_path), scopes or REQUIRED_SCOPES
    )
    creds = flow.run_local_server(port=0)
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(json.dumps({
        "refresh_token": creds.refresh_token,
        "scopes": list(creds.scopes or []),
    }))
    token_path.chmod(0o600)


def import_gog_token(
    gog_token_path: Path,
    gog_creds_path: Path | None = None,
    out_token_path: Path | None = None,
    out_creds_path: Path | None = None,
) -> None:
    """Import a refresh-token + OAuth-client pair from gog.

    gog stores the OAuth client at
    `~/Library/Application Support/gogcli/credentials.json` (or the platform
    equivalent) and exports a token via `gog auth tokens export <email> --out
    PATH`. This function copies both into our config dir.
    """
    if gog_creds_path is None:
        gog_creds_path = (
            Path.home() / "Library" / "Application Support" / "gogcli"
            / "credentials.json"
        )
    if not gog_token_path.exists():
        raise AuthError(
            f"gog token export not found at {gog_token_path}. Run "
            f"`gog auth tokens export <email> --out {gog_token_path}` first."
        )
    if not gog_creds_path.exists():
        raise AuthError(f"gog credentials not found at {gog_creds_path}")

    out_token_path = out_token_path or DEFAULT_TOKEN_PATH
    out_creds_path = out_creds_path or DEFAULT_CREDS_PATH
    out_token_path.parent.mkdir(parents=True, exist_ok=True)

    gog_token = json.loads(gog_token_path.read_text())
    gog_creds = json.loads(gog_creds_path.read_text())

    out_token_path.write_text(json.dumps({
        "refresh_token": gog_token["refresh_token"],
        "scopes": gog_token.get("scopes", []),
    }))
    out_token_path.chmod(0o600)

    out_creds_path.write_text(json.dumps({
        "client_id": gog_creds["client_id"],
        "client_secret": gog_creds["client_secret"],
    }))
    out_creds_path.chmod(0o600)


def auth_status(
    creds_path: Path | None = None,
    token_path: Path | None = None,
) -> dict:
    """Return diagnostic info about the current auth state."""
    creds_path = creds_path or DEFAULT_CREDS_PATH
    token_path = token_path or DEFAULT_TOKEN_PATH
    info = {
        "creds_path": str(creds_path),
        "creds_exists": creds_path.exists(),
        "token_path": str(token_path),
        "token_exists": token_path.exists(),
        "scopes": [],
    }
    if token_path.exists():
        token = json.loads(token_path.read_text())
        info["scopes"] = token.get("scopes", [])
    return info

"""Tests for OAuth credential import from gog."""

import json

import pytest

from google_doc_diff.auth import AuthError, import_gog_token


def _write(path, obj):
    path.write_text(json.dumps(obj))
    return path


@pytest.fixture
def gog_token(tmp_path):
    """A token blob as produced by `gog auth tokens export <email> --out`."""
    return _write(tmp_path / "gog-token.json", {
        "email": "user@example.com",
        "client": "fil",
        "refresh_token": "1//refresh-token-value",
        "scopes": [
            "https://www.googleapis.com/auth/documents",
            "https://www.googleapis.com/auth/drive.readonly",
        ],
    })


def test_import_rejects_gog_credentials_without_a_secret(tmp_path, gog_token):
    """Modern gog keeps the client secret in its keyring, leaving only a
    client_id in credentials.json. Importing from that file alone cannot
    produce working credentials, so it must fail with actionable advice
    rather than writing a credentials file that silently 401s later."""
    gog_creds = _write(tmp_path / "gogcli-credentials.json", {
        "client_id": "780118085612-abc.apps.googleusercontent.com",
    })

    with pytest.raises(AuthError) as excinfo:
        import_gog_token(
            gog_token_path=gog_token,
            gog_creds_path=gog_creds,
            out_token_path=tmp_path / "token.json",
            out_creds_path=tmp_path / "credentials.json",
        )

    assert "client_secret" in str(excinfo.value)


def test_import_takes_the_client_from_a_downloaded_secrets_file(tmp_path, gog_token):
    """The OAuth client comes from the JSON downloaded from the Cloud Console;
    only the refresh token comes from gog."""
    secrets = _write(tmp_path / "client_secret_780118085612.json", {
        "installed": {
            "client_id": "780118085612-abc.apps.googleusercontent.com",
            "client_secret": "GOCSPX-secret-value",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    })
    out_creds = tmp_path / "credentials.json"
    out_token = tmp_path / "token.json"

    import_gog_token(
        gog_token_path=gog_token,
        client_secrets_path=secrets,
        out_token_path=out_token,
        out_creds_path=out_creds,
    )

    creds = json.loads(out_creds.read_text())
    assert creds["client_id"] == "780118085612-abc.apps.googleusercontent.com"
    assert creds["client_secret"] == "GOCSPX-secret-value"

    token = json.loads(out_token.read_text())
    assert token["refresh_token"] == "1//refresh-token-value"
    assert token["scopes"] == [
        "https://www.googleapis.com/auth/documents",
        "https://www.googleapis.com/auth/drive.readonly",
    ]


def test_imported_files_are_not_world_readable(tmp_path, gog_token):
    secrets = _write(tmp_path / "client_secret.json", {
        "installed": {
            "client_id": "id.apps.googleusercontent.com",
            "client_secret": "GOCSPX-secret-value",
        }
    })
    out_creds = tmp_path / "credentials.json"
    out_token = tmp_path / "token.json"

    import_gog_token(
        gog_token_path=gog_token,
        client_secrets_path=secrets,
        out_token_path=out_token,
        out_creds_path=out_creds,
    )

    assert out_creds.stat().st_mode & 0o077 == 0
    assert out_token.stat().st_mode & 0o077 == 0


def test_cli_login_passes_client_secrets_file_through(
    tmp_path, gog_token, runner, monkeypatch
):
    """`gdoc auth login --import-gog-token X --client-secrets-file Y` wires
    the downloaded client through to the import."""
    from google_doc_diff import auth as auth_module
    from google_doc_diff.cli import cli

    # The CLI does not take a token path, so keep the default off the real
    # ~/.config/gdoc-diff/token.json.
    monkeypatch.setattr(auth_module, "DEFAULT_TOKEN_PATH", tmp_path / "token.json")

    secrets = _write(tmp_path / "client_secret.json", {
        "installed": {
            "client_id": "id.apps.googleusercontent.com",
            "client_secret": "GOCSPX-secret-value",
        }
    })
    out_creds = tmp_path / "credentials.json"

    result = runner.invoke(cli, [
        "auth", "login",
        "--import-gog-token", str(gog_token),
        "--client-secrets-file", str(secrets),
        "--credentials-file", str(out_creds),
    ])

    assert result.exit_code == 0, result.output
    assert json.loads(out_creds.read_text())["client_secret"] == "GOCSPX-secret-value"

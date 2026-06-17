"""One-time local OAuth helper to mint a read-only Gmail refresh token (Phase 19).

Run this ONCE, on your laptop (NOT in-cluster). It performs the interactive
OAuth consent flow against an OAuth *Desktop app* client and prints the three
values you paste into cluster/agent/gmail-secret.yaml (GMAIL_CLIENT_ID,
GMAIL_CLIENT_SECRET, GMAIL_REFRESH_TOKEN).

SCOPE: gmail.readonly ONLY — the minted credential physically cannot send,
draft, delete, modify, or label mail. This is the single source of the scope
on the minting side; agent/gmail_client.py holds the same scope on the runtime
side.

Prerequisites (see docs/phases/phase-19-gmail-readonly.md for the full GCP
setup — reuse an EXISTING GCP project; project creation is quota-blocked):
  1. Gmail API enabled in the project.
  2. OAuth consent screen configured; your Google account added as a test user.
  3. An OAuth client of type "Desktop app" created; download its JSON
     (client_secret_XXX.json).
  4. This script's only extra dependency (laptop-only, NOT in the agent image):
       pip install google-auth-oauthlib

Usage:
  python scripts/gmail_oauth.py /path/to/client_secret.json

A browser window opens for consent; on success the refresh token is printed.
"""

import json
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

# Read-only. Do not widen this.
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python scripts/gmail_oauth.py /path/to/client_secret.json")
        raise SystemExit(2)

    client_secret_path = sys.argv[1]

    flow = InstalledAppFlow.from_client_secrets_file(client_secret_path, SCOPES)
    # access_type=offline + prompt=consent forces Google to return a refresh
    # token (without prompt=consent a second run for an already-consented client
    # may omit it).
    creds = flow.run_local_server(
        port=0,
        access_type="offline",
        prompt="consent",
    )

    with open(client_secret_path) as f:
        info = json.load(f)
    client_cfg = info.get("installed") or info.get("web") or {}
    client_id = client_cfg.get("client_id", "")
    client_secret = client_cfg.get("client_secret", "")

    print("\n=== Paste these into cluster/agent/gmail-secret.yaml ===\n")
    print(f"GMAIL_CLIENT_ID:     {client_id}")
    print(f"GMAIL_CLIENT_SECRET: {client_secret}")
    print(f"GMAIL_REFRESH_TOKEN: {creds.refresh_token}")
    print("\nScope granted:", ", ".join(creds.scopes or SCOPES))
    print("Keep these secret. Do NOT commit them.")


if __name__ == "__main__":
    main()

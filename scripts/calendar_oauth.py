"""One-time local OAuth helper to mint a read-only Google Calendar refresh token (Phase 20).

Run this ONCE, on your laptop (NOT in-cluster). It performs the interactive
OAuth consent flow against an OAuth *Desktop app* client and prints the three
values you paste into cluster/agent/gcal-secret.yaml (GCAL_CLIENT_ID,
GCAL_CLIENT_SECRET, GCAL_REFRESH_TOKEN).

You can reuse the same client_secret_XXX.json from the Gmail OAuth setup —
the Desktop app client is scope-agnostic; scopes are requested at flow time.

SCOPE: calendar.readonly ONLY — the minted credential physically cannot create,
edit, or delete events.

Prerequisites:
  1. Google Calendar API enabled in the GCP project.
  2. OAuth consent screen configured; your Google account added as a test user.
  3. An OAuth client of type "Desktop app" (reuse the Gmail one or create a new
     one); download its JSON (client_secret_XXX.json).
  4. This script's only extra dependency (laptop-only, NOT in the agent image):
       pip install google-auth-oauthlib

Usage:
  python scripts/calendar_oauth.py /path/to/client_secret.json
"""

import json
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

# Read-only. Do not widen this.
SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python scripts/calendar_oauth.py /path/to/client_secret.json")
        raise SystemExit(2)

    client_secret_path = sys.argv[1]

    flow = InstalledAppFlow.from_client_secrets_file(client_secret_path, SCOPES)
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

    print("\n=== Paste these into cluster/agent/gcal-secret.yaml ===\n")
    print(f"GCAL_CLIENT_ID:     {client_id}")
    print(f"GCAL_CLIENT_SECRET: {client_secret}")
    print(f"GCAL_REFRESH_TOKEN: {creds.refresh_token}")
    print("\nScope granted:", ", ".join(creds.scopes or SCOPES))
    print("Keep these secret. Do NOT commit them.")


if __name__ == "__main__":
    main()

"""One-time helper: sign in as the personal Google account and print a refresh token (scopes: integrations/gcal.py).

Usage (from the repo root):
    .venv/bin/python bot/google_auth.py ~/Downloads/client_secret_*.json
"""

import json
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

from integrations.gcal import SCOPES

client_file = sys.argv[1]
flow = InstalledAppFlow.from_client_secrets_file(client_file, SCOPES)
creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")
client = json.load(open(client_file))["installed"]

print("\nAdd these to .env (and later to Fly secrets):\n")
print(f"GOOGLE_CLIENT_ID={client['client_id']}")
print(f"GOOGLE_CLIENT_SECRET={client['client_secret']}")
print(f"GOOGLE_REFRESH_TOKEN={creds.refresh_token}")

"""One-time OAuth flow. Run on your own machine:  python -m app.google_auth"""

import os

from google_auth_oauthlib.flow import InstalledAppFlow

from app.google_mcp_server import SCOPES

if __name__ == "__main__":
    secret = os.environ.get("GOOGLE_CLIENT_SECRET_FILE", "secrets/client_secret.json")
    token = os.environ.get("GOOGLE_TOKEN_FILE", "secrets/token.json")
    flow = InstalledAppFlow.from_client_secrets_file(secret, SCOPES)
    creds = flow.run_local_server(port=0)
    os.makedirs(os.path.dirname(token) or ".", exist_ok=True)
    with open(token, "w") as f:
        f.write(creds.to_json())
    print(f"Saved {token}")

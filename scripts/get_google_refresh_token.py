#!/usr/bin/env python3
"""One-time script to get Google OAuth refresh token.

Run this locally:
    python scripts/get_google_refresh_token.py

It will:
1. Open a browser for you to authorize
2. Run a local server to catch the callback
3. Exchange the code for tokens
4. Print the refresh token to store in SSM
"""

import http.server
import json
import urllib.parse
import webbrowser
from pathlib import Path

import httpx

# Load credentials from the downloaded JSON
CREDENTIALS_FILE = Path(__file__).parent.parent / "gcp_client_secret.json"

with open(CREDENTIALS_FILE) as f:
    creds = json.load(f)["web"]

CLIENT_ID = creds["client_id"]
CLIENT_SECRET = creds["client_secret"]
REDIRECT_URI = "http://localhost:8080/oauth/callback"

# Scopes for read-only calendar access
SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events.readonly",
]


class OAuthHandler(http.server.BaseHTTPRequestHandler):
    """Handle OAuth callback."""

    def do_GET(self):
        # Parse the authorization code from the callback
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        if "code" not in params:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"No authorization code received")
            return

        code = params["code"][0]

        # Exchange code for tokens
        token_response = httpx.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": REDIRECT_URI,
            },
        )

        tokens = token_response.json()

        if "refresh_token" in tokens:
            refresh_token = tokens["refresh_token"]

            # Success response
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(
                b"<h1>Success!</h1><p>You can close this window. "
                b"Check your terminal for the refresh token.</p>"
            )

            # Print the refresh token
            print("\n" + "=" * 60)
            print("SUCCESS! Here's your refresh token:")
            print("=" * 60)
            print(f"\n{refresh_token}\n")
            print("=" * 60)
            print("\nStore it in SSM with:")
            print(f'aws ssm put-parameter --name "/kairos/google-refresh-token" \\')
            print(f'  --value "{refresh_token}" --type SecureString')
            print("=" * 60 + "\n")

            # Store for server shutdown
            self.server.refresh_token = refresh_token
        else:
            self.send_response(400)
            self.end_headers()
            error_msg = tokens.get("error_description", tokens.get("error", "Unknown error"))
            self.wfile.write(f"Error: {error_msg}".encode())
            print(f"Error getting tokens: {tokens}")

    def log_message(self, format, *args):
        pass  # Suppress request logging


def main():
    # Build authorization URL
    auth_params = {
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",  # Required for refresh token
        "prompt": "consent",  # Force consent to get refresh token
    }
    auth_url = "https://accounts.google.com/o/oauth2/auth?" + urllib.parse.urlencode(auth_params)

    print("Opening browser for Google authorization...")
    print(f"\nIf browser doesn't open, visit:\n{auth_url}\n")

    # Start local server
    server = http.server.HTTPServer(("localhost", 8080), OAuthHandler)
    server.refresh_token = None

    # Open browser
    webbrowser.open(auth_url)

    print("Waiting for authorization callback...")

    # Handle one request (the callback)
    server.handle_request()

    if server.refresh_token:
        print("Done! You can now run the SSM command above.")
    else:
        print("Failed to get refresh token. Check the error above.")


if __name__ == "__main__":
    main()


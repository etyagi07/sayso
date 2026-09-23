"""The documentation quick-start, start to finish, with the method names
the installed SDK actually exposes. Steps 2 and 3 only - no order is sent.
"""

import os
import webbrowser
from urllib.parse import urlparse, parse_qs

from NorenRestApiPy.NorenApi import NorenApi

from shoonya.client import _load_dotenv  # reads .env into os.environ

_load_dotenv()

CLIENT_ID   = os.environ["SHOONYA_CLIENT_ID"]
USER_ID     = os.environ["SHOONYA_USER_ID"]
SECRET_CODE = os.environ["SHOONYA_SECRET_CODE"]

api = NorenApi(host="https://api.shoonya.com/NorenWClientAPI/",
               websocket="wss://api.shoonya.com/NorenWSAPI/")

# --- 2. Authenticate -------------------------------------------------------
# Log in at the authorize URL; the redirect back carries a ?code= param.
auth_url = api.getOAuthURL("https://api.shoonya.com/OAuthlogin/authorize/oauth", CLIENT_ID)
print(f"\nOpen this and log in:\n\n  {auth_url}\n")
webbrowser.open(auth_url)

pasted = input("Paste the redirect URL (or just the code): ").strip()
auth_code = parse_qs(urlparse(pasted).query)["code"][0] if pasted.startswith("http") else pasted

# getAccessToken does the client_id + secret_code + auth_code SHA256 itself.
result = api.getAccessToken(auth_code, SECRET_CODE, CLIENT_ID, USER_ID)
if not result:
    raise SystemExit("Token exchange failed.")

access_token, uid, refresh_token, actid = result
print(f"\nLogged in: uid={uid} actid={actid}")

# --- 3. Fetch a quote ------------------------------------------------------
quote = api.get_quotes(exchange="NSE", token="2885")  # RELIANCE-EQ
print("Quote:", quote["tsym"], quote["lp"])

# --- 4. Place an order -----------------------------------------------------
# Left out on purpose: the doc's own snippet sends a live order. Run it
# yourself once steps 2 and 3 above come back clean.

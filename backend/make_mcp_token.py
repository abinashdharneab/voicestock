"""
Create the MCP access token for one shop.

Run on the server from ~/voicestock/backend :   python3 make_mcp_token.py 7
(7 = the shop's account id). Give the token to the assistant as:  Authorization: Bearer <token>
Anyone holding the token can read and change that shop's stock, so treat it like a password.
"""
import sys

from app.mcp_server import make_token

if len(sys.argv) != 2 or not sys.argv[1].isdigit():
    sys.exit("Usage: python3 make_mcp_token.py <shop_id>")
print(make_token(int(sys.argv[1])))

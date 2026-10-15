"""
VoiceStock - Cost price patch (mcp_server.py), for Alexa+.

Before running, app/confirm.py must be the version with plan_set_cost_price. Requires
business_mcp_patch.py to already be applied.
Run from ~/voicestock/backend :   venv/bin/python cost_price_mcp_patch.py
Edits app/mcp_server.py. Stops WITHOUT changing anything if an anchor is not found
exactly once. Safe to run twice.
"""
import os
import sys

P = "app/mcp_server.py"
s = open(P).read()

assert os.path.exists("app/confirm.py"), "app/confirm.py is missing. Copy it into app/ first, then run this again. Nothing was changed."
assert "plan_set_cost_price" in open("app/confirm.py").read(), (
    "app/confirm.py looks like the old version (no plan_set_cost_price). Copy the latest confirm.py, then run this again.")

if "confirm.plan_set_cost_price" in s:
    print("already patched - nothing to do")
    sys.exit(0)


def swap(old, new):
    global s
    assert s.count(old) == 1, "anchor not found exactly once, nothing changed. Send me this: " + old[:90]
    s = s.replace(old, new)


swap('''    @mcp.tool()
    def set_price(product_name: str, new_price: float) -> dict:
        """Plan changing a product's selling price. Read the old and new price to the owner, then call
        confirm_action with the returned action_id ONLY after a clear yes."""
        return _with_db(lambda db, s: confirm.plan_set_price(db, s, product_name, new_price))''',
'''    @mcp.tool()
    def set_price(product_name: str, new_price: float) -> dict:
        """Plan changing a product's selling price. Read the old and new price to the owner, then call
        confirm_action with the returned action_id ONLY after a clear yes."""
        return _with_db(lambda db, s: confirm.plan_set_price(db, s, product_name, new_price))

    @mcp.tool()
    def set_cost_price(product_name: str, cost_price: float) -> dict:
        """Plan setting what a product costs (its cost price), applied to all its current stock - not only new
        stock. Use when the owner states a cost price directly, not through add_stock. Read the summary, then
        call confirm_action with the returned action_id ONLY after a clear yes."""
        return _with_db(lambda db, s: confirm.plan_set_cost_price(db, s, product_name, cost_price))''')

compile(s, P, "exec")
open(P, "w").write(s)
print("patched")

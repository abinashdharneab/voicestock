"""
VoiceStock - Minimum stock patch (mcp_server.py), for Alexa+.

Before running, app/confirm.py must be the version with "set_min_stock" in EXECUTORS.
Requires confirm_gate_mcp_patch.py to already be applied.
Run from ~/voicestock/backend :   venv/bin/python min_stock_mcp_patch.py
Edits app/mcp_server.py. Stops WITHOUT changing anything if an anchor is not found
exactly once. Safe to run twice.
"""
import os
import sys

P = "app/mcp_server.py"
s = open(P).read()

assert os.path.exists("app/confirm.py"), "app/confirm.py is missing. Copy it into app/ first, then run this again. Nothing was changed."
assert '"set_min_stock"' in open("app/confirm.py").read(), (
    "app/confirm.py looks like the old version (no set_min_stock executor). Copy the latest confirm.py, then run this again.")

if "confirm.plan_set_min_stock" in s:
    print("already patched - nothing to do")
    sys.exit(0)


def swap(old, new):
    global s
    assert s.count(old) == 1, "anchor not found exactly once, nothing changed. Send me this: " + old[:90]
    s = s.replace(old, new)


swap('''    @mcp.tool()
    def set_price(product_name: str, new_price: float) -> dict:''',
'''    @mcp.tool()
    def set_min_stock(product_name: str, min_stock_level: int) -> dict:
        """Plan changing a product's minimum stock level - the number used to decide if it's 'low stock'. Read
        the old and new minimum, then call confirm_action with the returned action_id ONLY after a clear yes."""
        return _with_db(lambda db, s: confirm.plan_set_min_stock(db, s, product_name, min_stock_level))

    @mcp.tool()
    def set_price(product_name: str, new_price: float) -> dict:''')

compile(s, P, "exec")
open(P, "w").write(s)
print("patched")

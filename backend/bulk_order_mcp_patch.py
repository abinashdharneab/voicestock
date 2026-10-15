"""
VoiceStock - Bulk order patch (mcp_server.py), for Alexa+.

Before running, app/bulk_order.py must be in ~/voicestock/backend/app/, and app/confirm.py
must be the version with "bulk_order" in EXECUTORS. Requires confirm_gate_mcp_patch.py to
already be applied.
Run from ~/voicestock/backend :   venv/bin/python bulk_order_mcp_patch.py
Edits app/mcp_server.py. Stops WITHOUT changing anything if an anchor is not found
exactly once. Safe to run twice.
"""
import os
import sys

P = "app/mcp_server.py"
s = open(P).read()

assert os.path.exists("app/bulk_order.py"), "app/bulk_order.py is missing. Copy it into app/ first, then run this again. Nothing was changed."
assert '"bulk_order"' in open("app/confirm.py").read(), (
    "app/confirm.py looks like the old version (no bulk_order executor). Copy the latest confirm.py, then run this again.")

if "bulk_order.plan" in s:
    print("already patched - nothing to do")
    sys.exit(0)


def swap(old, new):
    global s
    assert s.count(old) == 1, "anchor not found exactly once, nothing changed. Send me this: " + old[:90]
    s = s.replace(old, new)


swap("from . import models, nearby, restock, tools, confirm, business\n",
     "from . import models, nearby, restock, tools, confirm, business, bulk_order\n")

swap('''    @mcp.tool()
    def set_cost_price(product_name: str, cost_price: float) -> dict:''',
'''    @mcp.tool()
    def order_multiple(items: list) -> dict:
        """Plan ordering SEVERAL different products from wholesalers in one go - use instead of
        order_from_wholesaler whenever more than one item is named at once. Pass items as a list of objects,
        each with product_name and quantity. Picks the fewest wholesalers needed (nearest first when there is a
        choice) and gives one combined total. Read the breakdown, then call confirm_action with the returned
        action_id ONLY after a clear yes."""
        return _with_db(lambda db, s: bulk_order.plan(db, s, items))

    @mcp.tool()
    def set_cost_price(product_name: str, cost_price: float) -> dict:''')

compile(s, P, "exec")
open(P, "w").write(s)
print("patched")

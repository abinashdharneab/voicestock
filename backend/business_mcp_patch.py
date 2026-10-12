"""
VoiceStock - Business manager patch (mcp_server.py), for Alexa+.

Before running, app/business.py must be in ~/voicestock/backend/app/, and app/confirm.py
must be the UPDATED version with plan_set_price. Requires confirm_gate_mcp_patch.py to
already be applied.
Run from ~/voicestock/backend :   venv/bin/python business_mcp_patch.py
Edits app/mcp_server.py. Stops WITHOUT changing anything if an anchor is not found
exactly once. Safe to run twice.

Adds the same tools as business_patch.py (main.py), for Alexa+: product_margin,
profit_report, top_sellers, low_margin_items, set_price (planned, confirmed the same way
as a sale/order), and the business-manager tone in the server's instructions.
"""
import os
import sys

P = "app/mcp_server.py"
s = open(P).read()

for f, needle in (("app/business.py", None), ("app/confirm.py", "plan_set_price")):
    assert os.path.exists(f), f"{f} is missing. Copy it into app/ first, then run this again. Nothing was changed."
    if needle:
        assert needle in open(f).read(), f"{f} looks like the old version (no {needle}). Copy the latest confirm.py, then run this again."

if "business.profit_report" in s:
    print("already patched - nothing to do")
    sys.exit(0)


def swap(old, new):
    global s
    assert s.count(old) == 1, "anchor not found exactly once, nothing changed. Send me this: " + old[:90]
    s = s.replace(old, new)


swap("from . import models, nearby, restock, tools, confirm\n",
     "from . import models, nearby, restock, tools, confirm, business\n")

swap('''            "confirm_restock ONLY after the owner clearly says yes. Never confirm a plan the owner has not heard. "
            "record_sale, delete_item, fix_product_name and order_from_wholesaler only PLAN now and return an "
            "action_id; nothing changes until confirm_action is called with that EXACT action_id after a clear yes. "
            "If the owner says no, call cancel_action instead. Never reuse an action_id from earlier."),''',
'''            "confirm_restock ONLY after the owner clearly says yes. Never confirm a plan the owner has not heard. "
            "record_sale, delete_item, fix_product_name, order_from_wholesaler and set_price only PLAN now and return "
            "an action_id; nothing changes until confirm_action is called with that EXACT action_id after a clear yes. "
            "If the owner says no, call cancel_action instead. Never reuse an action_id from earlier. "
            "You are not just a stock clerk: think like an experienced business manager. Use product_margin, "
            "profit_report, top_sellers and low_margin_items on your own when relevant, not only when asked word "
            "for word, and keep it to one short practical line, not a dump of numbers."),''')

swap('''    @mcp.tool()
    def my_orders(limit: int = 10) -> dict:''',
'''    @mcp.tool()
    def product_margin(product_name: str) -> dict:
        """Selling price, cost price, profit per unit and margin percent for one product, plus how much profit is
        sitting in current stock."""
        return _with_db(lambda db, s: business.product_margin(db, s, product_name))

    @mcp.tool()
    def profit_report(days: int = 30) -> dict:
        """Revenue and profit for the last N days (default 30), with the top items by profit. Partial if some
        products have no cost price saved."""
        return _with_db(lambda db, s: business.profit_report(db, s, days))

    @mcp.tool()
    def top_sellers(days: int = 7, by: str = "quantity") -> dict:
        """What is selling best over the last N days (default 7), by 'quantity' or 'revenue'."""
        return _with_db(lambda db, s: business.top_sellers(db, s, days, by=by))

    @mcp.tool()
    def low_margin_items() -> dict:
        """Products selling for not much over their cost price (under 15% margin), worth a price review."""
        return _with_db(lambda db, s: business.low_margin_items(db, s))

    @mcp.tool()
    def set_price(product_name: str, new_price: float) -> dict:
        """Plan changing a product's selling price. Read the old and new price to the owner, then call
        confirm_action with the returned action_id ONLY after a clear yes."""
        return _with_db(lambda db, s: confirm.plan_set_price(db, s, product_name, new_price))

    @mcp.tool()
    def my_orders(limit: int = 10) -> dict:''')

compile(s, P, "exec")
open(P, "w").write(s)
print("patched")

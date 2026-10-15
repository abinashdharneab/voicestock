"""
VoiceStock - Cost price patch (main.py).

Before running, app/confirm.py must be the version with plan_set_cost_price (re-copy it
even if you already patched before). Requires business_patch.py to already be applied.
Run from ~/voicestock/backend :   venv/bin/python cost_price_patch.py
Edits app/main.py. Stops WITHOUT changing anything if an anchor is not found exactly
once. Safe to run twice.

Adds set_cost_price: the owner can now declare a product's cost price directly by voice
("cost price of Kurkure is 19 rupees") instead of only through add_stock when new stock
arrives. Gated the same way as set_price - plan, then confirm_action after a clear yes.
"""
import os
import sys

P = "app/main.py"
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


swap('''    "16) To change a selling price, call set_price with the product and new rupee amount - this only PLANS it, "
    "same as rule 7: read the old and new price, then call confirm_action only after a clear yes. "''',
'''    "16) To change a selling price, call set_price with the product and new rupee amount - this only PLANS it, "
    "same as rule 7: read the old and new price, then call confirm_action only after a clear yes. "
    "17) If the owner states a cost price directly ('cost price is 19 rupees', 'it costs me 19'), call "
    "set_cost_price - this applies to all current stock of that product, not just new stock. It is separate from "
    "set_price: if the owner gives both a selling price and a cost price in the same sentence, plan and confirm "
    "them one after another (two short confirmations), not as one action. "''')

swap('''    @strands_tool
    def set_price(product_name: str, new_price: float) -> dict:
        """Plan changing a product's selling price. Read the old and new price to the owner, then call
        confirm_action with the returned action_id ONLY after they clearly say yes."""
        db = SessionLocal()
        try:
            return confirm.plan_set_price(db, shop_id, product_name, new_price)
        finally:
            db.close()''',
'''    @strands_tool
    def set_price(product_name: str, new_price: float) -> dict:
        """Plan changing a product's selling price. Read the old and new price to the owner, then call
        confirm_action with the returned action_id ONLY after they clearly say yes."""
        db = SessionLocal()
        try:
            return confirm.plan_set_price(db, shop_id, product_name, new_price)
        finally:
            db.close()

    @strands_tool
    def set_cost_price(product_name: str, cost_price: float) -> dict:
        """Plan setting what a product costs (its cost price), applied to all its current stock - not only new
        stock. Use this when the owner states a cost price directly, not through add_stock. Read the summary to
        the owner, then call confirm_action with the returned action_id ONLY after a clear yes."""
        db = SessionLocal()
        try:
            return confirm.plan_set_cost_price(db, shop_id, product_name, cost_price)
        finally:
            db.close()''')

swap('''            confirm_action, cancel_action, product_margin, profit_report, top_sellers,
            low_margin_items, set_price]''',
'''            confirm_action, cancel_action, product_margin, profit_report, top_sellers,
            low_margin_items, set_price, set_cost_price]''')

compile(s, P, "exec")
open(P, "w").write(s)
print("patched")

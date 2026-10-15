"""
VoiceStock - Bulk order patch (main.py).

Before running, app/bulk_order.py must be in ~/voicestock/backend/app/, and app/confirm.py
must be the version with "bulk_order" in EXECUTORS (re-copy it even if you patched before).
Requires confirm_gate_patch.py to already be applied.

Run from ~/voicestock/backend :   venv/bin/python bulk_order_patch.py
Edits app/main.py. Stops WITHOUT changing anything if an anchor is not found exactly
once. Safe to run twice.

Adds order_multiple: the owner can order several different products from wholesalers in
one go ("10 Milk, 5 Maida, 20 Aloo"). It works out the fewest wholesalers needed, nearest
first when there's a choice, falls back to another wholesaler only for items the first
one doesn't have, and gives one combined total. Gated the same way as a single order -
plan, then confirm_action after a clear yes.
"""
import os
import sys

P = "app/main.py"
s = open(P).read()

assert os.path.exists("app/bulk_order.py"), "app/bulk_order.py is missing. Copy it into app/ first, then run this again. Nothing was changed."
assert os.path.exists("app/confirm.py"), "app/confirm.py is missing. Copy it into app/ first, then run this again. Nothing was changed."
assert '"bulk_order"' in open("app/confirm.py").read(), (
    "app/confirm.py looks like the old version (no bulk_order executor). Copy the latest confirm.py, then run this again.")

if "bulk_order.plan" in s:
    print("already patched - nothing to do")
    sys.exit(0)


def swap(old, new):
    global s
    assert s.count(old) == 1, "anchor not found exactly once, nothing changed. Send me this: " + old[:90]
    s = s.replace(old, new)


swap("from . import confirm\n", "from . import confirm\nfrom . import bulk_order\n")

swap('''    @strands_tool
    def set_cost_price(product_name: str, cost_price: float) -> dict:''',
'''    @strands_tool
    def order_multiple(items: list) -> dict:
        """Plan ordering SEVERAL different products from wholesalers in one go - use this instead of
        order_from_wholesaler whenever the owner names more than one item at once (e.g. '10 Milk, 5 Maida, 20
        Aloo'). Pass items as a list of objects, each with product_name and quantity. This picks the fewest
        wholesalers needed (nearest first when there is a choice) and gives one combined total. Read the
        per-wholesaler breakdown and total to the owner, then call confirm_action with the returned action_id
        ONLY after a clear yes."""
        db = SessionLocal()
        try:
            return bulk_order.plan(db, shop_id, items)
        finally:
            db.close()

    @strands_tool
    def set_cost_price(product_name: str, cost_price: float) -> dict:''')

swap('''            low_margin_items, set_price, set_cost_price]''',
'''            low_margin_items, set_price, set_cost_price, order_multiple]''')

swap('''    "17) If the owner states a cost price directly ('cost price is 19 rupees', 'it costs me 19'), call "
    "set_cost_price - this applies to all current stock of that product, not just new stock. It is separate from "
    "set_price: if the owner gives both a selling price and a cost price in the same sentence, plan and confirm "
    "them one after another (two short confirmations), not as one action. "''',
'''    "17) If the owner states a cost price directly ('cost price is 19 rupees', 'it costs me 19'), call "
    "set_cost_price - this applies to all current stock of that product, not just new stock. It is separate from "
    "set_price: if the owner gives both a selling price and a cost price in the same sentence, plan and confirm "
    "them one after another (two short confirmations), not as one action. "
    "18) When the owner wants to order TWO OR MORE different products at once, call order_multiple with all of "
    "them together - never call order_from_wholesaler several times in a row for this. Read the wholesaler "
    "breakdown and total, then confirm_action only after a clear yes. "''')

compile(s, P, "exec")
open(P, "w").write(s)
print("patched")

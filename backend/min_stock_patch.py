"""
VoiceStock - Minimum stock patch (main.py).

Before running, app/confirm.py must be the version with "set_min_stock" in EXECUTORS
(re-copy it even if patched before). Requires confirm_gate_patch.py to already be applied.
Run from ~/voicestock/backend :   venv/bin/python min_stock_patch.py
Edits app/main.py. Stops WITHOUT changing anything if an anchor is not found exactly
once. Safe to run twice.

Adds set_min_stock: the owner can now change what counts as "low stock" for a product by
voice ("Maggi ka minimum 10 rakho") instead of it being stuck at whatever it started at.
Gated the same way as set_price - plan, then confirm_action after a clear yes.
"""
import os
import sys

P = "app/main.py"
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


swap('''    @strands_tool
    def set_price(product_name: str, new_price: float) -> dict:''',
'''    @strands_tool
    def set_min_stock(product_name: str, min_stock_level: int) -> dict:
        """Plan changing a product's minimum stock level - the number used to decide if it's 'low stock'. Read
        the old and new minimum to the owner, then call confirm_action with the returned action_id ONLY after a
        clear yes."""
        db = SessionLocal()
        try:
            return confirm.plan_set_min_stock(db, shop_id, product_name, min_stock_level)
        finally:
            db.close()

    @strands_tool
    def set_price(product_name: str, new_price: float) -> dict:''')

swap('''            low_margin_items, set_price, set_cost_price, order_multiple, get_current_datetime]''',
'''            low_margin_items, set_price, set_cost_price, order_multiple, get_current_datetime,
            set_min_stock]''')

compile(s, P, "exec")
open(P, "w").write(s)
print("patched")

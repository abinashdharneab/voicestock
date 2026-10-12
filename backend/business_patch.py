"""
VoiceStock - Business manager patch (main.py).

Before running, app/business.py must already be in ~/voicestock/backend/app/, and
app/confirm.py must be the UPDATED version with plan_set_price (re-copy it even if you
already patched before - this version adds price changes to the same confirm gate).
Also requires confirm_gate_patch.py to already be applied.

Run from ~/voicestock/backend :   venv/bin/python business_patch.py
Edits app/main.py. Stops WITHOUT changing anything if an anchor is not found exactly
once. Safe to run twice.

Adds:
  - voice tools: product_margin, profit_report, top_sellers, low_margin_items,
    set_price (planned, confirm like a sale/order)
  - the assistant now speaks like an experienced shop business manager: it uses profit
    and margin tools on its own when relevant, not only when asked word for word
"""
import os
import sys

P = "app/main.py"
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


# 1. import
swap("from . import confirm\n", "from . import confirm\nfrom . import business\n")

# 2. persona + 3 new rules, inserted right after the opening lines
swap('''    "The owner may speak Hindi, Hinglish or English, and pronunciation may be unclear. "
    "Rules: "''',
'''    "The owner may speak Hindi, Hinglish or English, and pronunciation may be unclear. "
    "You are not just a stock clerk: think and speak like an experienced business manager who wants this shop to "
    "grow. When it is naturally useful, mention margin, profit, or a slow/fast seller without being asked - briefly, "
    "one line, not a lecture. If the owner asks something open-ended like 'how is my shop doing' or 'any advice', "
    "pull together low stock, a quick profit_report, and low_margin_items into a short, practical answer, like a "
    "manager giving a quick update, not a dump of numbers. "
    "Rules: "''')

swap('''    "14) The microphone can pick up customers, friends and background talk. Only act on clear commands from the owner. "''',
'''    "15) product_margin(product) tells the selling price, cost price, profit per unit and margin percent for one "
    "item. profit_report(days) gives revenue and profit for a period (default 30 days); mention if some products "
    "have no cost price saved, since that part of the profit is then unknown. top_sellers(days, by) shows what is "
    "selling best, by quantity or revenue. low_margin_items() flags products selling for too little over their "
    "cost; suggest the owner consider raising those prices. "
    "16) To change a selling price, call set_price with the product and new rupee amount - this only PLANS it, "
    "same as rule 7: read the old and new price, then call confirm_action only after a clear yes. "
    "14) The microphone can pick up customers, friends and background talk. Only act on clear commands from the owner. "''')

# 3. new tools, added just before the tool list is returned
swap('''    return [list_all_items, check_item, get_expiring_items, get_low_stock, add_stock, record_sale,
            delete_item, fix_product_name, order_from_wholesaler, find_wholesaler,
            plan_restock, confirm_restock, morning_brief, list_wholesalers, wholesaler_items,
            confirm_action, cancel_action]''',
'''    @strands_tool
    def product_margin(product_name: str) -> dict:
        """Selling price, cost price, profit per unit and margin percent for one product, plus how much profit is
        sitting in current stock. Use when the owner asks about margin, profit, or 'is this worth selling'."""
        db = SessionLocal()
        try:
            return business.product_margin(db, shop_id, product_name)
        finally:
            db.close()

    @strands_tool
    def profit_report(days: int = 30) -> dict:
        """Revenue and profit for the last N days (default 30), with the top items by profit. Profit is partial
        if some products have no cost price saved - say so if the result mentions it."""
        db = SessionLocal()
        try:
            return business.profit_report(db, shop_id, days)
        finally:
            db.close()

    @strands_tool
    def top_sellers(days: int = 7, by: str = "quantity") -> dict:
        """What is selling best over the last N days (default 7), by 'quantity' or 'revenue'."""
        db = SessionLocal()
        try:
            return business.top_sellers(db, shop_id, days, by=by)
        finally:
            db.close()

    @strands_tool
    def low_margin_items() -> dict:
        """Products selling for not much over their cost price (under 15% margin), worth a price review."""
        db = SessionLocal()
        try:
            return business.low_margin_items(db, shop_id)
        finally:
            db.close()

    @strands_tool
    def set_price(product_name: str, new_price: float) -> dict:
        """Plan changing a product's selling price. Read the old and new price to the owner, then call
        confirm_action with the returned action_id ONLY after they clearly say yes."""
        db = SessionLocal()
        try:
            return confirm.plan_set_price(db, shop_id, product_name, new_price)
        finally:
            db.close()

    return [list_all_items, check_item, get_expiring_items, get_low_stock, add_stock, record_sale,
            delete_item, fix_product_name, order_from_wholesaler, find_wholesaler,
            plan_restock, confirm_restock, morning_brief, list_wholesalers, wholesaler_items,
            confirm_action, cancel_action, product_margin, profit_report, top_sellers,
            low_margin_items, set_price]''')

compile(s, P, "exec")
open(P, "w").write(s)
print("patched")

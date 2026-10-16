"""
VoiceStock - business.py  (goes in ~/voicestock/backend/app/)

Profit and margin reporting, so the assistant can act like a shop's business manager and
not only a stock clerk. Used by BOTH the voice assistant (main.py) and the MCP server
(mcp_server.py).

Cost price comes from whatever was entered when stock was added (add_stock's cost_price,
or a wholesaler order). Because batches shrink as they sell and aren't tagged per-sale,
the "cost" of a product is approximated as the AVERAGE cost_price across its batches
(the ones that have one saved) - good enough for a voice summary, not exact accounting.
If no batch has a cost price yet, profit for that product is reported as unknown rather
than guessed.

  product_margin(db, shop_id, product_name)        "what's my margin on Maggi?"
  profit_report(db, shop_id, days=30)               "how much profit this month?"
  top_sellers(db, shop_id, days=7, limit=5, by="quantity"|"revenue")
  low_margin_items(db, shop_id, threshold_pct=15.0) "what's my lowest margin stuff?"
"""
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from . import models


def _cost_of(product) -> float:
    costs = [b.cost_price for b in product.batches if b.cost_price]
    return round(sum(costs) / len(costs), 2) if costs else None


def _stock(product) -> int:
    return sum((b.quantity or 0) for b in product.batches)


def product_margin(db: Session, shop_id: int, product_name: str):
    from . import tools
    product, match_type, suggestions = tools.find_product(db, product_name, shop_id)
    if not product:
        return {"success": False, "message": f"No product matching '{product_name}'.", "suggestions": suggestions}
    if match_type in ("fuzzy", "ambiguous"):
        return {"success": False, "needs_clarification": True, "suggestions": suggestions,
                "message": f"'{product_name}' looks like: {', '.join(suggestions)}. Ask again with the exact name."}

    price = product.price_per_unit
    cost = _cost_of(product)
    stock = _stock(product)

    if not price:
        return {"success": True, "product": product.name, "selling_price": None, "cost_price": cost, "stock": stock,
                "message": f"{product.name} doesn't have a selling price set yet. Say 'set price of {product.name}' "
                           f"and a rupee amount to add one."}
    if not cost:
        return {"success": True, "product": product.name, "selling_price": price, "cost_price": None, "stock": stock,
                "message": f"{product.name} sells for \u20b9{price:g}, but I don't know the cost price yet. "
                           f"It gets saved when you add stock and mention the cost price."}

    profit = round(price - cost, 2)
    margin_pct = round((profit / price) * 100, 1) if price else None
    potential_profit = round(profit * stock, 2)
    return {"success": True, "product": product.name, "selling_price": price, "cost_price": cost,
            "profit_per_unit": profit, "margin_percent": margin_pct, "stock": stock,
            "potential_profit_if_sold": potential_profit,
            "message": f"{product.name}: sells at \u20b9{price:g}, costs about \u20b9{cost:g}, so about "
                       f"\u20b9{profit:g} profit per unit, roughly {margin_pct:g}% margin. {stock} in stock, "
                       f"about \u20b9{potential_profit:g} profit if all of it sells."}


def profit_report(db: Session, shop_id: int, days: int = 30):
    since = datetime.utcnow() - timedelta(days=days)
    rows = db.query(models.SaleTransaction, models.Product).join(
        models.Product, models.SaleTransaction.product_id == models.Product.id
    ).filter(models.Product.shop_id == shop_id, models.SaleTransaction.sold_at >= since).all()

    revenue, known_revenue, known_profit = 0.0, 0.0, 0.0
    per_product, missing_cost, cost_cache = {}, set(), {}

    for s, p in rows:
        rev_line = (s.price_per_unit or 0) * s.quantity
        revenue += rev_line
        if p.name not in cost_cache:
            cost_cache[p.name] = _cost_of(p)
        c = cost_cache[p.name]
        entry = per_product.setdefault(p.name, {"product": p.name, "quantity": 0, "revenue": 0.0,
                                                 "profit": 0.0, "cost_known": True})
        entry["quantity"] += s.quantity
        entry["revenue"] += rev_line
        if c is None:
            entry["cost_known"] = False
            missing_cost.add(p.name)
        else:
            profit_line = rev_line - c * s.quantity
            entry["profit"] += profit_line
            known_revenue += rev_line
            known_profit += profit_line

    for e in per_product.values():
        e["revenue"] = round(e["revenue"], 2)
        e["profit"] = round(e["profit"], 2) if e["cost_known"] else None

    margin_pct = round((known_profit / known_revenue) * 100, 1) if known_revenue else None
    top = sorted([e for e in per_product.values() if e["cost_known"]], key=lambda e: e["profit"], reverse=True)[:5]

    parts = [f"In the last {days} days: revenue \u20b9{round(revenue, 2):g}"]
    if known_revenue:
        parts.append(f"profit about \u20b9{round(known_profit, 2):g} ({margin_pct:g}% margin)")
    if missing_cost:
        parts.append(f"{len(missing_cost)} product(s) have no cost price saved, so that part of the profit is unknown")
    if not rows:
        parts = [f"No sales in the last {days} days"]
    message = ", ".join(parts) + "."

    return {"success": True, "days": days, "revenue": round(revenue, 2),
            "profit": round(known_profit, 2) if known_revenue else None, "margin_percent": margin_pct,
            "top_profit_items": top, "products_missing_cost": sorted(missing_cost), "message": message}


def top_sellers(db: Session, shop_id: int, days: int = 7, limit: int = 5, by: str = "quantity"):
    since = datetime.utcnow() - timedelta(days=days)
    rows = db.query(models.SaleTransaction, models.Product).join(
        models.Product, models.SaleTransaction.product_id == models.Product.id
    ).filter(models.Product.shop_id == shop_id, models.SaleTransaction.sold_at >= since).all()

    agg = {}
    for s, p in rows:
        e = agg.setdefault(p.name, {"product": p.name, "quantity": 0, "revenue": 0.0})
        e["quantity"] += s.quantity
        e["revenue"] += (s.price_per_unit or 0) * s.quantity

    key = "revenue" if by == "revenue" else "quantity"
    limit = max(1, min(limit, 10))
    ranked = sorted(agg.values(), key=lambda e: e[key], reverse=True)[:limit]
    for e in ranked:
        e["revenue"] = round(e["revenue"], 2)

    message = (f"Top sellers in the last {days} days by {key}: " +
               ", ".join(f"{e['product']} ({e['quantity']})" for e in ranked)) if ranked else f"No sales in the last {days} days."
    return {"success": True, "days": days, "by": key, "items": ranked, "message": message}


def low_margin_items(db: Session, shop_id: int, threshold_pct: float = 15.0):
    products = db.query(models.Product).filter(models.Product.shop_id == shop_id).all()
    flagged, missing_price, missing_cost = [], 0, 0
    evaluated = 0
    for p in products:
        if not p.price_per_unit:
            missing_price += 1
            continue
        cost = _cost_of(p)
        if not cost:
            missing_cost += 1
            continue
        evaluated += 1
        margin_pct = round(((p.price_per_unit - cost) / p.price_per_unit) * 100, 1)
        if margin_pct < threshold_pct:
            flagged.append({"product": p.name, "selling_price": p.price_per_unit, "cost_price": cost,
                            "margin_percent": margin_pct})
    flagged.sort(key=lambda e: e["margin_percent"])

    if evaluated == 0:
        message = (f"I can't check margins yet \u2014 none of your {len(products)} product(s) have both a selling "
                   f"price and a cost price saved. Say 'set price of <item>' for a selling price; cost price is "
                   f"saved when you add stock and mention what it cost.")
    else:
        message = (f"{len(flagged)} of {evaluated} checked product(s) are below {threshold_pct:g}% margin: " +
                   ", ".join(f"{e['product']} ({e['margin_percent']:g}%)" for e in flagged[:5])) if flagged \
            else f"None of the {evaluated} product(s) I could check are below {threshold_pct:g}% margin."
        gaps = []
        if missing_price:
            gaps.append(f"{missing_price} missing a selling price")
        if missing_cost:
            gaps.append(f"{missing_cost} missing a cost price")
        if gaps:
            message += f" ({' and '.join(gaps)}, so margin is unknown for those \u2014 not necessarily fine.)"

    return {"success": True, "threshold_percent": threshold_pct, "total_products": len(products),
            "evaluated": evaluated, "missing_selling_price": missing_price, "missing_cost_price": missing_cost,
            "count": len(flagged), "items": flagged[:10], "message": message}

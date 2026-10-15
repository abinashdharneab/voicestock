"""
VoiceStock - bulk_order.py  (goes in ~/voicestock/backend/app/)

"Order 10 Milk, 5 Maida, 20 Aloo" in one go. Works out the smartest combination of
wholesalers - as FEW wholesalers as possible (fewer packages/deliveries), nearest ones
when there's a choice, and falls back to a different wholesaler only for the items the
first one doesn't have. One combined total, then one list of orders placed together once
the owner confirms. Used by confirm.py (the voice assistant and MCP share this through
the normal plan -> confirm_action gate, same as a single order).
"""
import difflib

from sqlalchemy.orm import Session

from . import models, nearby


def _match_item(items_for_wholesaler, name):
    n = nearby._norm(name)
    exact = [i for i in items_for_wholesaler if nearby._norm(i.product_name) == n]
    if exact:
        return exact[0]
    scored = []
    for i in items_for_wholesaler:
        inn = nearby._norm(i.product_name)
        s = difflib.SequenceMatcher(None, n, inn).ratio()
        if len(n) >= 3 and len(inn) >= 3 and (n in inn or inn in n):
            s = max(s, 0.85)
        scored.append((s, i))
    scored.sort(key=lambda t: t[0], reverse=True)
    if scored and scored[0][0] >= 0.7:
        return scored[0][1]
    return None


def plan(db: Session, shop_id: int, items: list):
    """items: [{"product_name": str, "quantity": int}, ...]"""
    if not items:
        return {"success": False, "message": "Tell me the items and quantities to order."}

    cleaned = []
    for it in items:
        name = str((it or {}).get("product_name") or "").strip()
        try:
            qty = int((it or {}).get("quantity"))
        except Exception:
            qty = 0
        if name and qty > 0:
            cleaned.append({"product_name": name, "quantity": qty})
    if not cleaned:
        return {"success": False, "message": "I need a product name and a quantity (more than 0) for each item."}

    shop_pt = nearby._point(db, "retailer", shop_id)
    wh_items, wh_dist = {}, {}
    for w in db.query(models.Wholesaler).all():
        all_items = db.query(models.WholesalerItem).filter(models.WholesalerItem.wholesaler_id == w.id).all()
        can = {}
        for idx, req in enumerate(cleaned):
            match = _match_item(all_items, req["product_name"])
            if match and (match.available_quantity or 0) >= req["quantity"]:
                can[idx] = match
        if can:
            wh_items[w.id] = can
            wh_dist[w.id] = nearby._distance(db, shop_pt, w.id)

    # Greedy set-cover: each round, pick whichever wholesaler covers the most of what's
    # still needed; ties go to whoever is nearer. Repeat until everything is covered or
    # nothing left can supply what remains.
    remaining = set(range(len(cleaned)))
    assignments = []
    while remaining:
        best_w, best_cover = None, set()
        for wid, can in wh_items.items():
            cover = remaining & set(can.keys())
            if not cover:
                continue
            better = len(cover) > len(best_cover)
            tie_closer = (len(cover) == len(best_cover) and best_w is not None and
                          (wh_dist.get(wid) if wh_dist.get(wid) is not None else 1e9) <
                          (wh_dist.get(best_w) if wh_dist.get(best_w) is not None else 1e9))
            if better or tie_closer:
                best_w, best_cover = wid, cover
        if not best_w:
            break
        assignments.append({"wholesaler_id": best_w,
                            "lines": [{"index": i, "item": wh_items[best_w][i]} for i in sorted(best_cover)]})
        remaining -= best_cover

    unavailable = [cleaned[i]["product_name"] for i in sorted(remaining)]
    if not assignments:
        return {"success": False, "message": "No wholesaler currently has any of these items in stock."}

    order_lines, parts, grand_total = [], [], 0.0
    for a in assignments:
        w = db.get(models.Wholesaler, a["wholesaler_id"])
        dist = wh_dist.get(a["wholesaler_id"])
        sub_total, names = 0.0, []
        for ln in a["lines"]:
            req = cleaned[ln["index"]]
            item = ln["item"]
            line_total = round(item.price_per_unit * req["quantity"], 2)
            sub_total += line_total
            names.append(f"{req['quantity']} {item.product_name}")
            order_lines.append({"wholesaler_id": w.id, "wholesaler_name": w.name, "product_name": item.product_name,
                                "quantity": req["quantity"], "price_per_unit": item.price_per_unit,
                                "line_total": line_total})
        grand_total += sub_total
        dist_txt = f", {dist:g} km away" if dist is not None else ""
        parts.append(f"{w.name}{dist_txt}: {', '.join(names)} (\u20b9{round(sub_total, 2):g})")

    grand_total = round(grand_total, 2)
    summary = (f"{len(order_lines)} item(s) from {len(assignments)} wholesaler(s), total \u20b9{grand_total:g}. "
               + " | ".join(parts) + ".")
    if unavailable:
        summary += f" Not available anywhere right now: {', '.join(unavailable)}."

    from . import confirm
    return confirm._new(db, shop_id, "bulk_order", {"lines": order_lines}, summary)


def execute(db: Session, shop_id: int, payload: dict):
    placed, failed = [], []
    for ln in payload.get("lines", []):
        item = db.query(models.WholesalerItem).filter(
            models.WholesalerItem.wholesaler_id == ln["wholesaler_id"],
            models.WholesalerItem.product_name == ln["product_name"],
        ).first()
        if not item or (item.available_quantity or 0) < ln["quantity"]:
            have = item.available_quantity if item else 0
            failed.append({"product": ln["product_name"], "wholesaler": ln["wholesaler_name"],
                           "reason": f"only {have} left now"})
            continue
        if item.price_per_unit > ln["price_per_unit"] + 1e-9:
            failed.append({"product": ln["product_name"], "wholesaler": ln["wholesaler_name"],
                           "reason": f"price went up to \u20b9{item.price_per_unit:g}"})
            continue
        qty = ln["quantity"]
        total = round(item.price_per_unit * qty, 2)
        order = models.WholesaleOrder(retailer_id=shop_id, wholesaler_id=item.wholesaler_id,
                                      product_name=item.product_name, quantity=qty,
                                      unit_price=item.price_per_unit, total_price=total, status="placed")
        item.available_quantity -= qty
        db.add(order)
        db.commit()
        db.refresh(order)
        placed.append({"order_id": order.id, "product": item.product_name, "quantity": qty,
                       "wholesaler": ln["wholesaler_name"], "total": total})

    spent = round(sum(p["total"] for p in placed), 2)
    wh_count = len({p["wholesaler"] for p in placed})
    message = (f"Placed {len(placed)} order{'s' if len(placed) != 1 else ''} across "
               f"{wh_count} wholesaler{'s' if wh_count != 1 else ''}, total \u20b9{spent:g}."
               + (f" {len(failed)} line(s) could not be placed - prices or stock changed." if failed else ""))
    return {"success": bool(placed), "placed": placed, "failed": failed, "total_spent": spent, "message": message}

"""
VoiceStock - nearby.py  (goes in ~/voicestock/backend/app/)

"Which wholesalers are within 5 km?", "What does XYZ sell?", "Order 10 Rice from XYZ".
Used by BOTH the voice assistant (main.py) and the MCP server (mcp_server.py).

Locations come from the party_locations table that the location patch already created
(saved from the login page or the /set-location page). Distances are straight-line (haversine),
so they are a little shorter than road distance. A retailer's exact position is never returned
to anyone; wholesalers only ever see/return a distance and an area.
"""
import difflib
import math
import re

from sqlalchemy import text
from sqlalchemy.orm import Session

from . import models

MAX_LIST = 10


def _norm(s):
    s = re.sub(r"[^a-z0-9]", "", str(s or "").lower())
    return re.sub(r"(.)\1+", r"\1", s)


def haversine_km(lat1, lng1, lat2, lng2):
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _point(db: Session, role: str, pid: int):
    """(lat, lng) saved for this shop/wholesaler, or None."""
    try:
        row = db.execute(text("SELECT lat, lng FROM party_locations WHERE role = :r AND account_id = :i"),
                         {"r": role, "i": pid}).first()
    except Exception:           # table missing (location patch not applied)
        db.rollback()
        return None
    if row and row[0] is not None and row[1] is not None:
        return float(row[0]), float(row[1])
    return None


def _distance(db, shop_pt, wholesaler_id):
    if not shop_pt:
        return None
    wp = _point(db, "wholesaler", wholesaler_id)
    return round(haversine_km(shop_pt[0], shop_pt[1], wp[0], wp[1]), 1) if wp else None


def _stocked(db, wholesaler_id):
    return db.query(models.WholesalerItem).filter(
        models.WholesalerItem.wholesaler_id == wholesaler_id,
        models.WholesalerItem.available_quantity > 0).order_by(models.WholesalerItem.product_name).all()


def resolve_wholesaler(db: Session, name: str):
    """-> (wholesaler | None, status, candidates)   status: exact / fuzzy / ambiguous / none"""
    ws = db.query(models.Wholesaler).all()
    n = _norm(name)
    if not ws or not n:
        return None, "none", []
    exact = [w for w in ws if _norm(w.name) == n]
    if len(exact) == 1:
        return exact[0], "exact", []
    if len(exact) > 1:
        return None, "ambiguous", [f"{w.name} ({w.area})" if w.area else w.name for w in exact]
    scored = []
    for w in ws:
        wn = _norm(w.name)
        s = difflib.SequenceMatcher(None, n, wn).ratio()
        if len(n) >= 3 and len(wn) >= 3 and (n in wn or wn in n):
            s = max(s, 0.85)
        scored.append((s, w))
    scored.sort(key=lambda t: t[0], reverse=True)
    if scored[0][0] < 0.6:
        return None, "none", [w.name for _, w in scored[:3]]
    close = [w for s, w in scored if s >= scored[0][0] - 0.05]
    if len(close) > 1:
        return None, "ambiguous", [w.name for w in close]
    return close[0], "fuzzy", [close[0].name]


def _unresolved(name, status, cands):
    if status == "ambiguous":
        return {"success": False, "found": False, "needs_clarification": True, "suggestions": cands,
                "message": f"More than one wholesaler matches '{name}': {', '.join(cands)}. Ask which one."}
    return {"success": False, "found": False, "suggestions": cands,
            "message": f"No wholesaler matching '{name}'." + (f" Closest: {', '.join(cands)}." if cands else "")}


def list_wholesalers(db: Session, shop_id: int, max_km: float = None):
    shop_pt = _point(db, "retailer", shop_id)
    rows = []
    for w in db.query(models.Wholesaler).all():
        stocked = _stocked(db, w.id)
        if not stocked:
            continue                                  # nothing to buy from them right now
        rows.append({"name": w.name, "area": w.area, "distance_km": _distance(db, shop_pt, w.id),
                     "items_in_stock": len(stocked), "sample_items": [i.product_name for i in stocked[:3]]})

    if max_km is not None and not shop_pt:
        rows.sort(key=lambda r: r["name"].lower())
        return {"success": False, "needs_location": True, "retailer_location_saved": False, "count": len(rows),
                "wholesalers": rows[:MAX_LIST],
                "message": "I can't measure distance because your shop location isn't saved yet. Save it with "
                           "'Use my current location' on the login page or the Set location page, then ask again. "
                           "Here are all wholesalers instead."}

    no_loc = 0
    if max_km is not None:
        kept = []
        for r in rows:
            if r["distance_km"] is None:
                no_loc += 1
            elif r["distance_km"] <= max_km:
                kept.append(r)
        rows = kept
    rows.sort(key=lambda r: (r["distance_km"] is None, r["distance_km"] if r["distance_km"] is not None else 0,
                             r["name"].lower()))
    total = len(rows)
    msg = f"{total} wholesaler{'s' if total != 1 else ''}"
    if max_km is not None:
        msg += f" within {max_km:g} km"
    msg += "."
    if max_km is not None and no_loc:
        msg += f" {no_loc} more {'has' if no_loc == 1 else 'have'} not saved a location, so I can't tell their distance."
    if max_km is None and shop_pt is None:
        msg += " Distances are unknown because your shop location isn't saved."
    if max_km is not None and total == 0:
        msg = f"No wholesaler is within {max_km:g} km." + (f" {no_loc} have not saved a location." if no_loc else "")
    return {"success": True, "retailer_location_saved": bool(shop_pt), "max_km": max_km, "count": total,
            "showing": min(total, MAX_LIST), "wholesalers": rows[:MAX_LIST], "no_location_count": no_loc,
            "message": msg}


def wholesaler_items(db: Session, shop_id: int, wholesaler_name: str):
    w, status, cands = resolve_wholesaler(db, wholesaler_name)
    if not w:
        return _unresolved(wholesaler_name, status, cands)
    items = _stocked(db, w.id)
    dist = _distance(db, _point(db, "retailer", shop_id), w.id)
    return {"success": True, "found": True, "wholesaler": w.name, "area": w.area, "distance_km": dist,
            "count": len(items),
            "items": [{"product": i.product_name, "price_per_unit": i.price_per_unit,
                       "available_quantity": i.available_quantity} for i in items],
            "message": (f"{w.name}" + (f" ({dist:g} km away)" if dist is not None else "") +
                        (f" has {len(items)} item{'s' if len(items) != 1 else ''} in stock." if items
                         else " has nothing in stock right now."))}


def order_from_specific(db: Session, shop_id: int, wholesaler_name: str, product_name: str, quantity: int):
    if quantity is None or int(quantity) < 1:
        return {"success": False, "message": "Quantity must be at least 1."}
    quantity = int(quantity)
    w, status, cands = resolve_wholesaler(db, wholesaler_name)
    if not w:
        return _unresolved(wholesaler_name, status, cands)

    items = db.query(models.WholesalerItem).filter(models.WholesalerItem.wholesaler_id == w.id).all()
    n = _norm(product_name)
    match = [i for i in items if _norm(i.product_name) == n]
    if not match:
        scored = []
        for i in items:
            inn = _norm(i.product_name)
            s = difflib.SequenceMatcher(None, n, inn).ratio()
            if len(n) >= 3 and len(inn) >= 3 and (n in inn or inn in n):
                s = max(s, 0.85)
            scored.append((s, i))
        scored.sort(key=lambda t: t[0], reverse=True)
        if scored and scored[0][0] >= 0.7:
            close = [i for s, i in scored if s >= scored[0][0] - 0.05]
            if len(close) > 1:
                names = [i.product_name for i in close]
                return {"success": False, "needs_clarification": True, "suggestions": names,
                        "message": f"{w.name} has more than one match for '{product_name}': {', '.join(names)}. Ask which."}
            match = [close[0]]
    if not match:
        have = [i.product_name for i in items if i.available_quantity > 0][:8]
        return {"success": False, "message": f"{w.name} doesn't sell '{product_name}'."
                + (f" They have: {', '.join(have)}." if have else ""), "suggestions": have}

    item = match[0]
    if (item.available_quantity or 0) < quantity:
        return {"success": False, "message": f"{w.name} has only {item.available_quantity or 0} of {item.product_name}; "
                                             f"you asked for {quantity}.", "available_quantity": item.available_quantity or 0}
    total = round(item.price_per_unit * quantity, 2)
    order = models.WholesaleOrder(retailer_id=shop_id, wholesaler_id=w.id, product_name=item.product_name,
                                  quantity=quantity, unit_price=item.price_per_unit, total_price=total, status="placed")
    item.available_quantity -= quantity
    db.add(order)
    db.commit()
    db.refresh(order)
    return {"success": True, "order_id": order.id, "wholesaler_name": w.name, "wholesaler_contact": w.contact,
            "product": item.product_name, "quantity": quantity, "unit_price": item.price_per_unit, "total_price": total,
            "message": f"Order placed with {w.name} for {quantity} of {item.product_name} at ₹{item.price_per_unit:g} "
                       f"each. Total ₹{total:g}."}

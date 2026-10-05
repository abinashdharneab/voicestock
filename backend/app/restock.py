"""
VoiceStock - restock.py  (goes in ~/voicestock/backend/app/)

Agentic "Restock for me" flow plus the morning brief. Used by BOTH the voice assistant
(Strands tools in main.py) and the MCP server (mcp_server.py), so they always agree.

  plan_restock(db, shop_id, budget=None)  -> a plan the owner can hear and approve (nothing is ordered yet)
  confirm_restock(db, shop_id, plan_id)   -> places the orders, once, with the wholesalers in the plan
  morning_brief(db, shop_id)              -> low stock, expiring soon, orders on the way, last 24h sales

It only reads and writes tables that already exist, plus one new table (restock_plans) that
main.py creates automatically at startup.
"""
import json
import math
import secrets
from datetime import datetime, timedelta

from sqlalchemy import Column, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Session

from . import models
from .database import Base

PLAN_TTL_MINUTES = 30


class RestockPlan(Base):
    __tablename__ = "restock_plans"

    id = Column(String, primary_key=True)
    shop_id = Column(Integer, index=True, nullable=False)
    plan_json = Column(Text, nullable=False)
    status = Column(String, default="pending")      # pending -> executing -> executed
    result_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    executed_at = Column(DateTime, nullable=True)


def _norm(s):
    return "".join(ch for ch in str(s or "").lower() if ch.isalnum())


def _stock(product):
    return sum((b.quantity or 0) for b in product.batches)


def _sales_per_day(db: Session, product_id: int, days: int = 14):
    since = datetime.utcnow() - timedelta(days=days)
    total = db.query(func.coalesce(func.sum(models.SaleTransaction.quantity), 0)).filter(
        models.SaleTransaction.product_id == product_id,
        models.SaleTransaction.sold_at >= since,
    ).scalar() or 0
    return round(total / days, 2)


def _candidates(db: Session, name: str):
    """Wholesaler items for this product that are in stock. Exact name first, then partial match."""
    rows = db.query(models.WholesalerItem).filter(models.WholesalerItem.available_quantity > 0).all()
    n = _norm(name)
    exact = [r for r in rows if _norm(r.product_name) == n]
    if exact:
        return exact
    return [r for r in rows if n and (n in _norm(r.product_name) or _norm(r.product_name) in n)]


def _pick(cands, qty):
    """Cheapest wholesaler that can supply the full quantity; otherwise whoever has the most."""
    enough = [c for c in cands if c.available_quantity >= qty]
    if enough:
        return min(enough, key=lambda c: (c.price_per_unit, -c.available_quantity)), qty
    best = max(cands, key=lambda c: (c.available_quantity, -c.price_per_unit))
    return best, best.available_quantity


def plan_restock(db: Session, shop_id: int, budget: float = None, cover_days: int = 7):
    products = db.query(models.Product).filter(models.Product.shop_id == shop_id).all()
    needy = []
    for p in products:
        cur, mn = _stock(p), (p.min_stock_level or 0)
        if cur < mn:
            vel = _sales_per_day(db, p.id)
            target = max(mn * 2, math.ceil(vel * cover_days) + mn)
            needy.append((p, cur, mn, vel, max(target - cur, 1)))

    if not needy:
        return {"plan_id": None, "items": [], "skipped": [], "total": 0, "wholesaler_count": 0,
                "message": "Nothing is below its minimum level, so there is nothing to restock."}

    needy.sort(key=lambda t: ((t[1] / t[2]) if t[2] else 1, t[0].name))   # emptiest first
    remaining = float(budget) if budget is not None else None
    items, skipped = [], []

    for p, cur, mn, vel, need in needy:
        cands = _candidates(db, p.name)
        if not cands:
            skipped.append({"product": p.name, "reason": "no wholesaler has it in stock"})
            continue
        w, qty = _pick(cands, need)
        price = w.price_per_unit
        note = None
        if qty < need:
            note = f"wholesaler has only {qty}"
        if remaining is not None and price * qty > remaining:
            minimum = max(mn - cur, 1)
            afford = int(remaining // price) if price > 0 else 0
            if afford >= minimum:
                qty = min(qty, afford)
                note = "limited by your budget"
            else:
                skipped.append({"product": p.name, "reason": "over budget"})
                continue
        line_total = round(price * qty, 2)
        if remaining is not None:
            remaining -= line_total
        wh = db.get(models.Wholesaler, w.wholesaler_id)
        items.append({
            "product": p.name, "unit": p.unit, "current_stock": cur, "min_stock_level": mn,
            "sales_per_day": vel, "quantity": qty,
            "wholesaler_id": w.wholesaler_id, "wholesaler": wh.name if wh else "Wholesaler",
            "wholesaler_product": w.product_name, "price_per_unit": price,
            "line_total": line_total, "note": note,
        })

    total = round(sum(i["line_total"] for i in items), 2)
    wcount = len({i["wholesaler_id"] for i in items})
    if not items:
        why = "everything needed is either unavailable or over your budget"
        return {"plan_id": None, "items": [], "skipped": skipped, "total": 0, "wholesaler_count": 0,
                "message": f"I couldn't make a plan: {why}."}

    plan_id = secrets.token_hex(4)
    plan = {"items": items, "skipped": skipped, "total": total, "wholesaler_count": wcount, "budget": budget}
    db.add(RestockPlan(id=plan_id, shop_id=shop_id, plan_json=json.dumps(plan)))
    db.commit()
    msg = f"{len(items)} item{'s' if len(items) != 1 else ''} from {wcount} wholesaler{'s' if wcount != 1 else ''}, total ₹{total:g}."
    if skipped:
        msg += f" {len(skipped)} could not be included."
    return {"plan_id": plan_id, **plan, "message": msg + " Nothing is ordered until you confirm."}


def confirm_restock(db: Session, shop_id: int, plan_id: str):
    plan = db.get(RestockPlan, plan_id) if plan_id else None
    if not plan or plan.shop_id != shop_id:
        return {"success": False, "message": "I couldn't find that restock plan. Ask me to make a new one."}
    if plan.status == "executed" and plan.result_json:
        return {**json.loads(plan.result_json), "already_executed": True}
    if plan.status == "executing":
        return {"success": False, "message": "This plan is already being placed."}
    if datetime.utcnow() - plan.created_at > timedelta(minutes=PLAN_TTL_MINUTES):
        return {"success": False, "expired": True,
                "message": f"That plan is older than {PLAN_TTL_MINUTES} minutes and prices may have changed. Ask for a fresh plan."}

    # claim the plan so two confirmations can never order twice
    claimed = db.query(RestockPlan).filter(RestockPlan.id == plan_id, RestockPlan.status == "pending").update(
        {"status": "executing"})
    db.commit()
    if not claimed:
        return {"success": False, "message": "This plan is already being placed."}

    data = json.loads(plan.plan_json)
    placed, failed = [], []
    for it in data["items"]:
        try:
            witem = db.query(models.WholesalerItem).filter(
                models.WholesalerItem.wholesaler_id == it["wholesaler_id"],
                models.WholesalerItem.product_name == it["wholesaler_product"],
            ).first()
            if not witem or (witem.available_quantity or 0) <= 0:
                failed.append({"product": it["product"], "reason": "no longer available"})
                continue
            if witem.price_per_unit > it["price_per_unit"] + 1e-9:
                failed.append({"product": it["product"],
                               "reason": f"price went up to ₹{witem.price_per_unit:g}; ask for a fresh plan"})
                continue
            qty = min(it["quantity"], witem.available_quantity)
            order = models.WholesaleOrder(
                retailer_id=shop_id, wholesaler_id=witem.wholesaler_id, product_name=witem.product_name,
                quantity=qty, unit_price=witem.price_per_unit,
                total_price=round(witem.price_per_unit * qty, 2), status="placed")
            witem.available_quantity -= qty
            witem.updated_at = datetime.utcnow()
            db.add(order)
            db.commit()
            db.refresh(order)
            placed.append({"order_id": order.id, "product": it["product"], "quantity": qty,
                           "wholesaler": it["wholesaler"], "total": order.total_price})
        except Exception as e:      # one bad line must not stop the rest
            db.rollback()
            failed.append({"product": it["product"], "reason": f"error: {e}"})

    spent = round(sum(p["total"] for p in placed), 2)
    result = {"success": bool(placed), "placed": placed, "failed": failed, "total_spent": spent,
              "message": (f"Placed {len(placed)} order{'s' if len(placed) != 1 else ''}, total ₹{spent:g}."
                          + (f" {len(failed)} could not be placed." if failed else "")
                          + (" You'll be notified as they are confirmed and delivered." if placed else ""))}
    plan = db.get(RestockPlan, plan_id)
    plan.status = "executed"
    plan.result_json = json.dumps(result)
    plan.executed_at = datetime.utcnow()
    db.commit()
    return result


STATUS_WORDS = {"placed": "waiting for the wholesaler", "confirmed": "confirmed, waiting for a delivery partner",
                "picked_up": "out for delivery"}


def morning_brief(db: Session, shop_id: int):
    now = datetime.utcnow()
    products = db.query(models.Product).filter(models.Product.shop_id == shop_id).all()
    low = [{"product": p.name, "current_stock": _stock(p), "min_stock_level": p.min_stock_level}
           for p in products if _stock(p) < (p.min_stock_level or 0)]

    expiring = []
    batches = db.query(models.Batch).join(models.Product).filter(
        models.Product.shop_id == shop_id, models.Batch.expiry_date != None, models.Batch.quantity > 0).all()
    for b in batches:
        d = (b.expiry_date - now).days
        if 0 <= d <= 3:
            expiring.append({"product": b.product.name, "quantity": b.quantity, "days_left": d})

    orders = db.query(models.WholesaleOrder).filter(
        models.WholesaleOrder.retailer_id == shop_id,
        models.WholesaleOrder.status.in_(list(STATUS_WORDS))).all()
    on_the_way = []
    for o in orders:
        wh = db.get(models.Wholesaler, o.wholesaler_id)
        on_the_way.append({"order_id": o.id, "product": o.product_name, "quantity": o.quantity,
                           "wholesaler": wh.name if wh else None, "status": STATUS_WORDS[o.status]})

    since = now - timedelta(hours=24)
    sales = db.query(models.SaleTransaction, models.Product).join(
        models.Product, models.SaleTransaction.product_id == models.Product.id).filter(
        models.Product.shop_id == shop_id, models.SaleTransaction.sold_at >= since).all()
    units = sum(s.quantity for s, _ in sales)
    revenue = round(sum((s.price_per_unit or 0) * s.quantity for s, _ in sales), 2)
    by_name = {}
    for s, p in sales:
        by_name[p.name] = by_name.get(p.name, 0) + s.quantity
    top = max(by_name.items(), key=lambda kv: kv[1]) if by_name else None

    parts = []
    parts.append(f"{len(low)} item{'s are' if len(low) != 1 else ' is'} low on stock" if low else "Nothing is low on stock")
    if expiring:
        parts.append(f"{len(expiring)} batch{'es' if len(expiring) != 1 else ''} expiring within 3 days")
    if on_the_way:
        parts.append(f"{len(on_the_way)} order{'s' if len(on_the_way) != 1 else ''} on the way")
    parts.append(f"{units} unit{'s' if units != 1 else ''} sold in the last 24 hours" + (f" (₹{revenue:g})" if revenue else ""))
    summary = ". ".join(parts) + "."
    if low:
        summary += " Say 'restock' and I'll make a plan."
    return {"summary": summary, "low_stock": low, "expiring_soon": expiring, "orders_on_the_way": on_the_way,
            "sales_last_24h": {"units": units, "revenue": revenue, "top_item": top[0] if top else None}}

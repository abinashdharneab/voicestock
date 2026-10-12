"""
VoiceStock - confirm.py  (goes in ~/voicestock/backend/app/)

A "plan, then confirm" gate for voice/MCP actions that CHANGE data: selling, deleting,
renaming a product, and ordering from a wholesaler. This makes the "never do it without a
clear yes" rule a hard rule in code, not only a line in the system prompt: the first call
only describes what would happen and returns an action_id. NOTHING changes until the owner
clearly agrees and the SAME action_id is confirmed with confirm_action.

  plan_sale(db, shop_id, product_name, quantity, bill_id=None)
  plan_delete(db, shop_id, product_name)
  plan_rename(db, shop_id, product_name, new_name)
  plan_order(db, shop_id, product_name, quantity, wholesaler_name=None)
  confirm_action(db, shop_id, action_id)    -> runs the one action_id was issued for, once
  cancel_action(db, shop_id, action_id)     -> explicit "no", for a clean log

Used by BOTH the voice assistant (main.py) and the MCP server (mcp_server.py), so they
always agree. Nothing here replaces plan_restock/confirm_restock (restock.py); that flow
already works the same way and is left as is.
"""
import json
import secrets
from datetime import datetime, timedelta

from sqlalchemy import Column, DateTime, Integer, String, Text
from sqlalchemy.orm import Session

from . import models, tools
from .database import Base

ACTION_TTL_MINUTES = 3


class PendingAction(Base):
    __tablename__ = "pending_actions"

    id = Column(String, primary_key=True)
    shop_id = Column(Integer, index=True, nullable=False)
    kind = Column(String, nullable=False)
    payload_json = Column(Text, nullable=False)
    summary = Column(Text, nullable=False)
    status = Column(String, default="pending")      # pending -> executing -> executed / cancelled
    result_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    executed_at = Column(DateTime, nullable=True)


def _stock(product):
    return sum((b.quantity or 0) for b in product.batches)


def _new(db: Session, shop_id: int, kind: str, payload: dict, summary: str):
    action_id = secrets.token_hex(4)
    db.add(PendingAction(id=action_id, shop_id=shop_id, kind=kind,
                         payload_json=json.dumps(payload), summary=summary))
    db.commit()
    return {"success": True, "needs_confirmation": True, "action_id": action_id, "summary": summary,
            "message": summary + " Say yes to confirm, or no to cancel. Nothing has happened yet."}


# ---------------------------------------------------------------- planners (read-only)

def plan_sale(db: Session, shop_id: int, product_name: str, quantity: int, bill_id: str = None):
    if quantity is None or int(quantity) < 1:
        return {"success": False, "message": "Quantity must be at least 1."}
    quantity = int(quantity)
    product, match_type, suggestions = tools.find_product(db, product_name, shop_id)
    if not product:
        return {"success": False, "message": f"No product matching '{product_name}'.", "suggestions": suggestions}
    if match_type in ("fuzzy", "ambiguous"):
        return {"success": False, "needs_clarification": True, "suggestions": suggestions,
                "message": f"'{product_name}' looks like: {', '.join(suggestions)}. Ask again with the exact name."}
    have = _stock(product)
    if have < quantity:
        return {"success": False, "message": f"Only {have} {product.unit}(s) of {product.name} left; "
                                              f"you asked for {quantity}."}
    price = product.price_per_unit
    total = round(price * quantity, 2) if price else None
    summary = f"Sell {quantity} {product.unit}(s) of {product.name}" + (f" for \u20b9{total:g}" if total else "") + "."
    return _new(db, shop_id, "sale", {"product_name": product.name, "quantity": quantity, "bill_id": bill_id}, summary)


def plan_delete(db: Session, shop_id: int, product_name: str):
    product, match_type, suggestions = tools.find_product(db, product_name, shop_id)
    if not product:
        return {"success": False, "message": f"No product matching '{product_name}'.", "suggestions": suggestions}
    if match_type in ("fuzzy", "ambiguous"):
        return {"success": False, "needs_clarification": True, "suggestions": suggestions,
                "message": f"'{product_name}' looks like: {', '.join(suggestions)}. Ask again with the exact name."}
    summary = f"Delete {product.name}, including all its stock and sale history. This cannot be undone."
    return _new(db, shop_id, "delete", {"product_name": product.name}, summary)


def plan_rename(db: Session, shop_id: int, product_name: str, new_name: str):
    if not (new_name or "").strip():
        return {"success": False, "message": "Tell me the corrected name."}
    product, match_type, suggestions = tools.find_product(db, product_name, shop_id)
    if not product:
        return {"success": False, "message": f"No product matching '{product_name}'.", "suggestions": suggestions}
    if match_type in ("fuzzy", "ambiguous"):
        return {"success": False, "needs_clarification": True, "suggestions": suggestions,
                "message": f"'{product_name}' looks like: {', '.join(suggestions)}. Ask again with the exact name."}
    summary = f"Rename {product.name} to {tools._clean_name(new_name)}."
    return _new(db, shop_id, "rename", {"product_name": product.name, "new_name": new_name}, summary)


def plan_set_price(db: Session, shop_id: int, product_name: str, new_price: float):
    if new_price is None or float(new_price) <= 0:
        return {"success": False, "message": "Give a price more than 0."}
    new_price = round(float(new_price), 2)
    product, match_type, suggestions = tools.find_product(db, product_name, shop_id)
    if not product:
        return {"success": False, "message": f"No product matching '{product_name}'.", "suggestions": suggestions}
    if match_type in ("fuzzy", "ambiguous"):
        return {"success": False, "needs_clarification": True, "suggestions": suggestions,
                "message": f"'{product_name}' looks like: {', '.join(suggestions)}. Ask again with the exact name."}
    old_price = product.price_per_unit
    summary = (f"Change {product.name}'s price from \u20b9{old_price:g} to \u20b9{new_price:g}." if old_price
               else f"Set {product.name}'s price to \u20b9{new_price:g}.")
    return _new(db, shop_id, "set_price", {"product_name": product.name, "new_price": new_price}, summary)


def plan_order(db: Session, shop_id: int, product_name: str, quantity: int, wholesaler_name: str = None):
    if quantity is None or int(quantity) < 1:
        return {"success": False, "message": "Quantity must be at least 1."}
    quantity = int(quantity)

    if wholesaler_name:
        from . import nearby                   # local import: avoids a circular import at module load time
        w, status, cands = nearby.resolve_wholesaler(db, wholesaler_name)
        if not w:
            if status == "ambiguous":
                return {"success": False, "needs_clarification": True, "suggestions": cands,
                        "message": f"More than one wholesaler matches '{wholesaler_name}': {', '.join(cands)}. Ask which one."}
            return {"success": False, "suggestions": cands,
                    "message": f"No wholesaler matching '{wholesaler_name}'." + (f" Closest: {', '.join(cands)}." if cands else "")}
        items = db.query(models.WholesalerItem).filter(models.WholesalerItem.wholesaler_id == w.id).all()
        n = product_name.strip().lower()
        item = next((i for i in items if i.product_name.strip().lower() == n), None)
        if not item:
            item = next((i for i in items if n and (n in i.product_name.lower() or i.product_name.lower() in n)), None)
        if not item:
            have = [i.product_name for i in items if (i.available_quantity or 0) > 0][:8]
            return {"success": False, "message": f"{w.name} doesn't sell '{product_name}'."
                    + (f" They have: {', '.join(have)}." if have else ""), "suggestions": have}
        if (item.available_quantity or 0) < quantity:
            return {"success": False, "message": f"{w.name} has only {item.available_quantity or 0} of "
                    f"{item.product_name}; you asked for {quantity}."}
        total = round(item.price_per_unit * quantity, 2)
        summary = f"Order {quantity} {item.product_name} from {w.name} at \u20b9{item.price_per_unit:g} each, total \u20b9{total:g}."
        payload = {"wholesaler_id": w.id, "wholesaler_name": w.name, "product_name": item.product_name,
                   "quantity": quantity, "price_per_unit": item.price_per_unit}
        return _new(db, shop_id, "order", payload, summary)

    match = tools.find_best_wholesaler(db, product_name)
    if not match:
        return {"success": False, "message": f"No wholesaler currently has '{product_name}'."}
    if match["available_quantity"] < quantity:
        return {"success": False, "message": f"{match['wholesaler_name']} only has {match['available_quantity']}; "
                                              f"you asked for {quantity}."}
    total = round(match["price_per_unit"] * quantity, 2)
    summary = f"Order {quantity} {match['product_name']} from {match['wholesaler_name']} at \u20b9{match['price_per_unit']:g} each, total \u20b9{total:g}."
    payload = {"wholesaler_id": match["wholesaler_id"], "wholesaler_name": match["wholesaler_name"],
               "product_name": match["product_name"], "quantity": quantity, "price_per_unit": match["price_per_unit"]}
    return _new(db, shop_id, "order", payload, summary)


# ---------------------------------------------------------------- executors (run once, at confirm time)

def _exec_sale(db, shop_id, p):
    return tools.record_sale(db, p["product_name"], p["quantity"], shop_id, p.get("bill_id"))


def _exec_delete(db, shop_id, p):
    return tools.delete_product(db, p["product_name"], shop_id, confirm=True)


def _exec_rename(db, shop_id, p):
    return tools.rename_product(db, p["product_name"], p["new_name"], shop_id, confirm=True)


def _exec_set_price(db, shop_id, p):
    product, match_type, _ = tools.find_product(db, p["product_name"], shop_id)
    if not product:
        return {"success": False, "message": f"{p['product_name']} no longer exists."}
    old_price = product.price_per_unit
    product.price_per_unit = p["new_price"]
    db.commit()
    return {"success": True, "product": product.name, "old_price": old_price, "new_price": p["new_price"],
            "message": f"{product.name}'s price is now \u20b9{p['new_price']:g}."}


def _exec_order(db, shop_id, p):
    item = db.query(models.WholesalerItem).filter(
        models.WholesalerItem.wholesaler_id == p["wholesaler_id"],
        models.WholesalerItem.product_name == p["product_name"],
    ).first()
    if not item or (item.available_quantity or 0) < p["quantity"]:
        have = item.available_quantity if item else 0
        return {"success": False, "message": f"That changed in the meantime \u2014 only {have} left now. Ask again for a fresh price."}
    if item.price_per_unit > p.get("price_per_unit", item.price_per_unit) + 1e-9:
        return {"success": False, "message": f"The price went up to \u20b9{item.price_per_unit:g}; ask again for a fresh total before ordering."}
    qty = p["quantity"]
    total = round(item.price_per_unit * qty, 2)
    order = models.WholesaleOrder(retailer_id=shop_id, wholesaler_id=item.wholesaler_id, product_name=item.product_name,
                                  quantity=qty, unit_price=item.price_per_unit, total_price=total, status="placed")
    item.available_quantity -= qty
    db.add(order)
    db.commit()
    db.refresh(order)
    return {"success": True, "order_id": order.id, "wholesaler_name": p.get("wholesaler_name"),
            "product": item.product_name, "quantity": qty, "unit_price": item.price_per_unit, "total_price": total,
            "message": f"Order placed with {p.get('wholesaler_name')} for {qty} of {item.product_name} at "
                       f"\u20b9{item.price_per_unit:g} each. Total \u20b9{total:g}."}


EXECUTORS = {"sale": _exec_sale, "delete": _exec_delete, "rename": _exec_rename, "order": _exec_order,
             "set_price": _exec_set_price}


def confirm_action(db: Session, shop_id: int, action_id: str):
    action = db.get(PendingAction, action_id) if action_id else None
    if not action or action.shop_id != shop_id:
        return {"success": False, "message": "I couldn't find that one. Ask me again to get a fresh action to confirm."}
    if action.status == "executed" and action.result_json:
        return {**json.loads(action.result_json), "already_executed": True}
    if action.status == "cancelled":
        return {"success": False, "message": "That one was already cancelled. Ask again if you still want it."}
    if action.status == "executing":
        return {"success": False, "message": "This is already being done."}
    if datetime.utcnow() - action.created_at > timedelta(minutes=ACTION_TTL_MINUTES):
        return {"success": False, "expired": True,
                "message": f"That was more than {ACTION_TTL_MINUTES} minutes ago, so I won't risk stale prices or stock. Ask again for a fresh one."}

    # claim it so two confirmations (or a repeated "yes") can never run it twice
    claimed = db.query(PendingAction).filter(PendingAction.id == action_id, PendingAction.status == "pending").update(
        {"status": "executing"})
    db.commit()
    if not claimed:
        return {"success": False, "message": "This is already being done."}

    payload = json.loads(action.payload_json)
    try:
        result = EXECUTORS[action.kind](db, shop_id, payload)
    except Exception as e:
        db.rollback()
        result = {"success": False, "message": f"Something went wrong, nothing was changed: {e}"}

    action = db.get(PendingAction, action_id)
    action.status = "executed"
    action.result_json = json.dumps(result)
    action.executed_at = datetime.utcnow()
    db.commit()
    return result


def cancel_action(db: Session, shop_id: int, action_id: str):
    action = db.get(PendingAction, action_id) if action_id else None
    if not action or action.shop_id != shop_id or action.status != "pending":
        return {"success": True, "message": "Nothing to cancel."}
    action.status = "cancelled"
    db.commit()
    return {"success": True, "message": "Cancelled. Nothing was changed."}

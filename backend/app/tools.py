import re
import difflib
from datetime import datetime
from sqlalchemy.orm import Session
from . import models


def _norm(s: str) -> str:
    s = re.sub(r"[^a-z0-9]", "", s.lower())
    return re.sub(r"(.)\1+", r"\1", s)


def _clean_name(name: str) -> str:
    return " ".join(w[:1].upper() + w[1:] for w in name.strip().split())


def library_lookup(db: Session, name: str):
    entries = db.query(models.ProductImage).all()
    if not entries:
        return None
    nq = _norm(name)
    for e in entries:
        if e.norm_name == nq:
            return e.image_url
    best_score, best = 0.0, None
    for e in entries:
        score = difflib.SequenceMatcher(None, nq, e.norm_name).ratio()
        if score > best_score:
            best_score, best = score, e
    if best and best_score >= 0.85:
        return best.image_url
    return None


def save_to_library(db: Session, name: str, image_url: str):
    nq = _norm(name)
    entry = db.query(models.ProductImage).filter(models.ProductImage.norm_name == nq).first()
    if entry:
        entry.name = name
        entry.image_url = image_url
        entry.updated_at = datetime.utcnow()
    else:
        entry = models.ProductImage(name=name, norm_name=nq, image_url=image_url)
        db.add(entry)
    db.commit()


def find_product(db: Session, name: str, shop_id: int, cutoff: float = 0.6):
    products = db.query(models.Product).filter(models.Product.shop_id == shop_id).all()
    if not products:
        return None, "none", []

    q = name.strip().lower()
    for p in products:
        if p.name.lower() == q:
            return p, "exact", []

    nq = _norm(name)
    for p in products:
        if _norm(p.name) == nq:
            return p, "normalized", []

    scored = []
    for p in products:
        np_ = _norm(p.name)
        score = difflib.SequenceMatcher(None, nq, np_).ratio()
        if len(nq) >= 3 and len(np_) >= 3 and (nq in np_ or np_ in nq):
            score = max(score, 0.85)
        scored.append((score, p))
    scored.sort(key=lambda x: x[0], reverse=True)

    best_score, best = scored[0]
    if best_score >= cutoff:
        close = [p for s, p in scored if s >= best_score - 0.05]
        if len(close) > 1:
            return None, "ambiguous", [p.name for p in close]
        return best, "fuzzy", [best.name]

    return None, "none", [p.name for s, p in scored[:3]]


def list_products(db: Session, shop_id: int):
    items = []
    for p in db.query(models.Product).filter(models.Product.shop_id == shop_id).all():
        total = sum(b.quantity for b in p.batches)
        items.append({
            "product": p.name,
            "quantity": total,
            "unit": p.unit,
            "low_stock": total < p.min_stock_level,
            "image_url": p.image_url,
        })
    return {"count": len(items), "items": items}


def check_item(db: Session, product_name: str, shop_id: int):
    product, match_type, suggestions = find_product(db, product_name, shop_id)

    if not product:
        return {
            "found": False,
            "heard_as": product_name,
            "suggestions": suggestions,
            "message": f"No product matching '{product_name}'. Closest: {', '.join(suggestions) if suggestions else 'none'}.",
        }

    total_stock = sum(b.quantity for b in product.batches)
    return {
        "found": True,
        "product": product.name,
        "heard_as": product_name,
        "match_type": match_type,
        "total_quantity": total_stock,
        "unit": product.unit,
        "min_stock_level": product.min_stock_level,
        "low_stock": total_stock < product.min_stock_level,
        "image_url": product.image_url,
    }


def get_expiring_items(db: Session, within_days: int, shop_id: int):
    now = datetime.utcnow()
    batches = (
        db.query(models.Batch)
        .join(models.Product)
        .filter(models.Product.shop_id == shop_id, models.Batch.expiry_date != None)
        .all()
    )

    expiring = []
    for b in batches:
        days_left = (b.expiry_date - now).days
        if 0 <= days_left <= within_days:
            expiring.append({
                "product": b.product.name,
                "quantity": b.quantity,
                "expiry_date": b.expiry_date.strftime("%Y-%m-%d"),
                "days_left": days_left,
            })

    return {"count": len(expiring), "items": expiring}


def get_low_stock(db: Session, shop_id: int):
    products = db.query(models.Product).filter(models.Product.shop_id == shop_id).all()
    low_stock_items = []

    for p in products:
        total_stock = sum(b.quantity for b in p.batches)
        if total_stock < p.min_stock_level:
            low_stock_items.append({
                "product": p.name,
                "current_stock": total_stock,
                "min_stock_level": p.min_stock_level,
            })

    return {"count": len(low_stock_items), "items": low_stock_items}


def add_stock(db: Session, product_name: str, quantity: int, shop_id: int, expiry_date: str = None,
              cost_price: float = None, unit: str = "packet", confirm_new: bool = False,
              source: str = "manual", wholesale_order_id: int = None):
    product, match_type, suggestions = find_product(db, product_name, shop_id)

    if match_type in ("fuzzy", "ambiguous") and not confirm_new:
        return {
            "success": False,
            "needs_clarification": True,
            "heard_as": product_name,
            "suggestions": suggestions,
            "message": f"'{product_name}' looks similar to: {', '.join(suggestions)}. Ask the owner if they mean one of these, or a new product.",
        }

    existing = product if match_type in ("exact", "normalized") else None
    created_new = False

    if existing is None:
        clean = _clean_name(product_name)
        existing = models.Product(
            name=clean,
            unit=unit,
            shop_id=shop_id,
            image_url=library_lookup(db, clean),
        )
        db.add(existing)
        db.commit()
        db.refresh(existing)
        created_new = True
    elif not existing.image_url:
        existing.image_url = library_lookup(db, existing.name)

    parsed_expiry = None
    if expiry_date:
        try:
            parsed_expiry = datetime.strptime(expiry_date, "%Y-%m-%d")
        except ValueError:
            parsed_expiry = None

    batch = models.Batch(
        product_id=existing.id,
        quantity=quantity,
        expiry_date=parsed_expiry,
        cost_price=cost_price,
        source=source,
        wholesale_order_id=wholesale_order_id,
    )
    db.add(batch)
    db.commit()
    db.refresh(existing)

    total_stock = sum(b.quantity for b in existing.batches)
    return {
        "success": True,
        "product": existing.name,
        "created_new": created_new,
        "added_quantity": quantity,
        "new_total_stock": total_stock,
        "image_url": existing.image_url,
    }


def record_sale(db: Session, product_name: str, quantity: int, shop_id: int, bill_id: str = None):
    product, match_type, suggestions = find_product(db, product_name, shop_id)

    if not product:
        return {
            "success": False,
            "message": f"No product matching '{product_name}'. Closest: {', '.join(suggestions) if suggestions else 'none'}.",
            "suggestions": suggestions,
        }

    if match_type == "fuzzy":
        return {
            "success": False,
            "needs_clarification": True,
            "suggestions": [product.name],
            "message": f"Did the owner mean '{product.name}'? Confirm before recording the sale.",
        }

    if bill_id:
        existing = db.query(models.SaleTransaction).filter(
            models.SaleTransaction.bill_id == bill_id
        ).first()
        if existing:
            return {"success": False, "message": "This sale was already recorded (duplicate bill_id)."}

    batches = sorted(product.batches, key=lambda b: (b.expiry_date is None, b.expiry_date))

    remaining = quantity
    for batch in batches:
        if remaining <= 0:
            break
        if batch.quantity <= 0:
            continue
        deduct = min(batch.quantity, remaining)
        batch.quantity -= deduct
        remaining -= deduct

    if remaining > 0:
        db.rollback()
        return {
            "success": False,
            "message": f"Not enough stock. Could not fulfill {remaining} units of {product.name}.",
        }

    price = product.price_per_unit
    sale = models.SaleTransaction(product_id=product.id, quantity=quantity, bill_id=bill_id, price_per_unit=price)
    db.add(sale)
    db.commit()
    db.refresh(product)

    total_stock = sum(b.quantity for b in product.batches)
    return {
        "success": True,
        "product": product.name,
        "sold_quantity": quantity,
        "remaining_stock": total_stock,
        "revenue": (price * quantity) if price else None,
    }


def delete_product(db: Session, product_name: str, shop_id: int, confirm: bool = False):
    product, match_type, suggestions = find_product(db, product_name, shop_id)

    if not product:
        return {
            "success": False,
            "message": f"No product matching '{product_name}'. Closest: {', '.join(suggestions) if suggestions else 'none'}.",
            "suggestions": suggestions,
        }

    if match_type in ("fuzzy", "ambiguous") and not confirm:
        return {
            "success": False,
            "needs_clarification": True,
            "heard_as": product_name,
            "suggestions": suggestions,
            "message": f"'{product_name}' looks similar to: {', '.join(suggestions)}. Confirm the exact product before deleting.",
        }

    name = product.name
    db.query(models.SaleTransaction).filter(models.SaleTransaction.product_id == product.id).delete()
    db.query(models.Batch).filter(models.Batch.product_id == product.id).delete()
    db.delete(product)
    db.commit()

    return {"success": True, "deleted_product": name}


def rename_product(db: Session, product_name: str, new_name: str, shop_id: int, confirm: bool = False):
    product, match_type, suggestions = find_product(db, product_name, shop_id)

    if not product:
        return {
            "success": False,
            "message": f"No product matching '{product_name}'. Closest: {', '.join(suggestions) if suggestions else 'none'}.",
            "suggestions": suggestions,
        }

    if match_type in ("fuzzy", "ambiguous") and not confirm:
        return {
            "success": False,
            "needs_clarification": True,
            "heard_as": product_name,
            "suggestions": suggestions,
            "message": f"'{product_name}' looks similar to: {', '.join(suggestions)}. Confirm the exact product before renaming.",
        }

    old_name = product.name
    clean_new = _clean_name(new_name)

    existing_target, target_match, _ = find_product(db, clean_new, shop_id)
    if existing_target and target_match in ("exact", "normalized") and existing_target.id != product.id:
        for batch in list(product.batches):
            batch.product_id = existing_target.id
        db.query(models.SaleTransaction).filter(models.SaleTransaction.product_id == product.id).update(
            {"product_id": existing_target.id}
        )
        db.delete(product)
        db.commit()
        return {"success": True, "merged": True, "old_name": old_name, "final_name": existing_target.name}

    product.name = clean_new
    if not product.image_url:
        product.image_url = library_lookup(db, clean_new)
    db.commit()

    return {
        "success": True,
        "merged": False,
        "old_name": old_name,
        "final_name": product.name,
        "image_url": product.image_url,
    }


def find_best_wholesaler(db: Session, product_name: str):
    items = db.query(models.WholesalerItem).filter(
        models.WholesalerItem.product_name.ilike(f"%{product_name}%"),
        models.WholesalerItem.available_quantity > 0,
    ).all()

    if not items:
        return None

    best = min(items, key=lambda i: i.price_per_unit)
    wholesaler = db.query(models.Wholesaler).filter(models.Wholesaler.id == best.wholesaler_id).first()

    return {
        "wholesaler_id": wholesaler.id,
        "wholesaler_name": wholesaler.name,
        "wholesaler_area": wholesaler.area,
        "wholesaler_contact": wholesaler.contact,
        "price_per_unit": best.price_per_unit,
        "available_quantity": best.available_quantity,
        "product_name": best.product_name,
    }


def place_wholesale_order(db: Session, retailer_id: int, product_name: str, quantity: int):
    match = find_best_wholesaler(db, product_name)
    if not match:
        return {"success": False, "message": f"No wholesaler currently has '{product_name}' in stock."}

    if match["available_quantity"] < quantity:
        return {
            "success": False,
            "message": f"{match['wholesaler_name']} only has {match['available_quantity']} units, you asked for {quantity}.",
        }

    total = match["price_per_unit"] * quantity
    order = models.WholesaleOrder(
        retailer_id=retailer_id,
        wholesaler_id=match["wholesaler_id"],
        product_name=match["product_name"],
        quantity=quantity,
        unit_price=match["price_per_unit"],
        total_price=total,
        status="placed",
    )
    db.add(order)

    item = db.query(models.WholesalerItem).filter(
        models.WholesalerItem.wholesaler_id == match["wholesaler_id"],
        models.WholesalerItem.product_name == match["product_name"],
    ).first()
    item.available_quantity -= quantity

    db.commit()
    db.refresh(order)

    return {
        "success": True,
        "order_id": order.id,
        "wholesaler_name": match["wholesaler_name"],
        "wholesaler_contact": match["wholesaler_contact"],
        "quantity": quantity,
        "unit_price": match["price_per_unit"],
        "total_price": total,
        "message": f"Order placed with {match['wholesaler_name']} for {quantity} units of {match['product_name']} at ₹{match['price_per_unit']}/unit. Total ₹{total}.",
    }

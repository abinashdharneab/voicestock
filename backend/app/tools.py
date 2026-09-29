import re
import difflib
import requests
from datetime import datetime
from sqlalchemy.orm import Session
from . import models


# ---------- NAME MATCHING HELPERS ----------

def _norm(s: str) -> str:
    """lowercase, only letters/digits, collapse repeated letters (maggiee -> magi)."""
    s = re.sub(r"[^a-z0-9]", "", s.lower())
    return re.sub(r"(.)\1+", r"\1", s)


def _clean_name(name: str) -> str:
    """Capitalise each word for storing: 'parle g' -> 'Parle G'."""
    return " ".join(w[:1].upper() + w[1:] for w in name.strip().split())


# ---------- PRODUCT IMAGE LOOKUP ----------

def fetch_product_image(name: str):
    """Fetch a product photo URL from Open Food Facts. Returns None if not found."""
    try:
        r = requests.get(
            "https://world.openfoodfacts.org/cgi/search.pl",
            params={
                "search_terms": name,
                "search_simple": 1,
                "action": "process",
                "json": 1,
                "page_size": 10,
                "fields": "product_name,image_front_url",
            },
            headers={"User-Agent": "VoiceStock/1.0"},
            timeout=4,
        )
        r.raise_for_status()
        for p in r.json().get("products", []):
            if p.get("image_front_url"):
                return p["image_front_url"]
    except Exception:
        pass
    return None


def find_product(db: Session, name: str, cutoff: float = 0.6):
    """
    Returns (product, match_type, suggestions)
    match_type: exact | normalized | fuzzy | ambiguous | none
    """
    products = db.query(models.Product).all()
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


# ---------- LIST ALL ITEMS ----------
def list_products(db: Session):
    """List every product with its total stock."""
    items = []
    for p in db.query(models.Product).all():
        total = sum(b.quantity for b in p.batches)
        items.append({
            "product": p.name,
            "quantity": total,
            "unit": p.unit,
            "low_stock": total < p.min_stock_level,
            "image_url": p.image_url,
        })
    return {"count": len(items), "items": items}


# ---------- CHECK ITEM ----------
def check_item(db: Session, product_name: str):
    """Check stock for a product. Tolerates wrong spelling / pronunciation."""
    product, match_type, suggestions = find_product(db, product_name)

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


# ---------- GET EXPIRING ITEMS ----------
def get_expiring_items(db: Session, within_days: int = 7):
    now = datetime.utcnow()
    batches = db.query(models.Batch).filter(models.Batch.expiry_date != None).all()

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


# ---------- GET LOW STOCK ----------
def get_low_stock(db: Session):
    products = db.query(models.Product).all()
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


# ---------- ADD STOCK ----------
def add_stock(db: Session, product_name: str, quantity: int, expiry_date: str = None,
              cost_price: float = None, unit: str = "packet", confirm_new: bool = False):
    """
    Add stock. Merges into an existing product when the name matches (exact or same spelling).
    If the name only looks similar to an existing product, asks for confirmation first,
    unless confirm_new=True (owner said it is a brand new product).
    New products automatically get a photo from Open Food Facts when one is found.
    """
    product, match_type, suggestions = find_product(db, product_name)

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
            image_url=fetch_product_image(clean),
        )
        db.add(existing)
        db.commit()
        db.refresh(existing)
        created_new = True
    elif not existing.image_url:
        # older product without a photo: try once more
        existing.image_url = fetch_product_image(existing.name)

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


# ---------- RECORD SALE ----------
def record_sale(db: Session, product_name: str, quantity: int, bill_id: str = None):
    """Record a sale, reducing stock using FEFO (earliest expiry first)."""
    product, match_type, suggestions = find_product(db, product_name)

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

    batches = sorted(
        product.batches,
        key=lambda b: (b.expiry_date is None, b.expiry_date)
    )

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

    sale = models.SaleTransaction(
        product_id=product.id,
        quantity=quantity,
        bill_id=bill_id,
    )
    db.add(sale)
    db.commit()
    db.refresh(product)

    total_stock = sum(b.quantity for b in product.batches)
    return {
        "success": True,
        "product": product.name,
        "sold_quantity": quantity,
        "remaining_stock": total_stock,
    }


# ---------- DELETE PRODUCT ----------
def delete_product(db: Session, product_name: str, confirm: bool = False):
    """
    Delete a product entirely (and its batches/sale history).
    Fuzzy matches only get deleted if confirm=True, so the voice assistant
    should always read back the exact product name and ask before deleting.
    """
    product, match_type, suggestions = find_product(db, product_name)

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


# ---------- RENAME / FIX SPELLING ----------
def rename_product(db: Session, product_name: str, new_name: str, confirm: bool = False):
    """
    Rename a product, e.g. to fix a spelling mistake ('Magi' -> 'Maggi').
    Fuzzy matches on the OLD name need confirm=True before renaming.
    Refetches the product photo if it didn't have one.
    """
    product, match_type, suggestions = find_product(db, product_name)

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

    # merge into an existing product if the new name already exists
    existing_target, target_match, _ = find_product(db, clean_new)
    if existing_target and target_match in ("exact", "normalized") and existing_target.id != product.id:
        for batch in list(product.batches):
            batch.product_id = existing_target.id
        db.query(models.SaleTransaction).filter(models.SaleTransaction.product_id == product.id).update(
            {"product_id": existing_target.id}
        )
        db.delete(product)
        db.commit()
        return {
            "success": True,
            "merged": True,
            "old_name": old_name,
            "final_name": existing_target.name,
        }

    product.name = clean_new
    if not product.image_url:
        product.image_url = fetch_product_image(clean_new)
    db.commit()

    return {
        "success": True,
        "merged": False,
        "old_name": old_name,
        "final_name": product.name,
        "image_url": product.image_url,
    }
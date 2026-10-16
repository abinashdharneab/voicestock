"""
VoiceStock - Photo privacy patch (tools.py).

Before this patch, a new product's photo was auto-filled from a SHARED library used by
EVERY shop - so if shop A uploaded a photo for "Maggi", shop B's "Maggi" could silently
get the same photo too. This stops that: new products start with no photo, and every
shop uploads and keeps its own (the "Add Photo" button in the app already does this per
product, unaffected by this patch).

Run from ~/voicestock/backend :   venv/bin/python photo_privacy_patch.py
Edits app/tools.py. Stops WITHOUT changing anything if an anchor isn't found exactly
once. Safe to run twice.
"""
import sys

P = "app/tools.py"
s = open(P).read()

if "no longer auto-fills from the shared library" in s:
    print("already patched - nothing to do")
    sys.exit(0)


def swap(old, new):
    global s
    assert s.count(old) == 1, "anchor not found exactly once, nothing changed. Send me this: " + old[:90]
    s = s.replace(old, new)


# 1. a brand new product (add_stock): no auto-filled photo from other shops
swap('''        clean = _clean_name(product_name)
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
        existing.image_url = library_lookup(db, existing.name)''',
'''        clean = _clean_name(product_name)
        # no longer auto-fills from the shared library - every shop's photo stays its own
        existing = models.Product(
            name=clean,
            unit=unit,
            shop_id=shop_id,
            image_url=None,
        )
        db.add(existing)
        db.commit()
        db.refresh(existing)
        created_new = True''')

# 2. renaming a product to a brand-new name: same, no cross-shop auto-fill
swap('''    product.name = clean_new
    if not product.image_url:
        product.image_url = library_lookup(db, clean_new)
    db.commit()''',
'''    product.name = clean_new
    db.commit()''')

compile(s, P, "exec")
open(P, "w").write(s)
print("patched")

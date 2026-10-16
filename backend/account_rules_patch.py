"""
VoiceStock - Account rules patch (main.py).

Run from ~/voicestock/backend :   venv/bin/python account_rules_patch.py
Edits app/main.py. Stops WITHOUT changing anything if an anchor is not found exactly
once. Safe to run twice.

Changes:
  - Shop/wholesaler NAME no longer has to be unique. With an 8+ digit PIN, two accounts
    matching on both name AND pin is not a realistic risk, so names can repeat.
  - PIN becomes an 8-to-20 digit password (was exactly 4 digits), for signup, login, and
    changing it later from the profile page.
  - Since names can repeat, /login now checks the PIN against every account with that
    name for the chosen role, instead of only the first one found.
"""
import sys

P = "app/main.py"
s = open(P).read()

if "at least 8 digits" in s:
    print("already patched - nothing to do")
    sys.exit(0)


def swap(old, new):
    global s
    assert s.count(old) == 1, "anchor not found exactly once, nothing changed. Send me this: " + old[:90]
    s = s.replace(old, new)


def _pin_ok(expr):
    return f"not (8 <= len({expr}) <= 20 and {expr}.isdigit())"


# 1. /signup: drop the name-uniqueness check, require an 8-20 digit PIN
swap('''    Model = ROLE_TABLE.get(payload.role)
    if not Model:
        raise HTTPException(status_code=400, detail="Invalid role.")
    existing = db.query(Model).filter(Model.name == payload.name).first()
    if existing:
        raise HTTPException(status_code=400, detail="This name is already taken. Choose another.")
    if len(payload.pin) != 4 or not payload.pin.isdigit():
        raise HTTPException(status_code=400, detail="PIN must be exactly 4 digits.")
    account = Model(name=payload.name, pin=payload.pin, area=payload.area)''',
f'''    Model = ROLE_TABLE.get(payload.role)
    if not Model:
        raise HTTPException(status_code=400, detail="Invalid role.")
    if {_pin_ok("payload.pin")}:
        raise HTTPException(status_code=400, detail="Password must be 8 to 20 digits.")
    account = Model(name=payload.name, pin=payload.pin, area=payload.area)''')

# 2. /login: names may repeat now, so check the PIN against every match for that name+role
swap('''    Model = ROLE_TABLE.get(payload.role)
    if not Model:
        raise HTTPException(status_code=400, detail="Invalid role.")
    account = db.query(Model).filter(Model.name == payload.name).first()
    if not account or account.pin != payload.pin:
        raise HTTPException(status_code=401, detail="Wrong name or PIN.")
    return {"account_id": account.id, "name": account.name, "role": payload.role}''',
'''    Model = ROLE_TABLE.get(payload.role)
    if not Model:
        raise HTTPException(status_code=400, detail="Invalid role.")
    candidates = db.query(Model).filter(Model.name == payload.name).all()
    account = next((a for a in candidates if a.pin == payload.pin), None)
    if not account:
        raise HTTPException(status_code=401, detail="Wrong name or password.")
    return {"account_id": account.id, "name": account.name, "role": payload.role}''')

# 3. Profile page: renaming a shop no longer has to pick a never-used name
swap('''        if not (1 <= len(name) <= 40):
            raise HTTPException(status_code=400, detail="Name must be 1 to 40 characters.")
        clash = db.query(models.Shop).filter(models.Shop.name == name, models.Shop.id != account_id).first()
        if clash:
            raise HTTPException(status_code=400, detail="This name is already taken. Choose another.")
        shop.name = name''',
'''        if not (1 <= len(name) <= 40):
            raise HTTPException(status_code=400, detail="Name must be 1 to 40 characters.")
        shop.name = name''')

# 4. Changing the PIN from the profile page uses the same 8-20 digit rule
swap('''    if shop.pin != payload.current_pin:
        raise HTTPException(status_code=401, detail="Current PIN is wrong.")
    if len(payload.new_pin) != 4 or not payload.new_pin.isdigit():
        raise HTTPException(status_code=400, detail="New PIN must be exactly 4 digits.")
    if payload.new_pin == payload.current_pin:''',
f'''    if shop.pin != payload.current_pin:
        raise HTTPException(status_code=401, detail="Current password is wrong.")
    if {_pin_ok("payload.new_pin")}:
        raise HTTPException(status_code=400, detail="New password must be 8 to 20 digits.")
    if payload.new_pin == payload.current_pin:''')

compile(s, P, "exec")
open(P, "w").write(s)
print("patched")

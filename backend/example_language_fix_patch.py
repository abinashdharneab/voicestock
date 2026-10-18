"""
VoiceStock - Example language fix (main.py).

A system-prompt example written in Hindi ("Maggi ke 2 packet jod diye, ab 22 hain.") was
overriding the owner's chosen reply language - the model would copy that exact style for
every add_stock confirmation, even for an English-only shop. This replaces it with a
language-neutral description of the SHAPE of a good reply instead of literal words in one
language, so it no longer fights the language_lock_patch.py rule.

Requires language_lock_patch.py and terse_reply_patch.py to already be applied.
Run from ~/voicestock/backend :   venv/bin/python example_language_fix_patch.py
Edits app/main.py. Stops WITHOUT changing anything if the anchor isn't found exactly
once. Safe to run twice.
"""
import sys

P = "app/main.py"
s = open(P).read()

if "whichever language rule is in force" in s:
    print("already patched - nothing to do")
    sys.exit(0)

anchor = '''    "you got there. For example, after adding stock say just 'Maggi ke 2 packet jod diye, ab 22 hain.' and "
    "nothing more. "'''
assert s.count(anchor) == 1, "anchor not found exactly once, nothing changed. Send me this: " + anchor[:90]

new = '''    "you got there. For example, after adding stock, say only the product, how much was added, and the new "
    "total, in one short sentence, in whichever language rule is in force below - nothing more. "'''

s = s.replace(anchor, new)
compile(s, P, "exec")
open(P, "w").write(s)
print("patched")

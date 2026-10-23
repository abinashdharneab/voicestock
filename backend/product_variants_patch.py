"""
VoiceStock - Product variants patch (main.py).

The same product name can only point at ONE product per shop (one price, one stock
count) - so "Maggi" can't silently be both a 5-rupee pack and a 10-rupee pack with
different prices and quantities. This doesn't change the database (too risky this close
to a deadline); instead it teaches the assistant to handle variants the safe way: give
each price/size variant its OWN distinguishing name (e.g. "Maggi 5rs", "Maggi 10rs"), and
ask which one before acting if the owner says a bare name that could mean more than one.

Run from ~/voicestock/backend :   venv/bin/python product_variants_patch.py
Edits app/main.py. Stops WITHOUT changing anything if the anchor isn't found exactly
once. Safe to run twice.
"""
import sys

P = "app/main.py"
s = open(P).read()

if "distinguishing name" in s:
    print("already patched - nothing to do")
    sys.exit(0)

anchor = '''    "2) Always pass product names to tools in English letters using the standard brand spelling, "
    "even if the owner speaks Hindi (for example 'मैगी' becomes 'Maggi'). New products must be added with an English name. "'''
assert s.count(anchor) == 1, "anchor not found exactly once, nothing changed. Send me this: " + anchor[:90]

new = '''    "2) Always pass product names to tools in English letters using the standard brand spelling, "
    "even if the owner speaks Hindi (for example 'मैगी' becomes 'Maggi'). New products must be added with an English name. "
    "2a) The SAME product name can only ever have one price and one stock count - it cannot be two different "
    "packs or prices at once. If the owner describes a size or price variant of something that may already exist "
    "(e.g. 'Maggi ka 5 rupaye wala' when a plain 'Maggi' or a different Maggi variant may already be in stock), "
    "give that variant its own distinguishing name when adding it - like 'Maggi 5rs' and 'Maggi 10rs' - rather than "
    "reusing a name already used for a different price or size, which would silently merge them into one. If the "
    "owner says a bare name and a tool result shows more than one existing product containing that word (or says "
    "needs_clarification with several suggestions), read out the options and ask which one they mean before "
    "checking, selling, or adding to it - never guess. "'''

s = s.replace(anchor, new)
compile(s, P, "exec")
open(P, "w").write(s)
print("patched")

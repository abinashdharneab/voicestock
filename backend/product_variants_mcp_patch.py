"""
VoiceStock - Product variants patch (mcp_server.py), for Alexa+.

Same as product_variants_patch.py (main.py): teaches the assistant to give each
price/size variant of a product its own distinguishing name, and to ask which one the
owner means rather than guessing when a bare name matches more than one.

Run from ~/voicestock/backend :   venv/bin/python product_variants_mcp_patch.py
Edits app/mcp_server.py. Stops WITHOUT changing anything if the anchor isn't found
exactly once. Safe to run twice.
"""
import sys

P = "app/mcp_server.py"
s = open(P).read()

if "distinguishing name" in s:
    print("already patched - nothing to do")
    sys.exit(0)


def swap(old, new):
    global s
    assert s.count(old) == 1, "anchor not found exactly once, nothing changed. Send me this: " + old[:90]
    s = s.replace(old, new)


swap('''            "numbers; never guess. Product names are English brand spellings (Maggi, not मैगी). "''',
'''            "numbers; never guess. Product names are English brand spellings (Maggi, not मैगी). "
            "The SAME product name can only have one price and one stock count. If the owner describes a size "
            "or price variant of something that may already exist, give it its own distinguishing name when "
            "adding it (e.g. 'Maggi 5rs', 'Maggi 10rs') rather than reusing a name already used for a different "
            "price or size. If a bare name matches more than one existing product, ask which one before acting. "''')

compile(s, P, "exec")
open(P, "w").write(s)
print("patched")

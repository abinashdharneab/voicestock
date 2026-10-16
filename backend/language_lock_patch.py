"""
VoiceStock - Language lock patch (main.py).

The owner could pick English at signup but the assistant would still drift into Hindi or
Hinglish mid-conversation - the old instruction was a single soft line near the start of
a long prompt, easy to lose over a long conversation. This makes it a hard, repeated
rule, and states it plainly even when the owner themself speaks Hindi to it.

Run from ~/voicestock/backend :   venv/bin/python language_lock_patch.py
Edits app/main.py. Stops WITHOUT changing anything if an anchor isn't found exactly
once. Safe to run twice.
"""
import sys

P = "app/main.py"
s = open(P).read()

if "even if the owner speaks Hindi to you" in s:
    print("already patched - nothing to do")
    sys.exit(0)


def swap(old, new):
    global s
    assert s.count(old) == 1, "anchor not found exactly once, nothing changed. Send me this: " + old[:90]
    s = s.replace(old, new)


swap('''def _language_hint(lang: str) -> str:
    if lang == "en":
        return " Always reply in English."
    if lang == "hi":
        return " Always reply in Hindi or Hinglish, the way a shopkeeper in India would speak."
    return ""''',
'''def _language_hint(lang: str) -> str:
    if lang == "en":
        return (" The owner has chosen English as the reply language. Reply in English in EVERY single turn for "
                "this whole conversation, even if the owner speaks Hindi to you or mentions a Hindi product name - "
                "translate or transliterate as needed, but your own words are always English, never Hindi or "
                "Hinglish. This does not change partway through a long conversation.")
    if lang == "hi":
        return (" The owner has chosen Hindi as the reply language. Reply in Hindi or Hinglish, the way a "
                "shopkeeper in India would speak, in EVERY single turn for this whole conversation, even if the "
                "owner speaks English to you. This does not change partway through a long conversation.")
    return ""''')

swap('''        system_prompt=SYSTEM_PROMPT + _language_hint(_vs["voice"]["language"]) + " " + _now_ist_line(),''',
'''        system_prompt=(SYSTEM_PROMPT + " " + _now_ist_line() + _language_hint(_vs["voice"]["language"])),''')

compile(s, P, "exec")
open(P, "w").write(s)
print("patched")

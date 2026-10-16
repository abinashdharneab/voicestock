"""
VoiceStock - Wake greeting: language + variety patch (main.py).

Upgrades the wake greeting from wake_greet_backend_patch.py (must already be applied):
  - uses the shop's own saved voice-language setting (English/Hindi/auto) instead of
    leaving the model to guess, so an English-only owner doesn't get a Hindi line
  - asks the model for different wording each time, instead of the same fixed line
    ("Haan, boliye") every single time

Run from ~/voicestock/backend :   venv/bin/python wake_greet_lang_patch.py
Edits app/main.py. Stops WITHOUT changing anything if an anchor isn't found exactly
once. Safe to run twice.
"""
import sys

P = "app/main.py"
s = open(P).read()

if "weave it in naturally" in s:
    print("already patched - nothing to do")
    sys.exit(0)

assert "wake_greet" in s, (
    "app/main.py doesn't have the wake greeting yet. Run wake_greet_backend_patch.py first, then this one.")


def swap(old, new):
    global s
    assert s.count(old) == 1, "anchor not found exactly once, nothing changed. Send me this: " + old[:90]
    s = s.replace(old, new)


# 1. WebSocketInput now also takes the shop's saved voice language
swap('''    def __init__(self, websocket: WebSocket):
        self.websocket = websocket

    async def start(self, agent):
        pass

    async def stop(self):
        pass

    async def __call__(self):
        while True:''',
'''    def __init__(self, websocket: WebSocket, language: str = "auto"):
        self.websocket = websocket
        self.language = language

    async def start(self, agent):
        pass

    async def stop(self):
        pass

    async def __call__(self):
        while True:''')

# 2. the greeting instruction: pick the language explicitly, and ask for fresh wording each time
swap('''                from strands.types.content import TextBlock
                name = str(payload.get("name") or "").strip()[:40]
                who = f" The owner's name is {name}; use it if it sounds natural." if name else ""
                return TextBlock(
                    "[The owner just said the wake phrase and is waiting, before saying anything else. "
                    "Reply with ONLY one short natural greeting line (for example 'Haan, boliye' or 'Yes, "
                    "go ahead'), in the language you would normally use with them." + who +
                    " Call no tool. Say nothing else - just the greeting.]")''',
'''                from strands.types.content import TextBlock
                name = str(payload.get("name") or "").strip()[:40]
                who = f" Their name is {name} - weave it in naturally sometimes, not every single time." if name else ""
                lang = {"en": "in English", "hi": "in Hindi or Hinglish, the way a shopkeeper would speak"}.get(
                    self.language, "in whichever language fits - Hindi, Hinglish or English")
                return TextBlock(
                    "[The owner just said the wake phrase and is waiting, before saying anything else. Say ONLY "
                    "one very short line acknowledging you're listening, " + lang + "." + who +
                    " Make up your own natural wording each time - do not reuse the same exact sentence you said "
                    "last time. Call no tool. Say nothing else - just that one short line.]")''')

# 3. pass the shop's saved language setting in
swap('''    ws_input = WebSocketInput(websocket)''',
     '''    ws_input = WebSocketInput(websocket, language=_vs["voice"]["language"])''')

compile(s, P, "exec")
open(P, "w").write(s)
print("patched")

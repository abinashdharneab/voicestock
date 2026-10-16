"""
VoiceStock - Current date/time patch (main.py).

The assistant previously had no way to know today's date or the current time - it could
only guess from its training data, which is wrong. This gives it two things:
  1. Every voice connection starts knowing the current date and time (IST), so trivial
     questions ("what's the date today") never need a tool call.
  2. A get_current_datetime tool, for when it's useful mid-conversation (e.g. working out
     "how many days until this expires" precisely).

Run from ~/voicestock/backend :   venv/bin/python datetime_patch.py
Edits app/main.py. Stops WITHOUT changing anything if an anchor isn't found exactly
once. Safe to run twice.
"""
import sys

P = "app/main.py"
s = open(P).read()

if "get_current_datetime" in s:
    print("already patched - nothing to do")
    sys.exit(0)


def swap(old, new):
    global s
    assert s.count(old) == 1, "anchor not found exactly once, nothing changed. Send me this: " + old[:90]
    s = s.replace(old, new)


# 1. import timedelta (datetime is already imported)
swap('''from datetime import datetime''', '''from datetime import datetime, timedelta''')

# 2. a tiny IST-now helper, placed right before it's first needed
swap('''def _language_hint(lang: str) -> str:''',
'''IST = timedelta(hours=5, minutes=30)


def _now_ist_line() -> str:
    now = datetime.utcnow() + IST
    return f"Right now it is {now.strftime('%A, %d %B %Y, %I:%M %p')} IST."


def _language_hint(lang: str) -> str:''')

# 3. the live date/time goes into the system prompt every time a voice connection starts
swap('''        system_prompt=SYSTEM_PROMPT + _language_hint(_vs["voice"]["language"]),''',
'''        system_prompt=SYSTEM_PROMPT + _language_hint(_vs["voice"]["language"]) + " " + _now_ist_line(),''')

# 4. a tool for precise on-demand lookups mid-conversation
swap('''    @strands_tool
    def confirm_action(action_id: str) -> dict:''',
'''    @strands_tool
    def get_current_datetime() -> dict:
        """The current date and time (India, IST). Use this whenever exact 'today', 'tomorrow', or a precise
        days-until-expiry count matters, rather than assuming."""
        now = datetime.utcnow() + IST
        return {"date": now.strftime("%Y-%m-%d"), "time": now.strftime("%H:%M"), "day_of_week": now.strftime("%A"),
                "message": _now_ist_line()}

    @strands_tool
    def confirm_action(action_id: str) -> dict:''')

swap('''            confirm_action, cancel_action, product_margin, profit_report, top_sellers,
            low_margin_items, set_price, set_cost_price, order_multiple]''',
'''            confirm_action, cancel_action, product_margin, profit_report, top_sellers,
            low_margin_items, set_price, set_cost_price, order_multiple, get_current_datetime]''')

compile(s, P, "exec")
open(P, "w").write(s)
print("patched")

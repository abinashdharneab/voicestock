"""
VoiceStock - Current date/time patch (mcp_server.py), for Alexa+.

Run from ~/voicestock/backend :   venv/bin/python datetime_mcp_patch.py
Edits app/mcp_server.py. Stops WITHOUT changing anything if an anchor isn't found
exactly once. Safe to run twice.
"""
import sys

P = "app/mcp_server.py"
s = open(P).read()

if "get_current_datetime" in s:
    print("already patched - nothing to do")
    sys.exit(0)


def swap(old, new):
    global s
    assert s.count(old) == 1, "anchor not found exactly once, nothing changed. Send me this: " + old[:90]
    s = s.replace(old, new)


swap('''import contextvars
import hashlib
import hmac
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path''',
'''import contextvars
import hashlib
import hmac
import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path

IST = timedelta(hours=5, minutes=30)''')

swap('''    @mcp.tool()
    def confirm_action(action_id: str) -> dict:''',
'''    @mcp.tool()
    def get_current_datetime() -> dict:
        """The current date and time (India, IST). Use whenever exact 'today', 'tomorrow', or a precise
        days-until-expiry count matters, rather than assuming."""
        now = datetime.utcnow() + IST
        return {"date": now.strftime("%Y-%m-%d"), "time": now.strftime("%H:%M"), "day_of_week": now.strftime("%A"),
                "message": f"Right now it is {now.strftime('%A, %d %B %Y, %I:%M %p')} IST."}

    @mcp.tool()
    def confirm_action(action_id: str) -> dict:''')

compile(s, P, "exec")
open(P, "w").write(s)
print("patched")

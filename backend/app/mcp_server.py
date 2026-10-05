"""
VoiceStock - mcp_server.py  (goes in ~/voicestock/backend/app/)

A self-hosted MCP server (Streamable HTTP, MCP spec 2025-11-25) that lets an assistant such as
Alexa+ run a shop by voice: stock, sales, wholesalers and the one-shot "restock" plan.

It is mounted inside the existing FastAPI app at   https://<your-domain>/mcp
Every request must carry   Authorization: Bearer <token>   where the token belongs to ONE shop.
Create a token on the server with:   python3 make_mcp_token.py <shop_id>

The tools are thin wrappers over the same functions the voice assistant uses (tools.py, restock.py),
so the web app and the MCP server always give the same answers.
"""
import contextvars
import hashlib
import hmac
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from . import models, restock, tools
from .database import SessionLocal

PUBLIC_HOST = os.environ.get("VOICESTOCK_PUBLIC_HOST", "voicestock-abinash.duckdns.org")

_current_shop = contextvars.ContextVar("voicestock_shop_id", default=None)


# ---------------------------------------------------------------- tokens
def _secret() -> bytes:
    env = os.environ.get("MCP_SECRET")
    if env:
        return env.encode()
    path = Path(__file__).resolve().parent.parent / ".mcp_secret"
    if not path.exists():
        path.write_text(secrets.token_hex(32))
        try:
            path.chmod(0o600)
        except OSError:
            pass
    return path.read_text().strip().encode()


def make_token(shop_id: int) -> str:
    sig = hmac.new(_secret(), f"mcp:{int(shop_id)}".encode(), hashlib.sha256).hexdigest()[:40]
    return f"vs_{int(shop_id)}_{sig}"


def verify_token(token: str):
    """Returns the shop id the token belongs to, or None."""
    try:
        prefix, sid, sig = token.split("_", 2)
        if prefix != "vs" or not sid.isdigit():
            return None
        expected = make_token(int(sid)).rsplit("_", 1)[1]
        return int(sid) if hmac.compare_digest(sig, expected) else None
    except Exception:
        return None


# ---------------------------------------------------------------- ASGI helpers
class MCPPathFix:
    """Lets clients call /mcp (no trailing slash) without a redirect."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket") and scope.get("path") == "/mcp":
            scope = dict(scope, path="/mcp/", raw_path=b"/mcp/")
        await self.app(scope, receive, send)


class _Auth:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        auth = headers.get("authorization", "")
        shop_id = verify_token(auth[7:].strip()) if auth.lower().startswith("bearer ") else None
        if shop_id is None:
            body = b'{"error":"unauthorized","message":"Missing or invalid bearer token."}'
            await send({"type": "http.response.start", "status": 401, "headers": [
                (b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
                (b"www-authenticate", b'Bearer realm="voicestock"')]})
            await send({"type": "http.response.body", "body": body})
            return
        token = _current_shop.set(shop_id)
        try:
            await self.app(scope, receive, send)
        finally:
            _current_shop.reset(token)


def _shop() -> int:
    sid = _current_shop.get()
    if sid is None:
        raise RuntimeError("Not authenticated.")
    return sid


def _with_db(fn):
    db = SessionLocal()
    try:
        return fn(db, _shop())
    finally:
        db.close()


# ---------------------------------------------------------------- the server
def build_mcp():
    mcp = FastMCP(
        "VoiceStock",
        instructions=(
            "VoiceStock helps an Indian shop owner manage stock by voice. Always use the tools for stock "
            "numbers; never guess. Product names are English brand spellings (Maggi, not मैगी). "
            "For restocking: call plan_restock, read the plan to the owner in one or two sentences, and call "
            "confirm_restock ONLY after the owner clearly says yes. Never confirm a plan the owner has not heard."),
        stateless_http=True, json_response=True, streamable_http_path="/",
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[PUBLIC_HOST, "localhost:*", "127.0.0.1:*"],
            allowed_origins=[f"https://{PUBLIC_HOST}", "http://localhost:*", "http://127.0.0.1:*"]),
    )

    @mcp.tool()
    def list_items() -> dict:
        """List every product in the shop with quantity, unit, whether it is low and its photo URL."""
        return _with_db(lambda db, s: tools.list_products(db, s))

    @mcp.tool()
    def check_item(product_name: str) -> dict:
        """How much stock is left of one product. Similar names are matched automatically."""
        return _with_db(lambda db, s: tools.check_item(db, product_name, s))

    @mcp.tool()
    def get_low_stock() -> dict:
        """Products that are below their minimum stock level."""
        return _with_db(lambda db, s: tools.get_low_stock(db, s))

    @mcp.tool()
    def get_expiring_items(within_days: int = 7) -> dict:
        """Batches that expire within the given number of days."""
        return _with_db(lambda db, s: tools.get_expiring_items(db, within_days, s))

    @mcp.tool()
    def add_stock(product_name: str, quantity: int, expiry_date: str = None, cost_price: float = None,
                  unit: str = "packet", confirm_new: bool = False) -> dict:
        """Add stock the owner received. If the result says needs_clarification, ask the owner whether it is
        a new product, then call again with confirm_new=true."""
        return _with_db(lambda db, s: tools.add_stock(db, product_name, quantity, s, expiry_date, cost_price, unit,
                                                     confirm_new))

    @mcp.tool()
    def record_sale(product_name: str, quantity: int, bill_id: str = None) -> dict:
        """Record a sale and reduce stock."""
        return _with_db(lambda db, s: tools.record_sale(db, product_name, quantity, s, bill_id))

    @mcp.tool()
    def find_wholesaler(product_name: str) -> dict:
        """Which wholesaler has the best price for a product (does not order)."""
        def run(db, s):
            return tools.find_best_wholesaler(db, product_name) or {
                "found": False, "message": f"No wholesaler currently has '{product_name}'."}
        return _with_db(run)

    @mcp.tool()
    def plan_restock(budget: float = None) -> dict:
        """Build a restock plan for everything below its minimum: quantity from recent sales, cheapest wholesaler
        that can supply it, optional rupee budget. Orders NOTHING. Read the summary to the owner and ask for
        approval, then call confirm_restock with the plan_id."""
        return _with_db(lambda db, s: restock.plan_restock(db, s, budget))

    @mcp.tool()
    def confirm_restock(plan_id: str) -> dict:
        """Place the orders in a plan the owner approved. Safe to call twice: it never orders twice."""
        return _with_db(lambda db, s: restock.confirm_restock(db, s, plan_id))

    @mcp.tool()
    def morning_brief() -> dict:
        """Short daily summary: low stock, expiring soon, orders on the way and sales in the last 24 hours."""
        return _with_db(lambda db, s: restock.morning_brief(db, s))

    @mcp.tool()
    def my_orders(limit: int = 10) -> dict:
        """The owner's most recent wholesale orders and where each one is (placed, confirmed, out for delivery,
        delivered, cancelled)."""
        def run(db, s):
            rows = db.query(models.WholesaleOrder).filter(models.WholesaleOrder.retailer_id == s).order_by(
                models.WholesaleOrder.id.desc()).limit(max(1, min(limit, 30))).all()
            out = []
            for o in rows:
                wh = db.get(models.Wholesaler, o.wholesaler_id)
                out.append({"order_id": o.id, "product": o.product_name, "quantity": o.quantity,
                            "total_price": o.total_price, "status": o.status,
                            "wholesaler": wh.name if wh else None,
                            "placed_at": o.created_at.isoformat() if o.created_at else None})
            return {"count": len(out), "orders": out}
        return _with_db(run)

    asgi = _Auth(mcp.streamable_http_app())     # also creates mcp.session_manager

    @asynccontextmanager
    async def lifespan():
        async with mcp.session_manager.run():
            yield

    return asgi, lifespan

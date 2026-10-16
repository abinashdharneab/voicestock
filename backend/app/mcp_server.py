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
from datetime import datetime, timedelta
from pathlib import Path

IST = timedelta(hours=5, minutes=30)

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from . import models, nearby, restock, tools, confirm, business, bulk_order
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
            "confirm_restock ONLY after the owner clearly says yes. Never confirm a plan the owner has not heard. "
            "record_sale, delete_item, fix_product_name, order_from_wholesaler and set_price only PLAN now and return "
            "an action_id; nothing changes until confirm_action is called with that EXACT action_id after a clear yes. "
            "If the owner says no, call cancel_action instead. Never reuse an action_id from earlier. "
            "You are not just a stock clerk: think like an experienced business manager. Use product_margin, "
            "profit_report, top_sellers and low_margin_items on your own when relevant, not only when asked word "
            "for word, and keep it to one short practical line, not a dump of numbers. low_margin_items returning "
            "0 flagged items means 0 out of whatever it could check, not necessarily that everything is fine - "
            "always mention if it says products were skipped for missing price or cost."),
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
        """Plan recording a sale (reduces stock). Tell the owner the product, quantity and total, then call
        confirm_action with the returned action_id ONLY after they clearly say yes. Nothing changes until then."""
        return _with_db(lambda db, s: confirm.plan_sale(db, s, product_name, quantity, bill_id))

    @mcp.tool()
    def delete_item(product_name: str) -> dict:
        """Plan deleting a product entirely, including its stock and sale history. Read the warning to the owner
        and call confirm_action with the returned action_id ONLY after a clear yes. This cannot be undone."""
        return _with_db(lambda db, s: confirm.plan_delete(db, s, product_name))

    @mcp.tool()
    def fix_product_name(product_name: str, new_name: str) -> dict:
        """Plan renaming a product, e.g. to fix a spelling mistake. Confirm the old and new name with the owner,
        then call confirm_action with the returned action_id ONLY after a clear yes."""
        return _with_db(lambda db, s: confirm.plan_rename(db, s, product_name, new_name))

    @mcp.tool()
    def find_wholesaler(product_name: str) -> dict:
        """Which wholesaler has the best price for a product (does not order)."""
        def run(db, s):
            return tools.find_best_wholesaler(db, product_name) or {
                "found": False, "message": f"No wholesaler currently has '{product_name}'."}
        return _with_db(run)

    @mcp.tool()
    def list_wholesalers(max_km: float = None) -> dict:
        """Wholesalers that have items in stock, nearest first, with distance in km (straight-line). Pass max_km to
        only get wholesalers within that range, e.g. 5. If the owner's shop location isn't saved, the result says so."""
        return _with_db(lambda db, s: nearby.list_wholesalers(db, s, max_km))

    @mcp.tool()
    def wholesaler_items(wholesaler_name: str) -> dict:
        """Everything one wholesaler has in stock, with price per unit and quantity. The name can be approximate;
        if several match, the result asks which one."""
        return _with_db(lambda db, s: nearby.wholesaler_items(db, s, wholesaler_name))

    @mcp.tool()
    def order_from_wholesaler(product_name: str, quantity: int, wholesaler_name: str = None) -> dict:
        """Plan an order with a wholesaler. With wholesaler_name it plans that wholesaler; without it, the cheapest
        one. Tell the owner the wholesaler, quantity and total, then call confirm_action with the returned
        action_id ONLY after a clear yes. Nothing changes until then."""
        return _with_db(lambda db, s: confirm.plan_order(db, s, product_name, quantity, wholesaler_name))

    @mcp.tool()
    def get_current_datetime() -> dict:
        """The current date and time (India, IST). Use whenever exact 'today', 'tomorrow', or a precise
        days-until-expiry count matters, rather than assuming."""
        now = datetime.utcnow() + IST
        return {"date": now.strftime("%Y-%m-%d"), "time": now.strftime("%H:%M"), "day_of_week": now.strftime("%A"),
                "message": f"Right now it is {now.strftime('%A, %d %B %Y, %I:%M %p')} IST."}

    @mcp.tool()
    def confirm_action(action_id: str) -> dict:
        """Carry out a previously planned action (sale, delete, rename, or wholesaler order) using the action_id
        it returned. Call ONLY after the owner clearly said yes to what you read out. Safe to call twice: it
        never repeats an action."""
        return _with_db(lambda db, s: confirm.confirm_action(db, s, action_id))

    @mcp.tool()
    def cancel_action(action_id: str) -> dict:
        """Cancel a previously planned action the owner said no to, or changed their mind about."""
        return _with_db(lambda db, s: confirm.cancel_action(db, s, action_id))

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
    def product_margin(product_name: str) -> dict:
        """Selling price, cost price, profit per unit and margin percent for one product, plus how much profit is
        sitting in current stock."""
        return _with_db(lambda db, s: business.product_margin(db, s, product_name))

    @mcp.tool()
    def profit_report(days: int = 30) -> dict:
        """Revenue and profit for the last N days (default 30), with the top items by profit. Partial if some
        products have no cost price saved."""
        return _with_db(lambda db, s: business.profit_report(db, s, days))

    @mcp.tool()
    def top_sellers(days: int = 7, by: str = "quantity") -> dict:
        """What is selling best over the last N days (default 7), by 'quantity' or 'revenue'."""
        return _with_db(lambda db, s: business.top_sellers(db, s, days, by=by))

    @mcp.tool()
    def low_margin_items() -> dict:
        """Products selling for not much over their cost price (under 15% margin), worth a price review."""
        return _with_db(lambda db, s: business.low_margin_items(db, s))

    @mcp.tool()
    def set_price(product_name: str, new_price: float) -> dict:
        """Plan changing a product's selling price. Read the old and new price to the owner, then call
        confirm_action with the returned action_id ONLY after a clear yes."""
        return _with_db(lambda db, s: confirm.plan_set_price(db, s, product_name, new_price))

    @mcp.tool()
    def order_multiple(items: list) -> dict:
        """Plan ordering SEVERAL different products from wholesalers in one go - use instead of
        order_from_wholesaler whenever more than one item is named at once. Pass items as a list of objects,
        each with product_name and quantity. Picks the fewest wholesalers needed (nearest first when there is a
        choice) and gives one combined total. Read the breakdown, then call confirm_action with the returned
        action_id ONLY after a clear yes."""
        return _with_db(lambda db, s: bulk_order.plan(db, s, items))

    @mcp.tool()
    def set_cost_price(product_name: str, cost_price: float) -> dict:
        """Plan setting what a product costs (its cost price), applied to all its current stock - not only new
        stock. Use when the owner states a cost price directly, not through add_stock. Read the summary, then
        call confirm_action with the returned action_id ONLY after a clear yes."""
        return _with_db(lambda db, s: confirm.plan_set_cost_price(db, s, product_name, cost_price))

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

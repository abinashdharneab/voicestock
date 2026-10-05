from fastapi import FastAPI, Depends, HTTPException, WebSocket, WebSocketDisconnect, UploadFile, File, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
import base64
import os
import shutil
import hmac
import hashlib
from datetime import datetime

from .database import Base, engine, get_db, SessionLocal
from . import models, tools
from . import restock
from .mcp_server import build_mcp, MCPPathFix
from .bedrock_agent import chat_with_agent

from strands.experimental.bidi.agent import BidiAgent
from strands.experimental.bidi.models import BedrockNovaSonicModel
from strands.experimental.bidi.types.media import AudioDelta
from strands.experimental.bidi.types.events import BidiAudioStreamEvent, BidiTranscriptStreamEvent
from strands import tool as strands_tool

# ---------------- PROFILE TABLES (profile patch) ----------------
import json as _json
import re as _re
from sqlalchemy import Column as _Col, Integer as _Int, String as _Str, Text as _Text, DateTime as _DT, ForeignKey as _FK


class ShopProfile(Base):
    __tablename__ = "shop_profiles"

    shop_id = _Col(_Int, _FK("shops.id"), primary_key=True)
    email = _Col(_Str, nullable=True)
    phone = _Col(_Str, nullable=True)
    business_name = _Col(_Str, nullable=True)
    address = _Col(_Str, nullable=True)
    gstin = _Col(_Str, nullable=True)
    settings_json = _Col(_Text, nullable=True)
    plan = _Col(_Str, default="free")
    voice_used = _Col(_Int, default=0)
    usage_month = _Col(_Str, nullable=True)
    created_at = _Col(_DT, default=datetime.utcnow)


class VoiceLog(Base):
    __tablename__ = "voice_logs"

    id = _Col(_Int, primary_key=True, autoincrement=True)
    shop_id = _Col(_Int, _FK("shops.id"), index=True, nullable=False)
    role = _Col(_Str, nullable=False)
    text = _Col(_Text, nullable=False)
    created_at = _Col(_DT, default=datetime.utcnow)


# ---------------- LOCATION TABLE (location patch) ----------------
from sqlalchemy import Column as _LC, Integer as _LI, String as _LS, Float as _LF


class PartyLocation(Base):
    __tablename__ = "party_locations"

    role = _LC(_LS, primary_key=True)        # "wholesaler" or "retailer"
    account_id = _LC(_LI, primary_key=True)
    address = _LC(_LS, nullable=True)
    phone = _LC(_LS, nullable=True)
    lat = _LC(_LF, nullable=True)
    lng = _LC(_LF, nullable=True)


Base.metadata.create_all(bind=engine)

app = FastAPI(title="VoiceStock API")

from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://abinashdharneab.github.io",
        "http://127.0.0.1:5500",
        "http://localhost:5500",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)

os.makedirs("app/static/images", exist_ok=True)
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# ---------------- MCP server (restock patch) ----------------
from contextlib import asynccontextmanager as _acm

_mcp_asgi, _mcp_lifespan = build_mcp()
app.mount("/mcp", _mcp_asgi)
app.add_middleware(MCPPathFix)
_prev_lifespan = app.router.lifespan_context


@_acm
async def _lifespan_with_mcp(_app):
    async with _prev_lifespan(_app):
        async with _mcp_lifespan():
            yield


app.router.lifespan_context = _lifespan_with_mcp


class AuthRequest(BaseModel):
    role: str  # "retailer" | "wholesaler" | "delivery"
    name: str
    pin: str
    area: Optional[str] = None


ROLE_TABLE = {
    "retailer": models.Shop,
    "wholesaler": models.Wholesaler,
    "delivery": models.Driver,
}


@app.get("/login")
def login_page():
    return FileResponse("app/static/login.html")


@app.post("/signup")
def signup(payload: AuthRequest, db: Session = Depends(get_db)):
    Model = ROLE_TABLE.get(payload.role)
    if not Model:
        raise HTTPException(status_code=400, detail="Invalid role.")
    existing = db.query(Model).filter(Model.name == payload.name).first()
    if existing:
        raise HTTPException(status_code=400, detail="This name is already taken. Choose another.")
    if len(payload.pin) != 4 or not payload.pin.isdigit():
        raise HTTPException(status_code=400, detail="PIN must be exactly 4 digits.")
    account = Model(name=payload.name, pin=payload.pin, area=payload.area)
    db.add(account)
    db.commit()
    db.refresh(account)
    return {"account_id": account.id, "name": account.name, "role": payload.role}


@app.post("/login")
def login(payload: AuthRequest, db: Session = Depends(get_db)):
    Model = ROLE_TABLE.get(payload.role)
    if not Model:
        raise HTTPException(status_code=400, detail="Invalid role.")
    account = db.query(Model).filter(Model.name == payload.name).first()
    if not account or account.pin != payload.pin:
        raise HTTPException(status_code=401, detail="Wrong name or PIN.")
    return {"account_id": account.id, "name": account.name, "role": payload.role}


@app.get("/voice")
def voice_page():
    return FileResponse("app/static/voice.html")


@app.get("/")
def root():
    return {"message": "VoiceStock backend is running"}


@app.get("/products")
def list_products(account_id: int = Query(...), db: Session = Depends(get_db)):
    products = db.query(models.Product).filter(models.Product.shop_id == account_id).all()
    result = []
    for p in products:
        row = {c.name: getattr(p, c.name) for c in p.__table__.columns}
        row["batches"] = [
            {c.name: getattr(b, c.name) for c in b.__table__.columns}
            for b in p.batches
        ]
        row["total_quantity"] = sum((b.quantity or 0) for b in p.batches)
        result.append(row)
    return result


@app.post("/products/{product_id}/image")
def upload_product_image(product_id: int, file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Manual photo upload, for products where the automatic photo lookup found nothing."""
    product = db.get(models.Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    ext = os.path.splitext(file.filename or "")[1] or ".jpg"
    path = f"app/static/images/{product_id}{ext}"
    with open(path, "wb") as f:
        shutil.copyfileobj(file.file, f)
    product.image_url = f"/static/images/{product_id}{ext}"
    db.commit()
    tools.save_to_library(db, product.name, product.image_url)
    return {"image_url": product.image_url}


@app.get("/check-item")
def api_check_item(product_name: str, account_id: int = Query(...), db: Session = Depends(get_db)):
    return tools.check_item(db, product_name, account_id)


@app.get("/expiring-items")
def api_expiring_items(account_id: int = Query(...), within_days: int = 7, db: Session = Depends(get_db)):
    return tools.get_expiring_items(db, within_days, account_id)


@app.get("/low-stock")
def api_low_stock(account_id: int = Query(...), db: Session = Depends(get_db)):
    return tools.get_low_stock(db, account_id)


class AddStockRequest(BaseModel):
    product_name: str
    quantity: int
    shop_id: int
    expiry_date: Optional[str] = None
    cost_price: Optional[float] = None
    unit: Optional[str] = "packet"


@app.post("/add-stock")
def api_add_stock(payload: AddStockRequest, db: Session = Depends(get_db)):
    return tools.add_stock(
        db, payload.product_name, payload.quantity, payload.shop_id,
        payload.expiry_date, payload.cost_price, payload.unit
    )


class RecordSaleRequest(BaseModel):
    product_name: str
    quantity: int
    shop_id: int
    bill_id: Optional[str] = None


@app.post("/record-sale")
def api_record_sale(payload: RecordSaleRequest, db: Session = Depends(get_db)):
    result = tools.record_sale(db, payload.product_name, payload.quantity, payload.shop_id, payload.bill_id)
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["message"])
    return result


SYSTEM_PROMPT = (
    "You are VoiceStock, a helpful voice assistant for a small shop owner in India. "
    "The owner may speak Hindi, Hinglish or English, and pronunciation may be unclear. "
    "Rules: "
    "1) Always use the tools for stock questions and never guess numbers. "
    "2) Always pass product names to tools in English letters using the standard brand spelling, "
    "even if the owner speaks Hindi (for example 'मैगी' becomes 'Maggi'). New products must be added with an English name. "
    "3) If the owner asks what items or stock they have, or asks for everything, call list_all_items and read the items "
    "with their quantities directly. Do not ask for a name. "
    "4) The tools match similar names automatically, so pass the name as you heard it. If the result shows a different "
    "product name than what was heard, use the product name from the result in your reply. "
    "5) If a tool result says needs_clarification, ask the owner short: did you mean the suggested product, or is it a new product? "
    "If they say it is new, call add_stock again with confirm_new=true. "
    "6) If a product is not found, say so and offer the closest suggestions. "
    "7) To delete a product, call delete_item. If it needs_clarification, read out the suggested product name and ask the owner "
    "to confirm before calling delete_item again with confirm=true. Deleting a product removes all its stock and sale history, "
    "so never delete without the owner clearly confirming the exact product first. "
    "8) To fix a spelling mistake or rename a product, call fix_product_name with the current name and the corrected name. "
    "If it needs_clarification, confirm the exact product with the owner first, then call it again with confirm=true. "
    "9) When the owner asks to restock, reorder what is low, or 'order everything that is finished', call plan_restock "
    "(pass budget only if they gave a rupee limit). Tell them the plan in one or two short sentences: how many items, "
    "from how many wholesalers, the total, and anything that could not be included. Ask if you should place the orders. "
    "Call confirm_restock ONLY after a clear yes, and never for a plan they have not heard. "
    "10) When the owner asks for a morning brief, daily summary, or 'what should I know today', call morning_brief and "
    "read the summary naturally. "
    "Keep replies short, natural and conversational, in the language the owner is using."
)


class WebSocketInput:
    """Reads audio chunks sent by the browser over the WebSocket."""

    def __init__(self, websocket: WebSocket):
        self.websocket = websocket

    async def start(self, agent):
        pass

    async def stop(self):
        pass

    async def __call__(self):
        data = await self.websocket.receive_bytes()
        return AudioDelta(format="pcm", source={"bytes": data})


class WebSocketOutput:
    """Sends audio/text events from the agent back to the browser."""

    def __init__(self, websocket: WebSocket, shop_id: int = None):
        self.websocket = websocket
        self.shop_id = shop_id

    async def start(self, agent):
        pass

    async def stop(self):
        pass

    async def __call__(self, event):
        if isinstance(event, BidiAudioStreamEvent):
            audio_bytes = base64.b64decode(event.audio)
            await self.websocket.send_bytes(audio_bytes)
        elif isinstance(event, BidiTranscriptStreamEvent):
            _log_voice(self.shop_id, event.role, event.delta)
            await self.websocket.send_json({"type": "transcript", "text": event.delta, "role": event.role})


def build_tools_for_shop(shop_id: int):
    """Creates a fresh set of voice tools bound to one shop, so each connection only ever
    touches that shop's data."""

    @strands_tool
    def list_all_items() -> dict:
        """List ALL products in the shop with their current quantity."""
        db = SessionLocal()
        try:
            return tools.list_products(db, shop_id)
        finally:
            db.close()

    @strands_tool
    def check_item(product_name: str) -> dict:
        """Check how much stock is left for one product. Pass the name as heard."""
        db = SessionLocal()
        try:
            return tools.check_item(db, product_name, shop_id)
        finally:
            db.close()

    @strands_tool
    def get_expiring_items(within_days: int = 7) -> dict:
        """Get products expiring within a number of days."""
        db = SessionLocal()
        try:
            return tools.get_expiring_items(db, within_days, shop_id)
        finally:
            db.close()

    @strands_tool
    def get_low_stock() -> dict:
        """Get all products below their minimum stock level."""
        db = SessionLocal()
        try:
            return tools.get_low_stock(db, shop_id)
        finally:
            db.close()

    @strands_tool
    def add_stock(product_name: str, quantity: int, expiry_date: str = None, cost_price: float = None,
                  unit: str = "packet", confirm_new: bool = False) -> dict:
        """Add stock for a product. product_name MUST be in English letters."""
        db = SessionLocal()
        try:
            return tools.add_stock(db, product_name, quantity, shop_id, expiry_date, cost_price, unit, confirm_new)
        except Exception:
            import traceback
            traceback.print_exc()
            raise
        finally:
            db.close()

    @strands_tool
    def record_sale(product_name: str, quantity: int, bill_id: str = None) -> dict:
        """Record a sale, reducing stock."""
        db = SessionLocal()
        try:
            return tools.record_sale(db, product_name, quantity, shop_id, bill_id)
        finally:
            db.close()

    @strands_tool
    def delete_item(product_name: str, confirm: bool = False) -> dict:
        """Delete a product entirely, including its stock and sale history."""
        db = SessionLocal()
        try:
            return tools.delete_product(db, product_name, shop_id, confirm)
        finally:
            db.close()

    @strands_tool
    def fix_product_name(product_name: str, new_name: str, confirm: bool = False) -> dict:
        """Rename a product, e.g. to fix a spelling mistake."""
        db = SessionLocal()
        try:
            return tools.rename_product(db, product_name, new_name, shop_id, confirm)
        finally:
            db.close()

    @strands_tool
    def order_from_wholesaler(product_name: str, quantity: int) -> dict:
        """Find the cheapest wholesaler with this product in stock and place an order with them.
        Use this when the owner wants to reorder/restock from a wholesaler, not for adding stock manually."""
        db = SessionLocal()
        try:
            return tools.place_wholesale_order(db, shop_id, product_name, quantity)
        finally:
            db.close()

    @strands_tool
    def find_wholesaler(product_name: str) -> dict:
        """Check which wholesaler has the best price for a product, without placing an order."""
        db = SessionLocal()
        try:
            result = tools.find_best_wholesaler(db, product_name)
            return result or {"found": False, "message": f"No wholesaler currently has '{product_name}'."}
        finally:
            db.close()

    @strands_tool
    def plan_restock(budget: float = None) -> dict:
        """Make a restock plan for everything below its minimum stock: how much to buy (based on recent sales),
        from which wholesaler (cheapest that can supply it), optionally within a rupee budget.
        This ONLY plans, it orders nothing. Read the plan to the owner briefly and ask if they approve."""
        db = SessionLocal()
        try:
            return restock.plan_restock(db, shop_id, budget)
        finally:
            db.close()

    @strands_tool
    def confirm_restock(plan_id: str) -> dict:
        """Place the orders from a restock plan. Call this ONLY after the owner clearly said yes to the plan
        you read out. Pass the plan_id from plan_restock."""
        db = SessionLocal()
        try:
            return restock.confirm_restock(db, shop_id, plan_id)
        finally:
            db.close()

    @strands_tool
    def morning_brief() -> dict:
        """A short summary of the day: low stock, items expiring soon, orders on the way, sales in the last 24 hours."""
        db = SessionLocal()
        try:
            return restock.morning_brief(db, shop_id)
        finally:
            db.close()

    return [list_all_items, check_item, get_expiring_items, get_low_stock, add_stock, record_sale,
            delete_item, fix_product_name, order_from_wholesaler, find_wholesaler,
            plan_restock, confirm_restock, morning_brief]


@app.websocket("/ws/voice")
async def voice_websocket(websocket: WebSocket, account_id: int = Query(...)):
    await websocket.accept()

    _vs = _load_settings_for(account_id)
    try:
        model = BedrockNovaSonicModel(voice=_vs["voice"]["voice"])
    except TypeError:
        model = BedrockNovaSonicModel()  # older library without the voice option
    agent = BidiAgent(
        model=model,
        tools=build_tools_for_shop(account_id),
        system_prompt=SYSTEM_PROMPT + _language_hint(_vs["voice"]["language"]),
    )

    ws_input = WebSocketInput(websocket)
    ws_output = WebSocketOutput(websocket, account_id)

    try:
        await agent.run(inputs=[ws_input], outputs=[ws_output])
    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"Voice session error: {e}")


# ---------- WHOLESALER APIs ----------

class WholesalerItemRequest(BaseModel):
    product_name: str
    price_per_unit: float
    available_quantity: int


@app.get("/wholesaler")
def wholesaler_page():
    return FileResponse("app/static/wholesaler.html")


@app.get("/wholesaler/items")
def get_wholesaler_items(wholesaler_id: int = Query(...), db: Session = Depends(get_db)):
    items = db.query(models.WholesalerItem).filter(models.WholesalerItem.wholesaler_id == wholesaler_id).all()
    return [{"id": i.id, "product_name": i.product_name, "price_per_unit": i.price_per_unit,
             "available_quantity": i.available_quantity,
             "updated_at": i.updated_at.isoformat() if i.updated_at else None} for i in items]


@app.post("/wholesaler/items")
def upsert_wholesaler_item(wholesaler_id: int, payload: WholesalerItemRequest, db: Session = Depends(get_db)):
    payload.product_name = payload.product_name.strip()
    if not payload.product_name:
        raise HTTPException(status_code=400, detail="Product name is required.")
    if payload.price_per_unit <= 0:
        raise HTTPException(status_code=400, detail="Price must be more than 0.")
    if payload.available_quantity < 0:
        raise HTTPException(status_code=400, detail="Quantity can't be negative.")
    item = db.query(models.WholesalerItem).filter(
        models.WholesalerItem.wholesaler_id == wholesaler_id,
        models.WholesalerItem.product_name.ilike(payload.product_name),
    ).first()
    if item:
        item.price_per_unit = payload.price_per_unit
        item.available_quantity = payload.available_quantity
        item.updated_at = datetime.utcnow()
    else:
        item = models.WholesalerItem(
            wholesaler_id=wholesaler_id, product_name=payload.product_name,
            price_per_unit=payload.price_per_unit, available_quantity=payload.available_quantity,
        )
        db.add(item)
    db.commit()
    return {"success": True}


@app.get("/wholesaler/orders")
def get_wholesaler_orders(wholesaler_id: int = Query(...), db: Session = Depends(get_db)):
    orders = db.query(models.WholesaleOrder).filter(models.WholesaleOrder.wholesaler_id == wholesaler_id).order_by(
        models.WholesaleOrder.created_at.desc()
    ).all()
    out = []
    for o in orders:
        retailer = db.get(models.Shop, o.retailer_id)
        driver = db.get(models.Driver, o.driver_id) if o.driver_id else None
        out.append({
            "id": o.id, "product_name": o.product_name, "quantity": o.quantity,
            "unit_price": o.unit_price, "total_price": o.total_price, "status": o.status,
            "retailer_name": retailer.name if retailer else None,
            "retailer_area": retailer.area if retailer else None,
            "driver_name": driver.name if driver else None,
            "created_at": o.created_at.isoformat() if o.created_at else None,
            "updated_at": o.updated_at.isoformat() if o.updated_at else None,
            "confirmed_at": o.confirmed_at.isoformat() if o.confirmed_at else None,
            "picked_up_at": o.picked_up_at.isoformat() if o.picked_up_at else None,
            "delivered_at": o.delivered_at.isoformat() if o.delivered_at else None,
        })
    return out


class UpdateOrderStatusRequest(BaseModel):
    status: str


@app.post("/wholesaler/orders/{order_id}/status")
def update_order_status(order_id: int, payload: UpdateOrderStatusRequest, wholesaler_id: Optional[int] = None,
                        db: Session = Depends(get_db)):
    order = db.get(models.WholesaleOrder, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    if wholesaler_id is not None and order.wholesaler_id != wholesaler_id:
        raise HTTPException(status_code=403, detail="This order belongs to another wholesaler.")
    if payload.status != "confirmed":
        raise HTTPException(status_code=400, detail="You can only confirm an order. The delivery partner handles the rest.")
    if order.status == "confirmed":
        return {"success": True}
    if order.status != "placed":
        raise HTTPException(status_code=400, detail="Only a new order can be confirmed.")
    order.status = payload.status
    order.updated_at = datetime.utcnow()
    if payload.status == "confirmed" and not order.confirmed_at:
        order.confirmed_at = order.updated_at
    db.commit()
    return {"success": True}


# ---------- DELIVERY APIs ----------

@app.get("/delivery")
def delivery_page():
    return FileResponse("app/static/delivery.html")


@app.get("/delivery/jobs")
def get_delivery_jobs(db: Session = Depends(get_db)):
    jobs = db.query(models.WholesaleOrder).filter(
        models.WholesaleOrder.status == "confirmed",
        models.WholesaleOrder.driver_id.is_(None),
    ).all()
    return [_delivery_row(db, o) for o in jobs]


@app.get("/delivery/my-jobs")
def get_my_delivery_jobs(driver_id: int = Query(...), db: Session = Depends(get_db)):
    jobs = db.query(models.WholesaleOrder).filter(models.WholesaleOrder.driver_id == driver_id).all()
    return [_delivery_row(db, o) for o in jobs]


@app.post("/delivery/jobs/{order_id}/accept")
def accept_delivery_job(order_id: int, driver_id: int, db: Session = Depends(get_db)):
    order = db.get(models.WholesaleOrder, order_id)
    if not order or order.driver_id is not None:
        raise HTTPException(status_code=400, detail="Job unavailable")
    order.driver_id = driver_id
    order.status = "picked_up"
    order.picked_up_at = datetime.utcnow()
    db.commit()
    return {"success": True}


@app.post("/delivery/jobs/{order_id}/deliver")
def mark_delivered(order_id: int, db: Session = Depends(get_db)):
    order = db.get(models.WholesaleOrder, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    order.status = "delivered"
    order.updated_at = datetime.utcnow()
    db.commit()
    return {"success": True}


OTP_SECRET = os.getenv("OTP_SECRET", "voicestock-change-this-secret")


def generate_order_otp(order_id: int, retailer_id: int, ts: datetime) -> str:
    """5-digit OTP tied to this exact order, retailer, and timestamp.
    Same code can never apply to a different order, even generated the same second."""
    msg = f"{order_id}:{retailer_id}:{ts.isoformat()}"
    digest = hmac.new(OTP_SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()
    num = int(digest, 16) % 100000
    return f"{num:05d}"


@app.post("/delivery/jobs/{order_id}/generate-otp")
def generate_otp(order_id: int, db: Session = Depends(get_db)):
    order = db.get(models.WholesaleOrder, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.status != "picked_up":
        raise HTTPException(status_code=400, detail="Order not out for delivery yet")
    ts = datetime.utcnow()
    order.otp = generate_order_otp(order.id, order.retailer_id, ts)
    order.otp_generated_at = ts
    db.commit()
    return {"success": True}


@app.get("/retailer/orders")
def get_retailer_orders(retailer_id: int = Query(...), db: Session = Depends(get_db)):
    """All of this retailer's wholesale orders (any status) - used for bell notifications. No OTP is exposed."""
    orders = db.query(models.WholesaleOrder).filter(
        models.WholesaleOrder.retailer_id == retailer_id
    ).order_by(models.WholesaleOrder.created_at.desc()).limit(50).all()
    out = []
    for o in orders:
        wholesaler = db.get(models.Wholesaler, o.wholesaler_id)
        driver = db.get(models.Driver, o.driver_id) if o.driver_id else None
        out.append({
            "order_id": o.id,
            "product_name": o.product_name,
            "quantity": o.quantity,
            "total_price": o.total_price,
            "status": o.status,
            "wholesaler_name": wholesaler.name if wholesaler else None,
            "driver_name": driver.name if driver else None,
            "placed_at": o.created_at.isoformat() if o.created_at else None,
            "confirmed_at": o.confirmed_at.isoformat() if o.confirmed_at else None,
            "picked_up_at": o.picked_up_at.isoformat() if o.picked_up_at else None,
            "delivered_at": o.delivered_at.isoformat() if o.delivered_at else None,
        })
    return out


@app.get("/retailer/pending-otp")
def get_pending_otp(retailer_id: int = Query(...), db: Session = Depends(get_db)):
    orders = db.query(models.WholesaleOrder).filter(
        models.WholesaleOrder.retailer_id == retailer_id,
        models.WholesaleOrder.status == "picked_up",
        models.WholesaleOrder.otp.isnot(None),
    ).all()
    return [{"id": o.id, "product_name": o.product_name, "quantity": o.quantity, "otp": o.otp} for o in orders]


class VerifyOtpRequest(BaseModel):
    otp: str


@app.post("/delivery/jobs/{order_id}/verify-otp")
def verify_otp(order_id: int, payload: VerifyOtpRequest, db: Session = Depends(get_db)):
    order = db.get(models.WholesaleOrder, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    if not order.otp or order.otp != payload.otp.strip():
        raise HTTPException(status_code=400, detail="Incorrect OTP")
    order.status = "delivered"
    order.updated_at = datetime.utcnow()
    order.delivered_at = order.updated_at
    tools.add_stock(db, order.product_name, order.quantity, order.retailer_id,
                     source="wholesaler", wholesale_order_id=order.id)
    order.otp = None
    db.commit()
    return {"success": True}


@app.get("/retailer/history")
def get_retailer_history(retailer_id: int = Query(...), db: Session = Depends(get_db)):
    # ---- Item Added: from Wholesaler (full timeline) ----
    wholesale_orders = db.query(models.WholesaleOrder).filter(
        models.WholesaleOrder.retailer_id == retailer_id,
        models.WholesaleOrder.status == "delivered",
    ).order_by(models.WholesaleOrder.delivered_at.desc()).all()

    item_added_wholesaler = []
    for o in wholesale_orders:
        wholesaler = db.get(models.Wholesaler, o.wholesaler_id)
        driver = db.get(models.Driver, o.driver_id) if o.driver_id else None
        item_added_wholesaler.append({
            "order_id": o.id,
            "product_name": o.product_name,
            "quantity": o.quantity,
            "total_price": o.total_price,
            "wholesaler_name": wholesaler.name if wholesaler else None,
            "driver_name": driver.name if driver else None,
            "placed_at": o.created_at.isoformat() if o.created_at else None,
            "confirmed_at": o.confirmed_at.isoformat() if o.confirmed_at else None,
            "picked_up_at": o.picked_up_at.isoformat() if o.picked_up_at else None,
            "delivered_at": o.delivered_at.isoformat() if o.delivered_at else None,
        })

    # ---- Item Added: from Own Inventory (manual voice add) ----
    manual_batches = db.query(models.Batch).join(
        models.Product, models.Batch.product_id == models.Product.id
    ).filter(
        models.Product.shop_id == retailer_id,
        models.Batch.source == "manual",
    ).order_by(models.Batch.purchase_date.desc()).all()

    item_added_manual = [{
        "product_name": b.product.name,
        "quantity": b.quantity,
        "cost_price": b.cost_price,
        "added_at": b.purchase_date.isoformat() if b.purchase_date else None,
    } for b in manual_batches]

    # ---- Item Sold ----
    sales_q = db.query(models.SaleTransaction, models.Product.name).join(
        models.Product, models.SaleTransaction.product_id == models.Product.id
    ).filter(models.Product.shop_id == retailer_id).order_by(models.SaleTransaction.sold_at.desc()).all()

    item_sold = [{
        "product_name": name,
        "quantity": s.quantity,
        "price_per_unit": s.price_per_unit,
        "revenue": (s.price_per_unit * s.quantity) if s.price_per_unit else None,
        "sold_at": s.sold_at.isoformat() if s.sold_at else None,
    } for s, name in sales_q]

    return {
        "item_added_wholesaler": item_added_wholesaler,
        "item_added_manual": item_added_manual,
        "item_sold": item_sold,
    }


# ======================= PROFILE (profile patch) =======================

FREE_VOICE_LIMIT = 1000

DEFAULT_SETTINGS = {
    "notifications": {"low_stock": True, "new_order": False, "delivery": True, "wholesaler": True,
                      "sound": True, "vibration": True},
    "voice": {"enabled": True, "voice": "matthew", "language": "auto"},
    "appearance": {"theme": "dark", "density": "comfortable", "accent": "blue"},
}
SETTING_CHOICES = {
    ("voice", "voice"): {"matthew", "tiffany", "amy"},
    ("voice", "language"): {"auto", "en", "hi"},
    ("appearance", "theme"): {"dark", "light"},
    ("appearance", "density"): {"comfortable", "compact"},
    ("appearance", "accent"): {"blue", "purple", "teal", "orange", "pink"},
}


def _merge_settings(current: dict, incoming: dict) -> dict:
    out = {g: dict(v) for g, v in DEFAULT_SETTINGS.items()}
    for src in (current or {}, incoming or {}):
        for group, defaults in DEFAULT_SETTINGS.items():
            given = src.get(group)
            if not isinstance(given, dict):
                continue
            for key, default in defaults.items():
                if key not in given:
                    continue
                val = given[key]
                if isinstance(default, bool):
                    if isinstance(val, bool):
                        out[group][key] = val
                else:
                    if isinstance(val, str) and val in SETTING_CHOICES.get((group, key), set()):
                        out[group][key] = val
    return out


def _get_profile(db: Session, shop_id: int) -> "ShopProfile":
    shop = db.get(models.Shop, shop_id)
    if not shop:
        raise HTTPException(status_code=404, detail="Account not found")
    prof = db.get(ShopProfile, shop_id)
    if not prof:
        prof = ShopProfile(shop_id=shop_id, plan="free", voice_used=0)
        db.add(prof)
        db.commit()
        db.refresh(prof)
    month = datetime.utcnow().strftime("%Y-%m")
    if prof.usage_month != month:
        prof.usage_month = month
        prof.voice_used = 0
        db.commit()
    return prof


def _settings_of(prof: "ShopProfile") -> dict:
    try:
        stored = _json.loads(prof.settings_json) if prof.settings_json else {}
    except Exception:
        stored = {}
    return _merge_settings(stored, {})


def _member_since(db: Session, shop_id: int, prof: "ShopProfile"):
    candidates = [prof.created_at]
    q = db.query(models.Batch.purchase_date).join(
        models.Product, models.Batch.product_id == models.Product.id
    ).filter(models.Product.shop_id == shop_id).order_by(models.Batch.purchase_date.asc()).first()
    if q and q[0]:
        candidates.append(q[0])
    q = db.query(models.WholesaleOrder.created_at).filter(
        models.WholesaleOrder.retailer_id == shop_id).order_by(models.WholesaleOrder.created_at.asc()).first()
    if q and q[0]:
        candidates.append(q[0])
    candidates = [c for c in candidates if c]
    return min(candidates).isoformat() if candidates else None


def _profile_payload(db: Session, shop_id: int) -> dict:
    shop = db.get(models.Shop, shop_id)
    prof = _get_profile(db, shop_id)
    pro = (prof.plan or "free") != "free"
    return {
        "id": shop.id,
        "name": shop.name,
        "area": shop.area,
        "email": prof.email or "",
        "phone": prof.phone or "",
        "business_name": prof.business_name or "",
        "address": prof.address or "",
        "gstin": prof.gstin or "",
        "settings": _settings_of(prof),
        "plan": prof.plan or "free",
        "usage": {"used": prof.voice_used or 0, "limit": None if pro else FREE_VOICE_LIMIT,
                  "month": prof.usage_month},
        "member_since": _member_since(db, shop_id, prof),
    }


def _load_settings_for(shop_id: int) -> dict:
    db = SessionLocal()
    try:
        prof = db.get(ShopProfile, shop_id)
        return _settings_of(prof) if prof else _merge_settings({}, {})
    except Exception:
        return _merge_settings({}, {})
    finally:
        db.close()


def _language_hint(lang: str) -> str:
    if lang == "en":
        return " Always reply in English."
    if lang == "hi":
        return " Always reply in Hindi or Hinglish, the way a shopkeeper in India would speak."
    return ""


def _log_voice(shop_id, role, text):
    if not shop_id or not text or not str(text).strip():
        return
    db = SessionLocal()
    try:
        db.add(VoiceLog(shop_id=shop_id, role=str(role), text=str(text)))
        if str(role) == "user":
            prof = _get_profile(db, shop_id)
            prof.voice_used = (prof.voice_used or 0) + 1
        db.commit()
    except Exception as e:
        print(f"voice log failed: {e}")
        db.rollback()
    finally:
        db.close()


class ProfileUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    business_name: Optional[str] = None
    address: Optional[str] = None
    gstin: Optional[str] = None


@app.get("/profile")
def get_profile(account_id: int = Query(...), db: Session = Depends(get_db)):
    return _profile_payload(db, account_id)


@app.put("/profile")
def update_profile(payload: ProfileUpdate, account_id: int = Query(...), db: Session = Depends(get_db)):
    shop = db.get(models.Shop, account_id)
    if not shop:
        raise HTTPException(status_code=404, detail="Account not found")
    prof = _get_profile(db, account_id)

    if payload.name is not None:
        name = payload.name.strip()
        if not (1 <= len(name) <= 40):
            raise HTTPException(status_code=400, detail="Name must be 1 to 40 characters.")
        clash = db.query(models.Shop).filter(models.Shop.name == name, models.Shop.id != account_id).first()
        if clash:
            raise HTTPException(status_code=400, detail="This name is already taken. Choose another.")
        shop.name = name

    if payload.email is not None:
        email = payload.email.strip()
        if email and not _re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
            raise HTTPException(status_code=400, detail="Enter a valid email address.")
        prof.email = email or None

    if payload.phone is not None:
        phone = _re.sub(r"[\s\-()]", "", payload.phone)
        if phone and not _re.match(r"^\+?\d{7,15}$", phone):
            raise HTTPException(status_code=400, detail="Enter a valid phone number (7 to 15 digits).")
        prof.phone = phone or None

    if payload.business_name is not None:
        v = payload.business_name.strip()
        if len(v) > 60:
            raise HTTPException(status_code=400, detail="Store name is too long (max 60).")
        prof.business_name = v or None

    if payload.address is not None:
        v = payload.address.strip()
        if len(v) > 200:
            raise HTTPException(status_code=400, detail="Address is too long (max 200).")
        prof.address = v or None

    if payload.gstin is not None:
        v = payload.gstin.strip().upper()
        if v and not _re.match(r"^\d{2}[A-Z]{5}\d{4}[A-Z][A-Z\d]Z[A-Z\d]$", v):
            raise HTTPException(status_code=400, detail="GSTIN must be 15 characters, like 22AAAAA0000A1Z5.")
        prof.gstin = v or None

    db.commit()
    return _profile_payload(db, account_id)


class SettingsUpdate(BaseModel):
    settings: dict


@app.put("/profile/settings")
def update_settings(payload: SettingsUpdate, account_id: int = Query(...), db: Session = Depends(get_db)):
    prof = _get_profile(db, account_id)
    merged = _merge_settings(_settings_of(prof), payload.settings)
    prof.settings_json = _json.dumps(merged)
    db.commit()
    return {"settings": merged}


class ChangePinRequest(BaseModel):
    current_pin: str
    new_pin: str


@app.post("/profile/change-pin")
def change_pin(payload: ChangePinRequest, account_id: int = Query(...), db: Session = Depends(get_db)):
    shop = db.get(models.Shop, account_id)
    if not shop:
        raise HTTPException(status_code=404, detail="Account not found")
    if shop.pin != payload.current_pin:
        raise HTTPException(status_code=401, detail="Current PIN is wrong.")
    if len(payload.new_pin) != 4 or not payload.new_pin.isdigit():
        raise HTTPException(status_code=400, detail="New PIN must be exactly 4 digits.")
    if payload.new_pin == payload.current_pin:
        raise HTTPException(status_code=400, detail="New PIN must be different from the current one.")
    shop.pin = payload.new_pin
    db.commit()
    return {"success": True}


@app.get("/profile/voice-history")
def voice_history(account_id: int = Query(...), limit: int = 60, db: Session = Depends(get_db)):
    limit = max(1, min(limit, 200))
    rows = db.query(VoiceLog).filter(VoiceLog.shop_id == account_id).order_by(VoiceLog.id.desc()).limit(limit).all()
    return [{"role": r.role, "text": r.text, "at": r.created_at.isoformat() if r.created_at else None}
            for r in reversed(rows)]


@app.delete("/profile/voice-history")
def clear_voice_history(account_id: int = Query(...), db: Session = Depends(get_db)):
    db.query(VoiceLog).filter(VoiceLog.shop_id == account_id).delete()
    db.commit()
    return {"success": True}


@app.post("/wholesaler/orders/{order_id}/cancel")
def wholesaler_cancel_order(order_id: int, wholesaler_id: int = Query(...), db: Session = Depends(get_db)):
    order = db.get(models.WholesaleOrder, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.wholesaler_id != wholesaler_id:
        raise HTTPException(status_code=403, detail="This order belongs to another wholesaler.")
    if order.status == "cancelled":
        return {"success": True}
    if order.status != "placed":
        raise HTTPException(status_code=400, detail="Only a new order can be cancelled. This one is already confirmed.")
    item = db.query(models.WholesalerItem).filter(
        models.WholesalerItem.wholesaler_id == wholesaler_id,
        models.WholesalerItem.product_name == order.product_name,
    ).first()
    if item:
        item.available_quantity = (item.available_quantity or 0) + order.quantity
        item.updated_at = datetime.utcnow()
    order.status = "cancelled"
    order.updated_at = datetime.utcnow()
    db.commit()
    return {"success": True}


@app.delete("/wholesaler/items/{item_id}")
def delete_wholesaler_item(item_id: int, wholesaler_id: int = Query(...), db: Session = Depends(get_db)):
    item = db.get(models.WholesalerItem, item_id)
    if not item or item.wholesaler_id != wholesaler_id:
        raise HTTPException(status_code=404, detail="Item not found")
    db.delete(item)
    db.commit()
    return {"success": True}


# ======================= LOCATION (location patch) =======================

def _party(db, role, pid):
    loc = db.get(PartyLocation, (role, pid))
    name = phone = area = address = None
    if role == "wholesaler":
        w = db.get(models.Wholesaler, pid)
        if w:
            name, phone, area = w.name, w.contact, w.area
    else:
        sh = db.get(models.Shop, pid)
        if sh:
            name, area = sh.name, sh.area
        prof = db.get(ShopProfile, pid) if "ShopProfile" in globals() else None
        if prof:
            phone, address = prof.phone, prof.address
    return {
        "id": pid, "name": name, "area": area,
        "phone": (loc.phone if loc and loc.phone else phone),
        "address": (loc.address if loc and loc.address else address),
        "lat": loc.lat if loc else None, "lng": loc.lng if loc else None,
    }


def _delivery_row(db, o):
    return {
        "id": o.id, "product_name": o.product_name, "quantity": o.quantity,
        "status": o.status, "wholesaler_id": o.wholesaler_id, "total_price": o.total_price,
        "wholesaler": _party(db, "wholesaler", o.wholesaler_id),
        "retailer": _party(db, "retailer", o.retailer_id),
    }


class LocationUpdate(BaseModel):
    address: Optional[str] = None
    phone: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None


def _role_ok(role):
    if role not in ("wholesaler", "retailer"):
        raise HTTPException(status_code=400, detail="role must be wholesaler or retailer")


@app.get("/location")
def get_location(role: str = Query(...), account_id: int = Query(...), db: Session = Depends(get_db)):
    _role_ok(role)
    return _party(db, role, account_id)


@app.put("/location")
def put_location(payload: LocationUpdate, role: str = Query(...), account_id: int = Query(...),
                 db: Session = Depends(get_db)):
    _role_ok(role)
    if (payload.lat is None) != (payload.lng is None):
        raise HTTPException(status_code=400, detail="Send both latitude and longitude.")
    if payload.lat is not None and not (-90 <= payload.lat <= 90 and -180 <= payload.lng <= 180):
        raise HTTPException(status_code=400, detail="Location is out of range.")
    phone = None
    if payload.phone is not None:
        phone = "".join(ch for ch in payload.phone if ch not in " -()")
        if phone and not (phone.lstrip("+").isdigit() and 7 <= len(phone.lstrip("+")) <= 15):
            raise HTTPException(status_code=400, detail="Enter a valid phone number (7 to 15 digits).")
    loc = db.get(PartyLocation, (role, account_id))
    if not loc:
        loc = PartyLocation(role=role, account_id=account_id)
        db.add(loc)
    if payload.address is not None:
        loc.address = payload.address.strip()[:200] or None
    if payload.phone is not None:
        loc.phone = phone or None
    if payload.lat is not None:
        loc.lat, loc.lng = payload.lat, payload.lng
    db.commit()
    return _party(db, role, account_id)


@app.get("/set-location")
def set_location_page():
    return FileResponse("app/static/set-location.html")

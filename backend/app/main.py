from fastapi import FastAPI, Depends, HTTPException, WebSocket, WebSocketDisconnect, UploadFile, File
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
import base64
import os
import shutil

from .database import Base, engine, get_db, SessionLocal
from . import models, tools
from .bedrock_agent import chat_with_agent

from strands.experimental.bidi.agent import BidiAgent
from strands.experimental.bidi.models import BedrockNovaSonicModel
from strands.experimental.bidi.types.media import AudioDelta
from strands.experimental.bidi.types.events import BidiAudioStreamEvent, BidiTranscriptStreamEvent
from strands import tool as strands_tool

Base.metadata.create_all(bind=engine)

app = FastAPI(title="VoiceStock API")

os.makedirs("app/static/images", exist_ok=True)
app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.get("/voice")
def voice_page():
    return FileResponse("app/static/voice.html")


@app.get("/")
def root():
    return {"message": "VoiceStock backend is running"}


@app.get("/products")
def list_products(db: Session = Depends(get_db)):
    return db.query(models.Product).all()


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
    return {"image_url": product.image_url}


@app.get("/check-item")
def api_check_item(product_name: str, db: Session = Depends(get_db)):
    return tools.check_item(db, product_name)


@app.get("/expiring-items")
def api_expiring_items(within_days: int = 7, db: Session = Depends(get_db)):
    return tools.get_expiring_items(db, within_days)


@app.get("/low-stock")
def api_low_stock(db: Session = Depends(get_db)):
    return tools.get_low_stock(db)


class AddStockRequest(BaseModel):
    product_name: str
    quantity: int
    expiry_date: Optional[str] = None
    cost_price: Optional[float] = None
    unit: Optional[str] = "packet"


@app.post("/add-stock")
def api_add_stock(payload: AddStockRequest, db: Session = Depends(get_db)):
    return tools.add_stock(
        db, payload.product_name, payload.quantity,
        payload.expiry_date, payload.cost_price, payload.unit
    )


class RecordSaleRequest(BaseModel):
    product_name: str
    quantity: int
    bill_id: Optional[str] = None


@app.post("/record-sale")
def api_record_sale(payload: RecordSaleRequest, db: Session = Depends(get_db)):
    result = tools.record_sale(db, payload.product_name, payload.quantity, payload.bill_id)
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["message"])
    return result


class ChatRequest(BaseModel):
    message: str


@app.post("/chat")
def api_chat(payload: ChatRequest, db: Session = Depends(get_db)):
    reply = chat_with_agent(db, payload.message)
    return {"reply": reply}


# ---------- VOICE AGENT TOOLS ----------

@strands_tool
def list_all_items() -> dict:
    """List ALL products in the shop with their current quantity. Use when the owner asks what items or stock they have, without naming a specific product."""
    db = SessionLocal()
    try:
        return tools.list_products(db)
    finally:
        db.close()


@strands_tool
def check_item(product_name: str) -> dict:
    """Check how much stock is left for one product. Pass the name as heard; spelling mistakes and odd pronunciation are matched automatically."""
    db = SessionLocal()
    try:
        return tools.check_item(db, product_name)
    finally:
        db.close()


@strands_tool
def get_expiring_items(within_days: int = 7) -> dict:
    """Get products expiring within a number of days."""
    db = SessionLocal()
    try:
        return tools.get_expiring_items(db, within_days)
    finally:
        db.close()


@strands_tool
def get_low_stock() -> dict:
    """Get all products below their minimum stock level."""
    db = SessionLocal()
    try:
        return tools.get_low_stock(db)
    finally:
        db.close()


@strands_tool
def add_stock(product_name: str, quantity: int, expiry_date: str = None, cost_price: float = None,
              unit: str = "packet", confirm_new: bool = False) -> dict:
    """Add stock for a product. product_name MUST be in English letters. Set confirm_new=True only when the owner confirmed it is a brand new product that is not the similar one suggested."""
    db = SessionLocal()
    try:
        return tools.add_stock(db, product_name, quantity, expiry_date, cost_price, unit, confirm_new)
    finally:
        db.close()


@strands_tool
def record_sale(product_name: str, quantity: int, bill_id: str = None) -> dict:
    """Record a sale, reducing stock. Use when a customer buys something."""
    db = SessionLocal()
    try:
        return tools.record_sale(db, product_name, quantity, bill_id)
    finally:
        db.close()


@strands_tool
def delete_item(product_name: str, confirm: bool = False) -> dict:
    """Delete a product entirely, including its stock and sale history. Use when the owner asks to remove/delete an item.
    Set confirm=True only after the owner has confirmed the exact product name back to you."""
    db = SessionLocal()
    try:
        return tools.delete_product(db, product_name, confirm)
    finally:
        db.close()


@strands_tool
def fix_product_name(product_name: str, new_name: str, confirm: bool = False) -> dict:
    """Rename a product, e.g. to fix a spelling mistake. product_name is the current (possibly misspelled) name,
    new_name is the corrected English name. Set confirm=True only after the owner has confirmed the exact product to rename."""
    db = SessionLocal()
    try:
        return tools.rename_product(db, product_name, new_name, confirm)
    finally:
        db.close()


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

    def __init__(self, websocket: WebSocket):
        self.websocket = websocket

    async def start(self, agent):
        pass

    async def stop(self):
        pass

    async def __call__(self, event):
        if isinstance(event, BidiAudioStreamEvent):
            audio_bytes = base64.b64decode(event.audio)
            await self.websocket.send_bytes(audio_bytes)
        elif isinstance(event, BidiTranscriptStreamEvent):
            await self.websocket.send_json({"type": "transcript", "text": event.delta, "role": event.role})


@app.websocket("/ws/voice")
async def voice_websocket(websocket: WebSocket):
    await websocket.accept()

    model = BedrockNovaSonicModel()
    agent = BidiAgent(
        model=model,
        tools=[list_all_items, check_item, get_expiring_items, get_low_stock, add_stock, record_sale,
               delete_item, fix_product_name],
        system_prompt=SYSTEM_PROMPT,
    )

    ws_input = WebSocketInput(websocket)
    ws_output = WebSocketOutput(websocket)

    try:
        await agent.run(inputs=[ws_input], outputs=[ws_output])
    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"Voice session error: {e}")
import asyncio
from strands.experimental.bidi.agent import BidiAgent
from strands.experimental.bidi.models import BedrockNovaSonicModel
from strands.experimental.bidi.io import ConsoleIO
from strands import tool

from .database import SessionLocal
from . import tools as inventory_tools


# ---------- WRAP OUR EXISTING TOOLS FOR THE VOICE AGENT ----------

@tool
def check_item(product_name: str) -> dict:
    """Check how much stock is left for a product by name."""
    db = SessionLocal()
    try:
        return inventory_tools.check_item(db, product_name)
    finally:
        db.close()


@tool
def get_expiring_items(within_days: int = 7) -> dict:
    """Get products expiring within a number of days."""
    db = SessionLocal()
    try:
        return inventory_tools.get_expiring_items(db, within_days)
    finally:
        db.close()


@tool
def get_low_stock() -> dict:
    """Get all products below their minimum stock level."""
    db = SessionLocal()
    try:
        return inventory_tools.get_low_stock(db)
    finally:
        db.close()


@tool
def add_stock(product_name: str, quantity: int, expiry_date: str = None, cost_price: float = None, unit: str = "packet") -> dict:
    """Add new stock for a product. Creates the product if it doesn't exist."""
    db = SessionLocal()
    try:
        return inventory_tools.add_stock(db, product_name, quantity, expiry_date, cost_price, unit)
    finally:
        db.close()


@tool
def record_sale(product_name: str, quantity: int, bill_id: str = None) -> dict:
    """Record a sale, reducing stock. Use when a customer buys something."""
    db = SessionLocal()
    try:
        return inventory_tools.record_sale(db, product_name, quantity, bill_id)
    finally:
        db.close()


# ---------- RUN THE VOICE AGENT (TEXT MODE FOR NOW) ----------

async def main():
    model = BedrockNovaSonicModel()

    agent = BidiAgent(
        model=model,
        tools=[check_item, get_expiring_items, get_low_stock, add_stock, record_sale],
        system_prompt=(
            "You are VoiceStock, a helpful voice assistant for a small shop owner. "
            "You help check inventory, expiring items, low stock, add new stock, and record sales. "
            "Always use the tools provided to answer questions about stock. "
            "Keep your replies short, natural, and conversational."
        ),
    )

    text_io = ConsoleIO()

    print("VoiceStock is ready. Type your message below. Press Ctrl+C to stop.\n")

    await agent.run(
        inputs=[text_io.input()],
        outputs=[text_io.output()],
    )


if __name__ == "__main__":
    asyncio.run(main())
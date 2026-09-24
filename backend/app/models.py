from sqlalchemy import (
    Column, Integer, String, Float, DateTime, ForeignKey, Boolean, Enum
)
from sqlalchemy.orm import relationship
from datetime import datetime
import enum
from .database import Base


# ---------- ENUMS ----------

class OrderStatus(str, enum.Enum):
    placed = "placed"
    confirmed = "confirmed"
    delivered = "delivered"


class DeliveryStatus(str, enum.Enum):
    pending = "pending"
    assigned = "assigned"
    picked_up = "picked_up"
    delivered = "delivered"


class DriverStatus(str, enum.Enum):
    available = "available"
    busy = "busy"


# ---------- SHOP SIDE ----------

class Product(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, index=True)
    category = Column(String, nullable=True)
    unit = Column(String, default="piece")
    min_stock_level = Column(Integer, default=5)
    price_per_unit = Column(Float, nullable=True)

    batches = relationship("Batch", back_populates="product")


class Batch(Base):
    """A specific incoming lot of a product, with its own expiry date."""
    __tablename__ = "batches"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    quantity = Column(Integer, nullable=False)
    purchase_date = Column(DateTime, default=datetime.utcnow)
    expiry_date = Column(DateTime, nullable=True)
    cost_price = Column(Float, nullable=True)

    product = relationship("Product", back_populates="batches")


class SaleTransaction(Base):
    """A sale recorded from the billing system."""
    __tablename__ = "sales"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    quantity = Column(Integer, nullable=False)
    bill_id = Column(String, unique=True, nullable=True)  # avoid duplicate billing
    sold_at = Column(DateTime, default=datetime.utcnow)


# ---------- WHOLESALER SIDE ----------

class Wholesaler(Base):
    __tablename__ = "wholesalers"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    rating = Column(Float, default=4.0)
    delivery_time_days = Column(Float, default=1.0)
    contact = Column(String, nullable=True)

    inventory = relationship("WholesalerInventory", back_populates="wholesaler")


class WholesalerInventory(Base):
    __tablename__ = "wholesaler_inventory"

    id = Column(Integer, primary_key=True, index=True)
    wholesaler_id = Column(Integer, ForeignKey("wholesalers.id"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    price_per_unit = Column(Float, nullable=False)
    available_quantity = Column(Integer, default=0)
    batch_expiry_date = Column(DateTime, nullable=True)
    last_updated = Column(DateTime, default=datetime.utcnow)

    wholesaler = relationship("Wholesaler", back_populates="inventory")


# ---------- ORDERS & DELIVERY ----------

class Order(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    wholesaler_id = Column(Integer, ForeignKey("wholesalers.id"), nullable=False)
    quantity = Column(Integer, nullable=False)
    price = Column(Float, nullable=False)
    status = Column(Enum(OrderStatus), default=OrderStatus.placed)
    placed_at = Column(DateTime, default=datetime.utcnow)


class Driver(Base):
    __tablename__ = "drivers"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    vehicle_type = Column(String, nullable=True)
    phone = Column(String, nullable=True)
    rating = Column(Float, default=4.0)
    status = Column(Enum(DriverStatus), default=DriverStatus.available)
    current_lat = Column(Float, nullable=True)
    current_lng = Column(Float, nullable=True)


class Delivery(Base):
    __tablename__ = "deliveries"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    driver_id = Column(Integer, ForeignKey("drivers.id"), nullable=True)
    pickup_lat = Column(Float, nullable=True)
    pickup_lng = Column(Float, nullable=True)
    drop_lat = Column(Float, nullable=True)
    drop_lng = Column(Float, nullable=True)
    status = Column(Enum(DeliveryStatus), default=DeliveryStatus.pending)
    assigned_at = Column(DateTime, nullable=True)
    delivered_at = Column(DateTime, nullable=True)


class Payment(Base):
    """Mock payment — always simulated success for the demo."""
    __tablename__ = "payments"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    amount = Column(Float, nullable=False)
    status = Column(String, default="success")
    paid_at = Column(DateTime, default=datetime.utcnow)
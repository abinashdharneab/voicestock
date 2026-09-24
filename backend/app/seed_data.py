from datetime import datetime, timedelta
from .database import SessionLocal, engine, Base
from . import models

Base.metadata.create_all(bind=engine)

db = SessionLocal()

# ---------- PRODUCTS ----------
parle_g = models.Product(name="Parle-G", category="Biscuits", unit="packet", min_stock_level=10, price_per_unit=10)
maggi = models.Product(name="Maggi", category="Noodles", unit="packet", min_stock_level=15, price_per_unit=14)
surf = models.Product(name="Surf Excel", category="Detergent", unit="packet", min_stock_level=5, price_per_unit=45)

db.add_all([parle_g, maggi, surf])
db.commit()
db.refresh(parle_g)
db.refresh(maggi)
db.refresh(surf)

# ---------- BATCHES (stock with expiry) ----------
batches = [
    models.Batch(product_id=parle_g.id, quantity=50, expiry_date=datetime.utcnow() + timedelta(days=5), cost_price=8),
    models.Batch(product_id=maggi.id, quantity=8, expiry_date=datetime.utcnow() + timedelta(days=60), cost_price=12),
    models.Batch(product_id=surf.id, quantity=20, expiry_date=datetime.utcnow() + timedelta(days=200), cost_price=38),
]
db.add_all(batches)
db.commit()

# ---------- WHOLESALERS ----------
w1 = models.Wholesaler(name="ABC Wholesalers", rating=4.5, delivery_time_days=1, contact="9876500001")
w2 = models.Wholesaler(name="City Supply Co.", rating=4.0, delivery_time_days=2, contact="9876500002")
w3 = models.Wholesaler(name="Quick Mart Distributors", rating=3.8, delivery_time_days=0.5, contact="9876500003")

db.add_all([w1, w2, w3])
db.commit()
db.refresh(w1)
db.refresh(w2)
db.refresh(w3)

# ---------- WHOLESALER INVENTORY ----------
wholesaler_inventory = [
    models.WholesalerInventory(wholesaler_id=w1.id, product_id=maggi.id, price_per_unit=13, available_quantity=100, batch_expiry_date=datetime.utcnow() + timedelta(days=90)),
    models.WholesalerInventory(wholesaler_id=w2.id, product_id=maggi.id, price_per_unit=12.5, available_quantity=60, batch_expiry_date=datetime.utcnow() + timedelta(days=45)),
    models.WholesalerInventory(wholesaler_id=w3.id, product_id=maggi.id, price_per_unit=14, available_quantity=200, batch_expiry_date=datetime.utcnow() + timedelta(days=120)),
]
db.add_all(wholesaler_inventory)
db.commit()

# ---------- DRIVERS ----------
drivers = [
    models.Driver(name="Ravi Kumar", vehicle_type="bike", phone="9123456780", rating=4.6, current_lat=26.1806, current_lng=91.7539),
    models.Driver(name="Suresh Das", vehicle_type="van", phone="9123456781", rating=4.2, current_lat=26.1500, current_lng=91.7400),
]
db.add_all(drivers)
db.commit()

db.close()

print("Seed data inserted successfully!")
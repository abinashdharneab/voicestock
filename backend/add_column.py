from sqlalchemy import text
from app.database import engine

with engine.begin() as c:
    c.execute(text("ALTER TABLE products ADD COLUMN IF NOT EXISTS image_url VARCHAR"))
print("done")
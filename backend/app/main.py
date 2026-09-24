from fastapi import FastAPI, Depends
from sqlalchemy.orm import Session
from .database import Base, engine, get_db
from . import models

Base.metadata.create_all(bind=engine)

app = FastAPI(title="VoiceStock API")


@app.get("/")
def root():
    return {"message": "VoiceStock backend is running"}


@app.get("/products")
def list_products(db: Session = Depends(get_db)):
    return db.query(models.Product).all()
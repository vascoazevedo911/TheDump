from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy import create_engine, Column, Integer, String
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
import os

# --- Configuração da Base de Dados ---
# Em produção, isto viria de uma variável de ambiente
DATABASE_URL = "postgresql://user:password@localhost:5432/mini_db"

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# --- Modelo da Tabela ---
class Message(Base):
    __tablename__ = "messages"
    id = Column(Integer, primary_key=True, index=True)
    text = Column(String)

# Cria a tabela automaticamente ao iniciar
Base.metadata.create_all(bind=engine)

# --- Aplicação FastAPI ---
app = FastAPI()

# Dependência para obter a sessão da BD
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# --- Endpoints ---

@app.get("/")
def read_root():
    return {"status": "Web Service Online", "docs": "/docs"}

@app.post("/messages/")
def create_message(text: str, db: Session = Depends(get_db)):
    new_msg = Message(text=text)
    db.add(new_msg)
    db.commit()
    db.refresh(new_msg)
    return new_msg

@app.get("/messages/")
def list_messages(db: Session = Depends(get_db)):
    return db.query(Message).all()
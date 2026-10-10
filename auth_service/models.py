from datetime import datetime
from sqlalchemy import Boolean, Column, DateTime, Integer, String
from database import Base

class Usuario(Base):
    __tablename__ = "usuarios"

    id = Column(Integer, primary_key=True, index=True)
    nome = Column(String(100), nullable=False)
    email = Column(String(150), unique=True, index=True, nullable=False)
    senha_hash = Column(String(255), nullable=False)
    papel = Column(String(50), default="usuario")
    
    # Campos para o Plano Premium (Stripe)
    is_premium = Column(Boolean, default=False, nullable=False)
    stripe_customer_id = Column(String(255), nullable=True)

class RecuperacaoSenha(Base):
    __tablename__ = "recuperacoes_senha"

    id = Column(Integer, primary_key=True, index=True)
    usuario_id = Column(Integer, nullable=False)
    token = Column(String(255), unique=True, index=True, nullable=False)
    expira_em = Column(DateTime, nullable=False)
    usado = Column(Boolean, default=False)
    criado_em = Column(DateTime, default=datetime.utcnow)
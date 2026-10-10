from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text
from sqlalchemy.sql import func
from database import Base
from sqlalchemy import Column, Integer, String, Boolean

class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    # ... outros campos existentes ...
    is_premium = Column(Boolean, default=False, nullable=False) #
    stripe_customer_id = Column(String(255), nullable=True) # opcional, para rastreio

class Favorito(Base):
    __tablename__ = "favoritos"

    id = Column(Integer, primary_key=True, index=True)
    usuario_id = Column(Integer, nullable=False)
    tmdb_movie_id = Column(Integer, nullable=False)
    titulo = Column(String(255), nullable=False)
    poster_path = Column(String(255), nullable=True)
    criado_em = Column(DateTime(timezone=True), server_default=func.now())

class Comentario(Base):
    __tablename__ = "comentarios"

    id = Column(Integer, primary_key=True, index=True)
    usuario_id = Column(Integer, nullable=False)
    tmdb_movie_id = Column(Integer, nullable=False)
    texto = Column(Text, nullable=False)
    criado_em = Column(DateTime(timezone=True), server_default=func.now())

class Perfil(Base):
    __tablename__ = "perfis"

    id = Column(Integer, primary_key=True, index=True)
    usuario_id = Column(Integer, unique=True, nullable=False, index=True)
    bio = Column(Text, nullable=True, default="")
    foto_key = Column(String(255), nullable=True, default=None)
    atualizado_em = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, Float, DateTime, Boolean
from sqlalchemy.sql import func
from database import Base

class User(Base):
    __tablename__ = "users"
    __table_args__ = {'extend_existing': True}
    
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    is_premium = Column(Boolean, default=False, nullable=False)
    stripe_customer_id = Column(String(255), nullable=True)

class Favorito(Base):
    __tablename__ = "favoritos"
    __table_args__ = {'extend_existing': True}

    id = Column(Integer, primary_key=True, index=True)
    usuario_id = Column(Integer, nullable=False)
    tmdb_movie_id = Column(Integer, nullable=False)
    titulo = Column(String(255), nullable=False)
    poster_path = Column(String(255), nullable=True)
    criado_em = Column(DateTime(timezone=True), server_default=func.now())

class Comentario(Base):
    __tablename__ = "comentarios"
    __table_args__ = {'extend_existing': True}

    id = Column(Integer, primary_key=True, index=True)
    usuario_id = Column(Integer, nullable=False)
    tmdb_movie_id = Column(Integer, nullable=False)
    texto = Column(Text, nullable=False)
    criado_em = Column(DateTime(timezone=True), server_default=func.now())

class Perfil(Base):
    __tablename__ = "perfis"
    __table_args__ = {'extend_existing': True}

    id = Column(Integer, primary_key=True, index=True)
    usuario_id = Column(Integer, unique=True, nullable=False, index=True)
    bio = Column(Text, nullable=True, default="")
    foto_key = Column(String(255), nullable=True, default=None)
    atualizado_em = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Produto(Base):
    __tablename__ = "itens"
    __table_args__ = {'extend_existing': True}

    id = Column(Integer, primary_key=True, index=True)
    titulo = Column(String(150), nullable=False)
    descricao = Column(Text, nullable=True)
    categoria_id = Column(Integer, nullable=True, default=1)
    condicao = Column(String(50), nullable=True)
    modalidade = Column(String(50), nullable=True)
    preco = Column(Float, default=0.0)
    usuario_id = Column(Integer, nullable=True, default=1)
    foto_key = Column(String(255), nullable=True)
    imagem_url = Column(String(255), nullable=True)
    status = Column(String(50), default="ativo")
    criado_em = Column(DateTime, default=datetime.utcnow)

    def to_dict(self):
        url = self.imagem_url or (f"/api/fotos/{self.foto_key}" if self.foto_key else None)
        return {
            "id": self.id,
            "titulo": self.titulo,
            "descricao": self.descricao,
            "categoria_id": self.categoria_id,
            "condicao": self.condicao,
            "modalidade": self.modalidade,
            "preco": float(self.preco) if self.preco is not None else 0.0,
            "usuario_id": self.usuario_id,
            "foto_key": self.foto_key,
            "imagem_url": url,
            "foto_url": url,
            "status": self.status,
            "criado_em": self.criado_em.isoformat() if self.criado_em else None,
        }

Item = Produto

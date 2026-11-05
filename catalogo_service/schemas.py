from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime

class ItemBase(BaseModel):
    titulo: str = Field(..., max_length=150)
    descricao: str
    categoria_id: int
    condicao: str = Field(..., description="novo, pouco_usado, marcas_de_uso")
    modalidade: str = Field(..., description="venda, troca, emprestimo")
    preco: Optional[float] = 0.0

class ItemCreate(ItemBase):
    pass

class ItemUpdate(BaseModel):
    titulo: Optional[str] = None
    descricao: Optional[str] = None
    categoria_id: Optional[int] = None
    condicao: Optional[str] = None
    modalidade: Optional[str] = None
    preco: Optional[float] = None
    status: Optional[str] = None

class ItemResponse(ItemBase):
    id: int
    usuario_id: int
    foto_key: Optional[str] = None
    status: str
    criado_em: datetime

    class Config:
        from_attributes = True
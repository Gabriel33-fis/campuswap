from datetime import datetime
from typing import Optional
from pydantic import BaseModel, EmailStr


class UsuarioCriar(BaseModel):
    nome: str
    email: EmailStr
    senha: str
    papel: Optional[str] = "usuario"


class LoginRequest(BaseModel):
    email: EmailStr
    senha: str


class EsqueciSenhaRequest(BaseModel):
    email: EmailStr


class RedefinirSenhaRequest(BaseModel):
    token: str
    nova_senha: str


# Schema de retorno de dados do utilizador com suporte a Premium
class UsuarioResponse(BaseModel):
    id: int
    nome: str
    email: EmailStr
    papel: str
    is_premium: bool  # Informa se o utilizador possui o plano ativo[cite: 6]
    stripe_customer_id: Optional[str] = None

    class Config:
        from_attributes = True  # Compatível com Pydantic v2 (se usares v1, usa: orm_mode = True)


# Schema de retorno para a rota de checkout
class CheckoutResponse(BaseModel):
    url: str  # URL de redirecionamento gerada pelo Stripe[cite: 5, 6]
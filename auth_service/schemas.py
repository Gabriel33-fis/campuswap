from datetime import datetime
from typing import Optional, Literal
from pydantic import BaseModel, EmailStr, Field

# 3 papéis específicos do CampuSwap
PapeisCampuSwap = Literal["aluno", "moderador", "admin"]

class UsuarioCriar(BaseModel):
    nome: str
    email: EmailStr
    senha: str
    papel: Optional[PapeisCampuSwap] = "aluno"


class LoginRequest(BaseModel):
    email: EmailStr
    senha: str


class EsqueciSenhaRequest(BaseModel):
    email: EmailStr


class RedefinirSenhaRequest(BaseModel):
    token: str
    nova_senha: str


class UsuarioResponse(BaseModel):
    id: int
    nome: str
    email: EmailStr
    papel: str
    is_premium: bool
    stripe_customer_id: Optional[str] = None

    class Config:
        from_attributes = True


class CheckoutResponse(BaseModel):
    url: str
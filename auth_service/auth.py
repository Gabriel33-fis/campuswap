import os
import bcrypt
import jwt
from datetime import datetime, timedelta
import secrets
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from database import get_db
import models

JWT_SECRET = os.getenv("JWT_SECRET", "default_secret")
JWT_ALGORITHM = "HS256"
JWT_EXPIRATION_HOURS = 24

security = HTTPBearer()

def gerar_hash_senha(senha: str) -> str:
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(senha.encode('utf-8'), salt).decode('utf-8')

def verificar_senha(senha: str, hash_senha: str) -> bool:
    return bcrypt.checkpw(senha.encode('utf-8'), hash_senha.encode('utf-8'))

def gerar_token_recuperacao() -> str:
    """Gera um token seguro e aleatório para recuperação de senha."""
    return secrets.token_urlsafe(32)

def criar_access_token(usuario_id: int, nome: str, email: str, papel: str, is_premium: bool = False) -> str:
    expiracao = datetime.utcnow() + timedelta(hours=JWT_EXPIRATION_HOURS)
    payload = {
        "usuario_id": usuario_id,
        "nome": nome,
        "email": email,
        "papel": papel,
        "is_premium": is_premium,
        "exp": expiracao
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

def decodificar_token(token: str) -> dict:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError:
        return None

def obter_usuario_atual(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
) -> models.Usuario:
    """Dependência para autenticar a requisição via Header Authorization: Bearer <token>."""
    token = credentials.credentials
    payload = decodificar_token(token)

    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido ou expirado",
            headers={"WWW-Authenticate": "Bearer"},
        )

    usuario_id = payload.get("usuario_id")
    if not usuario_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token sem identificação de usuário",
            headers={"WWW-Authenticate": "Bearer"},
        )

    usuario = db.query(models.Usuario).filter(models.Usuario.id == usuario_id).first()
    if not usuario:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Usuário não encontrado"
        )

    return usuario
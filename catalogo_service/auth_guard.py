import os
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

JWT_SECRET = os.getenv("JWT_SECRET", "default_secret")
JWT_ALGORITHM = "HS256"

# auto_error=False mantém as mensagens de erro 401 personalizadas
bearer_scheme = HTTPBearer(auto_error=False, description="Cole apenas o access_token retornado pelo login")


def obter_usuario_atual(credenciais: HTTPAuthorizationCredentials = Depends(bearer_scheme)) -> dict:
    """Extrai e valida o payload JWT do cabeçalho de autorização."""
    if not credenciais or not credenciais.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token de autenticação ausente ou inválido"
        )
    token = credenciais.credentials
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expirado")
    except jwt.PyJWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token inválido")


def verificar_premium(usuario: dict = Depends(obter_usuario_atual)) -> dict:
    """Dependência para endpoints que exigem obrigatoriamente plano premium."""
    if not usuario.get("is_premium", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acesso exclusivo para assinantes do Plano Premium."
        )
    return usuario
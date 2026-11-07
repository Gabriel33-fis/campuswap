import os
import jwt
from typing import List, Callable
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

JWT_SECRET = os.getenv("JWT_SECRET", "chave_super_secreta_jwt_padrao")
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


def verificar_papeis(papeis_permitidos: List[str]) -> Callable:
    """
    Fábrica de dependências para controle de acesso baseado em papéis (RBAC).
    Retorna 403 Forbidden caso o papel do usuário não esteja na lista permitida.
    Atende ao Requisito 5: 'admin', 'moderador', 'aluno'.
    """
    def validador(usuario: dict = Depends(obter_usuario_atual)) -> dict:
        papel = usuario.get("papel", "aluno")
        if papel not in papeis_permitidos:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Acesso negado: o papel '{papel}' não possui permissão para esta operação."
            )
        return usuario
    return validador


def verificar_premium(usuario: dict = Depends(obter_usuario_atual)) -> dict:
    """Dependência para endpoints que exigem obrigatoriamente plano premium."""
    if not usuario.get("is_premium", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acesso exclusivo para assinantes do Plano Premium."
        )
    return usuario


# Aliases para manter compatibilidade com importações legadas
get_current_user = obter_usuario_atual
exigir_papeis = verificar_papeis

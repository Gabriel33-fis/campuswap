import os
import json
from typing import List, Literal, Optional

import redis
import httpx
from pydantic import BaseModel
from fastapi import FastAPI, Depends, HTTPException, status, UploadFile, File, Request, Response
from fastapi.responses import FileResponse, StreamingResponse

from schemas import ItemCreate, ItemUpdate, ItemResponse
from auth_guard import get_current_user

# Se o seu auth_guard.py já define verificar_papeis, ele é usado. Caso contrário, esta versão entra no lugar.
try:
    from auth_guard import verificar_papeis
except ImportError:
    def verificar_papeis(papeis_permitidos: List[str]):
        def dependencia(usuario: dict = Depends(get_current_user)):
            if usuario.get("papel", "aluno") not in papeis_permitidos:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Acesso proibido: seu perfil não tem permissão para esta ação.",
                )
            return usuario
        return dependencia
from database import get_db_connection
from storage import upload_imagem_item, baixar_imagem

# 1. Instância do FastAPI
app = FastAPI(title="CampuSwap - Catálogo de Materiais")

# 2. Configurações de serviços
AUTH_SERVICE_URL = os.getenv("AUTH_SERVICE_URL", "http://auth_service:5000")

# Papéis que podem editar/remover anúncios de outros usuários.
# Para permitir só administradores, use {"admin"}.
PAPEIS_PRIVILEGIADOS = {"admin", "moderador"}
PAPEIS_MODERACAO = ["moderador", "admin"]

# Campos que o PUT pode alterar (impede mudar usuario_id, id, criado_em etc.)
CAMPOS_EDITAVEIS = {"titulo", "descricao", "condicao", "modalidade", "preco",
                    "status", "categoria_id", "imagem_url"}


def migrar_banco():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        for ddl in (
            "ALTER TABLE itens_marketplace ADD COLUMN condicao VARCHAR(50) DEFAULT 'semi-novo'",
            "ALTER TABLE itens_marketplace ADD COLUMN imagem_url VARCHAR(500) NULL",
            "ALTER TABLE itens_marketplace ADD COLUMN foto_key VARCHAR(500) NULL",
        ):
            try:
                cursor.execute(ddl)
                conn.commit()
            except Exception:
                pass  # Coluna já existe
        conn.close()
    except Exception as e:
        print(f"Erro na migração do banco: {e}")


@app.on_event("startup")
def startup_event():
    migrar_banco()


# 3. Rotas essenciais
@app.get("/health")
def health():
    return {"status": "healthy"}


@app.get("/")
def home():
    index_path = os.path.join(os.path.dirname(__file__), "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"status": "CampuSwap API online", "docs": "/docs"}


# 4. Proxy de Autenticação
@app.post("/api/register")
@app.post("/api/usuarios")
async def proxy_register(request: Request):
    body = await request.json()
    async with httpx.AsyncClient() as client:
        try:
            res = await client.post(f"{AUTH_SERVICE_URL}/register", json=body, timeout=10.0)
            return Response(content=res.content, status_code=res.status_code, media_type="application/json")
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Erro ao conectar ao auth_service: {str(e)}")


@app.post("/api/login")
async def proxy_login(request: Request):
    body = await request.json()
    async with httpx.AsyncClient() as client:
        try:
            res = await client.post(f"{AUTH_SERVICE_URL}/login", json=body, timeout=10.0)
            return Response(content=res.content, status_code=res.status_code, media_type="application/json")
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Erro ao conectar ao auth_service: {str(e)}")


@app.post("/api/checkout")
async def proxy_checkout(request: Request):
    auth_header = request.headers.get("Authorization")
    headers = {"Authorization": auth_header} if auth_header else {}
    async with httpx.AsyncClient() as client:
        try:
            res = await client.post(f"{AUTH_SERVICE_URL}/checkout", headers=headers, timeout=15.0)
            return Response(content=res.content, status_code=res.status_code, media_type="application/json")
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Erro ao conectar com auth_service: {str(e)}")


# Conexão com o Redis para eventos de auditoria e negócio
REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)


def emitir_evento_log(acao: str, usuario_id, detalhes: dict):
    payload = {"acao": acao, "usuario_id": usuario_id, "detalhes": detalhes}
    try:
        redis_client.rpush("eventos_negocio", json.dumps(payload))
    except Exception as e:
        print(f"Erro ao emitir log no Redis: {e}")


# -------------------------------------------------------------
# Identidade e autorização (Requisito 6: isolamento por usuário)
# -------------------------------------------------------------

def obter_user_id(current_user: dict):
    """Lê o id do usuário no JWT, aceitando usuario_id, id ou sub."""
    user_id = current_user.get("usuario_id") or current_user.get("id") or current_user.get("sub")
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Identificador de usuário não encontrado no token.",
        )
    return user_id


def exigir_dono_ou_admin(item: dict, current_user: dict, verbo: str, evento: str):
    """Responde 403 se o item pertence a outro usuário e quem pede não é admin."""
    user_id = obter_user_id(current_user)
    papel = current_user.get("papel")
    # str() nos dois lados: o JWT pode trazer o id como texto e o banco como inteiro.
    if str(item["usuario_id"]) != str(user_id) and papel not in PAPEIS_PRIVILEGIADOS:
        emitir_evento_log(evento, user_id, {"item_alvo": item["id"]})
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Acesso não autorizado: não pode {verbo} anúncios de outros utilizadores.",
        )
    return user_id


def buscar_item(cursor, item_id: int) -> dict:
    cursor.execute("SELECT * FROM itens_marketplace WHERE id = %s", (item_id,))
    item = cursor.fetchone()
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item não encontrado.")
    return item


# -------------------------------------------------------------
# Endpoints Públicos (Vitrine e Consulta)
# -------------------------------------------------------------

@app.get("/api/itens", response_model=List[ItemResponse])
def listar_itens(categoria_id: Optional[int] = None, modalidade: Optional[str] = None):
    conn = get_db_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        query = "SELECT * FROM itens_marketplace WHERE status = 'disponivel'"
        params = []
        if categoria_id:
            query += " AND categoria_id = %s"
            params.append(categoria_id)
        if modalidade:
            query += " AND modalidade = %s"
            params.append(modalidade)
        query += " ORDER BY criado_em DESC"
        cursor.execute(query, tuple(params))
        return cursor.fetchall()
    finally:
        conn.close()


@app.get("/api/itens/{item_id}", response_model=ItemResponse)
def obter_item(item_id: int):
    conn = get_db_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        return buscar_item(cursor, item_id)
    finally:
        conn.close()


# -------------------------------------------------------------
# Endpoints Privados (CRUD com isolamento por usuário)
# -------------------------------------------------------------

@app.get("/api/meus-itens", response_model=List[ItemResponse])
def listar_meus_itens(current_user: dict = Depends(get_current_user)):
    user_id = obter_user_id(current_user)
    conn = get_db_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT * FROM itens_marketplace WHERE usuario_id = %s ORDER BY criado_em DESC",
            (user_id,),
        )
        return cursor.fetchall()
    finally:
        conn.close()


@app.post("/api/itens", response_model=ItemResponse, status_code=status.HTTP_201_CREATED)
def criar_item(item_in: ItemCreate, current_user: dict = Depends(get_current_user)):
    user_id = obter_user_id(current_user)
    conn = get_db_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            INSERT INTO itens_marketplace
            (usuario_id, categoria_id, titulo, descricao, condicao, modalidade, preco, imagem_url, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'disponivel')
            """,
            (
                user_id,
                item_in.categoria_id,
                item_in.titulo,
                item_in.descricao,
                item_in.condicao,
                item_in.modalidade,
                item_in.preco,
                getattr(item_in, "imagem_url", None),
            ),
        )
        conn.commit()

        novo_id = None
        if hasattr(cursor, "lastrowid") and cursor.lastrowid:
            novo_id = cursor.lastrowid
        elif hasattr(cursor, "cursor") and hasattr(cursor.cursor, "lastrowid"):
            novo_id = cursor.cursor.lastrowid
        else:
            cursor.execute("SELECT last_insert_rowid() AS id")
            row_id = cursor.fetchone()
            novo_id = row_id["id"] if isinstance(row_id, dict) else row_id[0]

        novo_item = buscar_item(cursor, novo_id)
    finally:
        conn.close()

    emitir_evento_log("item_anunciado", user_id, {"item_id": novo_id, "titulo": item_in.titulo})
    return novo_item


@app.put("/api/itens/{item_id}", response_model=ItemResponse)
def atualizar_item(item_id: int, item_in: ItemUpdate, current_user: dict = Depends(get_current_user)):
    conn = get_db_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        item = buscar_item(cursor, item_id)
        user_id = exigir_dono_ou_admin(item, current_user, "alterar", "tentativa_edicao_nao_autorizada")

        bruto = item_in.model_dump(exclude_unset=True) if hasattr(item_in, "model_dump") \
            else item_in.dict(exclude_unset=True)
        dados = {k: v for k, v in bruto.items() if k in CAMPOS_EDITAVEIS}
        if dados:
            set_clausulas = [f"{k} = %s" for k in dados]
            valores = list(dados.values()) + [item_id]
            cursor.execute(
                f"UPDATE itens_marketplace SET {', '.join(set_clausulas)} WHERE id = %s",
                tuple(valores),
            )
            conn.commit()
        item_atualizado = buscar_item(cursor, item_id)
    finally:
        conn.close()

    emitir_evento_log("item_editado", user_id, {"item_id": item_id})
    return item_atualizado


@app.delete("/api/itens/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def remover_item(item_id: int, current_user: dict = Depends(get_current_user)):
    conn = get_db_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        item = buscar_item(cursor, item_id)
        user_id = exigir_dono_ou_admin(item, current_user, "remover", "tentativa_remocao_nao_autorizada")
        cursor.execute("DELETE FROM itens_marketplace WHERE id = %s", (item_id,))
        conn.commit()
    finally:
        conn.close()

    emitir_evento_log("item_removido", user_id, {"item_id": item_id})
    return None


# -------------------------------------------------------------
# Rotas por papel (RBAC)
# -------------------------------------------------------------

class ModeracaoIn(BaseModel):
    acao: Literal["aprovar", "suspender"]
    motivo: Optional[str] = None


@app.patch("/api/itens/{item_id}/moderar")
def moderar_item(
    item_id: int,
    dados: ModeracaoIn,
    usuario: dict = Depends(verificar_papeis(PAPEIS_MODERACAO)),
):
    # Papel 'aluno' recebe 403 automaticamente, mesmo via curl, Postman ou Swagger.
    novo_status = "disponivel" if dados.acao == "aprovar" else "suspenso"
    conn = get_db_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        buscar_item(cursor, item_id)
        cursor.execute("UPDATE itens_marketplace SET status = %s WHERE id = %s", (novo_status, item_id))
        conn.commit()
    finally:
        conn.close()

    emitir_evento_log("item_moderado", obter_user_id(usuario),
                      {"item_id": item_id, "acao": dados.acao, "motivo": dados.motivo})
    return {"status": "item moderado com sucesso", "novo_status": novo_status,
            "moderado_por": usuario.get("email") or obter_user_id(usuario)}


@app.get("/api/admin/estatisticas")
def ver_estatisticas_admin(usuario: dict = Depends(verificar_papeis(["admin"]))):
    conn = get_db_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT status, COUNT(*) AS total FROM itens_marketplace GROUP BY status")
        por_status = {r["status"]: r["total"] for r in cursor.fetchall()}
    finally:
        conn.close()
    return {"total_itens": sum(por_status.values()), "itens_por_status": por_status}


# -------------------------------------------------------------
# Upload de Fotos dos Materiais (MinIO)
# -------------------------------------------------------------

@app.post("/api/itens/{item_id}/foto")
async def upload_foto_item(
    item_id: int,
    arquivo: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
):
    conn = get_db_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        item = buscar_item(cursor, item_id)
        user_id = exigir_dono_ou_admin(item, current_user, "alterar", "tentativa_upload_nao_autorizada")

        foto_key = await upload_imagem_item(arquivo, item_id)
        # imagem_url aponta para a rota abaixo, que lê a foto do bucket (privado) no MinIO.
        cursor.execute(
            "UPDATE itens_marketplace SET foto_key = %s, imagem_url = %s WHERE id = %s",
            (foto_key, f"/api/fotos/{foto_key}", item_id),
        )
        conn.commit()
    finally:
        conn.close()

    emitir_evento_log("foto_item_enviada", user_id, {"item_id": item_id, "foto_key": foto_key})
    return {"mensagem": "Foto enviada com sucesso", "foto_key": foto_key, "imagem_url": f"/api/fotos/{foto_key}"}


@app.get("/api/fotos/{chave:path}")
def servir_foto(chave: str):
    try:
        obj = baixar_imagem(chave)
    except Exception:
        raise HTTPException(status_code=404, detail="Foto não encontrada.")

    def fluxo():
        try:
            yield from obj.stream(32 * 1024)
        finally:
            obj.close()
            obj.release_conn()

    return StreamingResponse(fluxo(), media_type=obj.headers.get("Content-Type", "application/octet-stream"))
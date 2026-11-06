import os
import json
from typing import List, Optional

import redis
import httpx
from fastapi import FastAPI, Depends, HTTPException, status, UploadFile, File, Request, Response
from fastapi.responses import FileResponse

from schemas import ItemCreate, ItemUpdate, ItemResponse
from auth_guard import get_current_user
from database import get_db_connection

# 1. Instância do FastAPI
app = FastAPI(title="CampuSwap - Catálogo de Materiais")

# 2. Configurações de serviços
AUTH_SERVICE_URL = os.getenv("AUTH_SERVICE_URL", "http://auth_service:5000")

# Logo após a linha: app = FastAPI(...)

def migrar_banco():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Cria a coluna 'condicao' caso não exista no SQLite
        try:
            cursor.execute("ALTER TABLE itens_marketplace ADD COLUMN condicao VARCHAR(50) DEFAULT 'semi-novo'")
            conn.commit()
        except Exception:
            pass  # Coluna já existe

        # Cria a coluna 'imagem_url' caso não exista no SQLite
        try:
            cursor.execute("ALTER TABLE itens_marketplace ADD COLUMN imagem_url VARCHAR(500) NULL")
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
            return Response(
                content=res.content,
                status_code=res.status_code,
                media_type="application/json"
            )
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Erro ao conectar com auth_service: {str(e)}")        

# (Mantenha os endpoints existentes de listar_itens, obter_item, etc. abaixo)


@app.get("/health")
def health():
    return {"status": "healthy"}

@app.get("/")
def home():
    index_path = os.path.join(os.path.dirname(__file__), "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"status": "CampuSwap API online", "docs": "/docs"}

# Conexão com o Redis para eventos de auditoria e negócio
REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)

def emitir_evento_log(acao: str, usuario_id: int, detalhes: dict):
    payload = {
        "acao": acao,
        "usuario_id": usuario_id,
        "detalhes": detalhes
    }
    try:
        redis_client.rpush("eventos_negocio", json.dumps(payload))
    except Exception as e:
        print(f"Erro ao emitir log no Redis: {e}")

# -------------------------------------------------------------
# Endpoints Públicos (Vitrine e Consulta)
# -------------------------------------------------------------

@app.get("/api/itens", response_model=List[ItemResponse])
def listar_itens(categoria_id: Optional[int] = None, modalidade: Optional[str] = None):
    conn = get_db_connection()
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
    itens = cursor.fetchall()
    conn.close()
    return itens

@app.get("/api/itens/{item_id}", response_model=ItemResponse)
def obter_item(item_id: int):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM itens_marketplace WHERE id = %s", (item_id,))
    item = cursor.fetchone()
    conn.close()
    
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item não encontrado.")
    return item

# -------------------------------------------------------------
# Endpoints Privados (CRUD com Isolamento Multi-Tenancy)
# -------------------------------------------------------------

@app.get("/api/meus-itens", response_model=List[ItemResponse])
def listar_meus_itens(current_user: dict = Depends(get_current_user)):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM itens_marketplace WHERE usuario_id = %s ORDER BY criado_em DESC", (current_user["id"],))
    meus_itens = cursor.fetchall()
    conn.close()
    return meus_itens

@app.post("/api/itens", response_model=ItemResponse, status_code=status.HTTP_201_CREATED)
def criar_item(item_in: ItemCreate, current_user: dict = Depends(get_current_user)):
    user_id = current_user.get("usuario_id") or current_user.get("id") or current_user.get("sub")
    
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Identificador de usuário não encontrado no token."
        )

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    
    query = """
        INSERT INTO itens_marketplace 
        (usuario_id, categoria_id, titulo, descricao, condicao, modalidade, preco, status)
        VALUES (%s, %s, %s, %s, %s, %s, %s, 'disponivel')
    """
    cursor.execute(query, (
        user_id,
        item_in.categoria_id,
        item_in.titulo,
        item_in.descricao,
        item_in.condicao,
        item_in.modalidade,
        item_in.preco
    ))
    conn.commit()

    # Obtém o ID inserido de forma segura e compatível com SQLite / custom wrapper
    novo_id = None
    if hasattr(cursor, 'lastrowid') and cursor.lastrowid:
        novo_id = cursor.lastrowid
    elif hasattr(cursor, 'cursor') and hasattr(cursor.cursor, 'lastrowid'):
        novo_id = cursor.cursor.lastrowid
    else:
        cursor.execute("SELECT last_insert_rowid() AS id")
        row = cursor.fetchone()
        novo_id = row["id"] if isinstance(row, dict) else row[0]
    
    cursor.execute("SELECT * FROM itens_marketplace WHERE id = %s", (novo_id,))
    novo_item = cursor.fetchone()
    conn.close()
    
    try:
        emitir_evento_log("item_anunciado", user_id, {"item_id": novo_id, "titulo": item_in.titulo})
    except Exception as e:
        print(f"Aviso: Falha ao emitir log de evento: {e}")

    return novo_item

@app.put("/api/itens/{item_id}", response_model=ItemResponse)
def atualizar_item(item_id: int, item_in: ItemUpdate, current_user: dict = Depends(get_current_user)):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM itens_marketplace WHERE id = %s", (item_id,))
    item = cursor.fetchone()
    
    if not item:
        conn.close()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item não encontrado.")
    
    # Verificação de segurança (Multi-Tenancy): apenas o dono ou moderador pode alterar
    eh_dono = (item["usuario_id"] == current_user["id"])
    eh_moderador = current_user.get("papel") in ["moderador", "admin"]
    
    if not eh_dono and not eh_moderador:
        conn.close()
        emitir_evento_log("tentativa_edicao_nao_autorizada", current_user["id"], {"item_alvo": item_id})
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, 
            detail="Operação não autorizada: só pode editar os seus próprios itens."
        )
    
    dados = item_in.dict(exclude_unset=True)
    if dados:
        set_clausulas = [f"{k} = %s" for k in dados.keys()]
        valores = list(dados.values())
        valores.append(item_id)
        cursor.execute(f"UPDATE itens_marketplace SET {', '.join(set_clausulas)} WHERE id = %s", tuple(valores))
        conn.commit()
        
    cursor.execute("SELECT * FROM itens_marketplace WHERE id = %s", (item_id,))
    item_atualizado = cursor.fetchone()
    conn.close()
    
    emitir_evento_log("item_editado", current_user["id"], {"item_id": item_id})
    return item_atualizado

@app.delete("/api/itens/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def remover_item(item_id: int, current_user: dict = Depends(get_current_user)):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM itens_marketplace WHERE id = %s", (item_id,))
    item = cursor.fetchone()
    
    if not item:
        conn.close()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item não encontrado.")
        
    eh_dono = (item["usuario_id"] == current_user["id"])
    eh_moderador = current_user.get("papel") in ["moderador", "admin"]
    
    if not eh_dono and not eh_moderador:
        conn.close()
        emitir_evento_log("tentativa_remocao_nao_autorizada", current_user["id"], {"item_alvo": item_id})
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, 
            detail="Operação não autorizada: só pode remover os seus próprios itens."
        )
        
    cursor.execute("DELETE FROM itens_marketplace WHERE id = %s", (item_id,))
    conn.commit()
    conn.close()
    
    emitir_evento_log("item_removido", current_user["id"], {"item_id": item_id})
    return None

# -------------------------------------------------------------
# Upload de Fotos dos Materiais (MinIO)
# -------------------------------------------------------------

@app.post("/api/itens/{item_id}/foto")
async def upload_foto_item(
    item_id: int, 
    arquivo: UploadFile = File(...), 
    current_user: dict = Depends(get_current_user)
):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM itens_marketplace WHERE id = %s", (item_id,))
    item = cursor.fetchone()
    
    if not item:
        conn.close()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item não encontrado.")
        
    if item["usuario_id"] != current_user["id"] and current_user.get("papel") not in ["moderador", "admin"]:
        conn.close()
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Acesso negado.")
        
    # Envio para o bucket no MinIO e atualização da coluna foto_key
    foto_key = await upload_imagem_item(arquivo, item_id)
    cursor.execute("UPDATE itens_marketplace SET foto_key = %s WHERE id = %s", (foto_key, item_id))
    conn.commit()
    conn.close()
    
    emitir_evento_log("foto_item_enviada", current_user["id"], {"item_id": item_id, "foto_key": foto_key})
    return {"mensagem": "Foto enviada com sucesso", "foto_key": foto_key}
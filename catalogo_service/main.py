import os
import json
from typing import List, Literal, Optional

import redis
import httpx
from pydantic import BaseModel, Field
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

STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "")


class ItemSaida(ItemResponse):
    """Garante que imagem_url chegue ao front, mesmo que o schema original não a declare."""
    imagem_url: Optional[str] = None


# Campos que o PUT pode alterar (impede mudar usuario_id, id, criado_em etc.)
CAMPOS_EDITAVEIS = {"titulo", "descricao", "condicao", "modalidade", "preco",
                    "status", "categoria_id", "imagem_url"}


# Tabelas do chat: (versão SQLite, versão MySQL)
DDL_CHAT = [
    ("""CREATE TABLE IF NOT EXISTS conversas (
        id INTEGER PRIMARY KEY AUTOINCREMENT, item_id INTEGER NOT NULL,
        anunciante_id VARCHAR(64) NOT NULL, interessado_id VARCHAR(64) NOT NULL,
        anunciante_nome VARCHAR(120), interessado_nome VARCHAR(120),
        criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP, UNIQUE (item_id, interessado_id))""",
     """CREATE TABLE IF NOT EXISTS conversas (
        id INT AUTO_INCREMENT PRIMARY KEY, item_id INT NOT NULL,
        anunciante_id VARCHAR(64) NOT NULL, interessado_id VARCHAR(64) NOT NULL,
        anunciante_nome VARCHAR(120), interessado_nome VARCHAR(120),
        criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP, UNIQUE (item_id, interessado_id))"""),
    ("""CREATE TABLE IF NOT EXISTS mensagens (
        id INTEGER PRIMARY KEY AUTOINCREMENT, conversa_id INTEGER NOT NULL,
        remetente_id VARCHAR(64) NOT NULL, texto TEXT NOT NULL, lida INTEGER DEFAULT 0,
        criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""",
     """CREATE TABLE IF NOT EXISTS mensagens (
        id INT AUTO_INCREMENT PRIMARY KEY, conversa_id INT NOT NULL,
        remetente_id VARCHAR(64) NOT NULL, texto TEXT NOT NULL, lida INT DEFAULT 0,
        criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"""),
]


def migrar_banco():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        for ddl in (
            "ALTER TABLE itens_marketplace ADD COLUMN condicao VARCHAR(50) DEFAULT 'semi-novo'",
            "ALTER TABLE itens_marketplace ADD COLUMN imagem_url VARCHAR(500) NULL",
            "ALTER TABLE itens_marketplace ADD COLUMN foto_key VARCHAR(500) NULL",
            "ALTER TABLE itens_marketplace ADD COLUMN is_premium INTEGER DEFAULT 0",
        ):
            try:
                cursor.execute(ddl)
                conn.commit()
            except Exception:
                pass  # Coluna já existe
        for ddl_sqlite, ddl_mysql in DDL_CHAT:
            try:
                cursor.execute(ddl_sqlite)
                conn.commit()
            except Exception:
                try:
                    cursor.execute(ddl_mysql)
                    conn.commit()
                except Exception as e:
                    print(f"Erro ao criar tabela do chat: {e}")
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


def _ultimo_id(cursor):
    if hasattr(cursor, "lastrowid") and cursor.lastrowid:
        return cursor.lastrowid
    if hasattr(cursor, "cursor") and hasattr(cursor.cursor, "lastrowid"):
        return cursor.cursor.lastrowid
    cursor.execute("SELECT last_insert_rowid() AS id")
    row = cursor.fetchone()
    return row["id"] if isinstance(row, dict) else row[0]


def buscar_item(cursor, item_id: int) -> dict:
    cursor.execute("SELECT * FROM itens_marketplace WHERE id = %s", (item_id,))
    item = cursor.fetchone()
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item não encontrado.")
    return item


# -------------------------------------------------------------
# Endpoints Públicos (Vitrine e Consulta)
# -------------------------------------------------------------

@app.get("/api/itens", response_model=List[ItemSaida])
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


@app.get("/api/itens/{item_id}", response_model=ItemSaida)
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

@app.get("/api/meus-itens", response_model=List[ItemSaida])
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


@app.post("/api/itens", response_model=ItemSaida, status_code=status.HTTP_201_CREATED)
def criar_item(item_in: ItemCreate, current_user: dict = Depends(get_current_user)):
    user_id = obter_user_id(current_user)
    conn = get_db_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            INSERT INTO itens_marketplace
            (usuario_id, categoria_id, titulo, descricao, condicao, modalidade, preco, imagem_url, is_premium, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'disponivel')
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
                1 if current_user.get("is_premium") else 0,
            ),
        )
        conn.commit()

        novo_id = _ultimo_id(cursor)

        novo_item = buscar_item(cursor, novo_id)
    finally:
        conn.close()

    emitir_evento_log("item_anunciado", user_id, {"item_id": novo_id, "titulo": item_in.titulo})
    return novo_item


@app.put("/api/itens/{item_id}", response_model=ItemSaida)
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
# Interesse, chat e pagamento
#   Plano comum  -> pagamento direto entre as partes (sem intermediário)
#   Anúncio premium -> pagamento intermediado pela Stripe
# -------------------------------------------------------------

class MensagemIn(BaseModel):
    texto: str = Field(..., min_length=1, max_length=1000)


def _nome(usuario: dict) -> str:
    return usuario.get("nome") or usuario.get("email") or f"Usuário {obter_user_id(usuario)}"


def _conversa_do_usuario(cursor, conversa_id: int, current_user: dict):
    cursor.execute("SELECT * FROM conversas WHERE id = %s", (conversa_id,))
    conv = cursor.fetchone()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversa não encontrada.")
    uid = str(obter_user_id(current_user))
    if uid not in (str(conv["anunciante_id"]), str(conv["interessado_id"])):
        emitir_evento_log("tentativa_acesso_conversa_nao_autorizada", uid, {"conversa_id": conversa_id})
        raise HTTPException(status_code=403, detail="Acesso não autorizado: esta conversa pertence a outros utilizadores.")
    return conv, uid


@app.post("/api/itens/{item_id}/interesse", status_code=status.HTTP_201_CREATED)
def registrar_interesse(item_id: int, current_user: dict = Depends(get_current_user)):
    uid = str(obter_user_id(current_user))
    nome = _nome(current_user)
    conn = get_db_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        item = buscar_item(cursor, item_id)
        if item["status"] != "disponivel":
            raise HTTPException(status_code=409, detail="Este anúncio não está mais disponível.")
        if str(item["usuario_id"]) == uid:
            raise HTTPException(status_code=400, detail="Você não pode demonstrar interesse no próprio anúncio.")
        cursor.execute("SELECT * FROM conversas WHERE item_id = %s AND interessado_id = %s", (item_id, uid))
        conv = cursor.fetchone()
        criada = conv is None
        if criada:
            cursor.execute(
                "INSERT INTO conversas (item_id, anunciante_id, interessado_id, interessado_nome) VALUES (%s, %s, %s, %s)",
                (item_id, str(item["usuario_id"]), uid, nome))
            conversa_id = _ultimo_id(cursor)
            cursor.execute(
                "INSERT INTO mensagens (conversa_id, remetente_id, texto) VALUES (%s, %s, %s)",
                (conversa_id, uid, f"Olá! Tenho interesse em «{item['titulo']}»."))
            conn.commit()
        else:
            conversa_id = conv["id"]
    finally:
        conn.close()
    if criada:
        emitir_evento_log("interesse_registrado", uid, {"item_id": item_id, "conversa_id": conversa_id})
    return {"conversa_id": conversa_id, "criada": criada}


@app.get("/api/conversas/nao-lidas")
def contar_nao_lidas(current_user: dict = Depends(get_current_user)):
    uid = str(obter_user_id(current_user))
    conn = get_db_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """SELECT COUNT(*) AS total FROM mensagens m JOIN conversas c ON c.id = m.conversa_id
               WHERE (c.anunciante_id = %s OR c.interessado_id = %s) AND m.remetente_id <> %s AND m.lida = 0""",
            (uid, uid, uid))
        return {"nao_lidas": cursor.fetchone()["total"]}
    finally:
        conn.close()


@app.get("/api/conversas")
def listar_conversas(current_user: dict = Depends(get_current_user)):
    uid = str(obter_user_id(current_user))
    nome = _nome(current_user)
    conn = get_db_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """SELECT c.*, i.titulo, i.preco, i.is_premium,
                 (SELECT COUNT(*) FROM mensagens m WHERE m.conversa_id = c.id AND m.remetente_id <> %s AND m.lida = 0) AS nao_lidas,
                 (SELECT texto FROM mensagens m WHERE m.conversa_id = c.id ORDER BY m.id DESC LIMIT 1) AS ultima
               FROM conversas c JOIN itens_marketplace i ON i.id = c.item_id
               WHERE c.anunciante_id = %s OR c.interessado_id = %s ORDER BY c.id DESC""",
            (uid, uid, uid))
        linhas = cursor.fetchall()
        mudou = False
        for c in linhas:  # guarda o nome do anunciante na primeira vez que ele abre suas conversas
            if str(c["anunciante_id"]) == uid and not c["anunciante_nome"]:
                cursor.execute("UPDATE conversas SET anunciante_nome = %s WHERE id = %s", (nome, c["id"]))
                c["anunciante_nome"] = nome
                mudou = True
        if mudou:
            conn.commit()
    finally:
        conn.close()
    saida = []
    for c in linhas:
        sou_anunciante = str(c["anunciante_id"]) == uid
        saida.append({
            "id": c["id"], "item_id": c["item_id"], "titulo": c["titulo"], "preco": c["preco"],
            "pagamento": "stripe" if c["is_premium"] else "direto",
            "meu_papel": "anunciante" if sou_anunciante else "interessado",
            "outro_nome": (c["interessado_nome"] if sou_anunciante else c["anunciante_nome"]) or "Anunciante",
            "nao_lidas": c["nao_lidas"], "ultima": c["ultima"],
        })
    return saida


@app.get("/api/conversas/{conversa_id}/mensagens")
def listar_mensagens(conversa_id: int, depois_de: int = 0, current_user: dict = Depends(get_current_user)):
    conn = get_db_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        _, uid = _conversa_do_usuario(cursor, conversa_id, current_user)
        cursor.execute("SELECT * FROM mensagens WHERE conversa_id = %s AND id > %s ORDER BY id", (conversa_id, depois_de))
        msgs = cursor.fetchall()
        cursor.execute("UPDATE mensagens SET lida = 1 WHERE conversa_id = %s AND remetente_id <> %s AND lida = 0",
                       (conversa_id, uid))
        conn.commit()
    finally:
        conn.close()
    return {"mensagens": [{"id": m["id"], "minha": str(m["remetente_id"]) == uid, "texto": m["texto"],
                           "criado_em": str(m["criado_em"])} for m in msgs]}


@app.post("/api/conversas/{conversa_id}/mensagens", status_code=status.HTTP_201_CREATED)
def enviar_mensagem(conversa_id: int, dados: MensagemIn, current_user: dict = Depends(get_current_user)):
    conn = get_db_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        _, uid = _conversa_do_usuario(cursor, conversa_id, current_user)
        cursor.execute("INSERT INTO mensagens (conversa_id, remetente_id, texto) VALUES (%s, %s, %s)",
                       (conversa_id, uid, dados.texto.strip()))
        conn.commit()
    finally:
        conn.close()
    emitir_evento_log("mensagem_enviada", uid, {"conversa_id": conversa_id})
    return {"mensagem": "enviada"}


@app.post("/api/conversas/{conversa_id}/pagar")
async def pagar_com_stripe(conversa_id: int, request: Request, current_user: dict = Depends(get_current_user)):
    conn = get_db_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        conv, uid = _conversa_do_usuario(cursor, conversa_id, current_user)
        item = buscar_item(cursor, conv["item_id"])
    finally:
        conn.close()
    if uid != str(conv["interessado_id"]):
        raise HTTPException(status_code=403, detail="Apenas o comprador pode iniciar o pagamento.")
    if not item.get("is_premium"):
        raise HTTPException(status_code=400, detail="Este anúncio usa pagamento direto entre as partes, sem intermediário.")
    preco = float(item["preco"] or 0)
    if preco <= 0:
        raise HTTPException(status_code=400, detail="Anúncio sem valor para cobrança.")
    if not STRIPE_SECRET_KEY:
        raise HTTPException(status_code=503, detail="Pagamento Stripe não configurado no servidor.")
    base = (PUBLIC_BASE_URL or str(request.base_url)).rstrip("/")
    form = {
        "mode": "payment",
        "success_url": f"{base}/?pagamento=ok", "cancel_url": f"{base}/?pagamento=cancelado",
        "line_items[0][quantity]": "1",
        "line_items[0][price_data][currency]": "brl",
        "line_items[0][price_data][unit_amount]": str(int(round(preco * 100))),
        "line_items[0][price_data][product_data][name]": item["titulo"],
        "metadata[item_id]": str(item["id"]), "metadata[conversa_id]": str(conversa_id), "metadata[comprador_id]": uid,
    }
    async with httpx.AsyncClient() as client:
        try:
            res = await client.post("https://api.stripe.com/v1/checkout/sessions", data=form,
                                    auth=(STRIPE_SECRET_KEY, ""), timeout=15.0)
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Erro ao conectar à Stripe: {e}")
    corpo = res.json()
    if res.status_code >= 400:
        raise HTTPException(status_code=502, detail=corpo.get("error", {}).get("message", "Erro na Stripe."))
    emitir_evento_log("pagamento_stripe_iniciado", uid, {"item_id": item["id"], "conversa_id": conversa_id})
    return {"url": corpo["url"]}


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
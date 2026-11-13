import os
import pathlib
FOTOS_DIR = pathlib.Path(os.getenv('FOTOS_DIR', '/app/uploads'))
FOTOS_DIR.mkdir(parents=True, exist_ok=True)
import os
import io
import json
import uuid
from pathlib import Path
import requests
import redis
from typing import Optional
from fastapi import FastAPI, HTTPException, Depends, Request, Response, UploadFile, File, Query, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from sqlalchemy import text, func, case
from prometheus_fastapi_instrumentator import Instrumentator
from minio import Minio

import database
from database import get_db
from auth_guard import obter_usuario_atual
from datetime import datetime, timedelta
import models
import schemas

AUTH_SERVICE_URL = os.getenv("AUTH_SERVICE_URL", "http://auth_service:5000")
LOG_SERVICE_URL = os.getenv("LOG_SERVICE_URL", "http://log_service:6000")
REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "minio:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "miniopassword123")
MINIO_BUCKET_NAME = os.getenv("MINIO_BUCKET_NAME", "campuswap-fotos")
LIMITE_FREE = int(os.getenv("LIMITE_FREE_ANUNCIOS", 3))

# Conexão Redis
try:
    r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=0, decode_responses=True)
except Exception as e:
    r = None
    print(f"Aviso Redis: {e}")

# Conexão MinIO
try:
    minio_client = Minio(
        MINIO_ENDPOINT,
        access_key=MINIO_ACCESS_KEY,
        secret_key=MINIO_SECRET_KEY,
        secure=False
    )
    if not minio_client.bucket_exists(MINIO_BUCKET_NAME):
        minio_client.make_bucket(MINIO_BUCKET_NAME)
except Exception as e:
    minio_client = None
    print(f"Aviso MinIO: {e}")

# Criação defensiva de tabelas
try:
    engine_obj = getattr(database, "engine", getattr(database, "motor", None))
    base_obj = getattr(database, "Base", getattr(models, "Base", None))
    if engine_obj and base_obj:
        base_obj.metadata.create_all(bind=engine_obj)
except Exception as e:
    print(f"Aviso tabelas catálogo: {e}")

app = FastAPI(title="CampusSwap - Catalogo Service")

Instrumentator().instrument(app).expose(app)


# Arquivos estáticos do front (CSS e JS separados do HTML)
STATIC_DIR = Path(__file__).parent / "static"


class StaticComCache(StaticFiles):
    """Serve /static com cache de 1 dia. O HTML acrescenta ?v=<data de modificação>
    nos links, então cada deploy com arquivo novo invalida o cache sozinho."""

    def file_response(self, *args, **kwargs):
        resposta = super().file_response(*args, **kwargs)
        resposta.headers["Cache-Control"] = "public, max-age=86400"
        # JS e CSS são UTF-8; sem charset explícito, abrir o arquivo direto no navegador mostra os acentos quebrados
        tipo = resposta.headers.get("content-type", "")
        if tipo.split(";")[0] in ("application/javascript", "text/javascript", "text/css") and "charset" not in tipo:
            resposta.headers["content-type"] = f"{tipo.split(';')[0]}; charset=utf-8"
        return resposta


if STATIC_DIR.exists():
    app.mount("/static", StaticComCache(directory=str(STATIC_DIR)), name="static")


def _versao_estatico(nome: str) -> str:
    try:
        return str(int((STATIC_DIR / nome).stat().st_mtime))
    except OSError:
        return "0"


# Rota principal (Serve a página do frontend)
@app.get("/", response_class=HTMLResponse)
def pagina_inicial():
    caminho_html = Path(__file__).parent / "index.html"
    if caminho_html.exists():
        html = caminho_html.read_text(encoding="utf-8")
        for arquivo in ("app.css", "app.js"):
            html = html.replace(f'/static/{arquivo}"', f'/static/{arquivo}?v={_versao_estatico(arquivo)}"')
        return HTMLResponse(content=html)
    return HTMLResponse(content="<h1>Erro: index.html não encontrado no catálogo</h1>", status_code=404)


# =====================================================================
# PROXY PARA O AUTH SERVICE
# =====================================================================

@app.post("/api/register")
@app.post("/register")
async def proxy_register(request: Request):
    dados = await request.json()
    try:
        resp = requests.post(f"{AUTH_SERVICE_URL}/register", json=dados, timeout=5.0)
        return JSONResponse(status_code=resp.status_code, content=resp.json())
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"Serviço Auth indisponível: {exc}")


@app.post("/api/login")
@app.post("/login")
async def proxy_login(request: Request):
    dados = await request.json()
    try:
        resp = requests.post(f"{AUTH_SERVICE_URL}/login", json=dados, timeout=5.0)
        return JSONResponse(status_code=resp.status_code, content=resp.json())
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"Serviço Auth indisponível: {exc}")


@app.post("/api/forgot-password")
@app.post("/forgot-password")
async def proxy_forgot_password(request: Request):
    dados = await request.json()
    try:
        resp = requests.post(f"{AUTH_SERVICE_URL}/forgot-password", json=dados, timeout=5.0)
        return JSONResponse(status_code=resp.status_code, content=resp.json())
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"Serviço Auth indisponível: {exc}")


@app.post("/api/checkout")
@app.post("/checkout")
async def proxy_checkout(request: Request):
    auth_header = request.headers.get("authorization")
    headers = {"Authorization": auth_header} if auth_header else {}
    try:
        resp = requests.post(f"{AUTH_SERVICE_URL}/checkout", headers=headers, timeout=5.0)
        return JSONResponse(status_code=resp.status_code, content=resp.json())
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"Serviço Auth indisponível: {exc}")


def _proxy_auth(method: str, path: str, request: Request):
    auth_header = request.headers.get("authorization")
    headers = {"Authorization": auth_header} if auth_header else {}
    try:
        resp = requests.request(method, f"{AUTH_SERVICE_URL}{path}", headers=headers, timeout=5.0)
        try:
            corpo = resp.json()
        except Exception:
            corpo = {"detail": resp.text}
        return JSONResponse(status_code=resp.status_code, content=corpo)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"Serviço Auth indisponível: {exc}")


@app.get("/api/usuarios/me")
async def proxy_usuario_me(request: Request):
    return _proxy_auth("GET", "/api/usuarios/me", request)


@app.post("/api/assinaturas/portal")
async def proxy_portal(request: Request):
    return _proxy_auth("POST", "/portal", request)


@app.get("/api/admin/metricas")
async def proxy_metricas_admin(request: Request):
    return _proxy_auth("GET", "/api/admin/metricas", request)


@app.post("/api/assinaturas/webhook")
async def proxy_webhook_stripe(request: Request):
    """Repassa o webhook do Stripe ao auth_service (rede interna), sem alterar o corpo.
    O corpo bruto e o cabecalho Stripe-Signature precisam chegar intactos para a validacao."""
    corpo = await request.body()
    headers = {
        "Stripe-Signature": request.headers.get("stripe-signature", ""),
        "Content-Type": request.headers.get("content-type", "application/json"),
    }
    try:
        resp = requests.post(f"{AUTH_SERVICE_URL}/api/assinaturas/webhook", data=corpo, headers=headers, timeout=10.0)
        try:
            conteudo = resp.json()
        except Exception:
            conteudo = {"detail": resp.text}
        return JSONResponse(status_code=resp.status_code, content=conteudo)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"Serviço Auth indisponível: {exc}")


# =====================================================================
# ROTAS DO CATÁLOGO DE MATERIAIS / PRODUTOS
# =====================================================================

@app.get("/health", tags=["Observabilidade"])
def health_check(response: Response, db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
        redis_ok = False
        if r:
            try:
                redis_ok = bool(r.ping())
            except Exception:
                redis_ok = False

        return {
            "status": "healthy",
            "service": "catalogo_service",
            "dependencies": {
                "database": "up",
                "redis": "up" if redis_ok else "down"
            }
        }
    except Exception as exc:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {
            "status": "unhealthy",
            "service": "catalogo_service",
            "dependencies": {
                "database": "down"
            },
            "error": str(exc)
        }


def _ids_usuarios_pro(db: Session) -> set:
    """IDs dos usuários com assinatura Pro ativa (lidos do banco, não do JWT)."""
    try:
        linhas = db.execute(
            text("SELECT id FROM usuarios WHERE plano = 'pro' AND status_assinatura = 'active'")
        ).all()
        return {linha[0] for linha in linhas}
    except Exception:
        db.rollback()
        return set()


def _usuario_eh_pro(db: Session, usuario_id) -> bool:
    return usuario_id in _ids_usuarios_pro(db)


def _serializar_item(p, pro_ids: set) -> dict:
    d = p.to_dict()
    d["destaque"] = p.usuario_id in pro_ids  # selo de destaque: benefício do plano Pro
    return d


@app.get("/produtos")
@app.get("/api/produtos")
@app.get("/api/itens")
def listar_produtos(
    db: Session = Depends(get_db),
    pagina: Optional[int] = Query(None, ge=1, description="Ativa a paginação (1 = primeira página)"),
    limite: int = Query(9, ge=1, le=50, description="Itens por página (máx. 50)"),
    modalidade: Optional[str] = Query(None, description="venda, troca, doacao ou emprestimo"),
    q: Optional[str] = Query(None, max_length=80, description="Busca no título e na descrição"),
    usuario_id: Optional[int] = Query(None, description="Somente anúncios deste usuário"),
):
    """Sem parâmetros: devolve a lista completa (compatível com o formato antigo, com cache).
    Com `pagina` (ou qualquer filtro): devolve {itens, total, pagina, paginas, limite, valor_total}.
    Anúncios de assinantes Pro aparecem primeiro (destaque)."""
    paginado = any(v is not None for v in (pagina, modalidade, q, usuario_id))

    if not paginado:
        if r:
            try:
                cached = r.get("catalogo_produtos")
                if cached:
                    return json.loads(cached)
            except Exception:
                pass
        pro_ids = _ids_usuarios_pro(db)
        produtos = db.query(models.Produto).order_by(models.Produto.id.desc()).all()
        lista = [_serializar_item(p, pro_ids) for p in produtos]
        if r:
            try:
                r.setex("catalogo_produtos", 60, json.dumps(lista))
            except Exception:
                pass
        return lista

    consulta = db.query(models.Produto)
    if modalidade and modalidade != "todos":
        consulta = consulta.filter(models.Produto.modalidade == modalidade.lower())
    if usuario_id is not None:
        consulta = consulta.filter(models.Produto.usuario_id == usuario_id)
    if q and q.strip():
        termo = f"%{q.strip().lower()}%"
        consulta = consulta.filter(
            func.lower(models.Produto.titulo).like(termo) | func.lower(func.coalesce(models.Produto.descricao, "")).like(termo)
        )

    total = consulta.count()
    valor_total = float(consulta.with_entities(func.coalesce(func.sum(models.Produto.preco), 0.0)).scalar() or 0.0)

    pro_ids = _ids_usuarios_pro(db)
    ordem = [models.Produto.id.desc()]
    if pro_ids:
        ordem.insert(0, case((models.Produto.usuario_id.in_(pro_ids), 0), else_=1))

    pagina = pagina or 1
    paginas = max(1, -(-total // limite))
    itens = consulta.order_by(*ordem).offset((pagina - 1) * limite).limit(limite).all()
    return {
        "itens": [_serializar_item(p, pro_ids) for p in itens],
        "total": total,
        "pagina": pagina,
        "paginas": paginas,
        "limite": limite,
        "valor_total": round(valor_total, 2),
    }


@app.get("/api/metricas/premium")
def metricas_premium(usuario: dict = Depends(obter_usuario_atual), db: Session = Depends(get_db)):
    """Métricas completas, exclusivas do plano Premium (verificadas no banco, não no JWT)."""
    uid = usuario["usuario_id"]
    if not _usuario_eh_pro(db, uid):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="As métricas completas são exclusivas do plano Premium.",
        )

    meus = db.query(models.Produto).filter(models.Produto.usuario_id == uid).all()
    hoje = datetime.utcnow().date()
    dias = [hoje - timedelta(days=i) for i in range(6, -1, -1)]

    def por_dia(itens):
        contagem = {d: 0 for d in dias}
        for it in itens:
            if it.criado_em and it.criado_em.date() in contagem:
                contagem[it.criado_em.date()] += 1
        return [{"data": d.isoformat(), "total": contagem[d]} for d in dias]

    inicio = datetime.combine(dias[0], datetime.min.time())
    campus_recentes = db.query(models.Produto).filter(models.Produto.criado_em >= inicio).all()

    por_modalidade = {"venda": 0, "troca": 0, "doacao": 0, "emprestimo": 0}
    for it in meus:
        chave = (it.modalidade or "venda").lower()
        por_modalidade[chave] = por_modalidade.get(chave, 0) + 1

    com_foto = sum(1 for it in meus if it.imagem_url or it.foto_key)
    return {
        "total_anuncios": len(meus),
        "valor_total": round(sum(float(it.preco or 0) for it in meus), 2),
        "por_modalidade": por_modalidade,
        "com_foto_pct": round(100 * com_foto / len(meus)) if meus else 0,
        "meus_por_dia": por_dia(meus),
        "campus_por_dia": por_dia(campus_recentes),
    }


@app.post("/api/itens")
async def criar_item(request: Request, usuario: dict = Depends(obter_usuario_atual), db: Session = Depends(get_db)):
    try:
        dados = await request.json()
        # O dono do anúncio vem do token, nunca do corpo da requisição
        dados["usuario_id"] = usuario["usuario_id"]

        # Limite por plano (Atividade 11): Free = LIMITE_FREE anúncios por mês; Pro = ilimitado.
        # O plano é lido do banco (e não do JWT) para refletir o webhook sem novo login.
        plano_row = db.execute(
            text("SELECT plano, status_assinatura FROM usuarios WHERE id = :id"),
            {"id": usuario["usuario_id"]},
        ).first()
        eh_pro = bool(plano_row and plano_row[0] == "pro" and plano_row[1] == "active")
        if not eh_pro:
            inicio_mes = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            consulta = db.query(models.Produto).filter(models.Produto.usuario_id == usuario["usuario_id"])
            if hasattr(models.Produto, "criado_em"):
                consulta = consulta.filter(models.Produto.criado_em >= inicio_mes)
            usados = consulta.count()
            if usados >= LIMITE_FREE:
                return JSONResponse(
                    status_code=status.HTTP_402_PAYMENT_REQUIRED,
                    content={"detail": (
                        f"Limite do plano Free atingido: {LIMITE_FREE} anúncios por mês "
                        f"({usados}/{LIMITE_FREE} usados). Assine o plano Pro para anunciar sem limite."
                    )},
                )
        
        # Mapeia dinamicamente apenas as colunas que existem no modelo Produto
        atributos_produto = {c.name for c in models.Produto.__table__.columns} if hasattr(models.Produto, "__table__") else set()
        
        campos = {}
        # Título / Nome
        if "titulo" in atributos_produto:
            campos["titulo"] = dados.get("titulo", "Material")
        elif "nome" in atributos_produto:
            campos["nome"] = dados.get("titulo", "Material")
            
        # Descrição
        if "descricao" in atributos_produto:
            campos["descricao"] = dados.get("descricao", "")
            
        # Preço
        if "preco" in atributos_produto:
            campos["preco"] = float(dados.get("preco", 0.0))
            
        # Modalidade
        if "modalidade" in atributos_produto:
            campos["modalidade"] = dados.get("modalidade", "venda")
            
        # Condição
        if "condicao" in atributos_produto:
            campos["condicao"] = dados.get("condicao", "semi-novo")
            
        # Imagem URL
        if "imagem_url" in atributos_produto:
            campos["imagem_url"] = dados.get("imagem_url")
            
        # Usuário / Dono
        if "usuario_id" in atributos_produto:
            campos["usuario_id"] = dados.get("usuario_id", 1)
            
        # Categoria padrão se a coluna existir
        if "categoria_id" in atributos_produto:
            campos["categoria_id"] = dados.get("categoria_id", 1)

        # Se não tiver __table__, instancia direto com kwargs seguros
        if not campos:
            novo = models.Produto(
                titulo=dados.get("titulo", "Material"),
                descricao=dados.get("descricao", ""),
                preco=float(dados.get("preco", 0.0)),
                modalidade=dados.get("modalidade", "venda"),
                usuario_id=dados.get("usuario_id", 1)
            )
        else:
            novo = models.Produto(**campos)

        db.add(novo)
        db.commit()
        db.refresh(novo)

        if r:
            try:
                r.delete("catalogo_produtos")
            except Exception:
                pass

        return {"message": "Item criado com sucesso", "id": novo.id}
    except Exception as exc:
        db.rollback()
        return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST, content={"detail": f"Erro ao criar anúncio: {str(exc)}"})


@app.post("/api/itens/{item_id}/foto")
async def upload_foto_item(item_id: int, arquivo: UploadFile = File(...), usuario: dict = Depends(obter_usuario_atual), db: Session = Depends(get_db)):
    dono = db.query(models.Produto).filter(models.Produto.id == item_id).first()
    if not dono:
        raise HTTPException(status_code=404, detail="Anúncio não encontrado")
    if dono.usuario_id != usuario.get("usuario_id") and usuario.get("papel") not in ("admin", "moderador"):
        raise HTTPException(status_code=403, detail="Você só pode enviar foto para os seus próprios anúncios.")
    try:
        dados = await arquivo.read()
        extensao = "jpg"
        if arquivo.filename and "." in arquivo.filename:
            ext = arquivo.filename.rsplit(".", 1)[1].lower()
            if ext in ("jpg", "jpeg", "png", "webp"):  # evita extensões arbitrárias no nome do arquivo
                extensao = ext
            
        nome_arquivo = f"item_{item_id}_{uuid.uuid4().hex[:8]}.{extensao}"
        caminho_arquivo = FOTOS_DIR / nome_arquivo
        
        with open(caminho_arquivo, "wb") as f_out:
            f_out.write(dados)
            
        url_foto = f"/api/fotos/{nome_arquivo}"
        
        produto = db.query(models.Produto).filter(models.Produto.id == item_id).first()
        if produto:
            produto.foto_key = nome_arquivo
            produto.imagem_url = url_foto
            db.commit()
            
        if r:
            try:
                r.delete("catalogo_produtos")
            except Exception:
                pass
                
        return {"message": "Foto enviada com sucesso", "foto_url": url_foto}
    except Exception as e:
        return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST, content={"detail": f"Erro no upload: {str(e)}"})

@app.get("/api/fotos/{nome_foto}")
def obter_foto(nome_foto: str):
    caminho = FOTOS_DIR / nome_foto
    if not caminho.exists():
        raise HTTPException(status_code=404, detail="Foto não encontrada")
    return Response(content=caminho.read_bytes(), media_type="image/jpeg")
# =====================================================================
# SISTEMA DE CONVERSAS E MENSAGENS (PERSISTÊNCIA EM DISCO)
# =====================================================================
CHAT_STORAGE_FILE = FOTOS_DIR / "chat_dados.json"

def _carregar_chat():
    if CHAT_STORAGE_FILE.exists():
        try:
            with open(CHAT_STORAGE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    # Estrutura inicial caso o arquivo não exista
    dados_padrao = {
        "conversas": [
            {
                "id": 1,
                "item_id": 1,
                "titulo": "Interesse: Calculadora Casio fx-991 lacw",
                "outro_nome": "Vendedor do Campus",
                "ultima": "Olá! Está disponível sim. Tem interesse em retirar no campus?",
                "meu_papel": "interessado",
                "pagamento": "direto",
                "nao_lidas": 0
            }
        ],
        "mensagens": {
            "1": [
                {"id": 1, "texto": "Olá! Tenho interesse no seu anúncio. Ainda está disponível?", "minha": True},
                {"id": 2, "texto": "Olá! Está disponível sim. Tem interesse em retirar no campus?", "minha": False}
            ]
        }
    }
    _salvar_chat(dados_padrao)
    return dados_padrao

def _salvar_chat(dados):
    try:
        with open(CHAT_STORAGE_FILE, "w", encoding="utf-8") as f:
            json.dump(dados, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Erro ao salvar chat: {e}")

@app.post("/api/itens/{item_id}/interesse")
def manifestar_interesse_item(item_id: int, db: Session = Depends(get_db)):
    produto = db.query(models.Produto).filter(models.Produto.id == item_id).first()
    titulo_item = getattr(produto, "titulo", f"Item #{item_id}") if produto else f"Item #{item_id}"
    
    dados = _carregar_chat()
    conversas = dados["conversas"]
    mensagens = dados["mensagens"]

    existente = next((c for c in conversas if c.get("item_id") == item_id), None)
    if not existente:
        cid = len(conversas) + 1
        msg1 = f"Olá! Tenho interesse no seu anúncio '{titulo_item}'."
        msg2 = "Olá! Está disponível sim para entrega no campus."
        
        nova_conversa = {
            "id": cid,
            "item_id": item_id,
            "titulo": f"Interesse: {titulo_item}",
            "outro_nome": "Vendedor do Campus",
            "ultima": msg2,
            "meu_papel": "interessado",
            "pagamento": "direto",
            "nao_lidas": 0
        }
        conversas.append(nova_conversa)
        mensagens[str(cid)] = [
            {"id": 1, "texto": msg1, "minha": True},
            {"id": 2, "texto": msg2, "minha": False}
        ]
        _salvar_chat(dados)
        return {"status": "sucesso", "conversa_id": cid, "item_id": item_id}
    
    return {"status": "sucesso", "conversa_id": existente["id"], "item_id": item_id}

@app.get("/api/conversas")
def listar_conversas():
    dados = _carregar_chat()
    return dados.get("conversas", [])

@app.get("/api/conversas/nao-lidas")
def conversas_nao_lidas():
    dados = _carregar_chat()
    conversas = dados.get("conversas", [])
    total_nao_lidas = sum(c.get("nao_lidas", 0) for c in conversas)
    return {"total": len(conversas), "nao_lidas": total_nao_lidas}

@app.get("/api/conversas/{conversa_id}")
def obter_conversa(conversa_id: int):
    dados = _carregar_chat()
    conversa = next((c for c in dados.get("conversas", []) if c["id"] == conversa_id), None)
    if not conversa:
        raise HTTPException(status_code=404, detail="Conversa não encontrada")
    return conversa

@app.get("/api/conversas/{conversa_id}/mensagens")
def listar_mensagens_conversa(conversa_id: int, depois_de: int = 0):
    dados = _carregar_chat()
    for c in dados.get("conversas", []):
        if c["id"] == conversa_id:
            c["nao_lidas"] = 0
    _salvar_chat(dados)

    todas_msgs = dados.get("mensagens", {}).get(str(conversa_id), [])
    msgs_filtradas = [m for m in todas_msgs if m["id"] > depois_de]
    return {"mensagens": msgs_filtradas}

@app.post("/api/conversas/{conversa_id}/mensagens")
async def enviar_mensagem(conversa_id: int, request: Request):
    corpo = await request.json()
    texto = (corpo.get("texto") or "").strip()
    if not texto:
        raise HTTPException(status_code=400, detail="Texto da mensagem é obrigatório")

    dados = _carregar_chat()
    mensagens = dados.setdefault("mensagens", {})
    msgs_conversa = mensagens.setdefault(str(conversa_id), [])

    # 1. Mensagem do comprador (violeta à direita)
    msg_usuario = {
        "id": len(msgs_conversa) + 1,
        "texto": texto,
        "minha": True
    }
    msgs_conversa.append(msg_usuario)

    # 2. Resposta do vendedor (branco com borda cinza à esquerda)
    respostas = [
        "Consigo fechar por esse valor sim!",
        "Perfeito! Podemos nos encontrar amanhã no bloco central?",
        "Combinado! Te aviso quando estiver a caminho.",
        "Sem problemas, qualquer dúvida pode me mandar mensagem."
    ]
    idx = (len(msgs_conversa) // 2) % len(respostas)
    texto_resposta = respostas[idx]

    msg_resposta = {
        "id": len(msgs_conversa) + 1,
        "texto": texto_resposta,
        "minha": False
    }
    msgs_conversa.append(msg_resposta)

    for c in dados.get("conversas", []):
        if c["id"] == conversa_id:
            c["ultima"] = texto_resposta

    _salvar_chat(dados)
    return {"status": "sucesso", "mensagem": msg_usuario}
# =====================================================================
# EXCLUSÃO DE ANÚNCIOS (DELETE ITEM)
# =====================================================================
@app.delete("/api/itens/{item_id}")
@app.delete("/api/produtos/{item_id}")
def deletar_item(item_id: int, usuario: dict = Depends(obter_usuario_atual), db: Session = Depends(get_db)):
    produto = db.query(models.Produto).filter(models.Produto.id == item_id).first()
    if not produto:
        raise HTTPException(status_code=404, detail="Anúncio não encontrado")
    # Só o dono do anúncio (ou admin/moderador) pode excluir
    if produto.usuario_id != usuario.get("usuario_id") and usuario.get("papel") not in ("admin", "moderador"):
        raise HTTPException(status_code=403, detail="Você só pode excluir os seus próprios anúncios.")

    # Remove o arquivo físico da foto caso exista localmente
    imagem_url = getattr(produto, "imagem_url", "") or ""
    if "/api/fotos/" in imagem_url:
        nome_foto = imagem_url.split("/api/fotos/")[-1]
        caminho_foto = FOTOS_DIR / nome_foto
        if caminho_foto.exists():
            try:
                caminho_foto.unlink()
            except Exception as e:
                print(f"Aviso ao remover foto: {e}")

    # Remove do banco de dados
    try:
        db.delete(produto)
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Erro ao excluir anúncio: {exc}")

    # Invalida o cache no Redis
    if r:
        try:
            r.delete("catalogo_produtos")
        except Exception:
            pass

    return {"status": "sucesso", "message": f"Anúncio {item_id} removido com sucesso"}
@app.post("/api/reset-password")
async def proxy_reset_password(request: Request):
    dados = await request.json()
    print(f"[DEBUG PROXY] Recebido pedido de reset: {dados}")
    try:
        resp = requests.post(f"{AUTH_SERVICE_URL}/reset-password", json=dados, timeout=10.0)
        print(f"[DEBUG PROXY] Resposta do auth_service: status={resp.status_code}, body={resp.text}")
        return Response(
            content=resp.content,
            status_code=resp.status_code,
            media_type="application/json"
        )
    except Exception as exc:
        print(f"[DEBUG PROXY] Erro ao chamar auth_service: {exc}")
        raise HTTPException(status_code=500, detail=f"Erro de comunicacao com Auth: {exc}")
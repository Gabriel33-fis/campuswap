import pathlib
FOTOS_DIR = pathlib.Path('/app/uploads')
FOTOS_DIR.mkdir(parents=True, exist_ok=True)
import os
import io
import json
import uuid
from pathlib import Path
import requests
import redis
from fastapi import FastAPI, HTTPException, Depends, Request, Response, UploadFile, File, status
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import text
from prometheus_fastapi_instrumentator import Instrumentator
from minio import Minio

import database
from database import get_db
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


# Rota principal (Serve a página do frontend)
@app.get("/", response_class=HTMLResponse)
def pagina_inicial():
    caminho_html = Path(__file__).parent / "index.html"
    if caminho_html.exists():
        return HTMLResponse(content=caminho_html.read_text(encoding="utf-8"))
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


@app.get("/produtos")
@app.get("/api/produtos")
@app.get("/api/itens")
def listar_produtos(db: Session = Depends(get_db)):
    if r:
        try:
            cached = r.get("catalogo_produtos")
            if cached:
                return json.loads(cached)
        except Exception:
            pass

    produtos = db.query(models.Produto).all()
    lista = []
    for p in produtos:
        if hasattr(p, "to_dict"):
            lista.append(p.to_dict())
        else:
            lista.append({
                "id": p.id,
                "titulo": getattr(p, "titulo", getattr(p, "nome", "Item")),
                "descricao": getattr(p, "descricao", ""),
                "preco": getattr(p, "preco", 0.0),
                "modalidade": getattr(p, "modalidade", "venda"),
                "condicao": getattr(p, "condicao", "semi-novo"),
                "imagem_url": getattr(p, "imagem_url", None),
                "usuario_id": getattr(p, "usuario_id", None)
            })

    if r:
        try:
            r.setex("catalogo_produtos", 60, json.dumps(lista))
        except Exception:
            pass

    return lista


@app.post("/api/itens")
async def criar_item(request: Request, db: Session = Depends(get_db)):
    try:
        dados = await request.json()
        
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
async def upload_foto_item(item_id: int, arquivo: UploadFile = File(...), db: Session = Depends(get_db)):
    try:
        dados = await arquivo.read()
        extensao = "jpg"
        if arquivo.filename and "." in arquivo.filename:
            extensao = arquivo.filename.rsplit(".", 1)[1].lower()
            
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
import os
import io
import json
from pathlib import Path
import requests
import redis
from fastapi import FastAPI, HTTPException, Depends, Request, Response, status
from fastapi.responses import HTMLResponse
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

# Inicialização de tabelas
try:
    engine_obj = getattr(database, "engine", getattr(database, "motor", None))
    base_obj = getattr(database, "Base", getattr(models, "Base", None))
    if engine_obj and base_obj:
        base_obj.metadata.create_all(bind=engine_obj)
except Exception as e:
    print(f"Aviso tabelas catálogo: {e}")

app = FastAPI(title="CampusSwap - Catalogo Service")

Instrumentator().instrument(app).expose(app)


@app.get("/", response_class=HTMLResponse)
def pagina_inicial():
    caminho_html = Path(__file__).parent / "index.html"
    if caminho_html.exists():
        return HTMLResponse(content=caminho_html.read_text(encoding="utf-8"))
    return HTMLResponse(content="<h1>Erro: index.html nao encontrado dentro do catalogo_service</h1>", status_code=404)


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
                "imagem_url": getattr(p, "imagem_url", None),
                "usuario_id": getattr(p, "usuario_id", None)
            })

    if r:
        try:
            r.setex("catalogo_produtos", 60, json.dumps(lista))
        except Exception:
            pass

    return lista
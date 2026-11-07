"""Armazenamento de fotos no MinIO (S3)."""
import io
import os
import uuid

from fastapi import HTTPException, UploadFile
from starlette.concurrency import run_in_threadpool
from minio import Minio

# Ajuste os nomes das variáveis para os que existem no seu compose/stack do Portainer.
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "minio:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "minioadmin")
MINIO_BUCKET = os.getenv("MINIO_BUCKET", "campuswap")
MINIO_SECURE = os.getenv("MINIO_SECURE", "false").lower() == "true"

TIPOS_PERMITIDOS = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
TAMANHO_MAXIMO = 5 * 1024 * 1024  # 5 MB

_cliente = None


def cliente() -> Minio:
    global _cliente
    if _cliente is None:
        _cliente = Minio(MINIO_ENDPOINT, access_key=MINIO_ACCESS_KEY,
                         secret_key=MINIO_SECRET_KEY, secure=MINIO_SECURE)
    return _cliente


def _enviar(chave: str, dados: bytes, content_type: str):
    c = cliente()
    if not c.bucket_exists(MINIO_BUCKET):
        c.make_bucket(MINIO_BUCKET)
    c.put_object(MINIO_BUCKET, chave, io.BytesIO(dados), len(dados), content_type=content_type)


async def upload_imagem_item(arquivo: UploadFile, item_id: int) -> str:
    """Valida e envia a imagem. Retorna a chave do objeto no bucket."""
    extensao = TIPOS_PERMITIDOS.get(arquivo.content_type)
    if not extensao:
        raise HTTPException(status_code=415, detail="Formato não suportado. Envie JPG, PNG ou WebP.")
    dados = await arquivo.read()
    if len(dados) > TAMANHO_MAXIMO:
        raise HTTPException(status_code=413, detail="Imagem acima de 5 MB.")
    chave = f"itens/{item_id}/{uuid.uuid4().hex}{extensao}"
    try:
        await run_in_threadpool(_enviar, chave, dados, arquivo.content_type)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Falha ao enviar para o MinIO: {e}")
    return chave


def baixar_imagem(chave: str):
    """Abre o objeto no MinIO (resposta com .stream() e .headers)."""
    return cliente().get_object(MINIO_BUCKET, chave)
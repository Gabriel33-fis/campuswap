import os
from fastapi import UploadFile, HTTPException, status
from minio import Minio

MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "minio:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "minioadmin")
MINIO_BUCKET = os.getenv("MINIO_BUCKET", "campuswap-fotos")

# Inicializa o cliente MinIO
minio_client = Minio(
    MINIO_ENDPOINT,
    access_key=MINIO_ACCESS_KEY,
    secret_key=MINIO_SECRET_KEY,
    secure=False
)

# Cria o bucket se não existir
if not minio_client.bucket_exists(MINIO_BUCKET):
    minio_client.make_bucket(MINIO_BUCKET)

TIPOS_PERMITIDOS = ["image/jpeg", "image/png", "image/webp"]
TAMANHO_MAXIMO = 2 * 1024 * 1024  # 2 MB

async def upload_imagem_item(arquivo: UploadFile, item_id: int) -> str:
    # 1. Validação de formato
    if arquivo.content_type not in TIPOS_PERMITIDOS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Formato inválido. Apenas JPEG, PNG e WEBP são permitidos."
        )
    
    # 2. Leitura do conteúdo e validação de tamanho
    conteudo = await arquivo.read()
    if len(conteudo) > TAMANHO_MAXIMO:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="O ficheiro excede o tamanho máximo permitido de 2 MB."
        )
    
    # 3. Definição do nome do objeto no bucket
    extensao = arquivo.filename.split(".")[-1]
    object_name = f"itens/item_{item_id}_{arquivo.filename}"
    
    # 4. Envio para o bucket
    arquivo.file.seek(0)
    minio_client.put_object(
        bucket_name=MINIO_BUCKET,
        object_name=object_name,
        data=arquivo.file,
        length=len(conteudo),
        content_type=arquivo.content_type
    )
    
    return object_name
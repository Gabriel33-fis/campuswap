"""Configuração dos testes do catalogo_service: SQLite temporário, sem Redis/MinIO/auth reais."""
import os
import sys
import tempfile
from pathlib import Path

_tmp = tempfile.mkdtemp(prefix="campuswap_cat_")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/catalogo.db"
os.environ["FOTOS_DIR"] = f"{_tmp}/uploads"
os.environ["JWT_SECRET"] = "segredo-de-teste"
os.environ["REDIS_HOST"] = "127.0.0.1"      # recusa na hora: o cache é opcional
os.environ["MINIO_ENDPOINT"] = "127.0.0.1:1"  # idem
os.environ["LIMITE_FREE_ANUNCIOS"] = "3"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

import database
import main

main.r = None  # sem Redis nos testes: o cache é opcional e o código já trata essa ausência


def token(usuario_id=1, papel="aluno"):
    payload = {"usuario_id": usuario_id, "nome": f"Usuario {usuario_id}", "email": f"u{usuario_id}@x.com", "papel": papel}
    return jwt.encode(payload, "segredo-de-teste", algorithm="HS256")


def auth(usuario_id=1, papel="aluno"):
    return {"Authorization": f"Bearer {token(usuario_id, papel)}"}


@pytest.fixture()
def client():
    database.Base.metadata.drop_all(bind=database.engine)
    database.Base.metadata.create_all(bind=database.engine)
    with database.engine.begin() as con:  # a tabela "usuarios" pertence ao auth_service; aqui só o necessário
        con.execute(text("DROP TABLE IF EXISTS usuarios"))
        con.execute(text("CREATE TABLE usuarios (id INTEGER PRIMARY KEY, plano TEXT, status_assinatura TEXT)"))
    return TestClient(main.app)


@pytest.fixture()
def tornar_pro():
    def _tornar(usuario_id):
        with database.engine.begin() as con:
            con.execute(text("INSERT OR REPLACE INTO usuarios (id, plano, status_assinatura) VALUES (:i, 'pro', 'active')"), {"i": usuario_id})
    return _tornar


@pytest.fixture()
def anunciar(client):
    def _anunciar(usuario_id=1, titulo="Cálculo vol. 1", **extra):
        corpo = {"titulo": titulo, "descricao": "Livro em bom estado", "preco": 40, "modalidade": "venda", **extra}
        return client.post("/api/itens", json=corpo, headers=auth(usuario_id))
    return _anunciar

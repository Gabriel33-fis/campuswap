"""Configuração dos testes do auth_service: banco SQLite temporário, sem serviços externos."""
import os
import sys
import tempfile
from pathlib import Path

_tmp = tempfile.mkdtemp(prefix="campuswap_auth_")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/auth.db"
os.environ["JWT_SECRET"] = "segredo-de-teste"
os.environ["LOG_SERVICE_URL"] = "http://127.0.0.1:9"  # recusa na hora; o log é "best effort"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

import database
import main
import models


@pytest.fixture()
def client():
    database.Base.metadata.drop_all(bind=database.engine)
    database.Base.metadata.create_all(bind=database.engine)
    return TestClient(main.app)


@pytest.fixture()
def db():
    sessao = database.SessionLocal()
    yield sessao
    sessao.close()


USUARIO = {"nome": "Ana Teste", "email": "ana@fatec.sp.gov.br", "senha": "senha123"}


@pytest.fixture()
def cadastrar(client):
    def _cadastrar(**extra):
        return client.post("/register", json={**USUARIO, **extra})
    return _cadastrar


def token_de(client, email=USUARIO["email"], senha=USUARIO["senha"]):
    resposta = client.post("/login", json={"email": email, "senha": senha})
    return resposta.json()["access_token"]

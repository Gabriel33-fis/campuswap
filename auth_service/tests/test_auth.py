from conftest import USUARIO, token_de
import models


def test_cadastro_cria_usuario_aluno_free(client, cadastrar, db):
    resposta = cadastrar()
    assert resposta.status_code == 201
    usuario = db.query(models.Usuario).filter_by(email=USUARIO["email"]).one()
    assert usuario.papel == "aluno"
    assert usuario.plano == "free"
    assert usuario.senha_hash != USUARIO["senha"]  # a senha é guardada com hash


def test_cadastro_nao_permite_escolher_papel_admin(client, cadastrar, db):
    resposta = cadastrar(papel="admin")
    assert resposta.status_code == 201
    assert db.query(models.Usuario).one().papel == "aluno"


def test_cadastro_com_email_repetido_e_rejeitado(client, cadastrar):
    assert cadastrar().status_code == 201
    repetido = cadastrar()
    assert repetido.status_code == 400
    assert "cadastrado" in repetido.json()["detail"]


def test_login_correto_devolve_token_e_dados_do_plano(client, cadastrar):
    cadastrar()
    resposta = client.post("/login", json={"email": USUARIO["email"], "senha": USUARIO["senha"]})
    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["token_type"] == "bearer"
    assert corpo["access_token"]
    assert corpo["papel"] == "aluno"
    assert corpo["is_premium"] is False


def test_login_com_senha_errada_retorna_401(client, cadastrar):
    cadastrar()
    resposta = client.post("/login", json={"email": USUARIO["email"], "senha": "errada"})
    assert resposta.status_code == 401


def test_me_exige_token_e_retorna_o_proprio_usuario(client, cadastrar):
    cadastrar()
    assert client.get("/me").status_code in (401, 403)  # sem token
    resposta = client.get("/me", headers={"Authorization": f"Bearer {token_de(client)}"})
    assert resposta.status_code == 200
    assert resposta.json()["email"] == USUARIO["email"]


def test_metricas_admin_negadas_para_aluno(client, cadastrar):
    cadastrar()
    resposta = client.get("/api/admin/metricas", headers={"Authorization": f"Bearer {token_de(client)}"})
    assert resposta.status_code == 403


def test_metricas_admin_liberadas_para_admin(client, cadastrar, db):
    cadastrar()
    db.query(models.Usuario).update({"papel": "admin"})
    db.commit()
    resposta = client.get("/api/admin/metricas", headers={"Authorization": f"Bearer {token_de(client)}"})
    assert resposta.status_code == 200
    assert "usuarios_por_plano" in resposta.json()

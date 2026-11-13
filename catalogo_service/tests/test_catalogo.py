from conftest import auth


def test_pagina_inicial_e_arquivos_estaticos(client):
    html = client.get("/")
    assert html.status_code == 200
    assert "CampuSwap" in html.text
    assert "/static/app.js?v=" in html.text
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/app.css").status_code == 200


def test_botao_de_tema_claro_escuro_presente(client):
    html = client.get("/").text
    assert 'id="btn-tema-claro"' in html and 'id="btn-tema-escuro"' in html
    assert 'data-theme="light"' in client.get("/static/app.css").text
    assert "function aplicarTema" in client.get("/static/app.js").text


def test_estaticos_declaram_utf8(client):
    for arquivo in ("app.js", "app.css"):
        assert "charset=utf-8" in client.get(f"/static/{arquivo}").headers["content-type"]


def test_criar_anuncio_sem_login_retorna_401(client):
    resposta = client.post("/api/itens", json={"titulo": "Calculadora"})
    assert resposta.status_code == 401


def test_criar_anuncio_logado_aparece_na_listagem(client, anunciar):
    criado = anunciar(titulo="Calculadora Casio")
    assert criado.status_code == 200
    lista = client.get("/api/itens?pagina=1").json()
    assert lista["total"] == 1
    assert lista["itens"][0]["titulo"] == "Calculadora Casio"
    assert lista["itens"][0]["usuario_id"] == 1  # o dono vem do token


def test_plano_free_tem_limite_de_anuncios_por_mes(client, anunciar):
    for i in range(3):
        assert anunciar(titulo=f"Item {i}").status_code == 200
    quarto = anunciar(titulo="Item 4")
    assert quarto.status_code == 402
    assert "Limite do plano Free" in quarto.json()["detail"]


def test_plano_pro_anuncia_sem_limite(client, anunciar, tornar_pro):
    tornar_pro(1)
    for i in range(5):
        assert anunciar(titulo=f"Item {i}").status_code == 200
    assert client.get("/api/itens?pagina=1").json()["total"] == 5


def test_paginacao_divide_os_resultados(client, anunciar, tornar_pro):
    tornar_pro(1)
    for i in range(5):
        anunciar(titulo=f"Item {i}")
    p1 = client.get("/api/itens?pagina=1&limite=2").json()
    p3 = client.get("/api/itens?pagina=3&limite=2").json()
    assert (len(p1["itens"]), p1["total"], p1["paginas"]) == (2, 5, 3)
    assert len(p3["itens"]) == 1
    assert client.get("/api/itens?limite=999&pagina=1").status_code == 422  # limite máximo é 50


def test_sem_parametros_mantem_lista_completa_compativel(client, anunciar):
    anunciar()
    lista = client.get("/api/itens").json()
    assert isinstance(lista, list) and len(lista) == 1


def test_filtro_por_modalidade_e_busca(client, anunciar, tornar_pro):
    tornar_pro(1)
    anunciar(titulo="Cálculo", modalidade="venda")
    anunciar(titulo="Apostila de Algoritmos", modalidade="troca")
    assert client.get("/api/itens?pagina=1&modalidade=troca").json()["total"] == 1
    assert client.get("/api/itens?pagina=1&q=alg").json()["itens"][0]["titulo"] == "Apostila de Algoritmos"


def test_anuncio_de_assinante_pro_vem_primeiro_com_destaque(client, anunciar, tornar_pro):
    anunciar(usuario_id=1, titulo="Do aluno free")
    tornar_pro(2)
    anunciar(usuario_id=2, titulo="Do assinante pro")
    itens = client.get("/api/itens?pagina=1").json()["itens"]
    assert itens[0]["titulo"] == "Do assinante pro" and itens[0]["destaque"] is True
    assert itens[1]["destaque"] is False


def test_so_o_dono_exclui_o_anuncio(client, anunciar):
    item_id = anunciar(usuario_id=1).json()["id"]
    assert client.delete(f"/api/itens/{item_id}").status_code == 401           # sem login
    assert client.delete(f"/api/itens/{item_id}", headers=auth(2)).status_code == 403  # outro aluno
    assert client.delete(f"/api/itens/{item_id}", headers=auth(1)).status_code == 200  # dono
    assert client.get("/api/itens?pagina=1").json()["total"] == 0


def test_admin_pode_excluir_anuncio_de_outro(client, anunciar):
    item_id = anunciar(usuario_id=1).json()["id"]
    assert client.delete(f"/api/itens/{item_id}", headers=auth(9, "admin")).status_code == 200


def test_metricas_premium_negadas_no_plano_free(client):
    assert client.get("/api/metricas/premium").status_code == 401
    resposta = client.get("/api/metricas/premium", headers=auth(1))
    assert resposta.status_code == 403
    assert "Premium" in resposta.json()["detail"]


def test_metricas_premium_liberadas_para_assinante(client, anunciar, tornar_pro):
    tornar_pro(1)
    anunciar(titulo="A", preco=30, modalidade="venda")
    anunciar(titulo="B", preco=20, modalidade="troca")
    dados = client.get("/api/metricas/premium", headers=auth(1)).json()
    assert dados["total_anuncios"] == 2
    assert dados["valor_total"] == 50
    assert dados["por_modalidade"]["troca"] == 1
    assert len(dados["meus_por_dia"]) == 7 and dados["meus_por_dia"][-1]["total"] == 2

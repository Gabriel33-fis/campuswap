import os
import json
import urllib.request
import urllib.error
from typing import List, Optional
from pydantic import BaseModel
from fastapi import FastAPI, HTTPException, Depends, Request, Response, status, Query, File, UploadFile
from fastapi.responses import HTMLResponse, StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import text
from database import get_db, engine, Base
import models
import schemas
import auth_guard
import requests  
from prometheus_fastapi_instrumentator import Instrumentator
import storage

LOG_SERVICE_URL = os.getenv("LOG_SERVICE_URL", "http://log_service:6000")
AUTH_SERVICE_URL = os.getenv("AUTH_SERVICE_URL", "http://auth_service:5000")
TMDB_API_KEY = os.getenv("TMDB_API_KEY", "")

try:
    Base.metadata.create_all(bind=engine)
except Exception as e:
    print(f"Aviso ao inicializar tabelas catalogo: {e}")

# Garante a existência do bucket dedicado no MinIO durante a inicialização
storage.assegurar_bucket()

app = FastAPI(
    title="Catálogo Tom Hanks & Microsserviços",
    description="Documentação oficial das APIs de Catálogo, Autenticação RBAC, Armazenamento de Objetos (MinIO) e Auditoria.",
    version="1.0.0"
)

# Instrumentação Prometheus para expor métricas na rota /metrics
Instrumentator().instrument(app).expose(app)

@app.get("/health", tags=["Observabilidade"])
def health_check(response: Response, db: Session = Depends(get_db)):
    """
    Readiness probe real: valida conexão com o banco de dados.
    Retorna 200 se saudável, 503 se o banco estiver indisponível.
    """
    try:
        db.execute(text("SELECT 1"))
        return {
            "status": "healthy",
            "service": "catalogo_service",
            "dependencies": {
                "database": "up"
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

# --- SCHEMAS PYDANTIC ---

class LoginSchema(BaseModel):
    email: str
    senha: str

    class Config:
        json_schema_extra = {
            "example": {
                "email": "aluno@teste.com",
                "senha": "123"
            }
        }

class RegisterSchema(BaseModel):
    nome: str
    email: str
    senha: str
    papel: Optional[str] = "usuario"

    class Config:
        json_schema_extra = {
            "example": {
                "nome": "Gabriel Graciano",
                "email": "gabriel@teste.com",
                "senha": "senhaSegura123",
                "papel": "usuario"
            }
        }

class ForgotPasswordSchema(BaseModel):
    email: str

    class Config:
        json_schema_extra = {
            "example": {
                "email": "usuario@teste.com"
            }
        }

class ResetPasswordSchema(BaseModel):
    token: str
    nova_senha: str

    class Config:
        json_schema_extra = {
            "example": {
                "token": "token-recebido-via-email-mailtrap",
                "nova_senha": "novaSenhaSegura456"
            }
        }

class PerfilUpdateSchema(BaseModel):
    bio: Optional[str] = ""

class MensagemResposta(BaseModel):
    message: str

class Erro400Resposta(BaseModel):
    detail: str

class Erro401Resposta(BaseModel):
    detail: str

class Erro403Resposta(BaseModel):
    detail: str

class Erro404Resposta(BaseModel):
    detail: str

RESPOSTAS_ERRO_AUTH = {
    401: {"model": Erro401Resposta, "description": "Token JWT ausente, inválido ou expirado."}
}

RESPOSTAS_ERRO_RBAC = {
    401: {"model": Erro401Resposta, "description": "Token JWT ausente ou inválido."},
    403: {"model": Erro403Resposta, "description": "Acesso negado: privilégios insuficientes."}
}

def extrair_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "desconhecido"

def registrar_log(usuario_id, acao, ip=None, detalhes=None):
    try:
        requests.post(
            f"{LOG_SERVICE_URL}/logs",
            json={
                "usuario_id": usuario_id,
                "acao": acao,
                "ip_origem": ip,
                "detalhes": detalhes
            },
            timeout=1.0
        )
    except Exception as e:
        print(f"Aviso log_service: {e}")

def chamar_auth_service(endpoint: str, payload: dict):
    url = f"{AUTH_SERVICE_URL}{endpoint}"
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(
        url,
        data=data,
        headers={'Content-Type': 'application/json'},
        method='POST'
    )
    try:
        with urllib.request.urlopen(req) as response:
            res_body = response.read().decode('utf-8')
            return response.status, json.loads(res_body) if res_body else {}
    except urllib.error.HTTPError as e:
        err_body = e.read().decode('utf-8')
        try:
            detail = json.loads(err_body).get("detail", "Erro no serviço de autenticação")
        except Exception:
            detail = err_body or "Erro no serviço de autenticação"
        raise HTTPException(status_code=e.code, detail=detail)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Falha de conexão com Auth Service: {str(e)}")

HTML_PAGE = """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>🎬 Catálogo Tom Hanks</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; }
        body { background-color: #141414; color: #fff; min-height: 100vh; display: flex; flex-direction: column; }
        nav { background: #000; padding: 15px 30px; display: flex; justify-content: space-between; align-items: center; border-bottom: 2px solid #e50914; }
        .nav-brand { display: flex; align-items: center; gap: 20px; }
        nav h1 { color: #e50914; font-size: 1.5rem; }
        .nav-links { display: flex; gap: 15px; }
        .nav-link { color: #aaa; text-decoration: none; font-weight: bold; cursor: pointer; font-size: 0.95rem; }
        .nav-link:hover, .nav-link.active { color: #fff; border-bottom: 2px solid #e50914; }
        .user-panel { display: flex; align-items: center; gap: 12px; }

        /* Estilos do Plano Premium (Atividade 7) */
        .badge-premium { background: #ffd700; color: #141414; padding: 4px 10px; border-radius: 4px; font-weight: bold; font-size: 0.8rem; letter-spacing: 0.5px; }
        .btn-premium { background: linear-gradient(135deg, #ffd700, #ffb703); color: #000; border: none; padding: 6px 12px; border-radius: 4px; font-weight: bold; font-size: 0.85rem; cursor: pointer; transition: opacity 0.2s; }
        .btn-premium:hover { opacity: 0.9; }

        .btn-logout { background: #333; color: white; border: none; padding: 6px 12px; border-radius: 4px; cursor: pointer; }
        .btn-logout:hover { background: #e50914; }
        .main-container { padding: 30px 20px; max-width: 1200px; margin: 0 auto; width: 100%; flex: 1; }
        .auth-card { max-width: 400px; margin: 40px auto; background: #1f1f1f; padding: 30px; border-radius: 8px; box-shadow: 0 4px 15px rgba(0,0,0,0.5); }
        .auth-card h2 { margin-bottom: 20px; color: #fff; text-align: center; }
        .form-group { margin-bottom: 15px; }
        .form-group label { display: block; margin-bottom: 5px; color: #aaa; font-size: 0.9rem; }
        .form-group input, .form-group textarea { width: 100%; padding: 12px; background: #333; border: 1px solid #444; border-radius: 4px; color: white; outline: none; }
        .form-group input:focus, .form-group textarea:focus { border-color: #e50914; }
        button.btn-primary { width: 100%; padding: 12px; background: #e50914; border: none; border-radius: 4px; color: white; font-weight: bold; cursor: pointer; font-size: 1rem; margin-top: 10px; }
        button.btn-primary:hover { background: #f40612; }
        .links-group { margin-top: 15px; display: flex; justify-content: space-between; font-size: 0.85rem; }
        .link-text { color: #aaa; text-decoration: none; cursor: pointer; }
        .link-text:hover { color: #fff; text-decoration: underline; }
        .hidden { display: none !important; }
        .msg { padding: 10px; border-radius: 4px; margin-top: 15px; font-size: 0.9rem; text-align: center; }
        .msg.success { background: rgba(46, 125, 50, 0.2); border: 1px solid #2e7d32; color: #4caf50; }
        .msg.error { background: rgba(198, 40, 40, 0.2); border: 1px solid #c62828; color: #ef5350; }
        .movies-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 25px; margin-top: 25px; }
        .movie-card { background: #1f1f1f; border-radius: 8px; overflow: hidden; display: flex; flex-direction: column; box-shadow: 0 4px 10px rgba(0,0,0,0.4); }
        .movie-card img { width: 100%; height: 320px; object-fit: cover; }
        .movie-info { padding: 15px; flex: 1; display: flex; flex-direction: column; }
        .movie-title { font-weight: bold; font-size: 1.05rem; margin-bottom: 6px; color: #fff; }
        .movie-release { color: #aaa; font-size: 0.85rem; margin-bottom: 12px; }
        .movie-overview { font-size: 0.8rem; color: #bbb; line-height: 1.4; max-height: 80px; overflow-y: auto; margin-bottom: 15px; flex: 1; }
        .movie-actions { display: flex; gap: 8px; margin-top: auto; }
        .btn-action { flex: 1; padding: 8px 0; border: none; border-radius: 4px; cursor: pointer; font-size: 0.85rem; font-weight: bold; }
        .btn-fav { background: #ffb703; color: #000; }
        .btn-fav:hover { background: #fb8500; }
        .btn-fav-remove { background: #d90429; color: #fff; }
        .btn-fav-remove:hover { background: #ef233c; }
        .btn-com { background: #0284c7; color: #fff; }
        .btn-com:hover { background: #0369a1; }
        
        /* ESTILOS DE PERFIL (ATIVIDADE 6) */
        .profile-container { background: #1f1f1f; border-radius: 8px; padding: 30px; margin-top: 20px; box-shadow: 0 4px 15px rgba(0,0,0,0.5); }
        .profile-header { display: flex; gap: 30px; align-items: center; border-bottom: 1px solid #333; padding-bottom: 25px; flex-wrap: wrap; }
        .avatar-box { width: 140px; height: 140px; border-radius: 50%; overflow: hidden; border: 3px solid #e50914; background: #2a2a2a; display: flex; align-items: center; justify-content: center; flex-shrink: 0; }
        .avatar-box img { width: 100%; height: 100%; object-fit: cover; }
        .profile-meta { flex: 1; min-width: 250px; }
        .profile-meta h2 { font-size: 1.8rem; margin-bottom: 5px; }
        .profile-meta p.role-badge { color: #ffb703; font-weight: bold; font-size: 0.9rem; margin-bottom: 12px; }
        .profile-form { margin-top: 20px; }
        .file-upload-area { margin-top: 15px; padding: 15px; background: #2a2a2a; border-radius: 6px; border: 1px dashed #555; }

        .modal { position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.75); display: flex; justify-content: center; align-items: center; z-index: 1000; }
        .modal-content { background: #1f1f1f; padding: 25px; border-radius: 8px; width: 90%; max-width: 550px; max-height: 85vh; display: flex; flex-direction: column; }
        .modal-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 15px; border-bottom: 1px solid #333; padding-bottom: 10px; }
        .modal-close { background: none; border: none; color: #aaa; font-size: 1.5rem; cursor: pointer; }
        .comments-list { flex: 1; overflow-y: auto; margin-bottom: 15px; max-height: 250px; }
        .comment-item { background: #2a2a2a; padding: 10px; border-radius: 4px; margin-bottom: 10px; display: flex; justify-content: space-between; align-items: flex-start; }
        .comment-body { flex: 1; }
        .comment-user { font-size: 0.85rem; font-weight: bold; color: #e50914; margin-bottom: 4px; }
        .comment-text { font-size: 0.9rem; color: #ddd; }
        .btn-del-comment { background: #dc2626; color: white; border: none; padding: 4px 8px; border-radius: 4px; cursor: pointer; font-size: 0.75rem; margin-left: 10px; }
        .btn-del-comment:hover { background: #b91c1c; }
    </style>
</head>
<body>
    <nav>
        <div class="nav-brand">
            <h1>🎬 Catálogo Tom Hanks</h1>
            <div id="nav-tabs" class="nav-links hidden">
                <a id="tab-cat" class="nav-link active" onclick="mostrarAba('catalogo')">Catálogo</a>
                <a id="tab-fav" class="nav-link" onclick="mostrarAba('favoritos')">Meus Favoritos</a>
                <a id="tab-perfil" class="nav-link" onclick="mostrarAba('perfil')">Meu Perfil</a>
            </div>
        </div>
        <div class="user-panel">
            <span id="nav-user-info">Não conectado</span>
            <span id="nav-badge-premium" class="badge-premium hidden">👑 PREMIUM</span>
            <button id="btn-assinar-premium" class="btn-premium hidden" onclick="iniciarCheckoutStripe()">⭐ Assinar Premium</button>
            <button id="btn-logout" class="btn-logout hidden" onclick="logout()">Sair</button>
        </div>
    </nav>
    <div class="main-container">
        <!-- AVISO DE RETORNO DO STRIPE -->
        <div id="msg-global" class="msg hidden"></div>

        <!-- LOGIN -->
        <div id="box-login" class="auth-card">
            <h2>Login</h2>
            <div class="form-group">
                <label>E-mail:</label>
                <input type="email" id="login-email" placeholder="seu@email.com">
            </div>
            <div class="form-group">
                <label>Senha:</label>
                <input type="password" id="login-senha" placeholder="Sua senha">
            </div>
            <button class="btn-primary" onclick="fazerLogin()">Entrar</button>
            <div class="links-group">
                <a class="link-text" onclick="alternarTela('box-forgot')">Esqueci a senha</a>
                <a class="link-text" onclick="alternarTela('box-register')">Cadastre-se</a>
            </div>
            <div id="msg-login" class="msg hidden"></div>
        </div>

        <!-- CADASTRO -->
        <div id="box-register" class="auth-card hidden">
            <h2>Criar Conta</h2>
            <div class="form-group">
                <label>Nome:</label>
                <input type="text" id="reg-nome" placeholder="Seu nome">
            </div>
            <div class="form-group">
                <label>E-mail:</label>
                <input type="email" id="reg-email" placeholder="seu@email.com">
            </div>
            <div class="form-group">
                <label>Senha:</label>
                <input type="password" id="reg-senha" placeholder="Crie uma senha">
            </div>
            <button class="btn-primary" onclick="fazerCadastro()">Cadastrar</button>
            <div class="links-group" style="justify-content: center;">
                <a class="link-text" onclick="alternarTela('box-login')">Voltar ao Login</a>
            </div>
            <div id="msg-reg" class="msg hidden"></div>
        </div>

        <!-- ESQUECI A SENHA -->
        <div id="box-forgot" class="auth-card hidden">
            <h2>Recuperar Senha</h2>
            <p style="color: #aaa; font-size: 0.85rem; margin-bottom: 15px; text-align: center;">Informe o e-mail para receber as instruções.</p>
            <div class="form-group">
                <label>E-mail cadastrado:</label>
                <input type="email" id="forgot-email" placeholder="seu@email.com">
            </div>
            <button class="btn-primary" onclick="solicitarRecuperacao()">Enviar E-mail</button>
            <div class="links-group">
                <a class="link-text" onclick="alternarTela('box-reset')">Já tenho um token</a>
                <a class="link-text" onclick="alternarTela('box-login')">Voltar ao Login</a>
            </div>
            <div id="msg-forgot" class="msg hidden"></div>
        </div>

        <!-- REDEFINIR SENHA -->
        <div id="box-reset" class="auth-card hidden">
            <h2>Redefinir Senha</h2>
            <div class="form-group">
                <label>Token recebido:</label>
                <input type="text" id="reset-token" placeholder="Cole o token recebido">
            </div>
            <div class="form-group">
                <label>Nova Senha:</label>
                <input type="password" id="reset-nova-senha" placeholder="Digite a nova senha">
            </div>
            <button class="btn-primary" onclick="redefinirSenha()">Alterar Senha</button>
            <div class="links-group" style="justify-content: center;">
                <a class="link-text" onclick="alternarTela('box-login')">Voltar ao Login</a>
            </div>
            <div id="msg-reset" class="msg hidden"></div>
        </div>

        <!-- CATÁLOGO GERAL -->
        <div id="box-catalogo" class="hidden">
            <h2>Catálogo de Filmes com Tom Hanks</h2>
            <div id="movies-container" class="movies-grid"></div>
        </div>

        <!-- ABA MEUS FAVORITOS -->
        <div id="box-favoritos" class="hidden">
            <h2>Meus Filmes Favoritos ⭐</h2>
            <div id="fav-movies-container" class="movies-grid"></div>
        </div>

        <!-- ABA MEU PERFIL (ATIVIDADE 6) -->
        <div id="box-perfil" class="hidden">
            <div class="profile-container">
                <div class="profile-header">
                    <div class="avatar-box">
                        <img id="profile-avatar" src="https://via.placeholder.com/140x140?text=Foto" alt="Foto de Perfil">
                    </div>
                    <div class="profile-meta">
                        <h2 id="profile-name">Nome do Usuário</h2>
                        <p class="role-badge" id="profile-role">Papel: Usuário</p>
                        <p id="profile-bio-text" style="color: #ccc; font-style: italic;">Nenhuma biografia adicionada.</p>
                    </div>
                </div>

                <div class="profile-form">
                    <div class="form-group">
                        <label>Editar Biografia:</label>
                        <textarea id="edit-bio-input" rows="3" placeholder="Conte sobre você ou seus filmes favoritos..."></textarea>
                    </div>
                    <button class="btn-primary" style="width: auto; padding: 10px 20px;" onclick="salvarBio()">Salvar Biografia</button>

                    <div class="file-upload-area">
                        <label>Atualizar Foto de Perfil (Envio direto para MinIO Storage):</label>
                        <p style="color: #888; font-size: 0.8rem; margin-bottom: 8px;">Formatos aceitos: JPG, PNG, WEBP (Máx. 2MB)</p>
                        <input type="file" id="foto-input" accept="image/png, image/jpeg, image/webp" style="margin-bottom: 10px;">
                        <button class="btn-primary" style="width: auto; padding: 10px 20px; background: #0284c7;" onclick="enviarFotoPerfil()">Fazer Upload de Foto</button>
                    </div>
                </div>

                <div style="margin-top: 35px;">
                    <h3>Filmes no Meu Perfil ⭐</h3>
                    <div id="profile-favs-container" class="movies-grid"></div>
                </div>
            </div>
        </div>
    </div>

    <!-- MODAL DE COMENTÁRIOS -->
    <div id="modal-comentarios" class="modal hidden">
        <div class="modal-content">
            <div class="modal-header">
                <h3 id="modal-movie-title">Comentários</h3>
                <button class="modal-close" onclick="fecharModalComentarios()">&times;</button>
            </div>
            <div id="comments-list" class="comments-list">
                <p style="color:#aaa;">Carregando comentários...</p>
            </div>
            <div class="form-group">
                <textarea id="novo-comentario-texto" rows="3" placeholder="Escreva seu comentário sobre o filme..."></textarea>
            </div>
            <button class="btn-primary" onclick="enviarComentario()">Enviar Comentário</button>
        </div>
    </div>

    <script>
        let filmeAtualComentario = null;
        let usuarioIdAtual = null;

        function alternarTela(id) {
            ['box-login', 'box-register', 'box-forgot', 'box-reset', 'box-catalogo', 'box-favoritos', 'box-perfil'].forEach(b => {
                const el = document.getElementById(b);
                if (el) el.classList.add('hidden');
            });
            document.getElementById(id).classList.remove('hidden');
        }

        function mostrarAba(aba) {
            ['tab-cat', 'tab-fav', 'tab-perfil'].forEach(t => {
                const el = document.getElementById(t);
                if (el) el.classList.remove('active');
            });

            if (aba === 'catalogo') {
                document.getElementById('tab-cat').classList.add('active');
                alternarTela('box-catalogo');
                carregarFilmes();
            } else if (aba === 'favoritos') {
                document.getElementById('tab-fav').classList.add('active');
                alternarTela('box-favoritos');
                carregarFavoritos();
            } else if (aba === 'perfil') {
                document.getElementById('tab-perfil').classList.add('active');
                alternarTela('box-perfil');
                carregarMeuPerfil();
            }
        }

        function exibirAviso(elemId, texto, ehErro) {
            const el = document.getElementById(elemId);
            el.className = 'msg ' + (ehErro ? 'error' : 'success');
            el.innerText = texto;
            el.classList.remove('hidden');
        }

        function atualizarUIStatusPremium(isPremium) {
            const badge = document.getElementById('nav-badge-premium');
            const btnAssinar = document.getElementById('btn-assinar-premium');
            if (isPremium) {
                badge.classList.remove('hidden');
                btnAssinar.classList.add('hidden');
            } else {
                badge.classList.add('hidden');
                btnAssinar.classList.remove('hidden');
            }
        }

        async function carregarFilmes() {
            const token = localStorage.getItem('token');
            const container = document.getElementById('movies-container');
            container.innerHTML = '<p style="color:#aaa;">Carregando filmes...</p>';
            try {
                const res = await fetch('/api/filmes', {
                    headers: { 'Authorization': `Bearer ${token}` }
                });
                if (!res.ok) throw new Error('Falha ao obter catálogo');
                const filmes = await res.json();
                container.innerHTML = '';
                filmes.forEach(f => {
                    const card = document.createElement('div');
                    card.className = 'movie-card';
                    card.innerHTML = `
                        <img src="${f.poster_path ? 'https://image.tmdb.org/t/p/w500' + f.poster_path : 'https://via.placeholder.com/300x450?text=Sem+Poster'}" alt="${f.title}">
                        <div class="movie-info">
                            <div class="movie-title">${f.title}</div>
                            <div class="movie-release">📅 ${f.release_date || 'N/A'}</div>
                            <div class="movie-overview">${f.overview || 'Sem descrição disponível.'}</div>
                            <div class="movie-actions">
                                <button class="btn-action btn-fav" onclick="favoritar(${f.id}, '${f.title.replace(/'/g, "\\\\'")}', '${f.poster_path || ''}')">⭐ Favorito</button>
                                <button class="btn-action btn-com" onclick="abrirModalComentarios(${f.id}, '${f.title.replace(/'/g, "\\\\'")}')">💬 Comentar</button>
                            </div>
                        </div>
                    `;
                    container.appendChild(card);
                });
            } catch (err) {
                container.innerHTML = `<p style="color:#ef5350;">Erro ao carregar catálogo: ${err.message}</p>`;
            }
        }

        async function carregarFavoritos() {
            const token = localStorage.getItem('token');
            const container = document.getElementById('fav-movies-container');
            container.innerHTML = '<p style="color:#aaa;">Carregando favoritos...</p>';
            try {
                const res = await fetch('/api/favoritos', {
                    headers: { 'Authorization': `Bearer ${token}` }
                });
                if (!res.ok) throw new Error('Falha ao carregar favoritos');
                const favoritos = await res.json();
                if (favoritos.length === 0) {
                    container.innerHTML = '<p style="color:#888;">Você ainda não favoritou nenhum filme.</p>';
                    return;
                }
                container.innerHTML = '';
                favoritos.forEach(f => {
                    const card = document.createElement('div');
                    card.className = 'movie-card';
                    card.innerHTML = `
                        <img src="${f.poster_path ? 'https://image.tmdb.org/t/p/w500' + f.poster_path : 'https://via.placeholder.com/300x450?text=Sem+Poster'}" alt="${f.titulo}">
                        <div class="movie-info">
                            <div class="movie-title">${f.titulo}</div>
                            <div class="movie-release">⭐ Salvo nos seus favoritos</div>
                            <div class="movie-actions">
                                <button class="btn-action btn-fav-remove" onclick="removerFavorito(${f.id})">❌ Remover</button>
                                <button class="btn-action btn-com" onclick="abrirModalComentarios(${f.tmdb_movie_id}, '${f.titulo.replace(/'/g, "\\\\'")}')">💬 Comentar</button>
                            </div>
                        </div>
                    `;
                    container.appendChild(card);
                });
            } catch (err) {
                container.innerHTML = `<p style="color:#ef5350;">Erro: ${err.message}</p>`;
            }
        }

        /* FUNÇÕES DE PERFIL E OBJECT STORAGE (ATIVIDADE 6) */
        async function carregarMeuPerfil() {
            const token = localStorage.getItem('token');
            try {
                const res = await fetch('/api/perfil/me', {
                    headers: { 'Authorization': `Bearer ${token}` }
                });
                if (!res.ok) throw new Error('Falha ao obter perfil');
                const data = await res.json();

                usuarioIdAtual = data.usuario_id;
                document.getElementById('profile-name').innerText = data.nome;
                document.getElementById('profile-role').innerText = `ID: #${data.usuario_id} | Papel: ${localStorage.getItem('papel') || 'usuario'}`;
                document.getElementById('profile-bio-text').innerText = data.bio || 'Nenhuma biografia adicionada.';
                document.getElementById('edit-bio-input').value = data.bio || '';

                const avatarImg = document.getElementById('profile-avatar');
                if (data.foto_url) {
                    avatarImg.src = data.foto_url + '?t=' + new Date().getTime();
                } else {
                    avatarImg.src = 'https://via.placeholder.com/140x140?text=Sem+Foto';
                }

                const favsContainer = document.getElementById('profile-favs-container');
                favsContainer.innerHTML = '';
                if (!data.favoritos || data.favoritos.length === 0) {
                    favsContainer.innerHTML = '<p style="color: #888;">Nenhum favorito cadastrado.</p>';
                } else {
                    data.favoritos.forEach(f => {
                        const card = document.createElement('div');
                        card.className = 'movie-card';
                        card.innerHTML = `
                            <img src="${f.poster_path ? 'https://image.tmdb.org/t/p/w500' + f.poster_path : 'https://via.placeholder.com/300x450?text=Sem+Poster'}" alt="${f.titulo}">
                            <div class="movie-info">
                                <div class="movie-title">${f.titulo}</div>
                            </div>
                        `;
                        favsContainer.appendChild(card);
                    });
                }
            } catch (e) {
                alert('Erro ao carregar perfil: ' + e.message);
            }
        }

        async function salvarBio() {
            const token = localStorage.getItem('token');
            const bio = document.getElementById('edit-bio-input').value;
            try {
                const res = await fetch(`/api/perfil/${usuarioIdAtual}`, {
                    method: 'PUT',
                    headers: {
                        'Content-Type': 'application/json',
                        'Authorization': `Bearer ${token}`
                    },
                    body: JSON.stringify({ bio })
                });
                const data = await res.json();
                if (!res.ok) throw new Error(data.detail || 'Erro ao atualizar biografia');
                alert('Biografia atualizada com sucesso!');
                carregarMeuPerfil();
            } catch (e) {
                alert(e.message);
            }
        }

        async function enviarFotoPerfil() {
            const fileInput = document.getElementById('foto-input');
            if (!fileInput.files || fileInput.files.length === 0) {
                alert('Por favor, selecione uma imagem antes de enviar.');
                return;
            }

            const file = fileInput.files[0];
            if (file.size > 2 * 1024 * 1024) {
                alert('A imagem excede o tamanho máximo de 2MB!');
                return;
            }

            const formData = new FormData();
            formData.append('file', file);

            const token = localStorage.getItem('token');
            try {
                const res = await fetch(`/api/perfil/${usuarioIdAtual}/foto`, {
                    method: 'POST',
                    headers: { 'Authorization': `Bearer ${token}` },
                    body: formData
                });
                const data = await res.json();
                if (!res.ok) throw new Error(data.detail || 'Erro ao enviar foto');
                alert('Upload concluído com sucesso no MinIO Storage!');
                fileInput.value = '';
                carregarMeuPerfil();
            } catch (e) {
                alert(e.message);
            }
        }

        /* FUNÇÃO DE FAVORITAR COM TRATAMENTO DO LIMITE DE 3 (ATIVIDADE 7) */
        async function favoritar(id, titulo, poster) {
            const token = localStorage.getItem('token');
            try {
                const res = await fetch('/api/favoritos', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'Authorization': `Bearer ${token}`
                    },
                    body: JSON.stringify({ tmdb_movie_id: Number(id), titulo, poster_path: poster || '' })
                });
                const data = await res.json();

                if (res.status === 403) {
                    alert('⚠️ ' + data.detail); // Alerta do limite de favoritos atingido (Print 1)
                    return;
                }

                if (!res.ok) {
                    throw new Error(data.detail || 'Não foi possível favoritar');
                }

                alert(`"${titulo}" adicionado aos favoritos!`);
            } catch (e) {
                alert(e.message);
            }
        }

        async function removerFavorito(favoritoId) {
            const token = localStorage.getItem('token');
            try {
                const res = await fetch(`/api/favoritos/${favoritoId}`, {
                    method: 'DELETE',
                    headers: { 'Authorization': `Bearer ${token}` }
                });
                if (!res.ok) throw new Error('Não foi possível remover o favorito');
                carregarFavoritos();
            } catch (e) {
                alert(e.message);
            }
        }

        /* CHECKOUT STRIPE (ATIVIDADE 7) */
        async function iniciarCheckoutStripe() {
            const token = localStorage.getItem('token');
            if (!token) {
                alert('Faça login primeiro para assinar!');
                return;
            }
            try {
                const res = await fetch('/api/stripe/checkout', {
                    method: 'POST',
                    headers: { 
                        'Content-Type': 'application/json',
                        'Authorization': `Bearer ${token}` 
                    }
                });
                const data = await res.json();
                if (!res.ok) throw new Error(data.detail || 'Falha ao iniciar checkout.');
                if (data.url) {
                    window.location.href = data.url;
                }
            } catch (err) {
                alert('Erro ao redirecionar para a Stripe: ' + err.message);
            }
        }

        async function abrirModalComentarios(movieId, movieTitle) {
            filmeAtualComentario = parseInt(movieId, 10);
            document.getElementById('modal-movie-title').innerText = `Comentários: ${movieTitle}`;
            document.getElementById('novo-comentario-texto').value = '';
            document.getElementById('modal-comentarios').classList.remove('hidden');
            await carregarComentariosDoFilme(filmeAtualComentario);
        }

        function fecharModalComentarios() {
            document.getElementById('modal-comentarios').classList.add('hidden');
            filmeAtualComentario = null;
        }

        async function carregarComentariosDoFilme(movieId) {
            const token = localStorage.getItem('token');
            const userPapel = localStorage.getItem('papel');
            const lista = document.getElementById('comments-list');
            lista.innerHTML = '<p style="color:#aaa;">Carregando...</p>';
            try {
                const res = await fetch(`/api/comentarios/${movieId}`, {
                    headers: { 'Authorization': `Bearer ${token}` }
                });
                const data = await res.json();
                if (!Array.isArray(data) || data.length === 0) {
                    lista.innerHTML = '<p style="color:#888;">Nenhum comentário feito ainda. Seja o primeiro!</p>';
                    return;
                }
                lista.innerHTML = '';
                data.forEach(c => {
                    const item = document.createElement('div');
                    item.className = 'comment-item';
                    
                    let botaoExcluir = '';
                    if (c.pode_apagar || userPapel === 'admin') {
                        botaoExcluir = `<button class="btn-del-comment" onclick="apagarComentario(${c.id})">Excluir</button>`;
                    }

                    item.innerHTML = `
                        <div class="comment-body">
                            <div class="comment-user">${c.usuario_nome}</div>
                            <div class="comment-text">${c.texto}</div>
                        </div>
                        ${botaoExcluir}
                    `;
                    lista.appendChild(item);
                });
            } catch (e) {
                lista.innerHTML = '<p style="color:#ef5350;">Erro ao carregar comentários.</p>';
            }
        }

        async function apagarComentario(comentarioId) {
            if (!confirm('Deseja realmente apagar este comentário?')) return;
            const token = localStorage.getItem('token');
            try {
                const res = await fetch(`/api/comentarios/${comentarioId}`, {
                    method: 'DELETE',
                    headers: { 'Authorization': `Bearer ${token}` }
                });
                const data = await res.json();
                if (!res.ok) {
                    alert(`Erro (${res.status}): ${data.detail || 'Não foi possível apagar o comentário.'}`);
                    return;
                }
                await carregarComentariosDoFilme(filmeAtualComentario);
            } catch (e) {
                alert('Erro na requisição: ' + e.message);
            }
        }

        async function enviarComentario() {
            const texto = document.getElementById('novo-comentario-texto').value.trim();
            if (!texto) {
                alert('Digite algo antes de enviar o comentário.');
                return;
            }
            const token = localStorage.getItem('token');
            try {
                const res = await fetch('/api/comentarios', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'Authorization': `Bearer ${token}`
                    },
                    body: JSON.stringify({ tmdb_movie_id: filmeAtualComentario, texto })
                });
                if (!res.ok) throw new Error('Falha ao registrar comentário');
                document.getElementById('novo-comentario-texto').value = '';
                await carregarComentariosDoFilme(filmeAtualComentario);
            } catch (e) {
                alert(e.message);
            }
        }

        async function fazerLogin() {
            const email = document.getElementById('login-email').value;
            const senha = document.getElementById('login-senha').value;
            try {
                const res = await fetch('/api/auth/login', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ email, senha })
                });
                const data = await res.json();
                if (!res.ok) throw new Error(data.detail || 'Falha no login');
                
                const isPremium = Boolean(data.is_premium);
                localStorage.setItem('token', data.access_token);
                localStorage.setItem('usuario_nome', data.usuario_nome);
                localStorage.setItem('papel', data.papel || data.role || 'usuario');
                localStorage.setItem('is_premium', isPremium ? 'true' : 'false');
                
                document.getElementById('nav-user-info').innerText = `${data.usuario_nome} [${data.papel || data.role || 'usuario'}]`;
                document.getElementById('btn-logout').classList.remove('hidden');
                document.getElementById('nav-tabs').classList.remove('hidden');
                atualizarUIStatusPremium(isPremium);
                
                mostrarAba('catalogo');
            } catch (err) {
                exibirAviso('msg-login', err.message, true);
            }
        }

        async function fazerCadastro() {
            const nome = document.getElementById('reg-nome').value;
            const email = document.getElementById('reg-email').value;
            const senha = document.getElementById('reg-senha').value;
            try {
                const res = await fetch('/api/auth/register', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ nome, email, senha, papel: 'usuario' })
                });
                const data = await res.json();
                if (!res.ok) throw new Error(data.detail || 'Falha no cadastro');
                exibirAviso('msg-reg', 'Conta criada com sucesso! Faça o login.');
                setTimeout(() => alternarTela('box-login'), 1200);
            } catch (err) {
                exibirAviso('msg-reg', err.message, true);
            }
        }

        async function solicitarRecuperacao() {
            const email = document.getElementById('forgot-email').value;
            try {
                const res = await fetch('/api/auth/forgot-password', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ email })
                });
                const data = await res.json();
                exibirAviso('msg-forgot', data.message || 'Verifique sua caixa de entrada no Mailtrap!');
            } catch (err) {
                exibirAviso('msg-forgot', 'Erro ao enviar solicitação.', true);
            }
        }

        async function redefinirSenha() {
            const token = document.getElementById('reset-token').value;
            const nova_senha = document.getElementById('reset-nova-senha').value;
            try {
                const res = await fetch('/api/auth/reset-password', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ token, nova_senha })
                });
                const data = await res.json();
                if (!res.ok) throw new Error(data.detail || 'Token inválido ou expirado');
                exibirAviso('msg-reset', 'Senha alterada com sucesso!');
                setTimeout(() => alternarTela('box-login'), 1500);
            } catch (err) {
                exibirAviso('msg-reset', err.message, true);
            }
        }

        async function logout() {
            const token = localStorage.getItem('token');
            if (token) {
                try {
                    await fetch('/api/auth/logout', {
                        method: 'POST',
                        headers: { 'Authorization': `Bearer ${token}` }
                    });
                } catch (e) {}
            }
            localStorage.clear();
            document.getElementById('nav-user-info').innerText = 'Não conectado';
            document.getElementById('btn-logout').classList.add('hidden');
            document.getElementById('nav-tabs').classList.add('hidden');
            document.getElementById('nav-badge-premium').classList.add('hidden');
            document.getElementById('btn-assinar-premium').classList.add('hidden');
            alternarTela('box-login');
        }

        window.onload = () => {
            const params = new URLSearchParams(window.location.search);
            const tokenParam = params.get('token');
            if (tokenParam) {
                alternarTela('box-reset');
                document.getElementById('reset-token').value = tokenParam;
                return;
            }

            if (params.get('session_id')) {
                exibirAviso('msg-global', '🎉 Pagamento confirmado pela Stripe! Sua conta agora é Premium.');
            } else if (params.get('status') === 'cancelado') {
                exibirAviso('msg-global', 'O checkout da Stripe foi cancelado.', true);
            }

            const token = localStorage.getItem('token');
            const nome = localStorage.getItem('usuario_nome');
            const papel = localStorage.getItem('papel');
            const isPremium = localStorage.getItem('is_premium') === 'true';

            if (token && nome) {
                document.getElementById('nav-user-info').innerText = `${nome} [${papel}]`;
                document.getElementById('btn-logout').classList.remove('hidden');
                document.getElementById('nav-tabs').classList.remove('hidden');
                atualizarUIStatusPremium(isPremium);
                mostrarAba('catalogo');
            } else {
                alternarTela('box-login');
            }
        };
    </script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse, tags=["Interface Web"])
def index():
    return HTMLResponse(content=HTML_PAGE)

@app.get("/redefinir-senha", response_class=HTMLResponse, tags=["Interface Web"])
def redefinir_senha_pagina():
    return HTMLResponse(content=HTML_PAGE)

# --- PROXY AUTH COM SCHEMAS COMPLETOS E RESPOSTAS MAPEADAS ---

@app.post(
    "/api/auth/register",
    tags=["Autenticação"],
    summary="Registrar novo usuário",
    responses={
        201: {"model": MensagemResposta, "description": "Usuário registrado com sucesso."},
        400: {"model": Erro400Resposta, "description": "E-mail já cadastrado ou dados inválidos."}
    }
)
async def register(dados: RegisterSchema):
    status_code, resp = chamar_auth_service("/register", dados.dict())
    return resp

@app.post(
    "/api/auth/login",
    tags=["Autenticação"],
    summary="Autenticar usuário e obter JWT",
    responses={
        200: {"description": "Login realizado com sucesso. Retorna access_token JWT e papel do usuário."},
        401: {"model": Erro401Resposta, "description": "Credenciais incorretas (e-mail ou senha inválidos)."}
    }
)
async def login(dados: LoginSchema):
    status_code, resp = chamar_auth_service("/login", dados.dict())
    return resp

@app.post(
    "/api/auth/logout",
    tags=["Autenticação"],
    summary="Logout da sessão",
    responses={**RESPOSTAS_ERRO_AUTH, 200: {"model": MensagemResposta}}
)
def logout_proxy(
    request: Request,
    usuario: dict = Depends(auth_guard.obter_usuario_atual)
):
    ip = extrair_ip(request)
    registrar_log(
        usuario_id=usuario.get("usuario_id"),
        acao="logout",
        ip=ip,
        detalhes="Logout efetuado com sucesso"
    )
    return {"message": "Logout registrado com sucesso"}

@app.post(
    "/api/auth/forgot-password",
    tags=["Autenticação"],
    summary="Solicitar redefinição de senha",
    responses={
        200: {"model": MensagemResposta, "description": "E-mail de recuperação despachado via SMTP (Mailtrap)."},
        404: {"model": Erro404Resposta, "description": "E-mail não encontrado na base de dados."}
    }
)
async def forgot_password(dados: ForgotPasswordSchema):
    status_code, resp = chamar_auth_service("/forgot-password", dados.dict())
    return resp

@app.post(
    "/api/auth/reset-password",
    tags=["Autenticação"],
    summary="Redefinir senha com token",
    responses={
        200: {"model": MensagemResposta, "description": "Senha alterada com sucesso."},
        400: {"model": Erro400Resposta, "description": "Token inválido, expirado ou já utilizado."}
    }
)
async def reset_password(dados: ResetPasswordSchema):
    status_code, resp = chamar_auth_service("/reset-password", dados.dict())
    return resp

# --- ENDPOINTS DO PERFIL E STORAGE MINIO (ATIVIDADE 6) ---

@app.get(
    "/api/perfil/me",
    tags=["Perfil & Armazenamento"],
    summary="Obter perfil do usuário logado",
    responses={**RESPOSTAS_ERRO_AUTH, 200: {"description": "Retorna o perfil completo, bio, foto e filmes favoritados."}}
)
def obter_meu_perfil(
    usuario: dict = Depends(auth_guard.obter_usuario_atual),
    db: Session = Depends(get_db)
):
    uid = usuario["usuario_id"]
    perfil = db.query(models.Perfil).filter(models.Perfil.usuario_id == uid).first()
    if not perfil:
        perfil = models.Perfil(usuario_id=uid, bio="")
        db.add(perfil)
        db.commit()
        db.refresh(perfil)

    favs = db.query(models.Favorito).filter(models.Favorito.usuario_id == uid).order_by(models.Favorito.criado_em.desc()).all()
    lista_favs = [
        {"id": f.id, "tmdb_movie_id": f.tmdb_movie_id, "titulo": f.titulo, "poster_path": f.poster_path}
        for f in favs
    ]

    foto_url = storage.obter_url_foto(perfil.foto_key) if perfil.foto_key else None

    return {
        "usuario_id": uid,
        "nome": usuario.get("nome", f"Usuário #{uid}"),
        "bio": perfil.bio or "",
        "foto_url": foto_url,
        "favoritos": lista_favs
    }

@app.put(
    "/api/perfil/{target_usuario_id}",
    tags=["Perfil & Armazenamento"],
    summary="Atualizar biografia do usuário",
    responses={**RESPOSTAS_ERRO_RBAC, 200: {"model": MensagemResposta}}
)
def atualizar_perfil(
    target_usuario_id: int,
    dados: PerfilUpdateSchema,
    request: Request,
    usuario: dict = Depends(auth_guard.obter_usuario_atual),
    db: Session = Depends(get_db)
):
    # Regra: cada usuário só pode editar o próprio perfil (Requisito 4)
    if usuario["usuario_id"] != target_usuario_id:
        ip = extrair_ip(request)
        registrar_log(
            usuario_id=usuario["usuario_id"],
            acao="tentativa_negada_403_perfil",
            ip=ip,
            detalhes=f"Tentativa não autorizada de editar o perfil {target_usuario_id}"
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acesso negado: não é permitido editar o perfil de outro usuário."
        )

    perfil = db.query(models.Perfil).filter(models.Perfil.usuario_id == target_usuario_id).first()
    if not perfil:
        perfil = models.Perfil(usuario_id=target_usuario_id, bio=dados.bio)
        db.add(perfil)
    else:
        perfil.bio = dados.bio
    db.commit()

    return {"message": "Biografia atualizada com sucesso"}

@app.post(
    "/api/stripe/checkout",
    tags=["Pagamentos (Stripe)"],
    summary="Iniciar checkout do Plano Premium",
    responses={**RESPOSTAS_ERRO_AUTH, 200: {"description": "Retorna URL de checkout do Stripe."}}
)
def iniciar_checkout(
    request: Request,
    usuario: dict = Depends(auth_guard.obter_usuario_atual)
):
    token = request.headers.get("authorization")
    try:
        resp = requests.post(
            f"{AUTH_SERVICE_URL}/checkout",
            headers={"Authorization": token},
            timeout=5.0
        )
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail=resp.text)
        return resp.json()
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Erro ao contatar auth_service: {str(e)}")

@app.post(
    "/api/perfil/{target_usuario_id}/foto",
    tags=["Perfil & Armazenamento"],
    summary="Upload de foto de perfil para o MinIO",
    responses={**RESPOSTAS_ERRO_RBAC, 200: {"description": "Foto enviada ao MinIO e referência salva no banco."}}
)
def upload_foto_perfil(
    target_usuario_id: int,
    request: Request,
    file: UploadFile = File(...),
    usuario: dict = Depends(auth_guard.obter_usuario_atual),
    db: Session = Depends(get_db)
):
    # Proteção: cada usuário só altera a própria foto (Requisito 4)
    if usuario["usuario_id"] != target_usuario_id:
        ip = extrair_ip(request)
        registrar_log(
            usuario_id=usuario["usuario_id"],
            acao="tentativa_negada_403_foto",
            ip=ip,
            detalhes=f"Tentativa de upload de foto no perfil {target_usuario_id}"
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acesso negado: não é permitido alterar a foto de outro usuário."
        )

    # Validação e upload para o bucket MinIO
    foto_key = storage.validar_e_enviar_foto(target_usuario_id, file)

    # Persiste apenas a chave/referência no SQLite
    perfil = db.query(models.Perfil).filter(models.Perfil.usuario_id == target_usuario_id).first()
    if not perfil:
        perfil = models.Perfil(usuario_id=target_usuario_id, foto_key=foto_key)
        db.add(perfil)
    else:
        perfil.foto_key = foto_key
    db.commit()

    ip = extrair_ip(request)
    registrar_log(
        usuario_id=usuario["usuario_id"],
        acao="upload_foto_perfil",
        ip=ip,
        detalhes=f"Upload de foto de perfil no storage MinIO com a chave: {foto_key}"
    )

    return {
        "message": "Foto de perfil atualizada com sucesso.",
        "foto_key": foto_key,
        "foto_url": storage.obter_url_foto(foto_key)
    }

@app.get(
    "/api/perfil/foto/{object_name}",
    tags=["Perfil & Armazenamento"],
    summary="Exibir foto de perfil a partir do MinIO"
)
def servir_foto_perfil(object_name: str):
    """Serve a imagem binária recuperada diretamente do MinIO Storage."""
    response = storage.obter_stream_imagem(object_name)
    media_type = "image/jpeg"
    if object_name.endswith(".png"):
        media_type = "image/png"
    elif object_name.endswith(".webp"):
        media_type = "image/webp"
    return StreamingResponse(response.stream(32 * 1024), media_type=media_type)

# --- ENDPOINTS DO CATÁLOGO ---

@app.get(
    "/api/filmes",
    tags=["Catálogo"],
    summary="Listar filmes de Tom Hanks (TMDB)",
    responses={**RESPOSTAS_ERRO_AUTH, 200: {"description": "Lista dos 30 principais filmes consultados na API do TMDB."}}
)
def listar_filmes(usuario: dict = Depends(auth_guard.obter_usuario_atual)):
    # URL atualizada usando o endpoint Discover com o ID do Tom Hanks (31)
    url = f"https://api.themoviedb.org/3/discover/movie?with_cast=31&sort_by=popularity.desc&api_key={TMDB_API_KEY}&language=pt-BR"
    
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req) as response:
            dados = json.loads(response.read().decode('utf-8'))
            # No endpoint discover, os filmes vêm dentro de uma lista chamada 'results'
            filmes = dados.get("results", [])
            
            # Retorna os 30 primeiros (a API retorna 20 por página por padrão, 
            # mas mantive a lógica de corte caso você adicione paginação depois)
            return filmes[:30]
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao consultar TMDB: {str(e)}")

@app.get(
    "/api/favoritos",
    tags=["Favoritos"],
    summary="Listar favoritos do usuário autenticado",
    responses={**RESPOSTAS_ERRO_AUTH, 200: {"description": "Lista de filmes favoritados pelo usuário logado."}}
)
def listar_favoritos(
    usuario: dict = Depends(auth_guard.obter_usuario_atual),
    db: Session = Depends(get_db)
):
    favs = db.query(models.Favorito).filter(models.Favorito.usuario_id == usuario["usuario_id"]).order_by(models.Favorito.criado_em.desc()).all()
    return [
        {
            "id": f.id,
            "tmdb_movie_id": f.tmdb_movie_id,
            "titulo": f.titulo,
            "poster_path": f.poster_path,
            "criado_em": str(f.criado_em)
        }
        for f in favs
    ]

LIMITE_FAVORITOS_GRATIS = 3  # <-- Adicione esta linha antes da rota

@app.post(
    "/api/favoritos",
    status_code=status.HTTP_201_CREATED,
    tags=["Favoritos"],
    summary="Adicionar filme aos favoritos",
    responses={**RESPOSTAS_ERRO_AUTH, 201: {"model": MensagemResposta}}
)
def favoritar(
    dados: schemas.FavoritoCriar,
    request: Request,
    usuario: dict = Depends(auth_guard.obter_usuario_atual),
    db: Session = Depends(get_db)
):
    user_id = usuario["usuario_id"]
    is_premium = usuario.get("is_premium", False)

    if not is_premium:
        total_favoritos = db.query(models.Favorito).filter(models.Favorito.usuario_id == user_id).count()
        if total_favoritos >= LIMITE_FAVORITOS_GRATIS:
            ip = extrair_ip(request)
            registrar_log(
                usuario_id=user_id,
                acao="tentativa_negada_limite_favoritos",
                ip=ip,
                detalhes=f"Usuário atingiu o limite gratuito de {LIMITE_FAVORITOS_GRATIS} favoritos."
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Limite de {LIMITE_FAVORITOS_GRATIS} favoritos atingido! Assine o Plano Premium para ter favoritos ilimitados."
            )
    ...

    # Evita duplicar o mesmo filme nos favoritos
    fav_existente = db.query(models.Favorito).filter(
        models.Favorito.usuario_id == user_id,
        models.Favorito.tmdb_movie_id == dados.tmdb_movie_id
    ).first()
    if fav_existente:
        return {"message": "Este filme já está nos seus favoritos"}

    novo_fav = models.Favorito(
        usuario_id=user_id,
        tmdb_movie_id=dados.tmdb_movie_id,
        titulo=dados.titulo,
        poster_path=dados.poster_path
    )
    db.add(novo_fav)
    db.commit()

    ip = extrair_ip(request)
    registrar_log(
        usuario_id=user_id,
        acao="favoritar_filme",
        ip=ip,
        detalhes=f"Favoritou o filme '{dados.titulo}' (TMDB ID: {dados.tmdb_movie_id})"
    )

    return {"message": "Favoritado com sucesso"}

@app.delete(
    "/api/favoritos/{favorito_id}",
    tags=["Favoritos"],
    summary="Remover filme dos favoritos",
    responses={
        **RESPOSTAS_ERRO_AUTH,
        200: {"model": MensagemResposta, "description": "Favorito removido com sucesso."},
        404: {"model": Erro404Resposta, "description": "Favorito não encontrado na base de dados."}
    }
)
def remover_favorito(
    favorito_id: int,
    usuario: dict = Depends(auth_guard.obter_usuario_atual),
    db: Session = Depends(get_db)
):
    fav = db.query(models.Favorito).filter(
        models.Favorito.id == favorito_id,
        models.Favorito.usuario_id == usuario["usuario_id"]
    ).first()
    if not fav:
        raise HTTPException(status_code=404, detail="Favorito não encontrado")
    db.delete(fav)
    db.commit()
    return {"message": "Favorito removido"}

@app.get(
    "/api/comentarios/{tmdb_movie_id}",
    tags=["Comentários"],
    summary="Listar comentários de um filme",
    responses={**RESPOSTAS_ERRO_AUTH, 200: {"description": "Lista de comentários do filme solicitado."}}
)
def listar_comentarios(
    tmdb_movie_id: int,
    usuario: dict = Depends(auth_guard.obter_usuario_atual),
    db: Session = Depends(get_db)
):
    comentarios = db.query(models.Comentario).filter(models.Comentario.tmdb_movie_id == tmdb_movie_id).order_by(models.Comentario.criado_em.desc()).all()
    user_id = usuario.get("usuario_id")
    papel = usuario.get("papel") or usuario.get("role")
    
    return [
        {
            "id": c.id,
            "usuario_id": c.usuario_id,
            "usuario_nome": f"Usuário #{c.usuario_id}",
            "tmdb_movie_id": c.tmdb_movie_id,
            "texto": c.texto,
            "criado_em": str(c.criado_em),
            "pode_apagar": (c.usuario_id == user_id or papel == "admin")
        }
        for c in comentarios
    ]

@app.post(
    "/api/comentarios",
    status_code=status.HTTP_201_CREATED,
    tags=["Comentários"],
    summary="Publicar comentário em um filme",
    responses={**RESPOSTAS_ERRO_AUTH, 201: {"model": MensagemResposta}}
)
def comentar(
    dados: schemas.ComentarioCriar,
    request: Request,
    usuario: dict = Depends(auth_guard.obter_usuario_atual),
    db: Session = Depends(get_db)
):
    novo_comentario = models.Comentario(
        usuario_id=usuario["usuario_id"],
        tmdb_movie_id=dados.tmdb_movie_id,
        texto=dados.texto
    )
    db.add(novo_comentario)
    db.commit()

    ip = extrair_ip(request)
    registrar_log(
        usuario_id=usuario["usuario_id"],
        acao="comentar",
        ip=ip,
        detalhes=f"Comentou no filme TMDB ID {dados.tmdb_movie_id}: {dados.texto[:40]}..."
    )

    return {"message": "Comentário adicionado com sucesso"}

@app.delete(
    "/api/comentarios/{comentario_id}",
    tags=["Comentários"],
    summary="Excluir comentário (Autor ou Admin)",
    responses={
        **RESPOSTAS_ERRO_RBAC,
        200: {"model": MensagemResposta, "description": "Comentário removido com sucesso."},
        404: {"model": Erro404Resposta, "description": "Comentário não encontrado na base de dados."}
    }
)
def deletar_comentario(
    comentario_id: int,
    request: Request,
    usuario: dict = Depends(auth_guard.obter_usuario_atual),
    db: Session = Depends(get_db)
):
    comentario = db.query(models.Comentario).filter(models.Comentario.id == comentario_id).first()
    if not comentario:
        raise HTTPException(status_code=404, detail="Comentário não encontrado")

    papel = usuario.get("papel") or usuario.get("role")
    user_id = usuario.get("usuario_id")
    ip = extrair_ip(request)

    if papel != "admin" and comentario.usuario_id != user_id:
        registrar_log(
            usuario_id=user_id,
            acao="tentativa_negada_403",
            ip=ip,
            detalhes=f"Usuário tentou apagar o comentário {comentario_id} pertencente ao usuário {comentario.usuario_id}"
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acesso negado (403 Forbidden): apenas administradores podem apagar comentários de outros usuários."
        )

    if papel == "admin" and comentario.usuario_id != user_id:
        registrar_log(
            usuario_id=user_id,
            acao="apagar_comentario_moderacao",
            ip=ip,
            detalhes=f"Admin moderou e apagou o comentário {comentario_id} do usuário {comentario.usuario_id}"
        )

    db.delete(comentario)
    db.commit()
    return {"message": "Comentário removido com sucesso"}

@app.get(
    "/api/admin/logs",
    tags=["Auditoria (Admin)"],
    summary="Consultar logs de auditoria do Redis Streams (Exclusivo Admin)",
    responses={
        **RESPOSTAS_ERRO_RBAC,
        200: {"description": "Lista de eventos de auditoria recuperados cronologicamente do Redis Streams."}
    }
)
def consultar_logs_admin(
    request: Request,
    limit: int = Query(50, ge=1, le=200, description="Quantidade máxima de eventos a retornar"),
    usuario: dict = Depends(auth_guard.obter_usuario_atual)
):
    papel = usuario.get("papel") or usuario.get("role")
    user_id = usuario.get("usuario_id")
    ip = extrair_ip(request)

    if papel != "admin":
        registrar_log(
            usuario_id=user_id,
            acao="tentativa_negada_403",
            ip=ip,
            detalhes="Tentativa não autorizada de consultar logs de auditoria"
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acesso negado (403 Forbidden): apenas administradores podem acessar os logs de auditoria."
        )

    auth_header = request.headers.get("authorization") or f"Bearer {request.cookies.get('token', '')}"
    try:
        resp = requests.get(
            f"{LOG_SERVICE_URL}/logs?limit={limit}",
            headers={"Authorization": auth_header},
            timeout=3.0
        )
        if resp.status_code != 200:
            detalhe = resp.json().get("detail", "Erro retornado pelo serviço de logs")
            raise HTTPException(status_code=resp.status_code, detail=detalhe)
        return resp.json()
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Falha de comunicação com log_service: {str(e)}")
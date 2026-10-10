import os
from datetime import datetime, timedelta
import stripe
import requests
from fastapi import FastAPI, HTTPException, Depends, Request, Response, Header, status
from sqlalchemy.orm import Session
from sqlalchemy import text
from prometheus_fastapi_instrumentator import Instrumentator

from database import get_db, engine, Base
import models
import schemas
import auth
import email_service

LOG_SERVICE_URL = os.getenv("LOG_SERVICE_URL", "http://log_service:6000")

# Configurações Stripe (Modo de Teste)
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
STRIPE_PRICE_ID = os.getenv("STRIPE_PRICE_ID")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")
CHECKOUT_SUCCESS_URL = os.getenv("CHECKOUT_SUCCESS_URL", "http://localhost:8000/success?session_id={CHECKOUT_SESSION_ID}")
CHECKOUT_CANCEL_URL = os.getenv("CHECKOUT_CANCEL_URL", "http://localhost:8000/cancel")

def extrair_ip(request: Request) -> str:
    """Extrai o IP real considerando proxies reversos ou cliente direto."""
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

# Cria as tabelas de autenticação no banco
try:
    Base.metadata.create_all(bind=engine)
except Exception as e:
    print(f"Aviso ao inicializar tabelas auth: {e}")

app = FastAPI(title="Auth Service")

# Instrumentação Prometheus para expor métricas na rota /metrics
Instrumentator().instrument(app).expose(app)

@app.get("/health", tags=["Observabilidade"])
def health_check(response: Response, db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
        return {
            "status": "healthy",
            "service": "auth_service",
            "dependencies": {
                "database": "up"
            }
        }
    except Exception as exc:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {
            "status": "unhealthy",
            "service": "auth_service",
            "dependencies": {
                "database": "down"
            },
            "error": str(exc)
        }

@app.post("/register", status_code=status.HTTP_201_CREATED)
def register(usuario_in: schemas.UsuarioCriar, request: Request, db: Session = Depends(get_db)):
    usuario_existente = db.query(models.Usuario).filter(models.Usuario.email == usuario_in.email).first()
    if usuario_existente:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="E-mail já cadastrado"
        )
    
    senha_hash = auth.gerar_hash_senha(usuario_in.senha)
    novo_usuario = models.Usuario(
        nome=usuario_in.nome,
        email=usuario_in.email,
        senha_hash=senha_hash,
        papel=usuario_in.papel or "usuario"
    )
    db.add(novo_usuario)
    db.commit()
    db.refresh(novo_usuario)

    ip = extrair_ip(request)
    registrar_log(
        usuario_id=novo_usuario.id,
        acao="registro",
        ip=ip,
        detalhes=f"Novo usuário cadastrado com papel '{novo_usuario.papel}'"
    )

    return {"message": "Usuário cadastrado com sucesso", "usuario_id": novo_usuario.id}

@app.post("/login")
def login(dados: schemas.LoginRequest, request: Request, db: Session = Depends(get_db)):
    ip = extrair_ip(request)
    usuario = db.query(models.Usuario).filter(models.Usuario.email == dados.email).first()
    
    if not usuario or not auth.verificar_senha(dados.senha, usuario.senha_hash):
        registrar_log(
            usuario_id=usuario.id if usuario else None,
            acao="falha_login",
            ip=ip,
            detalhes=f"Tentativa de login frustrada para o e-mail: {dados.email}"
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenciais inválidas"
        )
    
    token = auth.criar_access_token(
        usuario_id=usuario.id,
        email=usuario.email,
        papel=usuario.papel,
        nome=usuario.nome
    )

    registrar_log(
        usuario_id=usuario.id,
        acao="login",
        ip=ip,
        detalhes=f"Login realizado pelo usuário '{usuario.nome}' [{usuario.papel}]"
    )

    return {
        "access_token": token,
        "token_type": "bearer",
        "usuario_nome": usuario.nome,
        "papel": usuario.papel,
        "is_premium": usuario.is_premium
    }

@app.get("/me", response_model=schemas.UsuarioResponse)
def obter_usuario_me(
    usuario_atual: models.Usuario = Depends(auth.obter_usuario_atual)
):
    return usuario_atual

@app.post("/forgot-password")
def forgot_password(dados: schemas.EsqueciSenhaRequest, request: Request, db: Session = Depends(get_db)):
    ip = extrair_ip(request)
    usuario = db.query(models.Usuario).filter(models.Usuario.email == dados.email).first()
    if not usuario:
        return {"message": "Se o e-mail existir, as instruções foram enviadas."}

    token_reset = auth.gerar_token_recuperacao()
    expiracao = datetime.utcnow() + timedelta(minutes=15)

    reset_entry = models.RecuperacaoSenha(
        usuario_id=usuario.id,
        token=token_reset,
        expira_em=expiracao
    )
    db.add(reset_entry)
    db.commit()

    email_service.enviar_email_recuperacao(usuario.email, token_reset)

    registrar_log(
        usuario_id=usuario.id,
        acao="solicitacao_recuperacao_senha",
        ip=ip,
        detalhes=f"Token de recuperação enviado para {usuario.email}"
    )

    return {"message": "E-mail de recuperação enviado com sucesso."}

@app.post("/reset-password")
def reset_password(dados: schemas.RedefinirSenhaRequest, request: Request, db: Session = Depends(get_db)):
    ip = extrair_ip(request)
    reset_entry = db.query(models.RecuperacaoSenha).filter(
        models.RecuperacaoSenha.token == dados.token,
        models.RecuperacaoSenha.usado == False
    ).first()

    if not reset_entry or reset_entry.expira_em < datetime.utcnow():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Token inválido ou expirado"
        )

    usuario = db.query(models.Usuario).filter(models.Usuario.id == reset_entry.usuario_id).first()
    if not usuario:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuário não encontrado")

    usuario.senha_hash = auth.gerar_hash_senha(dados.nova_senha)
    reset_entry.usado = True
    db.commit()

    registrar_log(
        usuario_id=usuario.id,
        acao="redefinicao_senha",
        ip=ip,
        detalhes="Senha redefinida com sucesso com token de recuperação"
    )

    return {"message": "Senha redefinida com sucesso!"}

# =====================================================================
# ROTAS DO STRIPE (ATIVIDADE 7 - PLANO PREMIUM)
# =====================================================================

@app.post("/checkout", response_model=schemas.CheckoutResponse)
def criar_checkout_session(
    request: Request,
    usuario_atual: models.Usuario = Depends(auth.obter_usuario_atual)
):
    """Cria uma Checkout Session no Stripe associada ao ID do usuário."""
    if not STRIPE_PRICE_ID:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="STRIPE_PRICE_ID não configurado no servidor."
        )

    try:
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[{
                "price": STRIPE_PRICE_ID,
                "quantity": 1,
            }],
            mode="subscription",
            client_reference_id=str(usuario_atual.id),
            customer_email=usuario_atual.email,
            success_url=CHECKOUT_SUCCESS_URL,
            cancel_url=CHECKOUT_CANCEL_URL,
        )

        ip = extrair_ip(request)
        registrar_log(
            usuario_id=usuario_atual.id,
            acao="checkout_iniciado",
            ip=ip,
            detalhes=f"Checkout session {session.id} criada para plano premium"
        )

        return {"url": session.url}
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Erro ao criar sessão Stripe: {str(exc)}"
        )

@app.post("/webhook")
async def stripe_webhook(
    request: Request,
    stripe_signature: str = Header(None, alias="stripe-signature"),
    db: Session = Depends(get_db)
):
    """Recebe e processa eventos assíncronos do Stripe com verificação de assinatura."""
    if not STRIPE_WEBHOOK_SECRET:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="STRIPE_WEBHOOK_SECRET não configurado no servidor."
        )

    payload = await request.body()

    try:
        event = stripe.Webhook.construct_event(
            payload=payload,
            sig_header=stripe_signature,
            secret=STRIPE_WEBHOOK_SECRET
        )
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Payload inválido")
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Assinatura Stripe inválida")

    # Confirmação de pagamento bem-sucedido
    if event["type"] == "checkout.session.completed":
        session_obj = event["data"]["object"]
        usuario_id = session_obj.get("client_reference_id")

        if usuario_id:
            usuario = db.query(models.Usuario).filter(models.Usuario.id == int(usuario_id)).first()
            if usuario:
                usuario.is_premium = True
                usuario.stripe_customer_id = session_obj.get("customer")
                db.commit()

                registrar_log(
                    usuario_id=usuario.id,
                    acao="upgrade_premium",
                    ip=extrair_ip(request),
                    detalhes=f"Usuário {usuario.email} promovido a Premium via Stripe (Customer: {usuario.stripe_customer_id})"
                )

    return {"status": "success"}
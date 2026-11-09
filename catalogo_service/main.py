import os
from datetime import datetime, timedelta
import stripe
import requests
from fastapi import FastAPI, HTTPException, Depends, Request, Response, Header, status
from sqlalchemy.orm import Session
from sqlalchemy import text, func
from prometheus_fastapi_instrumentator import Instrumentator

from database import get_db, engine, Base
import models
import schemas
import auth
import email_service

LOG_SERVICE_URL = os.getenv("LOG_SERVICE_URL", "http://log_service:6000")

# Configurações Stripe (Modo de Teste)
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
STRIPE_PRICE_ID = os.getenv("STRIPE_PRICE_ID_PRO") or os.getenv("STRIPE_PRICE_ID")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")

CHECKOUT_SUCCESS_URL = os.getenv(
    "CHECKOUT_SUCCESS_URL",
    "https://gabriel-graciano-isw055.lapps.studio/?session_id={CHECKOUT_SESSION_ID}"
)
CHECKOUT_CANCEL_URL = os.getenv(
    "CHECKOUT_CANCEL_URL",
    "https://gabriel-graciano-isw055.lapps.studio/?status=cancelado"
)
FRONTEND_URL = os.getenv("FRONTEND_URL", "https://gabriel-graciano-isw055.lapps.studio")

VALOR_MENSAL_PRO_BRL = 19.90


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

app = FastAPI(title="CampusSwap - Auth Service")

# Instrumentação Prometheus
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
        papel=usuario_in.papel or "aluno",
        plano="free",
        status_assinatura="active"
    )
    db.add(novo_usuario)
    db.commit()
    db.refresh(novo_usuario)

    ip = extrair_ip(request)
    registrar_log(
        usuario_id=novo_usuario.id,
        acao="registro",
        ip=ip,
        detalhes=f"Novo usuário cadastrado com plano Free e papel '{novo_usuario.papel}'"
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
    
    plano_atual = getattr(usuario, "plano", "free")
    status_ass = getattr(usuario, "status_assinatura", "active")
    is_pro = plano_atual == "pro" and status_ass == "active"

    token = auth.criar_access_token(
        usuario_id=usuario.id,
        email=usuario.email,
        papel=usuario.papel,
        nome=usuario.nome,
        is_premium=is_pro
    )

    registrar_log(
        usuario_id=usuario.id,
        acao="login",
        ip=ip,
        detalhes=f"Login realizado por '{usuario.nome}' [{usuario.papel} | Plano: {plano_atual}]"
    )

    return {
        "access_token": token,
        "token_type": "bearer",
        "usuario_nome": usuario.nome,
        "papel": usuario.papel,
        "plano": plano_atual,
        "status_assinatura": status_ass,
        "is_premium": is_pro
    }


# Rota consultada pelo catalogo_service para validar limites em tempo real
@app.get("/me")
@app.get("/api/usuarios/me")
def obter_usuario_me(
    usuario_atual: models.Usuario = Depends(auth.obter_usuario_atual)
):
    return {
        "id": usuario_atual.id,
        "nome": usuario_atual.nome,
        "email": usuario_atual.email,
        "papel": usuario_atual.papel,
        "plano": getattr(usuario_atual, "plano", "free"),
        "status_assinatura": getattr(usuario_atual, "status_assinatura", "active"),
        "is_premium": getattr(usuario_atual, "plano", "free") == "pro",
        "stripe_customer_id": getattr(usuario_atual, "stripe_customer_id", None)
    }


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
# ROTAS DO STRIPE (ATIVIDADE 11 - SAAS COMPLETO & ASSINATURA RECORRENTE)
# =====================================================================

@app.post("/checkout")
@app.post("/api/assinaturas/checkout")
def criar_checkout_session(
    request: Request,
    usuario_atual: models.Usuario = Depends(auth.obter_usuario_atual),
    db: Session = Depends(get_db)
):
    """Cria uma Checkout Session no Stripe com mode='subscription' vinculada ao customer."""
    if not STRIPE_PRICE_ID:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="STRIPE_PRICE_ID não configurado no servidor."
        )

    try:
        customer_id = usuario_atual.stripe_customer_id
        if not customer_id:
            customer = stripe.Customer.create(
                email=usuario_atual.email,
                metadata={"user_id": str(usuario_atual.id)}
            )
            customer_id = customer.id
            usuario_atual.stripe_customer_id = customer_id
            db.commit()

        session = stripe.checkout.Session.create(
            customer=customer_id,
            payment_method_types=["card"],
            line_items=[{
                "price": STRIPE_PRICE_ID,
                "quantity": 1,
            }],
            mode="subscription",
            client_reference_id=str(usuario_atual.id),
            success_url=CHECKOUT_SUCCESS_URL,
            cancel_url=CHECKOUT_CANCEL_URL,
            metadata={"user_id": str(usuario_atual.id)}
        )

        ip = extrair_ip(request)
        registrar_log(
            usuario_id=usuario_atual.id,
            acao="checkout_recorrente_iniciado",
            ip=ip,
            detalhes=f"Checkout session {session.id} criada para plano Pro (Recorrente)"
        )

        return {"url": session.url}
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Erro ao criar sessão Stripe: {str(exc)}"
        )


@app.post("/portal")
@app.post("/api/assinaturas/portal")
def abrir_portal_cliente(
    usuario_atual: models.Usuario = Depends(auth.obter_usuario_atual)
):
    """Gera link para o Billing Portal da Stripe para troca de plano ou cancelamento."""
    if not usuario_atual.stripe_customer_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Usuário ainda não possui uma assinatura ou cliente cadastrado no Stripe."
        )

    try:
        session = stripe.billing_portal.Session.create(
            customer=usuario_atual.stripe_customer_id,
            return_url=FRONTEND_URL
        )
        return {"url": session.url}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Erro ao acessar Portal Stripe: {str(e)}")


@app.post("/webhook")
@app.post("/api/assinaturas/webhook")
async def stripe_webhook(
    request: Request,
    stripe_signature: str = Header(None, alias="stripe-signature"),
    db: Session = Depends(get_db)
):
    """Recebe e processa os 4 eventos do ciclo de vida SaaS com verificação de assinatura."""
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

    event_type = event["type"]
    data_object = event["data"]["object"]

    # 1. Checkout de assinatura finalizado com sucesso
    if event_type == "checkout.session.completed":
        customer_id = data_object.get("customer")
        subscription_id = data_object.get("subscription")
        user_id = data_object.get("client_reference_id") or data_object.get("metadata", {}).get("user_id")

        usuario = None
        if user_id:
            usuario = db.query(models.Usuario).filter(models.Usuario.id == int(user_id)).first()
        if not usuario and customer_id:
            usuario = db.query(models.Usuario).filter(models.Usuario.stripe_customer_id == customer_id).first()

        if usuario:
            usuario.plano = "pro"
            usuario.status_assinatura = "active"
            usuario.is_premium = True
            usuario.stripe_customer_id = customer_id
            usuario.stripe_subscription_id = subscription_id
            db.commit()

            registrar_log(
                usuario_id=usuario.id,
                acao="assinatura_iniciada",
                ip=extrair_ip(request),
                detalhes=f"Assinatura Pro ativada (Sub: {subscription_id}, Customer: {customer_id})"
            )

    # 2. Atualização de assinatura (troca de plano / renovação)
    elif event_type == "customer.subscription.updated":
        customer_id = data_object.get("customer")
        status_ass = data_object.get("status")
        
        usuario = db.query(models.Usuario).filter(models.Usuario.stripe_customer_id == customer_id).first()
        if usuario:
            usuario.status_assinatura = status_ass
            if status_ass == "active":
                usuario.plano = "pro"
                usuario.is_premium = True
            else:
                usuario.plano = "free"
                usuario.is_premium = False
            db.commit()

            registrar_log(
                usuario_id=usuario.id,
                acao="assinatura_atualizada",
                ip=extrair_ip(request),
                detalhes=f"Assinatura atualizada para status '{status_ass}' (Plano: {usuario.plano})"
            )

    # 3. Assinatura cancelada / encerrada
    elif event_type == "customer.subscription.deleted":
        customer_id = data_object.get("customer")
        usuario = db.query(models.Usuario).filter(models.Usuario.stripe_customer_id == customer_id).first()
        if usuario:
            usuario.plano = "free"
            usuario.status_assinatura = "canceled"
            usuario.is_premium = False
            usuario.stripe_subscription_id = None
            db.commit()

            registrar_log(
                usuario_id=usuario.id,
                acao="assinatura_cancelada",
                ip=extrair_ip(request),
                detalhes=f"Assinatura cancelada. Usuário retornou ao plano Free."
            )

    # 4. Falha na cobrança da renovação
    elif event_type == "invoice.payment_failed":
        customer_id = data_object.get("customer")
        usuario = db.query(models.Usuario).filter(models.Usuario.stripe_customer_id == customer_id).first()
        if usuario:
            usuario.status_assinatura = "past_due"
            usuario.plano = "free"
            usuario.is_premium = False
            db.commit()

            registrar_log(
                usuario_id=usuario.id,
                acao="falha_pagamento_assinatura",
                ip=extrair_ip(request),
                detalhes="Cobrança de renovação falhou. Conta marcada como inadimplente (past_due)."
            )

    return {"status": "success"}


# =====================================================================
# PAINEL ADMIN DE MÉTRICAS SAAS (MRR & PLANOS)
# =====================================================================

@app.get("/api/admin/metricas")
def obter_metricas_admin(
    usuario_atual: models.Usuario = Depends(auth.obter_usuario_atual),
    db: Session = Depends(get_db)
):
    """Retorna a contagem de usuários por plano, assinaturas ativas e o MRR de teste."""
    if usuario_atual.papel != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acesso restrito ao perfil de Administrador."
        )

    # Agrupamento de usuários por plano
    contagem_planos = db.query(
        models.Usuario.plano,
        func.count(models.Usuario.id)
    ).group_by(models.Usuario.plano).all()

    usuarios_por_plano = {plano: total for plano, total in contagem_planos}
    if "free" not in usuarios_por_plano:
        usuarios_por_plano["free"] = 0
    if "pro" not in usuarios_por_plano:
        usuarios_por_plano["pro"] = 0

    # Total de assinaturas ativas
    assinaturas_ativas = db.query(models.Usuario).filter(
        models.Usuario.plano == "pro",
        models.Usuario.status_assinatura == "active"
    ).count()

    mrr_teste = assinaturas_ativas * VALOR_MENSAL_PRO_BRL

    return {
        "usuarios_por_plano": usuarios_por_plano,
        "assinaturas_ativas": assinaturas_ativas,
        "mrr_teste_brl": round(mrr_teste, 2)
    }
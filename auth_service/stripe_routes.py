import os
import stripe
from fastapi import APIRouter, Request, HTTPException, Depends, Header
from sqlalchemy.orm import Session
from sqlalchemy import func
from database import get_db
import models
from auth import obter_usuario_atual, exigir_admin

stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
ENDPOINT_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")
PRICE_ID_PRO = os.getenv("STRIPE_PRICE_ID_PRO") or os.getenv("STRIPE_PRICE_ID")
FRONTEND_URL = os.getenv("FRONTEND_URL", "https://gabriel-graciano-isw055.lapps.studio")
VALOR_MENSAL_PRO_BRL = 19.90

router = APIRouter(prefix="/api/assinaturas", tags=["Assinaturas"])

# Iniciar assinatura recorrente
@router.post("/checkout")
def criar_checkout_session(usuario: dict = Depends(obter_usuario_atual), db: Session = Depends(get_db)):
    user_db = db.query(models.Usuario).filter(models.Usuario.id == usuario["id"]).first()
    if not user_db:
        raise HTTPException(status_code=404, detail="Usuário não encontrado.")

    try:
        customer_id = user_db.stripe_customer_id
        if not customer_id:
            customer = stripe.Customer.create(
                email=user_db.email,
                metadata={"user_id": str(user_db.id)}
            )
            customer_id = customer.id
            user_db.stripe_customer_id = customer_id
            db.commit()

        session = stripe.checkout.Session.create(
            customer=customer_id,
            payment_method_types=["card"],
            line_items=[{"price": PRICE_ID_PRO, "quantity": 1}],
            mode="subscription",
            success_url=f"{FRONTEND_URL}/?sucesso=true",
            cancel_url=f"{FRONTEND_URL}/?cancelado=true",
            metadata={"user_id": str(user_db.id)}
        )
        return {"url": session.url}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

# Portal do cliente do Stripe
@router.post("/portal")
def abrir_portal_cliente(usuario: dict = Depends(obter_usuario_atual), db: Session = Depends(get_db)):
    user_db = db.query(models.Usuario).filter(models.Usuario.id == usuario["id"]).first()
    if not user_db or not user_db.stripe_customer_id:
        raise HTTPException(status_code=400, detail="Usuário não possui conta no Stripe.")

    try:
        session = stripe.billing_portal.Session.create(
            customer=user_db.stripe_customer_id,
            return_url=FRONTEND_URL
        )
        return {"url": session.url}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

# Webhook que sincroniza o estado no banco
@router.post("/webhook")
async def stripe_webhook(request: Request, stripe_signature: str = Header(None, alias="Stripe-Signature"), db: Session = Depends(get_db)):
    payload = await request.body()
    try:
        event = stripe.Webhook.construct_event(payload, stripe_signature, ENDPOINT_SECRET)
    except (ValueError, stripe.error.SignatureVerificationError):
        raise HTTPException(status_code=400, detail="Assinatura de Webhook inválida.")

    event_type = event["type"]
    data = event["data"]["object"]

    # 1. Checkout concluído
    if event_type == "checkout.session.completed":
        customer_id = data.get("customer")
        subscription_id = data.get("subscription")
        user_id = data.get("metadata", {}).get("user_id")

        user = None
        if user_id:
            user = db.query(models.Usuario).filter(models.Usuario.id == user_id).first()
        if not user and customer_id:
            user = db.query(models.Usuario).filter(models.Usuario.stripe_customer_id == customer_id).first()

        if user:
            user.plano = "pro"
            user.status_assinatura = "active"
            user.stripe_customer_id = customer_id
            user.stripe_subscription_id = subscription_id
            db.commit()

    # 2. Assinatura atualizada (renovação / troca)
    elif event_type == "customer.subscription.updated":
        customer_id = data.get("customer")
        status = data.get("status")
        user = db.query(models.Usuario).filter(models.Usuario.stripe_customer_id == customer_id).first()
        if user:
            user.status_assinatura = status
            user.plano = "pro" if status == "active" else "free"
            db.commit()

    # 3. Assinatura cancelada/encerrada
    elif event_type == "customer.subscription.deleted":
        customer_id = data.get("customer")
        user = db.query(models.Usuario).filter(models.Usuario.stripe_customer_id == customer_id).first()
        if user:
            user.plano = "free"
            user.status_assinatura = "canceled"
            user.stripe_subscription_id = None
            db.commit()

    # 4. Falha na renovação
    elif event_type == "invoice.payment_failed":
        customer_id = data.get("customer")
        user = db.query(models.Usuario).filter(models.Usuario.stripe_customer_id == customer_id).first()
        if user:
            user.status_assinatura = "past_due"
            user.plano = "free"
            db.commit()

    return {"status": "success"}

# Painel Admin de Métricas
@router.get("/admin/metricas")
def painel_metricas_admin(admin: dict = Depends(exigir_admin), db: Session = Depends(get_db)):
    contagem_planos = db.query(
        models.Usuario.plano, func.count(models.Usuario.id)
    ).group_by(models.Usuario.plano).all()

    usuarios_por_plano = {plano: total for plano, total in contagem_planos}

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
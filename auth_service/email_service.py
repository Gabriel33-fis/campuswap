import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

MAILTRAP_HOST = os.getenv("MAILTRAP_HOST", "sandbox.smtp.mailtrap.io")
MAILTRAP_PORT = int(os.getenv("MAILTRAP_PORT", 2525))
MAILTRAP_USER = os.getenv("MAILTRAP_USER")
MAILTRAP_PASS = os.getenv("MAILTRAP_PASS")
# A variavel do compose se chama PUBLIC_BASE_URL (BASE_PUBLIC_URL e mantida por compatibilidade).
BASE_PUBLIC_URL = (
    os.getenv("PUBLIC_BASE_URL")
    or os.getenv("BASE_PUBLIC_URL")
    or os.getenv("FRONTEND_URL")
    or "http://localhost:8207"
).rstrip("/")

def enviar_email_recuperacao(destinatario: str, token: str):
    link_recuperacao = f"{BASE_PUBLIC_URL}/?token_reset={token}"
    
    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Recuperação de Senha - CampuSwap"
    msg["From"] = "no-reply@campuswap.app"
    msg["To"] = destinatario

    corpo_html = f"""
    <h2>Recuperação de Senha</h2>
    <p>Você solicitou a redefinição de senha da sua conta.</p>
    <p>Clique no link abaixo para criar uma nova senha (válido por 15 minutos):</p>
    <p><a href="{link_recuperacao}" style="background-color: #007bff; color: white; padding: 10px 15px; text-decoration: none; border-radius: 5px;">Redefinir Minha Senha</a></p>
    <p>Ou copie e cole o link: {link_recuperacao}</p>
    <p>Se não solicitou, ignore este e-mail.</p>
    """
    
    msg.attach(MIMEText(corpo_html, "html"))

    with smtplib.SMTP(MAILTRAP_HOST, MAILTRAP_PORT) as server:
        server.starttls()
        server.login(MAILTRAP_USER, MAILTRAP_PASS)
        server.sendmail(msg["From"], [destinatario], msg.as_string())
import os
import pymysql
from urllib.parse import urlparse

DATABASE_URL = os.getenv("DATABASE_URL")

def get_db_connection():
    """Retorna uma conexão direta PyMySQL compatível com cursor(dictionary=True)."""
    if DATABASE_URL and DATABASE_URL.startswith("mysql"):
        # Trata formato mysql://user:pass@host:port/dbname ou mysql+pymysql://...
        clean_url = DATABASE_URL.replace("mysql+pymysql://", "mysql://")
        parsed = urlparse(clean_url)
        return pymysql.connect(
            host=parsed.hostname or "mariadb",
            port=parsed.port or 3306,
            user=parsed.username or "root",
            password=parsed.password or "",
            database=parsed.path.lstrip("/"),
            autocommit=True,
            charset="utf8mb4"
        )
    else:
        # Fallback para variáveis individuais de ambiente
        return pymysql.connect(
            host=os.getenv("DB_HOST", "mariadb"),
            port=int(os.getenv("DB_PORT", "3306")),
            user=os.getenv("DB_USER", "root"),
            password=os.getenv("DB_PASSWORD", "rootpassword"),
            database=os.getenv("DB_NAME", "campuswap_db"),
            autocommit=True,
            charset="utf8mb4"
        )

# Mantém get_db por compatibilidade caso alguma rota use Depends(get_db)
def get_db():
    conn = get_db_connection()
    try:
        yield conn
    finally:
        conn.close()
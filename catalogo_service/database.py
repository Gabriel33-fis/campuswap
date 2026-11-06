import os
import sqlite3
import pymysql
from urllib.parse import urlparse

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:////app/catalogo.db")

class SQLiteRowDictCursor:
    """Emulador de cursor compatível com cursor(dictionary=True) do PyMySQL."""
    def __init__(self, cursor):
        self.cursor = cursor

    @property
    def lastrowid(self):
        return self.cursor.lastrowid

    @property
    def rowcount(self):
        return self.cursor.rowcount

    def execute(self, sql, params=None):
        # Converte placeholders %s do MySQL para ? do SQLite se estiver usando SQLite
        if params is not None:
            sql = sql.replace("%s", "?")
            return self.cursor.execute(sql, params)
        return self.cursor.execute(sql)

    def fetchall(self):
        rows = self.cursor.fetchall()
        return [dict(row) for row in rows]

    def fetchone(self):
        row = self.cursor.fetchone()
        return dict(row) if row else None

    def close(self):
        self.cursor.close()

    def __getattr__(self, name):
        # Repassa qualquer outro método ou atributo não implementado para o cursor nativo
        return getattr(self.cursor, name)

class SQLiteConnectionWrapper:
    def __init__(self, conn):
        self.conn = conn

    def cursor(self, dictionary=True):
        return SQLiteRowDictCursor(self.conn.cursor())

    def commit(self):
        self.conn.commit()

    def close(self):
        self.conn.close()

def get_db_connection():
    # Se for MySQL
    if DATABASE_URL.startswith("mysql"):
        clean_url = DATABASE_URL.replace("mysql+pymysql://", "mysql://")
        parsed = urlparse(clean_url)
        return pymysql.connect(
            host=parsed.hostname or os.getenv("DB_HOST", "localhost"),
            port=parsed.port or int(os.getenv("DB_PORT", 3306)),
            user=parsed.username or os.getenv("DB_USER", "root"),
            password=parsed.password or os.getenv("DB_PASSWORD", ""),
            database=parsed.path.lstrip("/") or os.getenv("DB_NAME", "campuswap_db"),
            autocommit=True,
            charset="utf8mb4"
        )
    
    # Se for SQLite (padrão configurado no docker-compose)
    db_path = DATABASE_URL.replace("sqlite:////", "/").replace("sqlite:///", "")
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    
    # Garante que a tabela itens_marketplace exista com todas as colunas
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS itens_marketplace (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            titulo TEXT NOT NULL,
            descricao TEXT,
            preco REAL DEFAULT 0.0,
            condicao TEXT DEFAULT 'semi-novo',
            modalidade TEXT DEFAULT 'venda',
            status TEXT DEFAULT 'disponivel',
            categoria_id INTEGER DEFAULT 1,
            usuario_id INTEGER DEFAULT 1,
            imagem_url TEXT,
            criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Migra tabelas antigas existentes que ainda não tinham condicao ou imagem_url
    try:
        cursor.execute("ALTER TABLE itens_marketplace ADD COLUMN condicao TEXT DEFAULT 'semi-novo'")
    except sqlite3.OperationalError:
        pass  # Coluna condicao já existe

    try:
        cursor.execute("ALTER TABLE itens_marketplace ADD COLUMN imagem_url TEXT")
    except sqlite3.OperationalError:
        pass  # Coluna imagem_url já existe

    conn.commit()
    return SQLiteConnectionWrapper(conn)

def get_db():
    conn = get_db_connection()
    try:
        yield conn
    finally:
        conn.close()
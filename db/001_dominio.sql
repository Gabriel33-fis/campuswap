-- ============================================================
-- CampuSwap — 001_dominio.sql
-- Script versionado do esquema do domínio (MariaDB 10.11).
-- Espelha os modelos SQLAlchemy de auth_service/models.py e
-- catalogo_service/models.py. Na aplicação, as tabelas também são
-- criadas automaticamente na inicialização (metadata.create_all);
-- este script permite criar/inspecionar o esquema manualmente.
-- É idempotente (IF NOT EXISTS). Não contém senhas nem dados reais.
--
-- Uso:
--   docker exec -i gabriel_mariadb mariadb -u <usuario> -p <banco> < db/001_dominio.sql
-- ============================================================

-- Usuários e planos (auth_service) ---------------------------
CREATE TABLE IF NOT EXISTS usuarios (
  id INT NOT NULL AUTO_INCREMENT,
  nome VARCHAR(100) NOT NULL,
  email VARCHAR(150) NOT NULL,
  senha_hash VARCHAR(255) NOT NULL,
  papel VARCHAR(50) NOT NULL DEFAULT 'aluno',          -- aluno | moderador | admin
  is_premium BOOLEAN NOT NULL DEFAULT 0,
  plano VARCHAR(20) NOT NULL DEFAULT 'free',            -- free | pro
  status_assinatura VARCHAR(30) NOT NULL DEFAULT 'active',
  stripe_customer_id VARCHAR(255) NULL,
  stripe_subscription_id VARCHAR(255) NULL,
  PRIMARY KEY (id),
  UNIQUE KEY ix_usuarios_email (email)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Recuperação de senha (token de uso único) ------------------
CREATE TABLE IF NOT EXISTS recuperacoes_senha (
  id INT NOT NULL AUTO_INCREMENT,
  usuario_id INT NOT NULL,
  token VARCHAR(255) NOT NULL,
  expira_em DATETIME NOT NULL,
  usado BOOLEAN DEFAULT 0,
  criado_em DATETIME NULL,
  PRIMARY KEY (id),
  UNIQUE KEY ix_recuperacoes_senha_token (token)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Eventos de webhook do Stripe já processados (idempotência) -
CREATE TABLE IF NOT EXISTS eventos_stripe (
  id INT NOT NULL AUTO_INCREMENT,
  event_id VARCHAR(255) NOT NULL,
  tipo VARCHAR(100) NOT NULL,
  processado_em DATETIME NULL,
  PRIMARY KEY (id),
  UNIQUE KEY ix_eventos_stripe_event_id (event_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Anúncios de materiais (catalogo_service) -------------------
CREATE TABLE IF NOT EXISTS itens (
  id INT NOT NULL AUTO_INCREMENT,
  titulo VARCHAR(150) NOT NULL,
  descricao TEXT NULL,
  categoria_id INT NULL DEFAULT 1,
  condicao VARCHAR(50) NULL,
  modalidade VARCHAR(50) NULL,                          -- venda | troca | emprestimo | doacao
  preco FLOAT NULL DEFAULT 0,
  usuario_id INT NULL DEFAULT 1,
  foto_key VARCHAR(255) NULL,
  imagem_url VARCHAR(255) NULL,
  status VARCHAR(50) NULL DEFAULT 'ativo',
  criado_em DATETIME NULL,
  PRIMARY KEY (id),
  KEY ix_itens_usuario_id (usuario_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Promoção a moderador/admin é feita só por quem administra o
-- banco (o cadastro público sempre cria 'aluno'). Exemplo:
-- UPDATE usuarios SET papel = 'admin' WHERE email = 'seu-email@exemplo.com';

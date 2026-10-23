# 📦 CampuSwap — Seu marketplace no campus

Atividade desenvolvida para a disciplina de Introdução à Computação em Nuvem (ISW055), sob orientação do professor [siriani](https://github.com/siriani).

---

## 1. Nome e Frase
**CampuSwap**: O marketplace universitário para compra, venda, empréstimo e troca de livros, calculadoras e materiais acadêmicos essenciais dentro do próprio campus.

---

## 2. A Dor
Estudantes universitários enfrentam custos elevados semestrais com livros didáticos, calculadoras científicas, jalecos e materiais técnicos que frequentemente são utilizados por apenas um semestre ou disciplina específica. Ao mesmo tempo, veteranos acumulam esses materiais parados em repúblicas e quartos sem um canal confiável e rápido para repassá-los. As alternativas atuais (grupos de WhatsApp desorganizados ou marketplaces genéricos) geram desconfiança, golpes, custos de frete inviáveis e desencontros geográficos.

---

## 3. Persona
* **Nome Fictício:** Lucas Ferreira, 21 anos.
* **Perfil:** Estudante do 3º semestre de Engenharia/Tecnologia na Fatec.
* **Rotina e Desafio:** Lucas tem orçamento mensal restrito e precisa de um livro de Cálculo e de uma calculadora gráfica para a matéria do semestre atual. Uma calculadora nova custa mais de R$ 250,00 na loja. Ele tenta garimpar ofertas no grupo de calouros/veteranos no WhatsApp, mas as mensagens se perdem em conversas paralelas e ele não sabe se o vendedor realmente estuda na faculdade ou se o produto está em bom estado.

---

## 4. Concorrentes e Alternativas

1. **Grupos de WhatsApp / Telegram de Turma:**
   * *O que faz mal:* Totalmente desestruturado; anúncios somem no feed de conversas, não há histórico de preços, não há busca por categoria e inexiste controle de itens já vendidos ou emprestados.
2. **Mercado Livre / Enjoei / OLX:**
   * *O que faz mal:* O valor do frete muitas vezes supera o preço do livro usado; transações distantes sem pontos de encontro práticos no campus e sem validação de vínculo universitário.
3. **Murais Físicos e Boca a Boca no Campus:**
   * *O que faz mal:* Alcance extremamente restrito, falta de fotos detalhadas dos itens e lentidão para fechar negociações.

---

## 5. Proposta de Valor
O CampuSwap transforma a comunidade acadêmica em uma rede prática e confiável de economia circular, viabilizando transações presenciais seguras dentro da faculdade e com custo zero de entrega.

---

## 6. Modelo de Receita com Stripe

O CampuSwap opera em modelo **Freemium** monetizado via **Stripe**:

* **Plano Gratuito (Free Tier):**
  * Até 3 anúncios ativos simultaneamente.
  * Modos de negociação básicos: Venda direta e Empréstimo simples.
  * Busca local com raio padrão de entrega no campus.
* **Plano Estudante Pro (Assinatura Stripe — R$ 6,90 / mês):**
  * Anúncios ilimitados simultâneos (ideal para veteranos e formandos repassando múltiplos itens).
  * Destaque visual dos anúncios na página inicial do catálogo.
  * Acesso liberado às modalidades de **Troca Direta (Swap)** e alertas de busca prioritária para livros concorridos.

---

## 7. Métricas de Sucesso
* **Aquisição:** Número de cadastros de estudantes verificados por mês.
* **Ativação:** Volume de anúncios de livros e materiais publicados semanalmente.
* **Receita (MRR):** Receita Recorrente Mensal gerada a partir das assinaturas do Plano Pro via Stripe.
* **Retenção / Liquidez:** Percentual de itens marcados como vendidos/emprestados em até 14 dias após a publicação.

---

## 8. Lean Canvas

| Bloco | Resposta do CampuSwap |
| :--- | :--- |
| **Problema** | 1. Alto custo de materiais acadêmicos pontuais. <br>2. Materiais úteis parados com veteranos. <br>3. Insegurança e desorganização nos grupos de WhatsApp. |
| **Segmentos de Clientes** | Universitários da Fatec e campi regionais (graduandos buscando economia e formandos querendo desapegar de materiais). |
| **Proposta Única de Valor** | Compartilhe mais, compre menos: o marketplace acadêmico confiável de troca, empréstimo e compra no seu campus. |
| **Solução** | 1. Catálogo estruturado de itens acadêmicos (livros, calculadoras, apostilas). <br>2. 4 modos de transação: Comprar, Vender, Emprestar e Trocar. <br>3. Perfis autenticados com foto e pontos de encontro no campus. |
| **Canais** | Divulgação em semanas acadêmicas, centros acadêmicos (CAs), atléticas, cartazes nos murais do campus e grupos institucionais. |
| **Fontes de Receita** | Assinatura mensal do Plano Estudante Pro (R$ 6,90/mês) processada via Stripe Checkout e Webhooks. |
| **Estrutura de Custos** | Servidor virtual (IaaS Proxmox), custos transacionais/taxas do Stripe e taxa de domínio/registro. |
| **Métricas-Chave** | Cadastros mensais, anúncios criados, taxa de conversão Free → Pro e MRR. |
| **Vantagem Injusta** | Base local fechada no campus (confiança entre pares), proximidade física imediata eliminando frete e validação acadêmica. |

---

## 🗺️ Mapa de Reaproveitamento Arquitetural

| Peça do Projeto Atual (Tom Hanks) | O que vira no SaaS (CampuSwap) | Exemplo Prático no Novo Domínio |
| :--- | :--- | :--- |
| **Catálogo de Filmes (TMDB)** | **Catálogo de Materiais Acadêmicos** | Título do livro/item, categoria (Cálculo, Eletrônica, etc.), estado de conservação, modalidade (Venda/Troca/Empréstimo). |
| **Microsserviço de Login + RBAC** | **Gestão de Usuários e Perfis do Campus** | Papéis: `usuario` (estudante comum), `pro` (assinante com anúncios ilimitados) e `admin` (moderação do campus). |
| **Logs e Auditoria (Redis)** | **Eventos de Marketplace** | Eventos assíncronos: `"anuncio_criado"`, `"item_marcado_vendido"`, `"solicitacao_troca"`. |
| **Upload de Perfil (MinIO)** | **Fotos dos Produtos e Materiais** | Armazenamento de imagens reais dos livros/calculadoras e fotos de perfil dos estudantes no bucket S3 MinIO. |
| **Plano Premium (Stripe)** | **Assinatura Estudante Pro** | Assinatura recorrente que destrava anúncios ilimitados e destaque visual nas buscas do campus. |
| **Swagger, CI/CD e Observabilidade** | **Infraestrutura e Contratos de API** | Documentação OpenAPI unificada, métricas Prometheus e deploy automatizado no Portainer. |

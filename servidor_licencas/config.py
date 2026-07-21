"""
config.py — Configuração do servidor de licenças
====================================================
Diferente da plataforma do cliente, este servidor é desenhado para correr
num único host que você controla (Railway, Render, Fly.io, VPS própria).
Segredos vêm de variáveis de ambiente — nunca commitar credenciais reais
neste ficheiro.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# ─── Banco de dados ────────────────────────────────────────────────────────────
# Configurável via LICENCA_DB_PATH para apontar a um volume persistente em
# hospedagem com filesystem efémero (Railway, Render, Fly.io) — sem isto,
# o banco de clientes é APAGADO a cada redeploy ou reinício do container.
# Ver DEPLOY.md para instruções de configuração do volume em cada provedor.
DB_PATH = Path(os.getenv("LICENCA_DB_PATH", str(BASE_DIR / "servidor_licencas.db")))

# ─── Chave de assinatura ────────────────────────────────────────────────────────
# Caminho do ficheiro .pem com a chave privada Ed25519. Em produção, prefira
# montar isto via secret do provedor de hospedagem (variável de ambiente
# LICENCA_CHAVE_PRIVADA_PEM com o conteúdo do .pem) em vez de um ficheiro em
# disco — sobretudo em plataformas com filesystem efémero (Railway, Fly.io).
CHAVE_PRIVADA_PEM_PATH = BASE_DIR / "chave_privada_SERVIDOR.pem"
CHAVE_PRIVADA_PEM_ENV  = os.getenv("LICENCA_CHAVE_PRIVADA_PEM", "")

# ─── Planos e validade ───────────────────────────────────────────────────────
# Quantos dias de validade um token recebe a cada emissão/renovação.
# Deve ser >= ao intervalo de cobrança do plano (mensal = 31 para
# tolerar atrasos de processamento do gateway de pagamento).
DIAS_VALIDADE_TOKEN = int(os.getenv("LICENCA_DIAS_VALIDADE_TOKEN", "31"))

PLANOS_VALIDOS = ("manutencao", "premium")

# ─── Autenticação de administração ───────────────────────────────────────────
# Token separado do token dos clientes — protege endpoints /admin/*
# (emissão manual, revogação, consulta de clientes). Gerado automaticamente
# na primeira execução se não fornecido via variável de ambiente.
ADMIN_TOKEN = os.getenv("LICENCA_ADMIN_TOKEN", "")

# ─── Webhooks — segredos dos processadores de pagamento ──────────────────────
# Preencher com os segredos reais obtidos no painel de cada processador.
# Vazio = endpoint correspondente rejeita tudo (fail-closed, nunca aceita
# eventos não assinados).
STRIPE_WEBHOOK_SECRET       = os.getenv("STRIPE_WEBHOOK_SECRET", "")
MERCADOPAGO_WEBHOOK_SECRET  = os.getenv("MERCADOPAGO_WEBHOOK_SECRET", "")

# Tolerância de relógio para validação de timestamp em webhooks Stripe
# (protege contra replay attacks de eventos antigos capturados).
STRIPE_TOLERANCIA_SEG = int(os.getenv("STRIPE_TOLERANCIA_SEG", "300"))

# ─── API / Servidor ──────────────────────────────────────────────────────────
API_HOST = os.getenv("LICENCA_API_HOST", "0.0.0.0")
API_PORT = int(os.getenv("LICENCA_API_PORT", "8100"))

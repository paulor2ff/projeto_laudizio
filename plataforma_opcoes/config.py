"""
config.py — Configurações centrais da plataforma
=================================================
Edite este arquivo para:
  - Adicionar/remover tickers monitorados
  - Preencher credenciais de autenticação (quando disponíveis)
  - Ajustar intervalos de coleta e caminhos
"""

from pathlib import Path
import sys

# ─── Caminhos ────────────────────────────────────────────────────────────────
# Path(__file__) aponta para dentro da pasta de extracção temporária quando
# o programa roda como binário compilado (Nuitka --onefile) — essa pasta é
# recriada e APAGADA a cada execução. Sem esta checagem, o banco de dados
# (opcoes_b3.db) seria recriado do zero toda vez que o utilizador fechasse
# e reabrisse o programa, perdendo todo o histórico coletado silenciosamente.
# "__compiled__" é injectado automaticamente pelo Nuitka em todo módulo
# compilado — é a forma documentada de detectar isso em runtime.
#
# CUIDADO — sys.executable NÃO resolve isto em modo --onefile (o modo usado
# por build/build_nuitka.sh): dentro do binário compilado, sys.executable
# aponta para dentro da MESMA pasta de extracção temporária efémera acima
# (algo como /tmp/onefile_<pid>_<hash>/python), que muda a cada execução —
# ou seja, o bug persistiria de qualquer forma. Confirmado compilando e
# executando de facto: duas execuções seguidas do mesmo binário resultavam
# em BASE_DIR diferente em cada uma. sys.argv[0] é o que o bootstrap onefile
# do Nuitka resolve para o caminho real e estável do executável — testado
# e estável em múltiplas execuções, caminho absoluto, caminho relativo, e
# invocado a partir de outro directório.
IS_COMPILED = "__compiled__" in globals()

if IS_COMPILED:
    BASE_DIR = Path(sys.argv[0]).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent

DB_PATH  = BASE_DIR / "opcoes_b3.db"
LOG_PATH = BASE_DIR / "plataforma.log"

# ─── Tickers monitorados ─────────────────────────────────────────────────────
# Adicione ou remova conforme necessário.
# Sufixo .SA identifica ações da B3 no Yahoo Finance.
TICKERS = [
    "BBAS3.SA",
]

# Ticker padrão para operações individuais via CLI
TICKER_PADRAO = "BBAS3.SA"

# ─── Coleta histórica ────────────────────────────────────────────────────────
PERIODO_PADRAO  = "2y"     # Opções: 1mo 3mo 6mo 1y 2y 5y max
CASAS_DECIMAIS  = "0.0001"

# ─── Scheduler — pregão B3 (segunda a sexta) ─────────────────────────────────
PREGAO_INICIO   = "10:00"  # Horário de Brasília
PREGAO_FIM      = "18:00"
INTERVALO_SEG   = 15       # Segundos entre atualizações em tempo real
TIMEZONE        = "America/Sao_Paulo"

# ─── API / Dashboard ─────────────────────────────────────────────────────────
API_HOST        = "0.0.0.0"  # Escuta em todas as interfaces — alterar para "127.0.0.1" se
                            # o dashboard for exclusivamente local
API_PORT        = 8000
API_RELOAD      = False    # NUNCA alterar para True em produção

# Bearer token para proteger os endpoints da API.
# Gerado automaticamente na primeira execução se vazio.
# Para recriar: deixar vazio e reiniciar o servidor.
# Usar alternativa via variável de ambiente: export OPCOES_API_TOKEN="seu-token"
import os as _os
API_TOKEN = _os.getenv("OPCOES_API_TOKEN", "")  # preenchido por inicializar_token()

# ─── Modelo de Greeks ────────────────────────────────────────────────────────
# "black_scholes" para opções europeias
# "binomial"      para opções americanas (mais preciso, mais lento)
# "auto"          detecta pelo campo modelo e aplica o mais adequado
MODELO_GREEKS   = "auto"
PASSOS_BINOMIAL = 100      # Número de passos na árvore binomial

# ─── IQ — Índice de Qualidade de Liquidez (aproximação local) ────────────────
IQ_PESO_VOLUME     = 0.40  # Peso do volume financeiro
IQ_PESO_NEGOCIOS   = 0.35  # Peso do número de negócios
IQ_PESO_PRESENCA   = 0.25  # Peso da presença histórica (últimos 30 pregões)
IQ_JANELA_PREGOES  = 30    # Pregões para cálculo de presença histórica
IQ_LIMIAR_ILIQUIDO = 20    # IQ abaixo deste valor = ilíquido

# ─── Taxa livre de risco (CDI via API do Banco Central) ───────────────────────
BCB_CDI_URL     = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.12/dados/ultimos/1?formato=json"
CDI_FALLBACK    = 0.1075   # Valor de fallback se a API do BCB estiver indisponível (10.75% a.a.)

# ─── Autenticação opcoes.net.br ───────────────────────────────────────────────
# Preencha quando as URLs dos endpoints forem mapeadas via inspeção de rede.
#
# Como preencher:
#   1. Faça login no opcoes.net.br com o DevTools aberto (F12 → Network → XHR)
#   2. Localize a chamada de login (geralmente POST para /Account/Login ou similar)
#   3. Copie a URL completa para AUTH_LOGIN_URL
#   4. Localize a chamada que retorna os dados das opções após autenticação
#   5. Copie a URL base para AUTH_OPCOES_URL (substitua o ticker por {ticker})
#
AUTH_LOGIN_URL  = ""       # TODO: ex: "https://opcoes.net.br/Account/Login"
AUTH_OPCOES_URL = ""       # TODO: ex: "https://opcoes.net.br/api/opcoes/{ticker}"
AUTH_USERNAME   = ""       # TODO: seu e-mail de cadastro
AUTH_PASSWORD   = ""       # TODO: sua senha (considere usar variável de ambiente)
#
# Alternativa mais segura com variáveis de ambiente:
#   export OPCOES_USER="seu@email.com"
#   export OPCOES_PASS="suasenha"
# E descomente as linhas abaixo:
# import os
# AUTH_USERNAME = os.getenv("OPCOES_USER", "")
# AUTH_PASSWORD = os.getenv("OPCOES_PASS", "")

# Headers que simulam um navegador real (necessário para evitar bloqueio)
AUTH_HEADERS = {
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
    "Accept":          "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With":"XMLHttpRequest",
    "Referer":         "https://opcoes.net.br/opcoes/bovespa/BBAS3",
}

# ─── Retry ───────────────────────────────────────────────────────────────────
MAX_RETRIES     = 3
RETRY_DELAY_SEG = 2

# ─── Licenciamento (subscrição) ────────────────────────────────────────────────
# Funcionalidades pagas (automação via scheduler, notificações de alertas, etc.)
# são protegidas por um token assinado (Ed25519) emitido pelo servidor de
# licenças após confirmação de pagamento. Verificação 100% local — não requer
# rede a cada execução, apenas para renovar o token periodicamente.
#
# ATENÇÃO — CHAVE DE TESTE: a chave abaixo foi gerada localmente para
# desenvolvimento (ver dev_tools/). Substituir pela chave pública real do
# servidor de licenças antes de distribuir a aplicação a clientes — a chave
# pública pode ser exposta livremente (só verifica, não assina).
LICENCA_CHAVE_PUBLICA  = "4SpGfgcgSUNNfBVAXjPNk0O9iWgWDpv8hFlw8wzb3+A="

# Período de carência após o vencimento: acesso total mantido, apenas com
# aviso. Após isso, funcionalidades pagas (scheduler, notificações) ficam
# bloqueadas até renovação, mas dados já coletados continuam acessíveis.
LICENCA_DIAS_CARENCIA   = 7    # dias com acesso total após vencer
LICENCA_DIAS_DEGRADADO  = 23   # dias adicionais bloqueado antes de... ainda preserva leitura
                               # total: 7+23 = 30 dias até bloqueio completo de novas ações

# URL do servidor de licenças para renovação automática do token.
# Vazio = renovação online desactivada; o sistema usa apenas o token em
# cache até expirar a carência. Preencher quando o servidor existir.
LICENCA_URL_RENOVACAO   = _os.getenv("OPCOES_LICENCA_URL", "")

# ─── Notificações de Alertas ───────────────────────────────────────────────────
# Quando um alerta dispara (após o cooldown), além de gravar no log, o sistema
# pode enviar por e-mail e/ou webhook (Discord, Slack, Telegram, ou endpoint
# próprio). Ambos são opcionais e independentes — deixe em branco para desativar.
#
# E-mail (via SMTP — funciona com Gmail, Outlook, etc. usando "senha de app"):
NOTIF_EMAIL_ATIVO     = False
NOTIF_EMAIL_SMTP_HOST = "smtp.gmail.com"
NOTIF_EMAIL_SMTP_PORT = 587
NOTIF_EMAIL_USER      = ""    # TODO: seu e-mail remetente
NOTIF_EMAIL_PASS      = ""    # TODO: senha de app (não a senha normal da conta)
NOTIF_EMAIL_PARA      = ""    # TODO: e-mail destinatário (pode ser o mesmo)
#
# Alternativa segura via variável de ambiente:
#   export OPCOES_EMAIL_USER="seu@email.com"
#   export OPCOES_EMAIL_PASS="sua-senha-de-app"
NOTIF_EMAIL_USER = _os.getenv("OPCOES_EMAIL_USER", NOTIF_EMAIL_USER)
NOTIF_EMAIL_PASS = _os.getenv("OPCOES_EMAIL_PASS", NOTIF_EMAIL_PASS)
#
# Webhook genérico (Discord, Slack, Telegram via bot, ou endpoint próprio).
# Formato do payload enviado: {"content": "texto"} — compatível nativamente
# com Discord. Para Slack, use {"text": "texto"} — ajustável em alertas.py.
NOTIF_WEBHOOK_URL    = _os.getenv("OPCOES_WEBHOOK_URL", "")  # TODO: cole a URL do webhook
NOTIF_WEBHOOK_FORMATO = "discord"   # "discord" | "slack" | "generico"

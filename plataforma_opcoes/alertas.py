"""
alertas.py — Sistema de alertas configuráveis
==============================================
Tipos de alerta suportados:
  delta      — Delta do contrato cruza um limiar
  preco      — Preço do ativo base sobe/desce de um valor
  variacao   — Variação % do prêmio supera um limiar
  iq         — IQ do contrato sobe acima de um valor mínimo
  vol_impl   — Volatilidade implícita cruza um limiar

Uso via CLI:
  python cli.py --alertas --ticker BBAS3
  python cli.py --alerta-add --ticker BBAS3 --tipo-alerta delta \
                --codigo BBAS3C02000A --operador ">" --valor 0.7
  python cli.py --alerta-remover --id 1
"""

import logging
import json
import os
import time
from datetime import datetime
from typing   import List, Optional

from config   import BASE_DIR

log = logging.getLogger(__name__)

ALERTAS_FILE = BASE_DIR / "alertas.json"
_LOCK_FILE   = BASE_DIR / "alertas.lock"
_LOCK_TIMEOUT_SEG     = 5.0
_LOCK_RETRY_INTERVALO = 0.05

# ─── Schema de um alerta ─────────────────────────────────────────────────────
# {
#   "id":             int,
#   "ticker":         str,            # ex: "BBAS3.SA"
#   "tipo":           str,            # "delta"|"preco"|"variacao"|"iq"|"vol_impl"
#   "codigo":         str|None,       # código do contrato (None para alertas de preço do ativo)
#   "operador":       str,            # ">" | "<" | ">=" | "<="
#   "valor":          float,          # limiar
#   "activo":         bool,
#   "disparado":      bool,           # True se já disparou alguma vez (informativo)
#   "criado_em":       str,
#   "ultimo_val":      float|None,    # último valor verificado
#   "cooldown_min":    float,         # minutos mínimos entre disparos consecutivos
#   "ultimo_disparo":  str|None,      # ISO timestamp do último disparo efectivo
# }

DEFAULT_COOLDOWN_MIN = 15.0  # minutos — evita re-disparo a cada ciclo de 30s

# ─── Lock de ficheiro (concorrência) ──────────────────────────────────────────
# CLI, API e scheduler podem correr ao mesmo tempo e ler/escrever alertas.json
# simultaneamente. Sem proteção, dois processos podem ler o mesmo estado
# antigo e cada um escrever a sua versão modificada — a última escrita
# ganha e a outra perde-se silenciosamente (lost update). Este lock,
# baseado em criação exclusiva atómica de ficheiro (os.O_EXCL), funciona
# em Linux/macOS/Windows sem dependências externas.

class _FileLock:
    def __enter__(self):
        inicio = time.monotonic()
        while True:
            try:
                fd = os.open(str(_LOCK_FILE), os.O_CREAT | os.O_EXCL | os.O_RDWR)
                os.close(fd)
                return self
            except FileExistsError:
                if time.monotonic() - inicio > _LOCK_TIMEOUT_SEG:
                    # Lock preso (processo anterior terminou sem libertar) —
                    # assumir stale e forçar remoção em vez de bloquear para sempre.
                    log.warning(
                        "Lock de alertas.json preso há mais de %.1fs — forçando remoção.",
                        _LOCK_TIMEOUT_SEG
                    )
                    try: _LOCK_FILE.unlink()
                    except FileNotFoundError: pass
                    continue
                time.sleep(_LOCK_RETRY_INTERVALO)

    def __exit__(self, exc_type, exc_val, exc_tb):
        try: _LOCK_FILE.unlink()
        except FileNotFoundError: pass
        return False


def _alertas_lock() -> _FileLock:
    return _FileLock()


# ─── Persistência ────────────────────────────────────────────────────────────

def _carregar() -> List[dict]:
    if not ALERTAS_FILE.exists():
        return []
    try:
        return json.loads(ALERTAS_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        log.error("Erro ao carregar alertas: %s", exc)
        return []

def _guardar(alertas: List[dict]) -> None:
    """
    Escrita atómica: grava num ficheiro temporário e usa os.replace()
    para substituir o ficheiro final numa única operação atómica do SO.
    Protege contra ficheiro corrompido/truncado se o processo for
    interrompido a meio da escrita (queda de energia, kill -9, etc.).
    """
    tmp_path = ALERTAS_FILE.with_suffix(".json.tmp")
    tmp_path.write_text(
        json.dumps(alertas, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    os.replace(str(tmp_path), str(ALERTAS_FILE))

# ─── CRUD ────────────────────────────────────────────────────────────────────

def listar_alertas(ticker: Optional[str] = None) -> List[dict]:
    """Devolve todos os alertas, ou filtrado por ticker."""
    alertas = _carregar()
    if ticker:
        t = ticker if ticker.endswith(".SA") else f"{ticker}.SA"
        alertas = [a for a in alertas if a["ticker"] == t]
    return alertas


def adicionar_alerta(
    ticker:       str,
    tipo:         str,
    operador:     str,
    valor:        float,
    codigo:       Optional[str] = None,
    cooldown_min: float         = DEFAULT_COOLDOWN_MIN,
) -> dict:
    """
    Adiciona um novo alerta.
    tipo:         delta | preco | variacao | iq | vol_impl
    operador:     > | < | >= | <=
    valor:        limiar numérico
    cooldown_min: minutos mínimos entre disparos consecutivos (evita spam
                  enquanto a condição se mantém verdadeira em ciclos seguidos)
    """
    tipos_validos     = ("delta","preco","variacao","iq","vol_impl")
    operadores_validos = (">","<",">=","<=")
    if tipo not in tipos_validos:
        raise ValueError(f"Tipo inválido: '{tipo}'. Use: {tipos_validos}")
    if operador not in operadores_validos:
        raise ValueError(f"Operador inválido: '{operador}'. Use: {operadores_validos}")
    if cooldown_min < 0:
        raise ValueError(f"cooldown_min não pode ser negativo: {cooldown_min}")

    ticker_fmt = ticker if ticker.endswith(".SA") else f"{ticker}.SA"

    with _alertas_lock():
        alertas = _carregar()
        novo_id = max((a["id"] for a in alertas), default=0) + 1

        alerta = {
            "id":             novo_id,
            "ticker":         ticker_fmt,
            "tipo":           tipo,
            "codigo":         codigo,
            "operador":       operador,
            "valor":          float(valor),
            "activo":         True,
            "disparado":      False,
            "criado_em":      datetime.now().isoformat(),
            "ultimo_val":     None,
            "cooldown_min":   float(cooldown_min),
            "ultimo_disparo": None,
        }
        alertas.append(alerta)
        _guardar(alertas)

    log.info("Alerta #%d criado: %s %s %s %s %s (cooldown=%.0fmin)",
             novo_id, ticker_fmt, tipo, codigo or "ativo", operador, valor, cooldown_min)
    return alerta


def remover_alerta(id_alerta: int) -> bool:
    """Remove um alerta por ID. Devolve True se removido."""
    with _alertas_lock():
        alertas = _carregar()
        antes = len(alertas)
        alertas = [a for a in alertas if a["id"] != id_alerta]
        if len(alertas) == antes:
            return False
        _guardar(alertas)
    log.info("Alerta #%d removido.", id_alerta)
    return True


def desactivar_alerta(id_alerta: int) -> bool:
    with _alertas_lock():
        alertas = _carregar()
        for a in alertas:
            if a["id"] == id_alerta:
                a["activo"] = False
                _guardar(alertas)
                return True
        return False

# ─── Notificações ────────────────────────────────────────────────────────────
# Canal real de aviso quando um alerta dispara — além do log.warning() já
# existente. Cada canal é independente e opcional; uma falha num canal nunca
# impede a verificação dos demais alertas (sempre via try/except isolado).

def _formatar_mensagem(alerta: dict) -> str:
    codigo = alerta.get("codigo") or "ativo"
    return (
        f"🔔 Alerta #{alerta['id']} disparado\n"
        f"Ticker: {alerta['ticker'].replace('.SA','')}\n"
        f"Tipo: {alerta['tipo']} ({codigo})\n"
        f"Condição: {alerta['operador']} {alerta['valor']}\n"
        f"Valor actual: {alerta.get('valor_actual')}"
    )


def _enviar_email(alerta: dict) -> bool:
    """Envia notificação por e-mail via SMTP. Devolve True se enviado com sucesso."""
    from config import (NOTIF_EMAIL_ATIVO, NOTIF_EMAIL_SMTP_HOST, NOTIF_EMAIL_SMTP_PORT,
                        NOTIF_EMAIL_USER, NOTIF_EMAIL_PASS, NOTIF_EMAIL_PARA)
    if not NOTIF_EMAIL_ATIVO or not (NOTIF_EMAIL_USER and NOTIF_EMAIL_PASS and NOTIF_EMAIL_PARA):
        return False
    try:
        import smtplib
        from email.mime.text import MIMEText
        msg = MIMEText(_formatar_mensagem(alerta), "plain", "utf-8")
        msg["Subject"] = f"[Opções B3] Alerta #{alerta['id']} — {alerta['ticker'].replace('.SA','')}"
        msg["From"]    = NOTIF_EMAIL_USER
        msg["To"]      = NOTIF_EMAIL_PARA
        with smtplib.SMTP(NOTIF_EMAIL_SMTP_HOST, NOTIF_EMAIL_SMTP_PORT, timeout=10) as smtp:
            smtp.starttls()
            smtp.login(NOTIF_EMAIL_USER, NOTIF_EMAIL_PASS)
            smtp.send_message(msg)
        log.info("Notificação por e-mail enviada para alerta #%d.", alerta["id"])
        return True
    except Exception as exc:
        log.warning("Falha ao enviar e-mail de alerta #%d: %s", alerta["id"], exc)
        return False


def _enviar_webhook(alerta: dict) -> bool:
    """Envia notificação via webhook (Discord/Slack/genérico). Devolve True se enviado."""
    from config import NOTIF_WEBHOOK_URL, NOTIF_WEBHOOK_FORMATO
    if not NOTIF_WEBHOOK_URL:
        return False
    try:
        import requests
        texto = _formatar_mensagem(alerta)
        if NOTIF_WEBHOOK_FORMATO == "discord":
            payload = {"content": texto}
        elif NOTIF_WEBHOOK_FORMATO == "slack":
            payload = {"text": texto}
        else:
            payload = {"alerta": alerta, "mensagem": texto}
        resp = requests.post(NOTIF_WEBHOOK_URL, json=payload, timeout=10)
        resp.raise_for_status()
        log.info("Notificação por webhook enviada para alerta #%d.", alerta["id"])
        return True
    except Exception as exc:
        log.warning("Falha ao enviar webhook de alerta #%d: %s", alerta["id"], exc)
        return False


def _notificar(alerta: dict) -> None:
    """
    Dispara todos os canais de notificação configurados para um alerta.
    Cada canal falha de forma isolada — uma falha de e-mail não impede
    a tentativa de webhook, e nenhuma falha de notificação afecta a
    verificação dos demais alertas (chamado de dentro de um try/except
    mais amplo em verificar_alertas).
    """
    _enviar_email(alerta)
    _enviar_webhook(alerta)

# ─── Verificação ─────────────────────────────────────────────────────────────

def _avaliar(valor_actual: float, operador: str, limiar: float) -> bool:
    return {
        ">":  valor_actual >  limiar,
        "<":  valor_actual <  limiar,
        ">=": valor_actual >= limiar,
        "<=": valor_actual <= limiar,
    }.get(operador, False)


def verificar_alertas(ticker: str) -> List[dict]:
    """
    Verifica todos os alertas activos para o ticker.
    Devolve lista de alertas que dispararam neste ciclo.

    Concorrência: o lock só é mantido durante a leitura inicial e a
    escrita final — NÃO durante cotacao_atual()/consultar_opcoes()/
    notificações (que envolvem rede e podem demorar vários segundos).
    Manter o lock preso durante chamadas lentas arriscaria que outro
    processo o considere "stale" e o remova à força enquanto ainda
    está legitimamente em uso. Na escrita final, o estado mais recente
    do ficheiro é relido e só os campos dos alertas efectivamente
    avaliados são mesclados por ID — alterações concorrentes a OUTROS
    alertas (ex: um --alerta-add a meio do ciclo) não são perdidas.
    """
    from database import consultar_opcoes
    from collector import cotacao_atual

    with _alertas_lock():
        alertas = _carregar()

    disparados   = []
    atualizacoes = {}   # id_alerta -> {campos a mesclar de volta}

    ticker_fmt = ticker if ticker.endswith(".SA") else f"{ticker}.SA"
    activos    = [a for a in alertas if a["activo"] and a["ticker"] == ticker_fmt]
    if not activos:
        return []

    # Obter dados actuais
    cot      = cotacao_atual(ticker_fmt)
    preco_at = cot.get("preco", 0.0)
    var_at   = cot.get("variacao_pct", 0.0)
    # IMPORTANTE: converter sqlite3.Row para dict — Row não tem .get(),
    # apenas __getitem__. Sem esta conversão, row.get(...) abaixo lança
    # AttributeError silenciosamente capturado pelo except, e nenhum
    # alerta dispara nunca.
    opcoes = {row["codigo"]: dict(row) for row in consultar_opcoes(ticker_fmt)}

    for alerta in activos:
        tipo     = alerta["tipo"]
        operador = alerta["operador"]
        limiar   = alerta["valor"]
        codigo   = alerta.get("codigo")
        val_atual = None

        try:
            if tipo == "preco":
                val_atual = preco_at
            elif tipo == "variacao":
                val_atual = abs(var_at)
            elif codigo and codigo in opcoes:
                row = opcoes[codigo]
                val_map = {
                    "delta":    row.get("delta"),
                    "iq":       row.get("iq_calc"),
                    "vol_impl": row.get("vol_implícita"),
                    "variacao": row.get("variacao_pct"),
                }
                val_atual = val_map.get(tipo)

            if val_atual is None:
                continue

            campos_alerta = {"ultimo_val": val_atual}

            if _avaliar(val_atual, operador, limiar):
                # Cooldown: só dispara de novo se já passou cooldown_min
                # desde o último disparo efectivo. Sem isto, o mesmo alerta
                # repete a cada ciclo do scheduler (30s) enquanto a condição
                # se mantiver — inundando o log sem agregar informação nova.
                cooldown_min = alerta.get("cooldown_min", DEFAULT_COOLDOWN_MIN)
                ultimo_disp  = alerta.get("ultimo_disparo")
                em_cooldown  = False
                if ultimo_disp:
                    decorrido_min = (
                        datetime.now() - datetime.fromisoformat(ultimo_disp)
                    ).total_seconds() / 60.0
                    em_cooldown = decorrido_min < cooldown_min

                if not em_cooldown:
                    campos_alerta["disparado"]      = True
                    campos_alerta["ultimo_disparo"] = datetime.now().isoformat()
                    disparado_completo = {**alerta, **campos_alerta, "valor_actual": val_atual}
                    disparados.append(disparado_completo)
                    log.warning(
                        "🔔 ALERTA #%d [%s] %s %s %s %s → actual=%s",
                        alerta["id"], ticker_fmt, tipo,
                        codigo or "ativo", operador, limiar, val_atual
                    )
                    # Notificação real (e-mail/webhook) — falhas não interrompem o ciclo
                    try:
                        _notificar(disparado_completo)
                    except Exception as exc_notif:
                        log.debug("Erro ao notificar alerta #%d: %s", alerta["id"], exc_notif)
                else:
                    log.debug(
                        "Alerta #%d em cooldown (%.1f/%.0fmin) — suprimido",
                        alerta["id"], decorrido_min, cooldown_min
                    )

            atualizacoes[alerta["id"]] = campos_alerta

        except Exception as exc:
            log.debug("Erro ao verificar alerta #%d: %s", alerta["id"], exc)

    if atualizacoes:
        with _alertas_lock():
            alertas_actual = _carregar()
            for a in alertas_actual:
                if a["id"] in atualizacoes:
                    a.update(atualizacoes[a["id"]])
            _guardar(alertas_actual)

    return disparados


def verificar_todos_alertas() -> List[dict]:
    """Verifica alertas para todos os tickers com alertas activos."""
    alertas = _carregar()
    tickers = {a["ticker"] for a in alertas if a["activo"]}
    todos_disparados = []
    for t in tickers:
        todos_disparados.extend(verificar_alertas(t))
    return todos_disparados

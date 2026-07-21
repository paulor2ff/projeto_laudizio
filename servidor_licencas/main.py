"""
main.py — Servidor de licenças
===================================
Endpoints:
  POST /webhooks/stripe          — recebe eventos Stripe
  POST /webhooks/mercadopago     — recebe notificações Mercado Pago
  POST /licencas/validar         — chamado pelo cliente para renovar o token
  GET  /admin/clientes           — lista clientes (requer admin token)
  GET  /admin/clientes/{id}      — detalhe de um cliente
  POST /admin/clientes/{id}/emitir   — emite/estende licença manualmente
  POST /admin/clientes/{id}/revogar  — revoga/suspende manualmente
  GET  /admin/eventos            — auditoria de eventos de pagamento recebidos
  GET  /chave-publica             — devolve a chave pública (para configurar clientes)
  GET  /saude                     — healthcheck
"""

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request

import database
import emissor
from admin import inicializar_admin_token, verificar_admin
from chaves import chave_publica_base64
from config import DIAS_VALIDADE_TOKEN, PLANOS_VALIDOS
from webhooks_stripe import verificar_assinatura_stripe, processar_evento_stripe
from webhooks_mercadopago import verificar_assinatura_mp, processar_notificacao_mp

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    database.inicializar()
    inicializar_admin_token()
    yield


app = FastAPI(title="Servidor de Licenças — Plataforma de Opções B3", lifespan=lifespan)

# ─── Rate limit para /licencas/validar ───────────────────────────────────────
# Mesmo padrão usado em plataforma_opcoes/api.py para /coletar/{ticker}.
# cliente_id não exige mais nada além de si mesmo para renovar um token —
# mitigado hoje pela entropia dos IDs vindos do Stripe/Mercado Pago
# ("stripe_cus_...", "mp_..."), mas um limite por IP é uma defesa extra
# barata contra tentativas de adivinhação em sequência.
import time as _time
_validar_timestamps: dict = {}   # {ip: [timestamps]}
_RATE_LIMIT_REQ = 10             # máximo de requisições
_RATE_LIMIT_JAN = 60             # por janela de N segundos


def _checar_rate_limit(ip: str) -> bool:
    agora = _time.time()
    historico = _validar_timestamps.setdefault(ip, [])
    historico[:] = [t for t in historico if agora - t < _RATE_LIMIT_JAN]
    if len(historico) >= _RATE_LIMIT_REQ:
        return False
    historico.append(agora)
    return True


# ─── Saúde e chave pública (públicos, sem autenticação) ──────────────────────

@app.get("/saude")
def saude():
    return {"status": "ok", "tempo": datetime.now().isoformat(timespec="seconds")}


@app.get("/chave-publica")
def obter_chave_publica():
    """
    Devolve a chave pública do servidor — segura para expor publicamente,
    serve apenas para verificar assinaturas, nunca para criá-las. Use
    este valor em LICENCA_CHAVE_PUBLICA no config.py do cliente.
    """
    return {"chave_publica_base64": chave_publica_base64()}


# ─── Webhook Stripe ──────────────────────────────────────────────────────────

@app.post("/webhooks/stripe")
async def webhook_stripe(request: Request):
    corpo_bruto = await request.body()
    assinatura  = request.headers.get("stripe-signature", "")

    if not verificar_assinatura_stripe(corpo_bruto, assinatura):
        raise HTTPException(status_code=400, detail="Assinatura Stripe inválida.")

    import json
    evento = json.loads(corpo_bruto)
    resultado = processar_evento_stripe(evento)
    return {"recebido": True, **resultado}


# ─── Webhook Mercado Pago ──────────────────────────────────────────────────────

def _buscar_recurso_mp_real(tipo: str, data_id: str) -> dict:
    """
    Implementação REAL — chama a API do Mercado Pago. Requer
    MERCADOPAGO_ACCESS_TOKEN configurado. Não testável neste ambiente
    por falta de acesso de rede a api.mercadopago.com; testada via
    injecção de função fake nos testes automatizados.
    """
    import os
    import requests
    access_token = os.getenv("MERCADOPAGO_ACCESS_TOKEN", "")
    if not access_token:
        raise RuntimeError("MERCADOPAGO_ACCESS_TOKEN não configurado.")
    url = f"https://api.mercadopago.com/{tipo}/{data_id}"
    resp = requests.get(url, headers={"Authorization": f"Bearer {access_token}"}, timeout=10)
    resp.raise_for_status()
    return resp.json()


@app.post("/webhooks/mercadopago")
async def webhook_mercadopago(request: Request):
    data_id      = request.query_params.get("data.id", "")
    tipo         = request.query_params.get("type", "")
    x_signature  = request.headers.get("x-signature", "")
    x_request_id = request.headers.get("x-request-id", "")

    if not verificar_assinatura_mp(data_id, x_signature, x_request_id):
        raise HTTPException(status_code=400, detail="Assinatura Mercado Pago inválida.")

    resultado = processar_notificacao_mp(tipo, data_id, _buscar_recurso_mp_real)
    return {"recebido": True, **resultado}


# ─── Renovação do cliente ──────────────────────────────────────────────────────

@app.post("/licencas/validar")
def validar_licenca(cliente_id: str, request: Request):
    """
    Chamado por _tentar_renovar_online() do lado do cliente. Devolve um
    token novo se o cliente estiver activo na base de dados — a validade
    do novo token reflecte directamente o estado actual da assinatura.
    """
    ip = request.client.host if request.client else "desconhecido"
    if not _checar_rate_limit(ip):
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit: máx {_RATE_LIMIT_REQ} pedidos/{_RATE_LIMIT_JAN}s por IP",
        )
    try:
        token = emissor.emitir_token_para_cliente(cliente_id)
        return token
    except emissor.ClienteNaoEncontradoError as exc:
        raise HTTPException(status_code=404, detail="Cliente não encontrado.") from exc
    except emissor.ClienteNaoActivoError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


# ─── Administração ───────────────────────────────────────────────────────────

@app.get("/admin/clientes")
def admin_listar_clientes(status: Optional[str] = None, _: bool = Depends(verificar_admin)):
    return {"clientes": database.listar_clientes(status)}


@app.get("/admin/clientes/{cliente_id}")
def admin_obter_cliente(cliente_id: str, _: bool = Depends(verificar_admin)):
    cliente = database.obter_cliente(cliente_id)
    if not cliente:
        raise HTTPException(status_code=404, detail="Cliente não encontrado.")
    return cliente


@app.post("/admin/clientes/{cliente_id}/emitir")
def admin_emitir(
    cliente_id: str,
    plano: str,
    dias: int = DIAS_VALIDADE_TOKEN,
    email: Optional[str] = None,
    nome: Optional[str] = None,
    _: bool = Depends(verificar_admin),
):
    """
    Emite ou estende manualmente a licença de um cliente — útil para
    vendas manuais, cortesias, ou correcção de problemas de cobrança
    sem depender de um webhook.
    """
    if plano not in PLANOS_VALIDOS:
        raise HTTPException(status_code=400, detail=f"Plano inválido. Use: {PLANOS_VALIDOS}")
    valido_ate = (datetime.now() + timedelta(days=dias)).isoformat(timespec="seconds")
    cliente = database.upsert_cliente(
        cliente_id=cliente_id, email=email, nome=nome, plano=plano,
        valido_ate=valido_ate, status="activo",
    )
    database.registar_evento(cliente_id, "emissao_manual", "admin", {
        "plano": plano, "dias": dias, "valido_ate": valido_ate,
    })
    log.info("Emissão manual: cliente=%s plano=%s válido_ate=%s", cliente_id, plano, valido_ate)
    return cliente


@app.post("/admin/clientes/{cliente_id}/revogar")
def admin_revogar(cliente_id: str, _: bool = Depends(verificar_admin)):
    if not database.actualizar_status(cliente_id, "cancelado"):
        raise HTTPException(status_code=404, detail="Cliente não encontrado.")
    database.registar_evento(cliente_id, "revogacao_manual", "admin", {})
    log.info("Revogação manual: cliente=%s", cliente_id)
    return {"cliente_id": cliente_id, "status": "cancelado"}


@app.get("/admin/eventos")
def admin_listar_eventos(
    cliente_id: Optional[str] = None, limite: int = 100,
    _: bool = Depends(verificar_admin),
):
    return {"eventos": database.listar_eventos(cliente_id, limite)}

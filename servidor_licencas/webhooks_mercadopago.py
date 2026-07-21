"""
webhooks_mercadopago.py — Recepção e processamento de notificações Mercado Pago
====================================================================================
Esquema de assinatura diferente do Stripe: cabeçalhos x-signature e
x-request-id, manifest construído a partir do id do recurso (vindo da
query string da própria URL do webhook) + request-id + timestamp.

IMPORTANTE — diferença estrutural do Stripe: o Mercado Pago envia apenas
uma notificação leve ("algo mudou no recurso X") — não o conteúdo
completo do recurso. Para saber o que de facto mudou (status da
assinatura, próxima data de cobrança), é necessário fazer uma chamada de
volta à API REST do Mercado Pago usando o id recebido e o access token
da sua conta. Essa chamada está parametrizada via injecção de
dependência (buscar_recurso_fn) para ser testável sem rede — em produção,
passar a função real que chama api.mercadopago.com.
"""

import hashlib
import hmac
import logging
from datetime import datetime, timedelta
from typing import Callable

from config import MERCADOPAGO_WEBHOOK_SECRET, DIAS_VALIDADE_TOKEN
from database import upsert_cliente, obter_cliente_por_mp_subscription, registar_evento, actualizar_status

log = logging.getLogger(__name__)


def verificar_assinatura_mp(
    data_id: str,
    x_signature: str,
    x_request_id: str,
    segredo: str = None,
) -> bool:
    """
    Verifica a assinatura HMAC-SHA256 de uma notificação Mercado Pago.

    data_id: o id do recurso, vindo da query string da URL do webhook
             (?data.id=xxxxx) — DEVE ser usado em minúsculas, conforme
             documentado pelo Mercado Pago.
    x_signature: cabeçalho completo, formato "ts=...,v1=..."
    x_request_id: cabeçalho x-request-id da requisição
    """
    segredo = segredo if segredo is not None else MERCADOPAGO_WEBHOOK_SECRET
    if not segredo:
        log.warning("MERCADOPAGO_WEBHOOK_SECRET não configurado — rejeitando (fail-closed).")
        return False

    try:
        partes = dict(
            item.split("=", 1) for item in x_signature.split(",") if "=" in item
        )
        ts = partes["ts"]
        assinatura_recebida = partes["v1"]
    except (KeyError, ValueError) as exc:
        log.warning("Cabeçalho x-signature malformado: %s", exc)
        return False

    manifest = f"id:{data_id.lower()};request-id:{x_request_id};ts:{ts};"
    assinatura_esperada = hmac.new(
        segredo.encode("utf-8"), manifest.encode("utf-8"), hashlib.sha256
    ).hexdigest()

    return hmac.compare_digest(assinatura_esperada, assinatura_recebida)


def _validade_padrao() -> str:
    return (datetime.now() + timedelta(days=DIAS_VALIDADE_TOKEN)).isoformat(timespec="seconds")


def processar_notificacao_mp(
    tipo: str,
    data_id: str,
    buscar_recurso_fn: Callable[[str, str], dict],
) -> dict:
    """
    Processa uma notificação MP já com assinatura verificada.

    buscar_recurso_fn(tipo, data_id) -> dict: função injectada que busca
    o recurso completo na API do Mercado Pago. Em produção, deve fazer
    GET https://api.mercadopago.com/v1/{tipo}/{data_id} com o access
    token da conta. Aqui é parametrizada para permitir testes sem rede.
    """
    registar_evento(None, tipo, "mercadopago", {"tipo": tipo, "data_id": data_id})

    if tipo != "subscription_preapproval":
        return {"acao": "ignorado", "motivo": f"tipo não tratado: {tipo}"}

    recurso = buscar_recurso_fn(tipo, data_id)

    subscription_id = recurso.get("id")
    status_mp        = recurso.get("status")  # "authorized" | "cancelled" | "paused" | ...
    payer_email      = recurso.get("payer_email")
    plano            = (recurso.get("external_reference") or "manutencao")

    cliente_id = f"mp_{subscription_id}"
    cliente = obter_cliente_por_mp_subscription(subscription_id)

    if status_mp == "authorized":
        valido_ate = _validade_padrao()
        upsert_cliente(
            cliente_id=cliente_id, email=payer_email, nome=None, plano=plano,
            valido_ate=valido_ate, status="activo",
            mp_subscription_id=subscription_id,
        )
        log.info("Assinatura MP autorizada/renovada: cliente=%s válido_ate=%s",
                  cliente_id, valido_ate)
        return {"acao": "activado_ou_renovado", "cliente_id": cliente_id, "valido_ate": valido_ate}

    if status_mp in ("cancelled", "paused"):
        if cliente:
            actualizar_status(cliente["id"], "cancelado" if status_mp == "cancelled" else "suspenso")
            log.info("Assinatura MP %s: cliente=%s", status_mp, cliente["id"])
            return {"acao": status_mp, "cliente_id": cliente["id"]}
        return {"acao": "ignorado", "motivo": "cliente não encontrado"}

    return {"acao": "ignorado", "motivo": f"status MP não tratado: {status_mp}"}

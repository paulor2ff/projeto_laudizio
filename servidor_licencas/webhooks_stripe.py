"""
webhooks_stripe.py — Recepção e processamento de eventos Stripe
====================================================================
Verificação de assinatura implementada directamente via HMAC-SHA256,
seguindo o esquema documentado da Stripe (cabeçalho Stripe-Signature),
sem depender do SDK oficial `stripe` — reduz uma dependência pesada para
uma operação que é, no fundo, comparação de HMAC com tolerância de tempo.

Esquema do cabeçalho Stripe-Signature:
    t=1614556800,v1=5257a869e7ecebeda32affa62cdca3fa51cad7e77a0e56ff536d0ce8e108d8bd,v0=...
Mensagem assinada pela Stripe: "{timestamp}.{corpo_bruto_da_requisicao}"
"""

import hashlib
import hmac
import logging
import time
from datetime import datetime, timezone
from typing import Optional

from config import STRIPE_WEBHOOK_SECRET, STRIPE_TOLERANCIA_SEG, DIAS_VALIDADE_TOKEN
from database import upsert_cliente, obter_cliente_por_stripe_subscription, registar_evento, actualizar_status

log = logging.getLogger(__name__)


class AssinaturaInvalidaError(Exception):
    pass


def verificar_assinatura_stripe(
    corpo_bruto: bytes,
    cabecalho_assinatura: str,
    segredo: str = None,
    tolerancia_seg: int = None,
    agora: Optional[float] = None,
) -> bool:
    """
    Verifica a assinatura HMAC-SHA256 de um webhook Stripe.
    `agora` é injectável para testes determinísticos (epoch seconds);
    em produção usa-se time.time() por omissão.
    """
    segredo = segredo if segredo is not None else STRIPE_WEBHOOK_SECRET
    tolerancia_seg = tolerancia_seg if tolerancia_seg is not None else STRIPE_TOLERANCIA_SEG
    agora = agora if agora is not None else time.time()

    if not segredo:
        log.warning("STRIPE_WEBHOOK_SECRET não configurado — rejeitando (fail-closed).")
        return False

    try:
        partes = dict(
            item.split("=", 1) for item in cabecalho_assinatura.split(",") if "=" in item
        )
        timestamp = int(partes["t"])
        assinatura_recebida = partes["v1"]
    except (KeyError, ValueError) as exc:
        log.warning("Cabeçalho Stripe-Signature malformado: %s", exc)
        return False

    if abs(agora - timestamp) > tolerancia_seg:
        log.warning(
            "Timestamp do webhook Stripe fora da tolerância (%ds) — possível replay.",
            tolerancia_seg
        )
        return False

    mensagem_assinada = f"{timestamp}.".encode("utf-8") + corpo_bruto
    assinatura_esperada = hmac.new(
        segredo.encode("utf-8"), mensagem_assinada, hashlib.sha256
    ).hexdigest()

    # Comparação em tempo constante — evita timing attack na verificação
    return hmac.compare_digest(assinatura_esperada, assinatura_recebida)


def _unix_para_iso(timestamp_unix: int) -> str:
    return datetime.fromtimestamp(timestamp_unix, tz=timezone.utc) \
                    .replace(tzinfo=None).isoformat(timespec="seconds")


def _validade_padrao() -> str:
    from datetime import timedelta
    return (datetime.now() + timedelta(days=DIAS_VALIDADE_TOKEN)).isoformat(timespec="seconds")


def processar_evento_stripe(evento: dict) -> dict:
    """
    Processa um evento Stripe já desserializado e verificado. Devolve um
    resumo do que foi feito — útil para logging e para os testes.
    """
    tipo = evento.get("type", "")
    obj  = evento.get("data", {}).get("object", {})

    registar_evento(None, tipo, "stripe", evento)

    if tipo == "checkout.session.completed":
        customer_id      = obj.get("customer")
        subscription_id  = obj.get("subscription")
        email            = obj.get("customer_email") or obj.get("customer_details", {}).get("email")
        plano            = (obj.get("metadata") or {}).get("plano", "manutencao")
        cliente_id       = f"stripe_{customer_id}"

        cliente = upsert_cliente(
            cliente_id=cliente_id, email=email, nome=None, plano=plano,
            valido_ate=_validade_padrao(), status="activo",
            stripe_customer_id=customer_id, stripe_subscription_id=subscription_id,
        )
        log.info("Checkout completo: cliente=%s plano=%s", cliente_id, plano)
        return {"acao": "cliente_activado", "cliente_id": cliente_id}

    if tipo == "invoice.paid":
        subscription_id = obj.get("subscription")
        linhas = obj.get("lines", {}).get("data", [])
        periodo_fim = linhas[0]["period"]["end"] if linhas and "period" in linhas[0] else None
        valido_ate = _unix_para_iso(periodo_fim) if periodo_fim else _validade_padrao()

        cliente = obter_cliente_por_stripe_subscription(subscription_id)
        if not cliente:
            log.warning("invoice.paid para subscription desconhecida: %s", subscription_id)
            return {"acao": "ignorado", "motivo": "cliente não encontrado"}

        upsert_cliente(
            cliente_id=cliente["id"], email=None, nome=None,
            plano=cliente["plano"], valido_ate=valido_ate, status="activo",
        )
        log.info("Renovação paga: cliente=%s válido_ate=%s", cliente["id"], valido_ate)
        return {"acao": "renovado", "cliente_id": cliente["id"], "valido_ate": valido_ate}

    if tipo == "customer.subscription.updated":
        subscription_id = obj.get("id")
        status_stripe    = obj.get("status")  # "active" | "past_due" | "canceled" | ...
        periodo_fim      = obj.get("current_period_end")

        cliente = obter_cliente_por_stripe_subscription(subscription_id)
        if not cliente:
            return {"acao": "ignorado", "motivo": "cliente não encontrado"}

        status_local = "activo" if status_stripe == "active" else "suspenso"
        valido_ate = _unix_para_iso(periodo_fim) if periodo_fim else cliente["valido_ate"]
        upsert_cliente(
            cliente_id=cliente["id"], email=None, nome=None,
            plano=cliente["plano"], valido_ate=valido_ate, status=status_local,
        )
        return {"acao": "actualizado", "cliente_id": cliente["id"], "status": status_local}

    if tipo == "customer.subscription.deleted":
        subscription_id = obj.get("id")
        cliente = obter_cliente_por_stripe_subscription(subscription_id)
        if not cliente:
            return {"acao": "ignorado", "motivo": "cliente não encontrado"}
        actualizar_status(cliente["id"], "cancelado")
        log.info("Assinatura cancelada: cliente=%s", cliente["id"])
        return {"acao": "cancelado", "cliente_id": cliente["id"]}

    if tipo == "invoice.payment_failed":
        subscription_id = obj.get("subscription")
        cliente = obter_cliente_por_stripe_subscription(subscription_id)
        if cliente:
            log.warning("Falha de pagamento: cliente=%s — licença seguirá o ciclo normal "
                        "de carência até valido_ate expirar.", cliente["id"])
        return {"acao": "falha_registada"}

    return {"acao": "ignorado", "motivo": f"tipo de evento não tratado: {tipo}"}

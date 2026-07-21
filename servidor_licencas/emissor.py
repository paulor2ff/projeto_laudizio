"""
emissor.py — Emissão de tokens de licença assinados
=======================================================
CRÍTICO: o formato e a serialização aqui têm de corresponder EXACTAMENTE
ao que licenca.py (módulo cliente, em plataforma_opcoes/) espera ao
verificar. Qualquer divergência na serialização canónica faz a assinatura
não validar do lado do cliente, mesmo sendo criptograficamente correcta.

Formato do payload (mesmos campos, mesma ordem de serialização):
    {
        "cliente_id": str,
        "plano": str,
        "emitido_em": str (ISO 8601, segundos),
        "valido_ate": str (ISO 8601, segundos),
    }

Serialização canónica para assinar: json.dumps(payload, sort_keys=True,
separators=(",", ":")) — chaves ordenadas, sem espaços. Esta é a mesma
função usada em licenca.py do lado do cliente para verificar.
"""

import base64
import json
import logging
from datetime import datetime

from chaves import obter_chave_privada
from database import obter_cliente
from config import PLANOS_VALIDOS

log = logging.getLogger(__name__)


class ClienteNaoEncontradoError(Exception):
    pass


class ClienteNaoActivoError(Exception):
    pass


def _mensagem_canonica(payload: dict) -> bytes:
    """Deve ser idêntica à função homónima em licenca.py do cliente."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def assinar_payload(payload: dict) -> str:
    """Assina um payload já construído e devolve a assinatura em base64."""
    chave = obter_chave_privada()
    assinatura = chave.sign(_mensagem_canonica(payload))
    return base64.b64encode(assinatura).decode("ascii")


def emitir_token(cliente_id: str, plano: str, valido_ate: str) -> dict:
    """
    Constrói e assina um token directamente a partir dos parâmetros
    fornecidos — usado internamente após validar o estado do cliente.
    """
    if plano not in PLANOS_VALIDOS:
        raise ValueError(f"Plano inválido: '{plano}'. Use: {PLANOS_VALIDOS}")

    payload = {
        "cliente_id": cliente_id,
        "plano": plano,
        "emitido_em": datetime.now().isoformat(timespec="seconds"),
        "valido_ate": valido_ate,
    }
    assinatura_b64 = assinar_payload(payload)
    return {"payload": payload, "assinatura": assinatura_b64}


def emitir_token_para_cliente(cliente_id: str) -> dict:
    """
    Emite um token para um cliente já existente na base de dados,
    usando o plano e validade actualmente registados. É isto que o
    endpoint /licencas/validar chama quando o cliente pede renovação.
    """
    cliente = obter_cliente(cliente_id)
    if not cliente:
        raise ClienteNaoEncontradoError(f"Cliente '{cliente_id}' não encontrado.")
    if cliente["status"] != "activo":
        raise ClienteNaoActivoError(
            f"Cliente '{cliente_id}' está com status '{cliente['status']}', não 'activo'."
        )
    return emitir_token(cliente_id, cliente["plano"], cliente["valido_ate"])

"""
chaves.py — Gestão da chave de assinatura do servidor
==========================================================
A chave privada é a única coisa que torna este servidor a autoridade —
quem a tiver pode emitir licenças válidas para qualquer cliente. Por isso:
nunca deve ser commitada num repositório público, nunca deve estar na
mesma máquina que roda a plataforma do cliente, e idealmente deve vir de
um secret manager do provedor de hospedagem em vez de um ficheiro em disco.
"""

import logging
from functools import lru_cache

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

from config import CHAVE_PRIVADA_PEM_PATH, CHAVE_PRIVADA_PEM_ENV

log = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def obter_chave_privada() -> Ed25519PrivateKey:
    """
    Carrega a chave privada do servidor. Prioridade:
    1. Variável de ambiente LICENCA_CHAVE_PRIVADA_PEM (conteúdo do .pem) —
       preferível em hospedagem com filesystem efémero.
    2. Ficheiro local chave_privada_SERVIDOR.pem — conveniente para
       desenvolvimento local ou VPS com disco persistente.
    Resultado em cache — a chave só é carregada uma vez por processo.
    """
    if CHAVE_PRIVADA_PEM_ENV:
        pem_bytes = CHAVE_PRIVADA_PEM_ENV.encode("utf-8")
        log.info("Chave privada carregada de variável de ambiente.")
        return serialization.load_pem_private_key(pem_bytes, password=None)

    if CHAVE_PRIVADA_PEM_PATH.exists():
        pem_bytes = CHAVE_PRIVADA_PEM_PATH.read_bytes()
        log.info("Chave privada carregada de %s.", CHAVE_PRIVADA_PEM_PATH)
        return serialization.load_pem_private_key(pem_bytes, password=None)

    raise RuntimeError(
        "Nenhuma chave privada de assinatura encontrada. Defina "
        "LICENCA_CHAVE_PRIVADA_PEM ou coloque chave_privada_SERVIDOR.pem "
        "na pasta do servidor. Para gerar uma nova chave, use a função "
        "gerar_novo_par() — mas isto invalida todas as licenças já emitidas "
        "com a chave anterior, pois os clientes confiam na chave pública antiga."
    )


def chave_publica_base64() -> str:
    """Devolve a chave pública correspondente, em base64 — segura para expor."""
    import base64
    chave = obter_chave_privada()
    pub_bytes = chave.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return base64.b64encode(pub_bytes).decode("ascii")


def gerar_novo_par(caminho_pem) -> str:
    """
    Gera um novo par de chaves e grava a privada no caminho indicado.
    ATENÇÃO: usar isto substitui a identidade do servidor — todas as
    licenças já emitidas (assinadas com a chave antiga) deixam de
    validar nos clientes que ainda confiam na chave pública antiga.
    Só deve ser usado na configuração inicial, nunca em produção já
    em uso, a menos que se pretenda forçar reemissão de todas as licenças.
    """
    import base64
    chave = Ed25519PrivateKey.generate()
    pem = chave.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    caminho_pem.write_bytes(pem)
    obter_chave_privada.cache_clear()
    pub_bytes = chave.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return base64.b64encode(pub_bytes).decode("ascii")

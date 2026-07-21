"""
emitir_licenca_teste.py — Utilitário de DESENVOLVIMENTO para emitir tokens
=============================================================================
ATENÇÃO: Este script NÃO deve ser distribuído com a aplicação ao cliente
final, e a chave privada (chave_privada_TESTE.pem) NUNCA deve sair desta
pasta de desenvolvimento. Em produção, a chave privada fica apenas no
servidor de licenças — nunca na mesma máquina que o cliente.

Este script simula o que o futuro servidor de licenças fará ao confirmar
um pagamento: assina um token com a chave privada e grava um ficheiro
pronto para ser importado via:

    python cli.py --licenca-importar dev_tools/licenca_teste.json

Uso:
    # Licença válida por 30 dias a partir de agora:
    python dev_tools/emitir_licenca_teste.py --cliente-id cli_0001 --plano manutencao

    # Licença que já está em período de carência (venceu há 3 dias):
    python dev_tools/emitir_licenca_teste.py --cliente-id cli_0001 --plano manutencao --vencido-ha 3

    # Licença em modo degradado (venceu há 15 dias):
    python dev_tools/emitir_licenca_teste.py --cliente-id cli_0001 --plano manutencao --vencido-ha 15

    # Licença bloqueada (venceu há 45 dias):
    python dev_tools/emitir_licenca_teste.py --cliente-id cli_0001 --plano manutencao --vencido-ha 45

    # Token com assinatura propositalmente inválida (para testar rejeição):
    python dev_tools/emitir_licenca_teste.py --cliente-id cli_0001 --plano manutencao --corromper
"""

import argparse
import base64
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

THIS_DIR     = Path(__file__).resolve().parent
CHAVE_PRIV   = THIS_DIR / "chave_privada_TESTE.pem"
CHAVE_PUB    = THIS_DIR / "chave_publica_TESTE.txt"


def _obter_ou_criar_chave_privada() -> Ed25519PrivateKey:
    if CHAVE_PRIV.exists():
        pem = CHAVE_PRIV.read_bytes()
        return serialization.load_pem_private_key(pem, password=None)
    chave = Ed25519PrivateKey.generate()
    pem = chave.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    CHAVE_PRIV.write_bytes(pem)
    pub_bytes = chave.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    CHAVE_PUB.write_text(base64.b64encode(pub_bytes).decode("ascii"))
    return chave


def _mensagem_canonica(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def emitir_token(
    chave_privada: Ed25519PrivateKey,
    cliente_id: str,
    plano: str,
    dias_validade: int = 30,
    vencido_ha: int = None,
    corromper: bool = False,
) -> dict:
    agora = datetime.now()
    if vencido_ha is not None:
        valido_ate = agora - timedelta(days=vencido_ha)
    else:
        valido_ate = agora + timedelta(days=dias_validade)

    payload = {
        "cliente_id": cliente_id,
        "plano": plano,
        "emitido_em": agora.isoformat(timespec="seconds"),
        "valido_ate": valido_ate.isoformat(timespec="seconds"),
    }
    mensagem = _mensagem_canonica(payload)
    assinatura = chave_privada.sign(mensagem)
    assinatura_b64 = base64.b64encode(assinatura).decode("ascii")

    if corromper:
        # Inverte um byte da assinatura — simula um token adulterado ou
        # corrompido, para testar se a verificação rejeita correctamente.
        assinatura_bytes = bytearray(base64.b64decode(assinatura_b64))
        assinatura_bytes[0] ^= 0xFF
        assinatura_b64 = base64.b64encode(bytes(assinatura_bytes)).decode("ascii")

    return {"payload": payload, "assinatura": assinatura_b64}


def main():
    parser = argparse.ArgumentParser(description="Emite tokens de licença de TESTE.")
    parser.add_argument("--cliente-id", required=True)
    parser.add_argument("--plano", default="manutencao")
    parser.add_argument("--dias", type=int, default=30, metavar="N",
                        help="Dias de validade a partir de agora (padrão: 30)")
    parser.add_argument("--vencido-ha", type=int, default=None, metavar="N",
                        help="Emite um token já vencido há N dias (para testar carência/degradado/bloqueio)")
    parser.add_argument("--corromper", action="store_true",
                        help="Corrompe a assinatura propositalmente (testar rejeição)")
    parser.add_argument("--saida", default=str(THIS_DIR / "licenca_teste.json"))
    args = parser.parse_args()

    chave = _obter_ou_criar_chave_privada()
    token = emitir_token(
        chave, args.cliente_id, args.plano,
        dias_validade=args.dias, vencido_ha=args.vencido_ha, corromper=args.corromper,
    )

    Path(args.saida).write_text(json.dumps(token, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"✅ Token gerado em: {args.saida}")
    print(f"   cliente_id  : {token['payload']['cliente_id']}")
    print(f"   plano       : {token['payload']['plano']}")
    print(f"   valido_ate  : {token['payload']['valido_ate']}")
    if args.corromper:
        print("   ⚠️  Assinatura propositalmente corrompida (--corromper)")
    print("\nChave pública para config.py (LICENCA_CHAVE_PUBLICA):")
    print(f"   {CHAVE_PUB.read_text().strip()}")
    print("\nPara importar:")
    print(f"   python cli.py --licenca-importar {args.saida}")


if __name__ == "__main__":
    sys.exit(main())

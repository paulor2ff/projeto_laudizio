"""
gerar_chave_producao.py — Gera a chave de assinatura REAL do servidor
==========================================================================
Executar UMA ÚNICA VEZ, na configuração inicial do servidor de produção.

ATENÇÃO: gerar uma nova chave DEPOIS de já ter clientes activos invalida
todas as licenças já emitidas — os clientes confiam na chave pública
antiga, gravada no LICENCA_CHAVE_PUBLICA do config.py deles. Trocar a
chave do servidor exige reemitir e redistribuir licenças a todos.

Por isso este script RECUSA sobrescrever uma chave já existente, a menos
que --forcar seja explicitamente passado.

Uso:
    python gerar_chave_producao.py
    python gerar_chave_producao.py --saida outro_caminho.pem
    python gerar_chave_producao.py --forcar   # ATENÇÃO: invalida licenças emitidas
"""

import argparse
import base64
import sys
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization


def gerar(caminho_pem: Path, forcar: bool = False) -> str:
    if caminho_pem.exists() and not forcar:
        print(f"❌ {caminho_pem} já existe.")
        print("   Gerar uma nova chave invalida TODAS as licenças já emitidas")
        print("   com a chave actual — os clientes confiam na chave pública antiga.")
        print("   Se tem a certeza do que está a fazer, use --forcar.")
        sys.exit(1)

    chave = Ed25519PrivateKey.generate()
    pem = chave.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    caminho_pem.write_bytes(pem)
    # Permissões restritivas — só o dono pode ler (sem efeito no Windows,
    # mas correcto e relevante em qualquer host Linux real de produção)
    try:
        caminho_pem.chmod(0o600)
    except (NotImplementedError, OSError):
        pass

    pub_bytes = chave.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    pub_b64 = base64.b64encode(pub_bytes).decode("ascii")

    caminho_pub = caminho_pem.with_name(caminho_pem.stem + "_PUBLICA.txt")
    caminho_pub.write_text(pub_b64)

    return pub_b64


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--saida", default="chave_privada_SERVIDOR.pem",
                        help="Caminho do ficheiro .pem a gerar (padrão: chave_privada_SERVIDOR.pem)")
    parser.add_argument("--forcar", action="store_true",
                        help="Sobrescreve uma chave existente — INVALIDA licenças já emitidas")
    args = parser.parse_args()

    caminho = Path(args.saida)
    pub_b64 = gerar(caminho, args.forcar)

    print("=" * 70)
    print("✅ Chave de produção gerada com sucesso")
    print("=" * 70)
    print(f"\n  Chave privada : {caminho.resolve()}")
    print(f"  Chave pública : {caminho.with_name(caminho.stem + '_PUBLICA.txt').resolve()}")
    print("\n  Chave pública (base64) — copiar para cada cliente:")
    print(f"\n    {pub_b64}\n")
    print("  Próximos passos:")
    print("  1. NUNCA commitar o ficheiro .pem em controlo de versão")
    print("  2. Em hospedagem com disco efémero (Railway/Render/Fly.io),")
    print("     copiar o CONTEÚDO do .pem para a variável de ambiente")
    print("     LICENCA_CHAVE_PRIVADA_PEM em vez de depender do ficheiro:")
    print(f"\n       cat {caminho} | pbcopy   # macOS")
    print(f"       cat {caminho}             # Linux — copiar manualmente")
    print("\n  3. Actualizar plataforma_opcoes/config.py em TODOS os clientes:")
    print(f"\n       LICENCA_CHAVE_PUBLICA = \"{pub_b64}\"")
    print("\n" + "=" * 70)


if __name__ == "__main__":
    main()

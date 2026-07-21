"""
admin_cli.py — Ferramenta de linha de comando para o operador
===================================================================
Wrapper sobre os endpoints /admin/* do servidor de licenças. Pensado
especificamente para o fluxo de cobrança manual via PIX: confirmar o
pagamento no extrato e emitir a licença num único comando, sem precisar
de construir chamadas curl à mão.

Configuração (uma vez):
    export LICENCA_SERVIDOR_URL="https://seu-servidor.up.railway.app"
    export LICENCA_ADMIN_TOKEN="seu-token-admin"

Uso:
    python admin_cli.py listar
    python admin_cli.py listar --status activo
    python admin_cli.py emitir --cliente-id joao_silva --plano manutencao \
                                --dias 31 --email joao@x.com --nome "João Silva"
    python admin_cli.py revogar --cliente-id joao_silva
    python admin_cli.py eventos
    python admin_cli.py eventos --cliente-id joao_silva
    python admin_cli.py saude
"""

import argparse
import json
import os
import sys
from pathlib import Path

import requests


def _config(args) -> tuple:
    servidor = args.servidor or os.getenv("LICENCA_SERVIDOR_URL", "")
    token = args.token or os.getenv("LICENCA_ADMIN_TOKEN", "")
    if not servidor:
        print("❌ Servidor não configurado. Use --servidor ou export LICENCA_SERVIDOR_URL=...")
        sys.exit(1)
    return servidor.rstrip("/"), token


def _headers(token: str) -> dict:
    if not token:
        print("❌ Token de admin não configurado. Use --token ou export LICENCA_ADMIN_TOKEN=...")
        sys.exit(1)
    return {"Authorization": f"Bearer {token}"}


def _tratar_resposta(r: requests.Response, contexto: str):
    if r.status_code == 401:
        print("❌ Token de admin inválido ou ausente.")
        sys.exit(1)
    if r.status_code == 404:
        print(f"❌ Não encontrado: {contexto}")
        sys.exit(1)
    if r.status_code >= 400:
        try:
            detalhe = r.json().get("detail", r.text)
        except Exception:
            detalhe = r.text
        print(f"❌ Erro {r.status_code}: {detalhe}")
        sys.exit(1)
    return r.json()


def cmd_saude(args):
    servidor, _ = _config(args)
    try:
        r = requests.get(f"{servidor}/saude", timeout=10)
        r.raise_for_status()
        print(f"✅ Servidor respondendo: {servidor}")
        print(f"   {r.json()}")
    except requests.exceptions.RequestException as exc:
        print(f"❌ Servidor inacessível em {servidor}: {exc}")
        sys.exit(1)


def cmd_listar(args):
    servidor, token = _config(args)
    params = {"status": args.status} if args.status else {}
    r = requests.get(f"{servidor}/admin/clientes", headers=_headers(token), params=params, timeout=15)
    data = _tratar_resposta(r, "listagem de clientes")
    clientes = data["clientes"]

    if not clientes:
        print("📭 Nenhum cliente encontrado.")
        return

    print(f"\n{'═'*100}")
    print(f"  Clientes ({len(clientes)})")
    print(f"{'═'*100}")
    print(f"  {'ID':<20} {'Email':<28} {'Plano':<14} {'Status':<10} {'Válido até':<20}")
    print("─" * 100)
    for c in clientes:
        print(f"  {c['id']:<20} {(c['email'] or '—'):<28} {c['plano']:<14} "
              f"{c['status']:<10} {c['valido_ate']:<20}")
    print("─" * 100 + "\n")


def cmd_emitir(args):
    servidor, token = _config(args)
    params = {"plano": args.plano, "dias": args.dias}
    if args.email: params["email"] = args.email
    if args.nome:  params["nome"]  = args.nome

    r = requests.post(
        f"{servidor}/admin/clientes/{args.cliente_id}/emitir",
        headers=_headers(token), params=params, timeout=15,
    )
    cliente = _tratar_resposta(r, f"cliente {args.cliente_id}")

    print(f"✅ Licença emitida para '{args.cliente_id}'")
    print(f"   Plano       : {cliente['plano']}")
    print(f"   Válido até  : {cliente['valido_ate']}")

    # Buscar imediatamente o token assinado, pronto para enviar ao cliente
    r2 = requests.post(f"{servidor}/licencas/validar",
                       params={"cliente_id": args.cliente_id}, timeout=15)
    token_assinado = _tratar_resposta(r2, "geração do token assinado")

    saida = Path(args.saida) if args.saida else Path(f"licenca_{args.cliente_id}.json")
    saida.write_text(json.dumps(token_assinado, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n📄 Token assinado gravado em: {saida.resolve()}")
    print("\n   Próximo passo: enviar este ficheiro ao cliente.")
    print("   O cliente activa com:")
    print(f"     python cli.py --licenca-importar {saida.name}")


def cmd_revogar(args):
    servidor, token = _config(args)
    r = requests.post(f"{servidor}/admin/clientes/{args.cliente_id}/revogar",
                      headers=_headers(token), timeout=15)
    resultado = _tratar_resposta(r, f"cliente {args.cliente_id}")
    print(f"✅ Cliente '{args.cliente_id}' revogado — status: {resultado['status']}")


def cmd_eventos(args):
    servidor, token = _config(args)
    params = {"limite": args.limite}
    if args.cliente_id:
        params["cliente_id"] = args.cliente_id
    r = requests.get(f"{servidor}/admin/eventos", headers=_headers(token), params=params, timeout=15)
    data = _tratar_resposta(r, "listagem de eventos")
    eventos = data["eventos"]

    if not eventos:
        print("📭 Nenhum evento registado.")
        return

    print(f"\n{'═'*100}")
    print(f"  Eventos de Pagamento ({len(eventos)})")
    print(f"{'═'*100}")
    print(f"  {'Cliente':<20} {'Tipo':<28} {'Fonte':<14} {'Quando':<20}")
    print("─" * 100)
    for e in eventos:
        print(f"  {(e['cliente_id'] or '—'):<20} {e['tipo']:<28} "
              f"{e['fonte']:<14} {e['processado_em']:<20}")
    print("─" * 100 + "\n")


def main():
    parser = argparse.ArgumentParser(
        description="Ferramenta de administração do servidor de licenças.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--servidor", default="", help="URL do servidor (ou LICENCA_SERVIDOR_URL)")
    parser.add_argument("--token", default="", help="Token de admin (ou LICENCA_ADMIN_TOKEN)")

    sub = parser.add_subparsers(dest="comando", required=True)

    sub.add_parser("saude", help="Verifica se o servidor está respondendo")

    p_listar = sub.add_parser("listar", help="Lista clientes")
    p_listar.add_argument("--status", default=None, help="Filtrar por status (activo, cancelado, suspenso)")

    p_emitir = sub.add_parser("emitir", help="Emite ou estende uma licença e gera o token para enviar ao cliente")
    p_emitir.add_argument("--cliente-id", required=True)
    p_emitir.add_argument("--plano", default="manutencao", choices=["manutencao", "premium"])
    p_emitir.add_argument("--dias", type=int, default=31)
    p_emitir.add_argument("--email", default=None)
    p_emitir.add_argument("--nome", default=None)
    p_emitir.add_argument("--saida", default=None, help="Caminho do ficheiro de token a gerar")

    p_revogar = sub.add_parser("revogar", help="Revoga o acesso de um cliente imediatamente")
    p_revogar.add_argument("--cliente-id", required=True)

    p_eventos = sub.add_parser("eventos", help="Lista o histórico de eventos de pagamento")
    p_eventos.add_argument("--cliente-id", default=None)
    p_eventos.add_argument("--limite", type=int, default=50)

    args = parser.parse_args()

    comandos = {
        "saude": cmd_saude, "listar": cmd_listar, "emitir": cmd_emitir,
        "revogar": cmd_revogar, "eventos": cmd_eventos,
    }
    comandos[args.comando](args)


if __name__ == "__main__":
    main()

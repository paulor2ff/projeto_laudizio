"""
cli.py — Interface de linha de comando unificada
=================================================
Uso:
  python cli.py --coletar [--ticker BBAS3] [--periodo max]
  python cli.py --coletar-opcoes [--ticker BBAS3]
  python cli.py --coletar-todos
  python cli.py --coletar-opcoes-todos
  python cli.py --calcular-greeks-todos
  python cli.py --calcular-greeks [--ticker BBAS3]
  python cli.py --consultar [--ticker BBAS3] [--de AAAA-MM-DD] [--ate AAAA-MM-DD]
  python cli.py --opcoes [--ticker BBAS3] [--tipo CALL] [--vencimento AAAA-MM-DD]
  python cli.py --resumo
  python cli.py --exportar [--ticker BBAS3] [--de AAAA-MM-DD] [--formato csv|xlsx|pdf]
  python cli.py --exportar-opcoes [--ticker BBAS3] [--formato csv|xlsx|pdf]
  python cli.py --dashboard
  python cli.py --status

  python cli.py --historico-greeks --ticker BBAS3 [--codigo BBAS3C200A]
  python cli.py --purgar [--dias-graca 5]

  python cli.py --alertas [--ticker BBAS3]
  python cli.py --alerta-add --ticker BBAS3 --tipo-alerta delta --operador ">" --valor 0.7 [--codigo BBAS3C200A]
  python cli.py --alerta-remover --id-alerta 3

  python cli.py --licenca
  python cli.py --licenca-importar caminho/para/licenca.json
"""

import argparse
import logging
import sys
from datetime import datetime

from config   import TICKER_PADRAO, TICKERS, API_HOST, API_PORT, LOG_PATH, DB_PATH
from database import inicializar, resumo_geral, consultar_cotacoes, consultar_opcoes

# ─── Logging ─────────────────────────────────────────────────────────────────

def configurar_logging(verbose: bool = False):
    nivel = logging.DEBUG if verbose else logging.INFO
    fmt   = "%(asctime)s  %(levelname)-8s  %(message)s"
    # Forçar nível mesmo se basicConfig já foi chamado antes
    root = logging.getLogger()
    root.setLevel(nivel)
    for h in root.handlers:
        h.setLevel(nivel)
    if not root.handlers:
        handlers = [logging.StreamHandler(sys.stdout)]
        try:
            handlers.append(logging.FileHandler(LOG_PATH, encoding="utf-8"))
        except Exception as _exc:
            print(f"⚠️  Log em arquivo indisponível ({LOG_PATH}): {_exc}", file=sys.stderr)
        logging.basicConfig(level=nivel, format=fmt, datefmt="%Y-%m-%d %H:%M:%S",
                            handlers=handlers)


# ─── Formatação ───────────────────────────────────────────────────────────────
# (definidas em exportadores.py — usadas também na montagem dos PDFs exportados)
from exportadores import _fmt, _fmt_i, _fmt_pct, _fmt_m  # noqa: E402


def _validar_data(valor: str, arg: str) -> str:
    import re as _re
    # Exigir exatamente AAAA-MM-DD com zeros — sem zeros o SQLite ordena errado
    if not _re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(valor)):
        print(f"❌ Formato inválido para {arg}: '{valor}'. Use AAAA-MM-DD com zeros (ex: 2025-01-01).")
        sys.exit(1)
    try:
        datetime.strptime(valor, "%Y-%m-%d")
    except ValueError:
        print(f"❌ Data inexistente para {arg}: '{valor}'.")
        sys.exit(1)
    return valor


def _normalizar_ticker(ticker: str) -> str:
    t = ticker.upper()
    return t if t.endswith(".SA") else f"{t}.SA"


# ─── Impressão de tabelas ─────────────────────────────────────────────────────

SEP82 = "─" * 90

def imprimir_cotacoes(rows, titulo="Cotações"):
    if not rows:
        print("📭 Nenhum registro encontrado.")
        return
    print(f"\n{'═'*90}")
    print(f"  {titulo}")
    print(f"{'═'*90}")
    print(f"  {'Data':<12} {'Abertura':>10} {'Máxima':>10} {'Mínima':>10} "
          f"{'Fechamento':>11} {'Volume':>14}")
    print(SEP82)
    for r in rows:
        data_str = str(r['data'])[:10]  # garante string AAAA-MM-DD mesmo se datetime.date
        print(f"  {data_str:<12} {_fmt(r['abertura']):>10} {_fmt(r['maxima']):>10} "
              f"{_fmt(r['minima']):>10} {_fmt(r['fechamento']):>11} "
              f"{_fmt_i(r['volume']):>14}")
    print(SEP82)
    print(f"  Total: {len(rows):,} registros\n")


def imprimir_opcoes(rows, titulo="Cadeia de Opções"):
    if not rows:
        print("📭 Nenhuma opção encontrada.")
        return
    print(f"\n{'═'*120}")
    print(f"  {titulo}")
    print(f"{'═'*120}")
    print(f"  {'Código':<16} {'Tipo':<6} {'Mon':<4} {'Strike':>8} "
          f"{'Venc':<12} {'Último':>8} {'Var%':>7} {'Nº Neg':>7} "
          f"{'Vol.Fin':>10} {'Delta':>7} {'Gamma':>8} "
          f"{'Theta':>7} {'Vega':>7} {'IQ':>5}")
    print("─" * 120)
    for r in rows:
        mon  = r['otm_atm_itm'] or "—"
        pref = "↑" if r['tipo'] == "CALL" else "↓"
        print(
            f"  {str(r['codigo']):<16} "
            f"{pref+str(r['tipo']):<6} "
            f"{mon:<4} "
            f"{_fmt(r['strike']):>8} "
            f"{str(r['vencimento'] or '—')[:10]:<12} "
            f"{_fmt(r['ultimo']):>8} "
            f"{_fmt_pct(r['variacao_pct']):>7} "
            f"{_fmt_i(r['num_negocios']):>7} "
            f"{_fmt_m(r['vol_financeiro']):>10} "
            f"{_fmt(r['delta'],4):>7} "
            f"{_fmt(r['gamma'],6):>8} "
            f"{_fmt(r['theta'],4):>7} "
            f"{_fmt(r['vega'],4):>7} "
            f"{_fmt(r['iq_calc'],1):>5}"
        )
    print("─" * 120)
    print(f"  Total: {len(rows):,} contratos\n")


def imprimir_resumo():
    r = resumo_geral()
    cot = r["cotacoes"]
    print(f"\n{'═'*55}")
    print("  📊 Resumo da Plataforma")
    print(f"{'═'*55}")
    print(f"  Cotações históricas : {cot.get('total',0):>10,} registros")
    print(f"  Período             : {cot.get('inicio','—')} → {cot.get('fim','—')}")
    print(f"  Contratos de opções : {r['opcoes']:>10,}")
    print(f"  Greeks calculados   : {r['greeks']:>10,}")
    print(f"  Greeks (histórico)  : {r.get('greeks_historico',0):>10,}")
    print(f"  Snapshots intraday  : {r['snapshots']:>10,}")
    print(f"  Tickers monitorados : {len(r['tickers'])}")
    if r['tickers']:
        nomes = ", ".join(t.replace(".SA","") for t in r['tickers'][:10])
        if len(r['tickers']) > 10:
            nomes += f" ... (+{len(r['tickers'])-10})"
        print(f"  → {nomes}")
    print(f"{'═'*55}\n")


def imprimir_historico_greeks(rows, titulo="Histórico de Greeks"):
    if not rows:
        print("📭 Nenhum registro de histórico encontrado.")
        return
    print(f"\n{'═'*108}")
    print(f"  {titulo}")
    print(f"{'═'*108}")
    print(f"  {'Data/Hora':<20} {'Código':<16} {'Delta':>7} {'Gamma':>8} "
          f"{'Theta':>7} {'Vega':>7} {'IQ':>5} {'Modelo':<10}")
    print("─" * 108)
    for r in rows:
        print(
            f"  {str(r['data_hora'])[:19]:<20} "
            f"{str(r['opcao_codigo']):<16} "
            f"{_fmt(r['delta'],4):>7} "
            f"{_fmt(r['gamma'],6):>8} "
            f"{_fmt(r['theta'],4):>7} "
            f"{_fmt(r['vega'],4):>7} "
            f"{_fmt(r['iq_calc'],1):>5} "
            f"{str(r['modelo_usado'] or '—'):<10}"
        )
    print("─" * 108)
    print(f"  Total: {len(rows):,} registros\n")


def imprimir_alertas(alertas, titulo="Alertas Configurados"):
    if not alertas:
        print("📭 Nenhum alerta configurado.")
        return
    print(f"\n{'═'*112}")
    print(f"  {titulo}")
    print(f"{'═'*112}")
    print(f"  {'ID':<4} {'Ticker':<10} {'Tipo':<10} {'Código':<16} "
          f"{'Condição':<12} {'Activo':<8} {'Disparado':<10} "
          f"{'Cooldown':<9} {'Últ. valor':>10}")
    print("─" * 112)
    for a in alertas:
        cond = f"{a['operador']} {a['valor']}"
        cooldown_str = f"{a.get('cooldown_min', 15):.0f}min"
        print(
            f"  {a['id']:<4} "
            f"{a['ticker'].replace('.SA',''):<10} "
            f"{a['tipo']:<10} "
            f"{str(a.get('codigo') or '—'):<16} "
            f"{cond:<12} "
            f"{'✅' if a['activo'] else '⛔':<8} "
            f"{'🔔' if a['disparado'] else '—':<10} "
            f"{cooldown_str:<9} "
            f"{_fmt(a.get('ultimo_val'),4):>10}"
        )
    print("─" * 112)
    print(f"  Total: {len(alertas):,} alerta(s)\n")


# ─── Exportação (csv / xlsx / pdf) ────────────────────────────────────────────
# A montagem de cada formato vive em exportadores.py (partilhada com a API,
# ver GET /exportar/... em api.py) — aqui só gravamos os bytes num ficheiro
# e imprimimos o status.

import exportadores as _exp


def _gravar_exportacao(gerador, nome_prefixo, ticker, extensao, rotulo, msg_vazio,
                        *args, **kwargs):
    conteudo, contagem = gerador(ticker, *args, **kwargs)
    if conteudo is None:
        print(msg_vazio)
        return
    nome = _exp._nome_exportacao(nome_prefixo, ticker, extensao)
    with open(nome, "wb") as f:
        f.write(conteudo)
    print(f"✅ Exportado: {nome}  ({contagem:,} {rotulo})")


def exportar_cotacoes_csv(ticker, data_inicio=None, data_fim=None):
    _gravar_exportacao(_exp.cotacoes_csv_bytes, "cotacoes", ticker, "csv", "registros",
                        "📭 Nenhum dado para exportar.", data_inicio, data_fim)


def exportar_opcoes_csv(ticker, tipo=None, vencimento=None, data_inicio=None, data_fim=None):
    _gravar_exportacao(_exp.opcoes_csv_bytes, "opcoes", ticker, "csv", "contratos",
                        "📭 Nenhuma opção para exportar.", tipo, vencimento, data_inicio, data_fim)


def exportar_cotacoes_xlsx(ticker, data_inicio=None, data_fim=None):
    _gravar_exportacao(_exp.cotacoes_xlsx_bytes, "cotacoes", ticker, "xlsx", "registros",
                        "📭 Nenhum dado para exportar.", data_inicio, data_fim)


def exportar_opcoes_xlsx(ticker, tipo=None, vencimento=None, data_inicio=None, data_fim=None):
    _gravar_exportacao(_exp.opcoes_xlsx_bytes, "opcoes", ticker, "xlsx", "contratos",
                        "📭 Nenhuma opção para exportar.", tipo, vencimento, data_inicio, data_fim)


def exportar_cotacoes_pdf(ticker, data_inicio=None, data_fim=None):
    _gravar_exportacao(_exp.cotacoes_pdf_bytes, "cotacoes", ticker, "pdf", "registros",
                        "📭 Nenhum dado para exportar.", data_inicio, data_fim)


def exportar_opcoes_pdf(ticker, tipo=None, vencimento=None, data_inicio=None, data_fim=None):
    _gravar_exportacao(_exp.opcoes_pdf_bytes, "opcoes", ticker, "pdf", "contratos",
                        "📭 Nenhuma opção para exportar.", tipo, vencimento, data_inicio, data_fim)


# ─── CLI principal ────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        prog        = "cli.py",
        description = "📈 Plataforma de Opções B3 — Interface de linha de comando",
        formatter_class = argparse.RawTextHelpFormatter,
    )

    # Ações principais
    parser.add_argument("--coletar",         action="store_true",
                        help="Coleta histórico de cotações do ticker")
    parser.add_argument("--coletar-opcoes",  action="store_true",
                        help="Coleta cadeia de opções do ticker")
    parser.add_argument("--coletar-todos",   action="store_true",
                        help="Coleta histórico de todos os tickers configurados")
    parser.add_argument("--coletar-opcoes-todos", action="store_true",
                        help="Coleta cadeia de opções de todos os tickers configurados")
    parser.add_argument("--calcular-greeks-todos", action="store_true",
                        help="Calcula Greeks para todos os tickers configurados")
    parser.add_argument("--calcular-greeks", action="store_true",
                        help="Calcula Greeks para os contratos do ticker")
    parser.add_argument("--tempo-real",      action="store_true",
                        help="Executa ciclo completo (cotação + opções + greeks)")
    parser.add_argument("--consultar",       action="store_true",
                        help="Exibe histórico de cotações")
    parser.add_argument("--opcoes",          action="store_true",
                        help="Exibe cadeia de opções com Greeks")
    parser.add_argument("--resumo",          action="store_true",
                        help="Estatísticas gerais do banco")
    parser.add_argument("--exportar",        action="store_true",
                        help="Exporta cotações para CSV")
    parser.add_argument("--exportar-opcoes", action="store_true",
                        help="Exporta cadeia de opções para CSV")
    parser.add_argument("--dashboard",       action="store_true",
                        help="Inicia o servidor web (FastAPI + dashboard)")
    parser.add_argument("--status",          action="store_true",
                        help="Exibe status do sistema")
    parser.add_argument("--historico-greeks", action="store_true",
                        help="Exibe evolução histórica dos Greeks de um ticker/contrato")
    parser.add_argument("--purgar",          action="store_true",
                        help="Remove opções vencidas há mais de --dias-graca dias")
    parser.add_argument("--alertas",         action="store_true",
                        help="Lista alertas configurados")
    parser.add_argument("--alerta-add",      action="store_true",
                        help="Adiciona um novo alerta (requer --tipo-alerta, --operador, --valor)")
    parser.add_argument("--alerta-remover",  action="store_true",
                        help="Remove um alerta por ID (requer --id-alerta)")
    parser.add_argument("--verificar-alertas", action="store_true",
                        help="Verifica alertas activos do ticker contra os dados actuais")
    parser.add_argument("--licenca",          action="store_true",
                        help="Exibe o estado actual da licença")
    parser.add_argument("--licenca-importar", metavar="ARQUIVO",
                        help="Importa um token de licença recebido após pagamento")

    # Filtros
    parser.add_argument("--ticker",      default=TICKER_PADRAO, metavar="TICKER",
                        help=f"Ticker do ativo (padrão: {TICKER_PADRAO})")
    parser.add_argument("--periodo",     default="max",           metavar="PERIODO",
                        help="Período de coleta: 1mo 3mo 6mo 1y 2y 5y max (padrão: max)")
    parser.add_argument("--de",          metavar="AAAA-MM-DD",
                        help="Data inicial do filtro")
    parser.add_argument("--ate",         metavar="AAAA-MM-DD",
                        help="Data final do filtro")
    parser.add_argument("--limite",      type=int, default=None,
                        help="Limite de registros a exibir")
    parser.add_argument("--tipo",        metavar="CALL|PUT",
                        help="Tipo de opção: CALL ou PUT")
    parser.add_argument("--vencimento",  metavar="AAAA-MM-DD",
                        help="Data de vencimento do contrato")
    parser.add_argument("--formato",     choices=["csv", "xlsx", "pdf"], default="csv",
                        metavar="csv|xlsx|pdf",
                        help="Formato de --exportar/--exportar-opcoes (padrão: csv)")
    parser.add_argument("--verbose",     action="store_true",
                        help="Log detalhado (DEBUG)")
    parser.add_argument("--codigo",      metavar="CODIGO",
                        help="Código do contrato (ex: BBAS3C200A)")
    parser.add_argument("--dias-graca",  type=int, default=5, metavar="N",
                        help="Dias de carência antes de purgar vencidos (padrão: 5)")
    parser.add_argument("--dias-retencao-historico", type=int, default=180, metavar="N",
                        help="Dias de retenção em greeks_historico ao purgar (padrão: 180)")
    parser.add_argument("--tipo-alerta", metavar="delta|preco|variacao|iq|vol_impl",
                        help="Tipo de alerta a adicionar")
    parser.add_argument("--operador",    metavar=">|<|>=|<=",
                        help="Operador de comparação do alerta")
    parser.add_argument("--valor",       type=float, metavar="N",
                        help="Valor limiar do alerta")
    parser.add_argument("--cooldown-min", type=float, default=15.0, metavar="N",
                        help="Minutos mínimos entre disparos consecutivos do mesmo alerta (padrão: 15)")
    parser.add_argument("--id-alerta",   type=int, metavar="ID",
                        help="ID do alerta a remover")

    args = parser.parse_args()

    # Nenhum argumento → ajuda
    acoes = ["coletar","coletar_opcoes","coletar_todos","coletar_opcoes_todos",
             "calcular_greeks","calcular_greeks_todos",
             "tempo_real","consultar","opcoes","resumo","exportar",
             "exportar_opcoes","dashboard","status","historico_greeks",
             "purgar","alertas","alerta_add","alerta_remover","verificar_alertas",
             "licenca","licenca_importar"]
    if not any(getattr(args, a.replace("-","_"), False) for a in acoes):
        parser.print_help()
        return

    configurar_logging(args.verbose)
    inicializar()

    ticker = _normalizar_ticker(args.ticker)

    if args.de:  args.de  = _validar_data(args.de,  "--de")
    if args.ate: args.ate = _validar_data(args.ate, "--ate")
    if args.tipo is not None:
        args.tipo = args.tipo.strip().upper()
        if args.tipo not in ("CALL","PUT"):
            print(f"❌ Valor inválido para --tipo: '{args.tipo}'. Use CALL ou PUT.")
            sys.exit(1)

    # ── Coleta ────────────────────────────────────────────────────────────────
    if args.coletar:
        from collector import coletar_historico
        coletar_historico(ticker, args.periodo)

    if args.coletar_opcoes:
        from collector import coletar_opcoes
        coletar_opcoes(ticker)

    if args.coletar_todos:
        from collector import coletar_historico_todos
        r = coletar_historico_todos(args.periodo)
        print(f"✅ Total: {sum(r.values()):,} novos registros em {len(r)} tickers")

    if args.coletar_opcoes_todos:
        from collector import coletar_opcoes_todos
        r = coletar_opcoes_todos()
        print(f"✅ Total: {sum(r.values()):,} contratos em {len(r)} tickers")

    if args.calcular_greeks_todos:
        from collector import calcular_greeks_todos
        r = calcular_greeks_todos()
        print(f"✅ Greeks calculados: {sum(r.values()):,} contratos em {len(r)} tickers")

    if args.calcular_greeks:
        from collector import calcular_greeks_ticker
        n = calcular_greeks_ticker(ticker)
        print(f"✅ Greeks calculados para {n:,} contratos de {ticker}")

    if args.tempo_real:
        from collector import ciclo_tempo_real
        r = ciclo_tempo_real(ticker)
        print(f"✅ [{ticker}] Preço: R$ {r['preco']:.2f} | "
              f"Opções: {r['n_opcoes']} | Greeks: {r['n_greeks']} | "
              f"{r['duracao_seg']}s | {r['status']}")

    # ── Consultas ─────────────────────────────────────────────────────────────
    if args.consultar:
        rows = consultar_cotacoes(ticker, args.de, args.ate, args.limite)
        titulo = f"Cotações {ticker}"
        if args.de or args.ate:
            titulo += f"  [{args.de or '...'} → {args.ate or '...'}]"
        imprimir_cotacoes(rows, titulo)

    if args.opcoes:
        rows = consultar_opcoes(ticker, args.tipo, args.vencimento,
                                 data_inicio=args.de, data_fim=args.ate,
                                 limite=args.limite or 200)
        titulo = f"Opções {ticker}"
        filtros = []
        if args.tipo:       filtros.append(args.tipo)
        if args.vencimento: filtros.append(f"venc={args.vencimento}")
        if filtros:         titulo += f" [{', '.join(filtros)}]"
        imprimir_opcoes(rows, titulo)

    if args.resumo:
        imprimir_resumo()

    # ── Exportação ────────────────────────────────────────────────────────────
    _exportar_cotacoes = {
        "csv": exportar_cotacoes_csv, "xlsx": exportar_cotacoes_xlsx, "pdf": exportar_cotacoes_pdf,
    }
    _exportar_opcoes = {
        "csv": exportar_opcoes_csv, "xlsx": exportar_opcoes_xlsx, "pdf": exportar_opcoes_pdf,
    }

    if args.exportar:
        _exportar_cotacoes[args.formato](ticker, args.de, args.ate)

    if args.exportar_opcoes:
        _exportar_opcoes[args.formato](ticker, args.tipo, args.vencimento, args.de, args.ate)

    # ── Status ────────────────────────────────────────────────────────────────
    if args.status:
        from auth import autenticacao_configurada
        try:
            from scheduler import status as sched_status
            sched = sched_status()
        except Exception:
            sched = {"rodando": False}
        print(f"\n{'═'*50}")
        print("  ⚙️  Status da Plataforma")
        print(f"{'═'*50}")
        print(f"  Banco de dados  : {DB_PATH}")
        print(f"  Scheduler       : {'✅ rodando' if sched.get('rodando') else '⛔ parado'}")
        print(f"  Autenticação    : {'✅ configurada' if autenticacao_configurada() else '⚠️  não configurada (usando yfinance)'}")
        print(f"  Tickers config. : {len(TICKERS)}")
        print(f"{'═'*50}\n")

    # ── Histórico de Greeks ───────────────────────────────────────────────────
    if args.historico_greeks:
        from database import consultar_greeks_historico
        rows = consultar_greeks_historico(
            ticker, args.codigo, args.de, args.ate, args.limite or 500
        )
        titulo = f"Histórico de Greeks {ticker}"
        if args.codigo:
            titulo += f"  [{args.codigo}]"
        imprimir_historico_greeks(rows, titulo)

    # ── Purga de vencimentos expirados ───────────────────────────────────────
    if args.purgar:
        from database import purgar_vencimentos_expirados, purgar_greeks_historico
        removidos = purgar_vencimentos_expirados(args.dias_graca)
        print(f"\n🗑️  Purga concluída (carência: {args.dias_graca} dias)")
        for tabela, n in removidos.items():
            print(f"  {tabela:<12}: {n:,} registro(s) removido(s)")
        # Retenção de greeks_historico — operação separada e explícita,
        # por design não incluída em purgar_vencimentos_expirados()
        n_hist = purgar_greeks_historico(args.dias_retencao_historico)
        print(f"  {'gr_historico':<12}: {n_hist:,} registro(s) removido(s) "
              f"(retenção: {args.dias_retencao_historico} dias)")
        print()

    # ── Alertas ───────────────────────────────────────────────────────────────
    if args.alertas:
        from alertas import listar_alertas
        alertas_lst = listar_alertas(ticker if args.ticker != TICKER_PADRAO else None)
        imprimir_alertas(alertas_lst)

    if args.alerta_add:
        from alertas import adicionar_alerta
        if not (args.tipo_alerta and args.operador and args.valor is not None):
            print("❌ --alerta-add requer --tipo-alerta, --operador e --valor.")
            sys.exit(1)
        try:
            alerta = adicionar_alerta(
                ticker, args.tipo_alerta, args.operador, args.valor,
                args.codigo, args.cooldown_min
            )
            print(f"✅ Alerta #{alerta['id']} criado: {ticker} "
                  f"{args.tipo_alerta} {args.codigo or 'ativo'} "
                  f"{args.operador} {args.valor}  (cooldown: {args.cooldown_min:.0f}min)")
        except ValueError as exc:
            print(f"❌ {exc}")
            sys.exit(1)

    if args.alerta_remover:
        from alertas import remover_alerta
        if args.id_alerta is None:
            print("❌ --alerta-remover requer --id-alerta.")
            sys.exit(1)
        if remover_alerta(args.id_alerta):
            print(f"✅ Alerta #{args.id_alerta} removido.")
        else:
            print(f"❌ Alerta #{args.id_alerta} não encontrado.")
            sys.exit(1)

    if args.verificar_alertas:
        from alertas import verificar_alertas
        disparados = verificar_alertas(ticker)
        if disparados:
            print(f"\n🔔 {len(disparados)} alerta(s) disparado(s) para {ticker}:")
            for a in disparados:
                print(f"  #{a['id']} {a['tipo']} {a.get('codigo') or 'ativo'} "
                      f"{a['operador']} {a['valor']}  →  actual={a['valor_actual']}")
        else:
            print(f"✅ Nenhum alerta disparado para {ticker}.")

    # ── Licença ───────────────────────────────────────────────────────────────
    if args.licenca:
        from licenca import verificar_licenca
        estado = verificar_licenca(forcar_reverificacao=True)
        icones = {"ok":"✅","carencia":"⚠️ ","degradado":"🔒","bloqueado":"⛔","sem_licenca":"❌"}
        print(f"\n{'═'*55}")
        print(f"  {icones.get(estado.estagio,'?')} Estado da Licença")
        print(f"{'═'*55}")
        print(f"  Estágio       : {estado.estagio}")
        if estado.cliente_id:
            print(f"  Cliente       : {estado.cliente_id}")
        if estado.plano:
            print(f"  Plano         : {estado.plano}")
        if estado.valido_ate:
            print(f"  Válido até    : {estado.valido_ate}")
        if estado.dias_desde_vencimento is not None:
            if estado.dias_desde_vencimento > 0:
                print(f"  Vencida há    : {estado.dias_desde_vencimento:.1f} dia(s)")
            else:
                print(f"  Expira em     : {-estado.dias_desde_vencimento:.1f} dia(s)")
        print(f"  Motivo        : {estado.motivo}")
        print(f"{'═'*55}\n")

    if args.licenca_importar:
        from licenca import importar_licenca
        try:
            estado = importar_licenca(args.licenca_importar)
            print(f"✅ Licença importada com sucesso — estágio: {estado.estagio}")
            if estado.valido_ate:
                print(f"   Válido até: {estado.valido_ate}")
        except (FileNotFoundError, ValueError) as exc:
            print(f"❌ {exc}")
            sys.exit(1)

    # ── Dashboard ─────────────────────────────────────────────────────────────
    if args.dashboard:
        try:
            import uvicorn
        except ImportError:
            print("❌ uvicorn não instalado. Execute: pip install uvicorn fastapi")
            sys.exit(1)
        print(f"\n🌐 Iniciando dashboard em http://{API_HOST}:{API_PORT}")
        print(f"   Documentação API: http://localhost:{API_PORT}/docs")
        print("   Ctrl+C para encerrar\n")
        # Import do objecto 'app' directamente (não via string "api:app") —
        # a string exige resolução dinâmica de módulo em runtime, que falha
        # em binários compilados com Nuitka (o compilador resolve imports
        # estaticamente e não consegue seguir uma string arbitrária).
        # Também é necessário rodar com um único worker: o modelo de
        # multiprocessing do uvicorn para múltiplos workers conflita com o
        # binário compilado — concorrência aqui é via asyncio, não processos,
        # o que já é como a API está desenhada.
        from api import app as fastapi_app
        uvicorn.run(fastapi_app, host=API_HOST, port=API_PORT, reload=False, workers=1)


if __name__ == "__main__":
    main()

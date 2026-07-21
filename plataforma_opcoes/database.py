"""
database.py — Banco de dados SQLite
=====================================
Quatro tabelas:
  cotacoes  — histórico OHLCV diário por ticker
  opcoes    — cadeia de opções (snapshot por data/hora)
  greeks    — indicadores calculados por contrato
  snapshots — registro de cada ciclo de coleta intraday
"""

import sqlite3
import logging
from contextlib import contextmanager
from typing      import List, Optional
from datetime import datetime
from zoneinfo import ZoneInfo

from config import DB_PATH

TZ = ZoneInfo("America/Sao_Paulo")

log = logging.getLogger(__name__)

def agora():
    return datetime.now(TZ)

timestamp = agora().isoformat()

def _validar_data(valor: Optional[str], campo: str = "data") -> Optional[str]:
    """
    Valida data no formato AAAA-MM-DD com zeros obrigatórios, pois o SQLite compara datas como strings.
    """
    if valor is None:
        return None
    import re as _re
    from datetime import datetime as _dt
    if not _re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(valor)):
        raise ValueError(
            f"Formato inválido para '{campo}': '{valor}'. "
            f"Use AAAA-MM-DD com zeros (ex: 2025-01-01)."
        )
    try:
        _dt.strptime(valor, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(f"Data inexistente para '{campo}': '{valor}'.") from exc
    return valor


# ─── Conexão ─────────────────────────────────────────────────────────────────

@contextmanager
def conexao():
    conn = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ─── Inicialização ────────────────────────────────────────────────────────────

def inicializar() -> None:
    with conexao() as conn:

        # ── cotacoes ──────────────────────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS cotacoes (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker     TEXT    NOT NULL,
                data       DATE    NOT NULL,
                abertura   REAL,
                maxima     REAL,
                minima     REAL,
                fechamento REAL,
                adj_close  REAL,
                volume     INTEGER,
                atualizado TEXT    DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS uidx_cotacoes
            ON cotacoes(ticker, data)
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cotacoes_data   ON cotacoes(data)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cotacoes_ticker ON cotacoes(ticker)")

        # ── opcoes ────────────────────────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS opcoes (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker_ativo    TEXT    NOT NULL,
                codigo          TEXT    NOT NULL,
                tipo            TEXT    NOT NULL CHECK(tipo IN ('CALL','PUT')),
                modelo          TEXT,                  -- 'Americano' | 'Europeu'
                strike          REAL,
                vencimento      DATE,
                ultimo          REAL,
                variacao_pct    REAL,
                data_hora       TEXT,
                num_negocios    INTEGER,
                vol_financeiro  REAL,
                vol_implícita   REAL,
                -- Campos exclusivos de assinante (preenchidos via sessão autenticada)
                iq              REAL,                  -- calculado localmente por aproximação
                coberto         REAL,                  -- NULL até integração com custódia B3
                descoberto      REAL,                  -- NULL até integração com custódia B3
                travado         REAL,                  -- NULL até integração com custódia B3
                titulares       INTEGER,               -- NULL até integração com custódia B3
                lancadores      INTEGER,               -- NULL até integração com custódia B3
                fonte           TEXT    DEFAULT 'yfinance',
                atualizado      TEXT    DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS uidx_opcoes
            ON opcoes(ticker_ativo, codigo, vencimento, data_hora)
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_opcoes_ticker    ON opcoes(ticker_ativo)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_opcoes_venc      ON opcoes(vencimento)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_opcoes_tipo      ON opcoes(tipo)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_opcoes_strike    ON opcoes(strike)")

        idx_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND name='uidx_opcoes'"
        ).fetchone()
        if idx_sql and "vencimento" not in idx_sql[0]:
            conn.execute("DROP INDEX IF EXISTS uidx_opcoes")
            conn.execute("""
                CREATE UNIQUE INDEX uidx_opcoes
                ON opcoes(ticker_ativo, codigo, vencimento, data_hora)
            """)
            log.info("Migração: uidx_opcoes atualizado para incluir vencimento.")

        # ── greeks ────────────────────────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS greeks (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                opcao_codigo TEXT    NOT NULL,
                ticker_ativo TEXT    NOT NULL,
                data_hora    TEXT    NOT NULL,
                delta        REAL,
                gamma        REAL,
                theta        REAL,
                vega         REAL,
                rho          REAL,
                otm_atm_itm  TEXT    CHECK(otm_atm_itm IN ('OTM','ATM','ITM')),
                dist_strike  REAL,   -- distância % do strike ao preço atual
                iq_calc      REAL,   -- IQ calculado localmente
                modelo_usado TEXT,   -- 'black_scholes' | 'binomial'
                preco_ativo  REAL,   -- preço do ativo base no momento do cálculo
                taxa_cdi     REAL,   -- CDI usado no cálculo
                atualizado   TEXT    DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS uidx_greeks
            ON greeks(opcao_codigo, data_hora)
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_greeks_ticker ON greeks(ticker_ativo)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_greeks_data   ON greeks(data_hora)")

        # ── greeks_historico ──────────────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS greeks_historico (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                opcao_codigo TEXT    NOT NULL,
                ticker_ativo TEXT    NOT NULL,
                data_hora    TEXT    NOT NULL,
                delta        REAL,
                gamma        REAL,
                theta        REAL,
                vega         REAL,
                rho          REAL,
                otm_atm_itm  TEXT,
                dist_strike  REAL,
                iq_calc      REAL,
                modelo_usado TEXT,
                preco_ativo  REAL,
                taxa_cdi     REAL,
                div_yield    REAL,
                gravado_em   TEXT    DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_ghist_codigo
            ON greeks_historico(opcao_codigo, data_hora)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_ghist_ticker
            ON greeks_historico(ticker_ativo, data_hora)
        """)

        # ── snapshots ─────────────────────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS snapshots (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker       TEXT    NOT NULL,
                timestamp    TEXT    NOT NULL,
                preco_atual  REAL,
                variacao_pct REAL,
                fonte        TEXT,
                status       TEXT    DEFAULT 'ok',
                mensagem     TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_snap_ticker ON snapshots(ticker)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_snap_ts     ON snapshots(timestamp)")

    log.info("Banco inicializado: %s", DB_PATH)


# ─── Cotações ─────────────────────────────────────────────────────────────────

def upsert_cotacoes(registros: list) -> int:
    with conexao() as conn:
        antes = conn.execute("SELECT COUNT(*) FROM cotacoes").fetchone()[0]
        conn.executemany("""
            INSERT INTO cotacoes
                (ticker, data, abertura, maxima, minima, fechamento, adj_close, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker, data) DO UPDATE SET
                abertura   = excluded.abertura,
                maxima     = excluded.maxima,
                minima     = excluded.minima,
                fechamento = excluded.fechamento,
                adj_close  = excluded.adj_close,
                volume     = excluded.volume,
                atualizado = datetime('now')
        """, registros)
        depois = conn.execute("SELECT COUNT(*) FROM cotacoes").fetchone()[0]
    return depois - antes


def consultar_cotacoes(
    ticker:       str,
    data_inicio:  Optional[str] = None,
    data_fim:     Optional[str] = None,
    limite:       Optional[int] = None,
) -> List[sqlite3.Row]:
    data_inicio = _validar_data(data_inicio, "data_inicio")
    data_fim    = _validar_data(data_fim,    "data_fim")
    params = {"ticker": ticker}
    where  = "ticker = :ticker"
    if data_inicio:
        where += " AND data >= :de";  params["de"]  = data_inicio
    if data_fim:
        where += " AND data <= :ate"; params["ate"] = data_fim
    sql = f"SELECT * FROM cotacoes WHERE {where} ORDER BY data DESC"
    if limite:
        sql += f" LIMIT {int(limite)}"
    with conexao() as conn:
        return conn.execute(sql, params).fetchall()


# ─── Opções ───────────────────────────────────────────────────────────────────

def upsert_opcoes(registros: list) -> int:
    if not registros:
        return 0

    campos = [
        "ticker_ativo", "codigo", "tipo", "modelo", "strike", "vencimento",
        "ultimo", "variacao_pct", "data_hora", "num_negocios", "vol_financeiro",
        "vol_implícita", "iq", "coberto", "descoberto", "travado",
        "titulares", "lancadores", "fonte",
    ]
    placeholders = ", ".join(f":{c}" for c in campos)
    updates = ", ".join(
        f"{c} = excluded.{c}"
        for c in campos if c not in ("ticker_ativo", "codigo", "data_hora")
    )

    sql = f"""
        INSERT INTO opcoes ({', '.join(campos)})
        VALUES ({placeholders})
        ON CONFLICT(ticker_ativo, codigo, vencimento, data_hora) DO UPDATE SET {updates},
            atualizado = datetime('now')
    """
    with conexao() as conn:
        antes = conn.execute("SELECT COUNT(*) FROM opcoes").fetchone()[0]
        conn.executemany(sql, registros)
        depois = conn.execute("SELECT COUNT(*) FROM opcoes").fetchone()[0]
    return depois - antes


def consultar_opcoes(
    ticker:      str,
    tipo:        Optional[str]  = None,
    vencimento:  Optional[str]  = None,
    strike_min:  Optional[float]= None,
    strike_max:  Optional[float]= None,
    data_inicio: Optional[str]  = None,
    data_fim:    Optional[str]  = None,
    limite:      Optional[int]  = None,
) -> List[sqlite3.Row]:
    data_inicio = _validar_data(data_inicio, "data_inicio")
    data_fim    = _validar_data(data_fim,    "data_fim")

    if tipo is not None:
        tipo_upper = tipo.strip().upper()
        if tipo_upper not in ("CALL", "PUT"):
            raise ValueError(f"Valor inválido para 'tipo': '{tipo}'. Use 'CALL' ou 'PUT'.")
        tipo = tipo_upper
    params = {"ticker": ticker}
    where  = "o.ticker_ativo = :ticker"
    if tipo:
        where += " AND o.tipo = :tipo";              params["tipo"]       = tipo
    if vencimento:
        where += " AND o.vencimento = :venc";        params["venc"]       = vencimento
    if strike_min is not None:
        where += " AND o.strike >= :smin";           params["smin"]       = strike_min
    if strike_max is not None:
        where += " AND o.strike <= :smax";           params["smax"]       = strike_max
    if data_inicio:
        where += " AND o.data_hora >= :de";          params["de"]         = data_inicio
    if data_fim:
        where += " AND o.data_hora <= :ate";         params["ate"]        = data_fim

    sql = f"""
        SELECT o.*, g.delta, g.gamma, g.theta, g.vega, g.rho,
               g.otm_atm_itm, g.dist_strike, g.iq_calc, g.modelo_usado
        FROM opcoes o
        LEFT JOIN greeks g
            ON g.opcao_codigo = o.codigo
            AND g.ticker_ativo = o.ticker_ativo
            AND g.data_hora = (
                SELECT MAX(g2.data_hora) FROM greeks g2
                WHERE g2.opcao_codigo = o.codigo
            )
        WHERE {where}
        ORDER BY o.vencimento ASC, o.strike ASC, o.data_hora DESC
    """
    if limite:
        sql += f" LIMIT {int(limite)}"
    with conexao() as conn:
        return conn.execute(sql, params).fetchall()


# ─── Greeks ───────────────────────────────────────────────────────────────────

def upsert_greeks(registros: list) -> None:
    if not registros:
        return
    campos = [
        "opcao_codigo", "ticker_ativo", "data_hora", "delta", "gamma",
        "theta", "vega", "rho", "otm_atm_itm", "dist_strike",
        "iq_calc", "modelo_usado", "preco_ativo", "taxa_cdi",
    ]
    placeholders = ", ".join(f":{c}" for c in campos)
    updates = ", ".join(
        f"{c} = excluded.{c}"
        for c in campos if c not in ("opcao_codigo", "data_hora")
    )
    sql = f"""
        INSERT INTO greeks ({', '.join(campos)})
        VALUES ({placeholders})
        ON CONFLICT(opcao_codigo, data_hora) DO UPDATE SET {updates},
            atualizado = datetime('now')
    """
    with conexao() as conn:
        conn.executemany(sql, registros)


# ─── Snapshots ────────────────────────────────────────────────────────────────

def registrar_snapshot(
    ticker: str,
    preco: float,
    variacao: float,
    fonte: str,
    status: str = "ok",
    msg: str = ""
) -> None:

    timestamp = datetime.now(TZ).isoformat()

    with conexao() as conn:
        conn.execute("""
            INSERT INTO snapshots
            (ticker, timestamp, preco_atual, variacao_pct, fonte, status, mensagem)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            ticker,
            timestamp,
            preco,
            variacao,
            fonte,
            status,
            msg
        ))


def consultar_snapshots(
    ticker:       str,
    limite:       Optional[int] = 100,
) -> List[sqlite3.Row]:
    sql = "SELECT * FROM snapshots WHERE ticker = :ticker ORDER BY timestamp DESC, id DESC"
    if limite:
        sql += f" LIMIT {int(limite)}"
    with conexao() as conn:
        return conn.execute(sql, {"ticker": ticker}).fetchall()


# ─── Histórico de Greeks ──────────────────────────────────────────────────────

def salvar_greeks_historico(registros: list) -> int:
    if not registros:
        return 0
    campos = [
        "opcao_codigo","ticker_ativo","data_hora","delta","gamma",
        "theta","vega","rho","otm_atm_itm","dist_strike",
        "iq_calc","modelo_usado","preco_ativo","taxa_cdi","div_yield",
    ]
    placeholders = ", ".join(f":{c}" for c in campos)
    sql = f"""
        INSERT INTO greeks_historico ({', '.join(campos)})
        VALUES ({placeholders})
    """
    regs_norm = []
    for reg in registros:
        r = dict(reg)
        r.setdefault("div_yield", 0.0)
        regs_norm.append(r)
    with conexao() as conn:
        conn.executemany(sql, regs_norm)
    return len(regs_norm)


def consultar_greeks_historico(
    ticker: str,
    codigo: Optional[str] = None,
    data_inicio: Optional[str] = None,
    data_fim: Optional[str] = None,
    limite: Optional[int] = 500,
) -> list:
    data_inicio = _validar_data(data_inicio, "data_inicio")
    data_fim    = _validar_data(data_fim,    "data_fim")
    params = {"ticker": ticker}
    where  = "ticker_ativo = :ticker"
    if codigo:
        where += " AND opcao_codigo = :codigo"; params["codigo"] = codigo
    if data_inicio:
        where += " AND data_hora >= :de";       params["de"]     = data_inicio
    if data_fim:
        where += " AND data_hora <= :ate";      params["ate"]    = data_fim
    sql = f"""
        SELECT * FROM greeks_historico WHERE {where}
        ORDER BY data_hora DESC
    """
    if limite:
        sql += f" LIMIT {int(limite)}"
    with conexao() as conn:
        return conn.execute(sql, params).fetchall()


# ─── Purga de vencimentos expirados ───────────────────────────────────────────

def purgar_vencimentos_expirados(dias_graca: int = 5) -> dict:
    from datetime import date, timedelta
    corte = (date.today() - timedelta(days=dias_graca)).isoformat()
    removidos = {}
    with conexao() as conn:
        r_opc = conn.execute(
            "DELETE FROM opcoes WHERE vencimento < ?", (corte,)
        )
        removidos["opcoes"] = r_opc.rowcount
        r_grk = conn.execute(
            """DELETE FROM greeks WHERE opcao_codigo NOT IN
               (SELECT codigo FROM opcoes)"""
        )
        removidos["greeks"] = r_grk.rowcount
        corte_snap = (date.today() - timedelta(days=90)).isoformat()
        r_snp = conn.execute(
            "DELETE FROM snapshots WHERE timestamp < ?", (corte_snap,)
        )
        removidos["snapshots"] = r_snp.rowcount
    log.info("Purga: %s", removidos)
    return removidos


def purgar_greeks_historico(dias_retencao: int = 180) -> int:
    from datetime import date, timedelta
    corte = (date.today() - timedelta(days=dias_retencao)).isoformat()
    with conexao() as conn:
        r = conn.execute(
            "DELETE FROM greeks_historico WHERE data_hora < ?", (corte,)
        )
        removidos = r.rowcount
    log.info("Retenção greeks_historico: %d registro(s) removido(s) (>%d dias)",
              removidos, dias_retencao)
    return removidos


# ─── Resumo ───────────────────────────────────────────────────────────────────

def resumo_geral() -> dict:
    with conexao() as conn:
        cot = conn.execute("""
            SELECT COUNT(*) AS total, MIN(data) AS inicio, MAX(data) AS fim
            FROM cotacoes
        """).fetchone()
        opc = conn.execute("SELECT COUNT(*) AS total FROM opcoes").fetchone()
        grk = conn.execute("SELECT COUNT(*) AS total FROM greeks").fetchone()
        ghist = conn.execute("SELECT COUNT(*) AS total FROM greeks_historico").fetchone()
        snp = conn.execute("SELECT COUNT(*) AS total FROM snapshots").fetchone()
        tks = conn.execute(
            "SELECT DISTINCT ticker FROM cotacoes ORDER BY ticker"
        ).fetchall()
    return {
        "cotacoes":         dict(cot),
        "opcoes":           opc["total"],
        "greeks":           grk["total"],
        "greeks_historico": ghist["total"],
        "snapshots":        snp["total"],
        "tickers":          [r["ticker"] for r in tks],
    }

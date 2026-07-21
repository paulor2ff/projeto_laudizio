"""
collector.py — Coleta de dados (yfinance + sessão autenticada)
==============================================================
Estratégia de fallback automático:
  1. Tenta fonte autenticada (opcoes.net.br) se configurada
  2. Cai para yfinance (15min de atraso) caso contrário
  3. Registra snapshot de cada ciclo no banco
"""

import logging
import time
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing  import List, Optional

import pandas as pd
import yfinance as yf

from config   import MAX_RETRIES, RETRY_DELAY_SEG, TICKERS, CASAS_DECIMAIS, MODELO_GREEKS
from database import (
    registrar_snapshot, upsert_cotacoes,
    upsert_opcoes, upsert_greeks,
)
from greeks   import calcular_contrato, tempo_ate_vencimento

log = logging.getLogger(__name__)


# ─── Conversão segura ─────────────────────────────────────────────────────────

def _dec(valor) -> Optional[float]:
    """Converte para float com precisão Decimal. Retorna None se NaN/inválido."""
    try:
        if valor is None or pd.isna(valor):
            return None
        return float(Decimal(str(valor)).quantize(
            Decimal(CASAS_DECIMAIS), rounding=ROUND_HALF_UP
        ))
    except Exception:
        return None


def _int_(valor) -> Optional[int]:
    try:
        return None if (valor is None or pd.isna(valor)) else int(valor)
    except Exception:
        return None


# ─── Validação de sanidade ────────────────────────────────────────────────────

def _validar_sanidade_cotacao(ticker: str, data: str, abertura, maxima, minima,
                               fechamento, volume) -> list:
    """
    Verifica se os valores de uma cotação são plausíveis.
    Retorna lista de avisos (strings). Lista vazia = OK.
    """
    avisos = []
    vals = [v for v in [abertura, maxima, minima, fechamento] if v is not None]
    if not vals:
        return avisos  # todos NULL → sem dados do dia, não é erro
    if any(v <= 0 for v in vals):
        avisos.append(f"[{ticker}/{data}] Preço negativo ou zero: {vals}")
    if maxima and minima and maxima < minima:
        avisos.append(f"[{ticker}/{data}] Máxima ({maxima}) < Mínima ({minima})")
    if abertura and fechamento:
        variacao = abs(fechamento - abertura) / abertura
        if variacao > 0.50:   # mais de 50% num dia → suspeito
            avisos.append(f"[{ticker}/{data}] Variação >50% em 1 dia: {variacao:.1%}")
    if volume is not None and volume < 0:
        avisos.append(f"[{ticker}/{data}] Volume negativo: {volume}")
    return avisos


def _validar_sanidade_opcao(reg: dict, preco_ativo: float = 0.0) -> list:
    """
    Verifica se os valores de um contrato de opção são plausíveis.
    """
    avisos = []
    codigo = reg.get("codigo","?")
    ultimo = reg.get("ultimo")
    strike = reg.get("strike")
    vol_impl = reg.get("vol_implícita")

    if ultimo is not None and ultimo < 0:
        avisos.append(f"[{codigo}] Prémio negativo: {ultimo}")

    if strike is not None and strike <= 0:
        avisos.append(f"[{codigo}] Strike inválido: {strike}")

    if vol_impl is not None:
        if vol_impl < 0:
            avisos.append(f"[{codigo}] Vol implícita negativa: {vol_impl}")
        if vol_impl > 5.0:   # > 500% a.a. → provavelmente erro de dados
            avisos.append(f"[{codigo}] Vol implícita >500%: {vol_impl:.2%}")

    # Prémio de CALL não pode exceder o preço do ativo
    if (ultimo is not None and preco_ativo > 0
            and reg.get("tipo") == "CALL" and ultimo > preco_ativo * 1.05):
        avisos.append(f"[{codigo}] Prémio CALL ({ultimo}) > preço ativo ({preco_ativo})")

    return avisos


# ─── Histórico de cotações ────────────────────────────────────────────────────

def _baixar_historico(ticker: str, periodo: str) -> pd.DataFrame:
    """Baixa histórico via yfinance com retry."""
    obj = yf.Ticker(ticker)
    for i in range(1, MAX_RETRIES + 1):
        try:
            df = obj.history(period=periodo, auto_adjust=False)
            if not df.empty:
                return df
            log.warning("[%s] Resposta vazia (tentativa %d/%d)", ticker, i, MAX_RETRIES)
        except Exception as exc:
            log.warning("[%s] Erro tentativa %d/%d: %s", ticker, i, MAX_RETRIES, exc)
        if i < MAX_RETRIES:
            time.sleep(RETRY_DELAY_SEG)
    return pd.DataFrame()


def coletar_historico(ticker: str, periodo: str = "2y") -> int:
    """
    Coleta e salva histórico OHLCV de um ticker.
    Retorna número de novos registros inseridos.
    """
    log.info("[%s] Coletando histórico (%s)...", ticker, periodo)
    df = _baixar_historico(ticker, periodo)
    if df.empty:
        log.error("[%s] Nenhum dado histórico retornado.", ticker)
        return 0

    # Normalizar índice — remover timezone
    idx = pd.to_datetime(df.index)
    if idx.tzinfo is not None or (hasattr(idx, 'tz') and idx.tz is not None):
        idx = idx.tz_convert("UTC").tz_localize(None)
    df.index = idx.normalize()
    df = df.sort_index()

    # Detectar coluna Adj Close de forma flexível
    adj_col = next(
        (c for c in df.columns
         if c.replace(" ", "").replace("_", "").lower() == "adjclose"),
        None
    )

    registros = []
    avisos_total = 0
    for data_idx, row in df.iterrows():
        data_str = data_idx.strftime("%Y-%m-%d")
        abert = _dec(row.get("Open"))
        maxim = _dec(row.get("High"))
        minim = _dec(row.get("Low"))
        fech  = _dec(row.get("Close"))
        adj   = _dec(row[adj_col]) if adj_col else fech
        vol   = _int_(row.get("Volume"))
        # Validação de sanidade
        avisos = _validar_sanidade_cotacao(ticker, data_str, abert, maxim, minim, fech, vol)
        for av in avisos:
            log.warning("Sanidade: %s", av)
            avisos_total += 1
        registros.append((ticker, data_str, abert, maxim, minim, fech, adj, vol))
    if avisos_total:
        log.warning("[%s] %d aviso(s) de sanidade nos dados coletados", ticker, avisos_total)

    novos = upsert_cotacoes(registros)
    log.info("[%s] %d registros processados → %d novos", ticker, len(registros), novos)
    return novos


def coletar_historico_todos(periodo: str = "2y") -> dict:
    """
    Coleta histórico de todos os tickers configurados.
    Falha em um ticker é isolada — os demais continuam sendo coletados.
    """
    resultado = {}
    for ticker in TICKERS:
        try:
            resultado[ticker] = coletar_historico(ticker, periodo)
        except Exception as exc:
            log.error("[%s] Falha na coleta histórica: %s", ticker, exc)
            resultado[ticker] = 0
    return resultado


# ─── Cotação atual ────────────────────────────────────────────────────────────

def cotacao_atual(ticker: str) -> dict:
    """Retorna preço atual, variação % e timestamp via yfinance."""
    try:
        obj  = yf.Ticker(ticker)
        info = obj.fast_info
        preco   = float(info.last_price or 0)
        fechant = float(info.previous_close or preco)
        var_pct = ((preco - fechant) / fechant * 100) if fechant else 0.0
        return {
            "ticker":      ticker,
            "preco":       round(preco, 4),
            "variacao_pct":round(var_pct, 4),
            "timestamp":   datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "fonte":       "yfinance",
        }
    except Exception as exc:
        log.warning("[%s] Erro ao obter cotação atual: %s", ticker, exc)
        return {"ticker": ticker, "preco": 0.0, "variacao_pct": 0.0,
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "fonte": "erro"}


# ─── Cadeia de opções ─────────────────────────────────────────────────────────

def _coletar_opcoes_yfinance(ticker: str) -> list:
    """
    Coleta cadeia de opções via yfinance.
    Retorna lista de dicts para upsert_opcoes().
    """
    obj = yf.Ticker(ticker)
    try:
        vencimentos = obj.options
    except Exception as exc:
        log.error("[%s] Não foi possível obter vencimentos: %s", ticker, exc)
        return []

    if not vencimentos:
        log.warning("[%s] Nenhum vencimento disponível.", ticker)
        return []

    agora     = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    resultado = []

    for venc in vencimentos:
        try:
            chain = obj.option_chain(venc)
            for tipo_str, df in [("CALL", chain.calls), ("PUT", chain.puts)]:
                if df is None or df.empty:
                    continue
                for _, row in df.iterrows():
                    reg = {
                        "ticker_ativo":   ticker,
                        "codigo":         str(row.get("contractSymbol", "")),
                        "tipo":           tipo_str,
                        "modelo":         None,
                        "strike":         _dec(row.get("strike")),
                        "vencimento":     venc,
                        "ultimo":         _dec(row.get("lastPrice")),
                        "variacao_pct":   _dec(row.get("percentChange")),
                        "data_hora":      str(row.get("lastTradeDate", agora)),
                        "num_negocios":   _int_(row.get("volume")),
                        "vol_financeiro": round(
                            (_dec(row.get("volume")) or 0) *
                            (_dec(row.get("lastPrice")) or 0), 2
                        ) or None,
                        "vol_implícita":  _dec(row.get("impliedVolatility")),
                        "iq":             None,
                        "coberto":        None,
                        "descoberto":     None,
                        "travado":        None,
                        "titulares":      None,
                        "lancadores":     None,
                        "fonte":          "yfinance",
                    }
                    # Validação de sanidade antes de guardar
                    for av in _validar_sanidade_opcao(reg):
                        log.warning("Sanidade: %s", av)
                    resultado.append(reg)
        except Exception as exc:
            log.warning("[%s] Erro ao coletar vencimento %s: %s", ticker, venc, exc)

    return resultado


def coletar_opcoes(ticker: str) -> int:
    """
    Coleta cadeia de opções com fallback automático.
    Tenta fonte autenticada → cai para yfinance se não configurada.
    Retorna número de novos registros inseridos.
    """
    # Tenta fonte autenticada primeiro
    try:
        from auth import autenticacao_configurada, coletar_opcoes_autenticado
        if autenticacao_configurada():
            log.info("[%s] Coletando opções via sessão autenticada...", ticker)
            registros = coletar_opcoes_autenticado(ticker)
            novos = upsert_opcoes(registros)
            log.info("[%s] Autenticado: %d contratos, %d novos", ticker, len(registros), novos)
            return novos
    except NotImplementedError:
        log.info("[%s] Autenticação não configurada — usando yfinance.", ticker)
    except Exception as exc:
        log.warning("[%s] Falha na coleta autenticada (%s) — usando yfinance.", ticker, exc)

    # Fallback: yfinance
    log.info("[%s] Coletando opções via yfinance (15min atraso)...", ticker)
    registros = _coletar_opcoes_yfinance(ticker)
    if not registros:
        return 0
    novos = upsert_opcoes(registros)
    log.info("[%s] yfinance: %d contratos, %d novos", ticker, len(registros), novos)
    return novos


def coletar_opcoes_todos() -> dict:
    """
    Coleta cadeia de opções de todos os tickers configurados.
    Falha em um ticker é isolada — os demais continuam sendo coletados.
    """
    resultado = {}
    for ticker in TICKERS:
        try:
            resultado[ticker] = coletar_opcoes(ticker)
        except Exception as exc:
            log.error("[%s] Falha na coleta de opções: %s", ticker, exc)
            resultado[ticker] = 0
    return resultado


# ─── Cálculo de Greeks em lote ────────────────────────────────────────────────

def calcular_greeks_ticker(ticker: str) -> int:
    """
    Calcula Greeks para todos os contratos ativos de um ticker.
    Consulta o banco, calcula e salva os resultados.
    Retorna número de contratos calculados.
    """
    from database import consultar_opcoes

    cot = cotacao_atual(ticker)
    preco_ativo = cot["preco"]
    if preco_ativo <= 0:
        log.error("[%s] Preço do ativo inválido (%.4f) — Greeks não calculados.", ticker, preco_ativo)
        return 0

    opcoes = consultar_opcoes(ticker)
    if not opcoes:
        log.warning("[%s] Nenhuma opção no banco para calcular Greeks.", ticker)
        return 0

    # Calcular presença histórica (simplificado: proporção de contratos com negócio)
    total = len(opcoes)
    com_negocio = sum(1 for o in opcoes if (o["num_negocios"] or 0) > 0)
    presenca_media = com_negocio / total if total > 0 else 0.0

    registros_g: List[dict] = []
    for opcao in opcoes:
        try:
            T = tempo_ate_vencimento(str(opcao["vencimento"] or ""))
            if T <= 0:
                continue  # ignora opções vencidas

            resultado = calcular_contrato(
                ticker_ativo   = ticker,
                codigo         = str(opcao["codigo"]),
                tipo           = str(opcao["tipo"]),
                strike         = float(opcao["strike"] or 0),
                vencimento     = str(opcao["vencimento"] or ""),
                preco_ativo    = preco_ativo,
                preco_opcao    = float(opcao["ultimo"] or 0),
                vol_financeiro = float(opcao["vol_financeiro"] or 0),
                num_negocios   = int(opcao["num_negocios"] or 0),
                presenca_30d   = presenca_media,
                modelo_hint    = MODELO_GREEKS,
            )
            registros_g.append(resultado)
        except Exception as exc:
            log.warning("[%s] Erro ao calcular Greeks para %s: %s",
                        ticker, opcao["codigo"], exc)

    upsert_greeks(registros_g)
    # Guardar também no histórico acumulativo
    from database import salvar_greeks_historico
    salvar_greeks_historico(registros_g)
    log.info("[%s] Greeks calculados para %d/%d contratos (histórico guardado).",
             ticker, len(registros_g), total)
    return len(registros_g)


def calcular_greeks_todos() -> dict:
    """Calcula Greeks para todos os tickers configurados."""
    return {ticker: calcular_greeks_ticker(ticker) for ticker in TICKERS}


# ─── Ciclo completo (tempo real) ──────────────────────────────────────────────

def ciclo_tempo_real(ticker: str) -> dict:
    """
    Executa um ciclo completo de atualização:
      1. Cotação atual
      2. Opções (autenticado ou yfinance)
      3. Greeks
      4. Snapshot
    Retorna resumo do ciclo.
    """
    inicio = time.time()
    cot    = cotacao_atual(ticker)

    try:
        n_opcoes = coletar_opcoes(ticker)
        n_greeks = calcular_greeks_ticker(ticker)
        status   = "ok"
        msg      = ""
    except Exception as exc:
        n_opcoes = 0
        n_greeks = 0
        status   = "erro"
        msg      = str(exc)
        log.error("[%s] Erro no ciclo tempo real: %s", ticker, exc)

    registrar_snapshot(
        ticker   = ticker,
        preco    = cot["preco"],
        variacao = cot["variacao_pct"],
        fonte    = cot["fonte"],
        status   = status,
        msg      = msg,
    )

    duracao = round(time.time() - inicio, 2)
    log.info("[%s] Ciclo concluído em %.1fs | preço: %.2f | opções: %d | greeks: %d",
             ticker, duracao, cot["preco"], n_opcoes, n_greeks)

    return {
        "ticker":      ticker,
        "preco":       cot["preco"],
        "variacao_pct":cot["variacao_pct"],
        "n_opcoes":    n_opcoes,
        "n_greeks":    n_greeks,
        "duracao_seg": duracao,
        "status":      status,
    }


def ciclo_tempo_real_todos() -> List[dict]:
    """Executa ciclo completo para todos os tickers."""
    return [ciclo_tempo_real(ticker) for ticker in TICKERS]

"""
greeks.py — Cálculo de Greeks e indicadores de opções
=======================================================
Modelos implementados:
  Black-Scholes  — opções europeias
  Binomial CRR   — opções americanas (Cox-Ross-Rubinstein)

Greeks calculados: Delta, Gamma, Theta, Vega, Rho
Outros: Vol. Implícita (solver numérico), A/I/OTM, Dist. Strike, IQ aprox.
"""

import logging
import math
from typing import Optional, Tuple

import requests

from config import (
    BCB_CDI_URL, CDI_FALLBACK, IQ_PESO_NEGOCIOS, IQ_PESO_PRESENCA, IQ_PESO_VOLUME,
    PASSOS_BINOMIAL,
)

log = logging.getLogger(__name__)

# scipy é opcional: se não instalado, usa aproximação polinomial para N(x)
try:
    from scipy.stats import norm as _norm
    def N(x: float) -> float:
        return float(_norm.cdf(x))
    def n(x: float) -> float:
        return float(_norm.pdf(x))
    SCIPY = True
except ImportError:
    log.warning("scipy não encontrado — usando aproximação polinomial para N(x)")
    SCIPY = False
    def N(x: float) -> float:
        """Aproximação polinomial de Hart (erro < 7.5e-8)."""
        a = abs(x)
        t = 1.0 / (1.0 + 0.2316419 * a)
        d = 0.3989422820 * math.exp(-0.5 * a * a)
        p = d * t * (0.3193815 + t * (-0.3565638 + t * (1.7814779 + t * (-1.8212560 + t * 1.3302744))))
        return 1.0 - p if x > 0 else p
    def n(x: float) -> float:
        return (1.0 / math.sqrt(2 * math.pi)) * math.exp(-0.5 * x * x)


# ─── CDI ─────────────────────────────────────────────────────────────────────

_cdi_cache: Optional[float] = None

def obter_cdi() -> float:
    """
    Busca o CDI atual via API pública do Banco Central do Brasil.
    Retorna taxa anual em decimal (ex: 0.1075 para 10.75% a.a.).
    Usa cache em memória para evitar chamadas repetidas.
    """
    global _cdi_cache
    if _cdi_cache is not None:
        return _cdi_cache
    try:
        resp = requests.get(BCB_CDI_URL, timeout=5)
        resp.raise_for_status()
        dados = resp.json()
        # API retorna lista com {"data": "DD/MM/AAAA", "valor": "10.75"}
        taxa_aa = float(dados[-1]["valor"]) / 100
        _cdi_cache = taxa_aa
        log.info("CDI obtido via BCB: %.4f (%.2f%% a.a.)", taxa_aa, taxa_aa * 100)
        return taxa_aa
    except Exception as exc:
        log.warning("Falha ao obter CDI do BCB (%s) — usando fallback %.4f", exc, CDI_FALLBACK)
        return CDI_FALLBACK


def invalidar_cache_cdi() -> None:
    """Invalida o cache do CDI (chamar uma vez por dia)."""
    global _cdi_cache
    _cdi_cache = None


# ─── Dividend Yield ───────────────────────────────────────────────────────────

_dy_cache: dict = {}  # {ticker: yield}

def obter_dividend_yield(ticker: str) -> float:
    """
    Obtém o dividend yield anual do ticker via yfinance.
    Retorna 0.0 se não disponível ou em caso de erro.
    Cache em memória para evitar chamadas repetidas.

    Normalização defensiva de escala: o campo 'dividendYield' do yfinance
    já devolveu, em diferentes versões, ora a fracção (0.06 = 6%) ora a
    percentagem (6.0 = 6%). Sem esta normalização, um valor em escala
    errada faria e^(-q*T) colapsar para perto de zero e destruiria os
    Greeks silenciosamente (sem erro, sem exceção — só números errados).
    """
    if ticker in _dy_cache:
        return _dy_cache[ticker]
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info
        dy_raw = float(info.get("dividendYield") or 0.0)

        # Normalização de escala: yields anuais plausíveis para ações B3
        # ficam tipicamente entre 0% e 20%. Se o valor já vier > 1.0,
        # é quase certo que está em percentagem (ex: 6.0 em vez de 0.06).
        if dy_raw > 1.0:
            dy = dy_raw / 100.0
            log.warning(
                "[%s] dividendYield=%.4f (>1.0) — assumido como percentagem, "
                "normalizado para fracção: %.4f", ticker, dy_raw, dy
            )
        else:
            dy = dy_raw

        # Sanidade: yield anual fora de [0, 0.20] é implausível para B3.
        # Em vez de usar um valor potencialmente corrompido, cair para 0.0
        # e avisar — é mais seguro subestimar o dividendo do que inflacioná-lo.
        if dy < 0.0 or dy > 0.20:
            log.warning(
                "[%s] dividend yield fora da faixa plausível (%.4f) — "
                "usando 0.0 em vez de arriscar Greeks corrompidos", ticker, dy
            )
            dy = 0.0

        _dy_cache[ticker] = dy
        log.info("[%s] Dividend yield: %.4f (%.2f%% a.a.)", ticker, dy, dy * 100)
        return dy
    except Exception as exc:
        log.debug("[%s] Dividend yield indisponível (%s) — usando 0.0", ticker, exc)
        _dy_cache[ticker] = 0.0
        return 0.0


def invalidar_cache_dy(ticker: Optional[str] = None) -> None:
    """Invalida cache de dividend yield. Se ticker=None, invalida todos."""
    global _dy_cache
    if ticker:
        _dy_cache.pop(ticker, None)
    else:
        _dy_cache.clear()


# ─── Black-Scholes ────────────────────────────────────────────────────────────

def _d1_d2(S: float, K: float, T: float, r: float, sigma: float,
           q: float = 0.0) -> Tuple[float, float]:
    """
    Calcula d1 e d2 do modelo Black-Scholes-Merton.
    q: dividend yield contínuo anual (ex: 0.06 para 6% a.a.)
    Para ações sem dividendo, q=0 reproduz o B-S clássico.
    """
    if T <= 0 or sigma <= 0:
        return 0.0, 0.0
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return d1, d2


def black_scholes(
    S:     float,   # Preço atual do ativo
    K:     float,   # Strike
    T:     float,   # Tempo até vencimento em anos
    r:     float,   # Taxa livre de risco (CDI) em decimal anual
    sigma: float,   # Volatilidade implícita em decimal anual
    tipo:  str,     # 'CALL' ou 'PUT'
    q:     float = 0.0,  # Dividend yield contínuo anual (ex: 0.06 = 6% a.a.)
) -> dict:
    """
    Calcula prêmio e todos os Greeks via Black-Scholes-Merton.
    Inclui ajuste de dividendo contínuo (q). Para q=0 reproduz B-S clássico.
    Importante para ações brasileiras de alto yield (BBAS3, ITUB4, VALE3, PETR4).
    Retorna dict com: preco, delta, gamma, theta, vega, rho
    """
    if T <= 0:
        intrinseco = max(S - K, 0) if tipo == "CALL" else max(K - S, 0)
        return {"preco": intrinseco, "delta": 1.0 if tipo == "CALL" else -1.0,
                "gamma": 0.0, "theta": 0.0, "vega": 0.0, "rho": 0.0}

    d1, d2 = _d1_d2(S, K, T, r, sigma, q)
    eq_T   = math.exp(-q * T)   # factor de desconto do dividendo

    if tipo == "CALL":
        preco = S * eq_T * N(d1) - K * math.exp(-r * T) * N(d2)
        delta = eq_T * N(d1)
        rho   = K * T * math.exp(-r * T) * N(d2) / 100
    else:
        preco = K * math.exp(-r * T) * N(-d2) - S * eq_T * N(-d1)
        delta = eq_T * (N(d1) - 1)
        rho   = -K * T * math.exp(-r * T) * N(-d2) / 100

    gamma = eq_T * n(d1) / (S * sigma * math.sqrt(T))
    vega  = S * eq_T * n(d1) * math.sqrt(T) / 100
    theta = (
        -(S * eq_T * n(d1) * sigma) / (2 * math.sqrt(T))
        - r * K * math.exp(-r * T) * (N(d2) if tipo == "CALL" else N(-d2))
        + q * S * eq_T * (N(d1) if tipo == "CALL" else -N(-d1))
    ) / 365

    return {
        "preco": round(preco, 4),
        "delta": round(delta, 6),
        "gamma": round(gamma, 6),
        "theta": round(theta, 6),
        "vega":  round(vega,  6),
        "rho":   round(rho,   6),
    }


# ─── Binomial CRR (opções americanas) ────────────────────────────────────────

def _binomial_preco(S: float, K: float, T: float, r: float,
                    sigma: float, tipo: str, N_: int,
                    q: float = 0.0) -> float:
    """
    Calcula apenas o PREÇO via árvore Binomial CRR com dividend yield q.
    Função interna — usada nas diferenças finitas de binomial_crr().
    """
    if T <= 0 or sigma <= 0:
        return max(S - K, 0) if tipo == "CALL" else max(K - S, 0)

    dt   = T / N_
    u    = math.exp(sigma * math.sqrt(dt))
    d    = 1.0 / u
    p    = (math.exp((r - q) * dt) - d) / (u - d)   # ajuste Merton
    disc = math.exp(-r * dt)

    precos = [S * (u ** (N_ - 2 * j)) for j in range(N_ + 1)]
    arvore = ([max(s - K, 0) for s in precos] if tipo == "CALL"
              else [max(K - s, 0) for s in precos])

    for i in range(N_ - 1, -1, -1):
        for j in range(i + 1):
            s_nodo    = S * (u ** (i - 2 * j))
            cont_val  = disc * (p * arvore[j] + (1 - p) * arvore[j + 1])
            exer_val  = max(s_nodo - K, 0) if tipo == "CALL" else max(K - s_nodo, 0)
            arvore[j] = max(cont_val, exer_val)

    return arvore[0]


def binomial_crr(
    S:     float,
    K:     float,
    T:     float,
    r:     float,
    sigma: float,
    tipo:  str,
    N_:    int   = PASSOS_BINOMIAL,
    q:     float = 0.0,   # dividend yield contínuo anual
) -> dict:
    """
    Modelo Binomial Cox-Ross-Rubinstein para opções americanas com dividendo.
    Considera exercício antecipado e ajuste de dividend yield (Merton).
    """
    if T <= 0 or sigma <= 0:
        intrinseco = max(S - K, 0) if tipo == "CALL" else max(K - S, 0)
        return {"preco": intrinseco, "delta": 0.0, "gamma": 0.0,
                "theta": 0.0, "vega": 0.0, "rho": 0.0}

    preco = _binomial_preco(S, K, T, r, sigma, tipo, N_, q)

    h      = S * 0.02
    p_up   = _binomial_preco(S + h, K, T, r, sigma, tipo, N_, q)
    p_dn   = _binomial_preco(S - h, K, T, r, sigma, tipo, N_, q)
    delta  = (p_up - p_dn) / (2 * h)
    gamma  = (p_up - 2 * preco + p_dn) / (h ** 2)

    dt_   = 1 / 365
    p_dt  = _binomial_preco(S, K, max(T - dt_, 1e-6), r, sigma, tipo, N_, q)
    theta = (p_dt - preco) / dt_

    dv    = 0.01
    p_vp  = _binomial_preco(S, K, T, r, sigma + dv, tipo, N_, q)
    p_vm  = _binomial_preco(S, K, T, r, sigma - dv, tipo, N_, q)
    vega  = (p_vp - p_vm) / (2 * dv) / 100

    dr    = 0.001
    p_rp  = _binomial_preco(S, K, T, r + dr, sigma, tipo, N_, q)
    p_rm  = _binomial_preco(S, K, T, r - dr, sigma, tipo, N_, q)
    rho   = (p_rp - p_rm) / (2 * dr) / 100

    return {
        "preco": round(preco,  4),
        "delta": round(delta,  6),
        "gamma": round(gamma,  6),
        "theta": round(theta,  6),
        "vega":  round(vega,   6),
        "rho":   round(rho,    6),
    }


# ─── Volatilidade Implícita ───────────────────────────────────────────────────

def volatilidade_implicita(
    preco_mercado: float,
    S: float, K: float, T: float, r: float, tipo: str,
    q: float = 0.0,
    tol: float = 1e-5, max_iter: int = 100,
) -> Optional[float]:
    """
    Resolve sigma via método de Brent.
    Retorna None se não convergir ou se os dados forem inconsistentes.
    Rejeita vol implícita > 500% (sigma > 5.0) — indica dado de mercado inválido.
    """
    if preco_mercado <= 0 or T <= 0:
        return None

    # Rejeitar prêmio abaixo do lower bound de Merton (inclui dividendo)
    import math as _math
    eq_T = _math.exp(-q * T)
    if tipo == "CALL":
        lower = max(S * eq_T - K * _math.exp(-r * T), 0)
    else:
        lower = max(K * _math.exp(-r * T) - S * eq_T, 0)
    if preco_mercado < lower * 0.95:
        return None

    def objetivo(sigma):
        return black_scholes(S, K, T, r, sigma, tipo, q)["preco"] - preco_mercado

    # Busca intervalo inicial
    lo, hi = 0.001, 10.0
    if objetivo(lo) * objetivo(hi) > 0:
        return None

    for _ in range(max_iter):
        mid = (lo + hi) / 2
        if abs(hi - lo) < tol:
            return round(mid, 6)
        if objetivo(mid) * objetivo(lo) < 0:
            hi = mid
        else:
            lo = mid
    return None


# ─── Classificação A/I/OTM ───────────────────────────────────────────────────

def classificar_moneyness(S: float, K: float, tipo: str, tolerancia: float = 0.005) -> str:
    """
    Classifica o contrato como ATM, ITM ou OTM.
    tolerancia: faixa percentual para ATM (padrão ±0.5%)
    """
    dist = (S - K) / K
    if abs(dist) <= tolerancia:
        return "ATM"
    if tipo == "CALL":
        return "ITM" if dist > 0 else "OTM"
    else:  # PUT
        return "ITM" if dist < 0 else "OTM"


def distancia_strike(S: float, K: float) -> float:
    """Retorna distância percentual do strike ao preço atual."""
    if S == 0:
        return 0.0
    return round((K - S) / S * 100, 4)


# ─── IQ — Índice de Qualidade de Liquidez (aproximação) ─────────────────────

def calcular_iq(
    vol_financeiro:   float,
    num_negocios:     int,
    presenca_30d:     float,     # 0.0 a 1.0 (proporção de pregões com negócio)
    max_vol_ref:      float = 1_000_000.0,
    max_neg_ref:      int   = 500,
) -> float:
    """
    Aproximação do IQ do opcoes.net.br.
    Combinação ponderada de três componentes, normalizada em 0–100.

    Componentes:
      volume    (40%): volume financeiro normalizado pelo máximo de referência
      negocios  (35%): número de negócios normalizado pelo máximo de referência
      presenca  (25%): proporção de pregões com pelo menos um negócio (últimos 30)

    Nota: Esta é uma aproximação — não é idêntica ao índice proprietário.
    """
    comp_vol  = min(vol_financeiro / max_vol_ref, 1.0) if max_vol_ref > 0 else 0.0
    comp_neg  = min(num_negocios   / max_neg_ref,  1.0) if max_neg_ref > 0 else 0.0
    comp_pres = max(0.0, min(presenca_30d, 1.0))

    iq = (
        IQ_PESO_VOLUME   * comp_vol  +
        IQ_PESO_NEGOCIOS * comp_neg  +
        IQ_PESO_PRESENCA * comp_pres
    ) * 100

    return round(iq, 2)


# ─── Tempo até vencimento ─────────────────────────────────────────────────────

def tempo_ate_vencimento(data_vencimento: str) -> float:
    """
    Calcula T em anos a partir de hoje até a data de vencimento.
    data_vencimento: string no formato 'AAAA-MM-DD'
    Retorna 0.0 se a data já passou.
    """
    from datetime import date
    try:
        venc = date.fromisoformat(data_vencimento)
        hoje = date.today()
        dias = (venc - hoje).days
        return max(dias / 365.0, 0.0)
    except ValueError:
        return 0.0


# ─── Cálculo completo de um contrato ─────────────────────────────────────────

def calcular_contrato(
    ticker_ativo:  str,
    codigo:        str,
    tipo:          str,
    strike:        float,
    vencimento:    str,
    preco_ativo:   float,
    preco_opcao:   float,
    vol_financeiro: float,
    num_negocios:  int,
    presenca_30d:  float,
    modelo_hint:   str = "auto",
) -> dict:
    """
    Calcula todos os indicadores para um contrato de opção.
    Retorna dict pronto para inserção em greeks via upsert_greeks().
    """
    from datetime import datetime

    r     = obter_cdi()
    q     = obter_dividend_yield(ticker_ativo)   # dividend yield Merton
    T     = tempo_ate_vencimento(vencimento)
    S     = preco_ativo
    K     = strike

    # Volatilidade implícita (com ajuste de dividendo)
    sigma = volatilidade_implicita(preco_opcao, S, K, T, r, tipo, q)
    if sigma is None:
        sigma = 0.30  # fallback: 30% de vol

    # Escolha do modelo
    if modelo_hint == "auto":
        modelo = "binomial" if tipo == "CALL" else "black_scholes"
    else:
        modelo = modelo_hint

    if modelo == "binomial":
        greeks = binomial_crr(S, K, T, r, sigma, tipo, N_=PASSOS_BINOMIAL, q=q)
    else:
        greeks = black_scholes(S, K, T, r, sigma, tipo, q=q)

    otm_atm_itm = classificar_moneyness(S, K, tipo)
    dist        = distancia_strike(S, K)
    iq          = calcular_iq(vol_financeiro, num_negocios, presenca_30d)

    return {
        "opcao_codigo": codigo,
        "ticker_ativo": ticker_ativo,
        "data_hora":    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "delta":        greeks["delta"],
        "gamma":        greeks["gamma"],
        "theta":        greeks["theta"],
        "vega":         greeks["vega"],
        "rho":          greeks["rho"],
        "otm_atm_itm":  otm_atm_itm,
        "dist_strike":  dist,
        "iq_calc":      iq,
        "modelo_usado": modelo,
        "preco_ativo":  S,
        "taxa_cdi":     r,
    }

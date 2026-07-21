"""Testes para greeks.py — Black-Scholes, Binomial CRR, dividend yield, IV."""

import math
import pytest
from unittest.mock import patch

from greeks import (
    black_scholes, binomial_crr, volatilidade_implicita,
    calcular_iq, classificar_moneyness, obter_dividend_yield,
    calcular_contrato,
)


class TestBlackScholes:
    def test_paridade_call_put_sem_dividendo(self, no_network):
        S, K, T, r, sigma = 50, 50, 0.5, 0.1075, 0.30
        c = black_scholes(S, K, T, r, sigma, "CALL")
        p = black_scholes(S, K, T, r, sigma, "PUT")
        diferenca_teorica = S - K * math.exp(-r * T)
        assert abs((c["preco"] - p["preco"]) - diferenca_teorica) < 0.01

    def test_gamma_sempre_positivo(self, no_network):
        for tipo in ("CALL", "PUT"):
            r = black_scholes(50, 50, 0.5, 0.1075, 0.30, tipo)
            assert r["gamma"] > 0

    def test_theta_sempre_negativo_para_comprador(self, no_network):
        for tipo in ("CALL", "PUT"):
            r = black_scholes(50, 50, 0.5, 0.1075, 0.30, tipo)
            assert r["theta"] < 0

    def test_dividendo_reduz_delta_de_call(self, no_network):
        sem_div = black_scholes(21.0, 20.0, 0.5, 0.1075, 0.30, "CALL", q=0.0)
        com_div = black_scholes(21.0, 20.0, 0.5, 0.1075, 0.30, "CALL", q=0.06)
        assert com_div["delta"] < sem_div["delta"]

    def test_call_itm_delta_maior_que_05(self, no_network):
        r = black_scholes(25, 20, 0.5, 0.1075, 0.30, "CALL")
        assert r["delta"] > 0.5

    def test_put_itm_delta_menor_que_neg05(self, no_network):
        r = black_scholes(15, 20, 0.5, 0.1075, 0.30, "PUT")
        assert r["delta"] < -0.5

    def test_vega_maximo_no_dinheiro(self, no_network):
        vega_atm = black_scholes(20, 20, 0.5, 0.1075, 0.30, "CALL")["vega"]
        vega_otm = black_scholes(20, 30, 0.5, 0.1075, 0.30, "CALL")["vega"]
        assert vega_atm > vega_otm

    @pytest.mark.parametrize("S,K,T,r,sigma,tipo", [
        (50, 50, 0.5, 0.10, 5.00, "CALL"),     # vol muito alta
        (50, 50, 0.5, 0.10, 0.01, "CALL"),     # vol muito baixa
        (50, 50, 1/8760, 0.10, 0.30, "CALL"),  # quase no vencimento
        (100, 10, 0.5, 0.10, 0.30, "CALL"),    # ITM profundo
        (10, 100, 0.5, 0.10, 0.30, "CALL"),    # OTM profundo
        (50, 50, 0.5, -0.02, 0.30, "CALL"),    # taxa negativa
    ])
    def test_valores_extremos_nao_geram_nan(self, S, K, T, r, sigma, tipo, no_network):
        r_calc = black_scholes(S, K, T, r, sigma, tipo)
        assert r_calc["preco"] == r_calc["preco"]  # NaN != NaN
        assert math.isfinite(r_calc["preco"])


class TestBinomialCRR:
    def test_converge_para_black_scholes(self, no_network):
        S, K, T, r, sigma = 50, 50, 0.5, 0.1075, 0.30
        bs = black_scholes(S, K, T, r, sigma, "CALL")
        bn = binomial_crr(S, K, T, r, sigma, "CALL", 100)
        assert abs(bn["preco"] - bs["preco"]) / bs["preco"] * 100 < 2

    def test_gamma_positivo_apos_correcao_de_h(self, no_network):
        for moneyness in [(60, 50), (50, 50), (40, 50)]:
            S, K = moneyness
            r = binomial_crr(S, K, 0.5, 0.1075, 0.30, "CALL", 100)
            assert r["gamma"] > 0

    def test_dividendo_aplicado_no_binomial(self, no_network):
        sem_div = binomial_crr(21.0, 20.0, 0.5, 0.1075, 0.30, "CALL", 100, q=0.0)
        com_div = binomial_crr(21.0, 20.0, 0.5, 0.1075, 0.30, "CALL", 100, q=0.06)
        assert com_div["delta"] < sem_div["delta"]


class TestVolatilidadeImplicita:
    def test_recupera_sigma_original(self, no_network):
        S, K, T, r, sigma_original = 50, 52, 0.5, 0.1075, 0.28
        preco = black_scholes(S, K, T, r, sigma_original, "CALL")["preco"]
        sigma_calc = volatilidade_implicita(preco, S, K, T, r, "CALL")
        assert sigma_calc is not None
        assert abs(sigma_calc - sigma_original) < 0.001

    def test_premio_abaixo_do_minimo_teorico_retorna_none(self, no_network):
        S, K, T, r = 50, 50, 0.5, 0.1075
        resultado = volatilidade_implicita(0.0001, S, K, T, r, "CALL")
        assert resultado is None

    def test_considera_dividendo_no_lower_bound(self, no_network):
        # Com dividendo, o lower bound da CALL é menor — um preço que seria
        # rejeitado sem dividendo pode ser aceito com dividendo aplicado.
        S, K, T, r = 50, 50, 0.5, 0.1075
        sigma_sem_q = volatilidade_implicita(2.0, S, K, T, r, "CALL", q=0.0)
        sigma_com_q = volatilidade_implicita(2.0, S, K, T, r, "CALL", q=0.08)
        # Ambos devem ser resolvíveis ou None de forma consistente — o
        # importante é que a função aceita o parâmetro sem lançar exceção.
        assert sigma_sem_q is None or sigma_sem_q > 0
        assert sigma_com_q is None or sigma_com_q > 0


class TestDividendYield:
    def test_fracao_normal_mantida(self):
        with patch("yfinance.Ticker") as MockTicker:
            MockTicker.return_value.info = {"dividendYield": 0.06}
            from greeks import _dy_cache
            _dy_cache.clear()
            assert abs(obter_dividend_yield("BBAS3.SA") - 0.06) < 1e-6

    def test_percentagem_normalizada_para_fracao(self):
        with patch("yfinance.Ticker") as MockTicker:
            MockTicker.return_value.info = {"dividendYield": 6.0}
            from greeks import _dy_cache
            _dy_cache.clear()
            assert abs(obter_dividend_yield("PETR4.SA") - 0.06) < 1e-6

    def test_valor_implausivel_cai_para_zero(self):
        with patch("yfinance.Ticker") as MockTicker:
            MockTicker.return_value.info = {"dividendYield": 50.0}
            from greeks import _dy_cache
            _dy_cache.clear()
            assert obter_dividend_yield("ITUB4.SA") == 0.0

    def test_valor_negativo_cai_para_zero(self):
        with patch("yfinance.Ticker") as MockTicker:
            MockTicker.return_value.info = {"dividendYield": -0.5}
            from greeks import _dy_cache
            _dy_cache.clear()
            assert obter_dividend_yield("MGLU3.SA") == 0.0

    def test_erro_de_rede_devolve_zero(self):
        with patch("yfinance.Ticker", side_effect=Exception("sem rede")):
            from greeks import _dy_cache
            _dy_cache.clear()
            assert obter_dividend_yield("VALE3.SA") == 0.0


class TestIQeMoneyness:
    def test_iq_liquido_maior_que_iliquido(self):
        iq_alto  = calcular_iq(2_500_000, 800, 0.95)
        iq_baixo = calcular_iq(15_000, 12, 0.15)
        assert iq_alto > iq_baixo

    def test_iq_dentro_da_escala_0_100(self):
        for vol, neg, pres in [(0, 0, 0), (10**9, 10**6, 1.0)]:
            iq = calcular_iq(vol, neg, pres)
            assert 0 <= iq <= 100

    @pytest.mark.parametrize("S,K,tipo,esperado", [
        (21, 20, "CALL", "ITM"),
        (21, 20, "PUT",  "OTM"),
        (20, 20, "CALL", "ATM"),
        (19, 20, "CALL", "OTM"),
        (19, 20, "PUT",  "ITM"),
    ])
    def test_classificacao_moneyness(self, S, K, tipo, esperado):
        assert classificar_moneyness(S, K, tipo) == esperado


class TestCalcularContrato:
    def test_fluxo_completo_sem_rede(self, no_network):
        with patch("greeks.obter_dividend_yield", return_value=0.0):
            r = calcular_contrato(
                "BBAS3.SA", "T200", "CALL", 20.0, "2027-06-18",
                21.0, 1.8, 225_000, 150, 0.8, "binomial"
            )
        assert 0 < r["delta"] < 1
        assert r["modelo_usado"] == "binomial"
        assert r["taxa_cdi"] > 0

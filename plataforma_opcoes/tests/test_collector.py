"""
Testes para collector.py — conversão segura, validação de sanidade,
coleta de histórico/opções via yfinance (mockado) e orquestração do
fallback autenticado → yfinance.

collector.py não tinha nenhum teste dedicado antes desta suíte (0% de
cobertura), apesar de ser o motor central de coleta de dados. yfinance
é sempre mockado (ver conftest.py) — nenhum destes testes toca a rede.
"""

import pandas as pd
from unittest.mock import MagicMock, PropertyMock

import collector


# ─── _dec / _int_ ──────────────────────────────────────────────────────────

class TestConversaoSegura:
    def test_dec_valor_valido(self):
        assert collector._dec(20.12345) == 20.1235  # ROUND_HALF_UP, 4 casas

    def test_dec_none_retorna_none(self):
        assert collector._dec(None) is None

    def test_dec_nan_retorna_none(self):
        assert collector._dec(float("nan")) is None

    def test_dec_string_invalida_retorna_none(self):
        assert collector._dec("não-é-número") is None

    def test_dec_zero_e_valido(self):
        assert collector._dec(0) == 0.0

    def test_int_valor_valido(self):
        assert collector._int_(1500.0) == 1500

    def test_int_none_retorna_none(self):
        assert collector._int_(None) is None

    def test_int_nan_retorna_none(self):
        assert collector._int_(float("nan")) is None


# ─── _validar_sanidade_cotacao ─────────────────────────────────────────────

class TestValidarSanidadeCotacao:
    def test_valores_plausiveis_sem_avisos(self):
        avisos = collector._validar_sanidade_cotacao(
            "BBAS3.SA", "2026-01-02", 20.0, 20.5, 19.8, 20.3, 1_000_000
        )
        assert avisos == []

    def test_todos_none_sem_avisos(self):
        """Dia sem pregão (feriado/fim de semana) — ausência de dados não é erro."""
        avisos = collector._validar_sanidade_cotacao(
            "BBAS3.SA", "2026-01-01", None, None, None, None, None
        )
        assert avisos == []

    def test_preco_zero_ou_negativo_gera_aviso(self):
        avisos = collector._validar_sanidade_cotacao(
            "BBAS3.SA", "2026-01-02", -1.0, 20.5, 19.8, 20.3, 1000
        )
        assert any("negativo" in a.lower() for a in avisos)

    def test_maxima_menor_que_minima_gera_aviso(self):
        avisos = collector._validar_sanidade_cotacao(
            "BBAS3.SA", "2026-01-02", 20.0, 18.0, 19.0, 20.3, 1000
        )
        assert any("Máxima" in a for a in avisos)

    def test_variacao_maior_que_50pct_gera_aviso(self):
        avisos = collector._validar_sanidade_cotacao(
            "BBAS3.SA", "2026-01-02", 10.0, 20.0, 10.0, 20.0, 1000
        )
        assert any("Variação" in a for a in avisos)

    def test_volume_negativo_gera_aviso(self):
        avisos = collector._validar_sanidade_cotacao(
            "BBAS3.SA", "2026-01-02", 20.0, 20.5, 19.8, 20.3, -100
        )
        assert any("Volume negativo" in a for a in avisos)


# ─── _validar_sanidade_opcao ───────────────────────────────────────────────

class TestValidarSanidadeOpcao:
    def _reg(self, **overrides):
        base = {"codigo": "T200", "tipo": "CALL", "ultimo": 1.5,
                "strike": 20.0, "vol_implícita": 0.30}
        base.update(overrides)
        return base

    def test_valores_plausiveis_sem_avisos(self):
        assert collector._validar_sanidade_opcao(self._reg(), preco_ativo=20.0) == []

    def test_premio_negativo_gera_aviso(self):
        avisos = collector._validar_sanidade_opcao(self._reg(ultimo=-0.5))
        assert any("negativo" in a.lower() for a in avisos)

    def test_strike_invalido_gera_aviso(self):
        avisos = collector._validar_sanidade_opcao(self._reg(strike=0))
        assert any("Strike" in a for a in avisos)

    def test_vol_implicita_negativa_gera_aviso(self):
        avisos = collector._validar_sanidade_opcao(self._reg(vol_implícita=-0.1))
        assert any("negativa" in a.lower() for a in avisos)

    def test_vol_implicita_acima_de_500pct_gera_aviso(self):
        avisos = collector._validar_sanidade_opcao(self._reg(vol_implícita=6.0))
        assert any(">500%" in a for a in avisos)

    def test_premio_call_maior_que_ativo_gera_aviso(self):
        avisos = collector._validar_sanidade_opcao(
            self._reg(tipo="CALL", ultimo=25.0), preco_ativo=20.0
        )
        assert any("preço ativo" in a for a in avisos)

    def test_premio_put_maior_que_ativo_nao_gera_aviso(self):
        """A checagem de prémio > ativo só se aplica a CALL, não a PUT."""
        avisos = collector._validar_sanidade_opcao(
            self._reg(tipo="PUT", ultimo=25.0), preco_ativo=20.0
        )
        assert avisos == []


# ─── _baixar_historico (retry) ─────────────────────────────────────────────

class TestBaixarHistorico:
    def test_sucesso_na_primeira_tentativa(self, yf_ticker, monkeypatch):
        monkeypatch.setattr(collector, "RETRY_DELAY_SEG", 0)
        df = pd.DataFrame({"Close": [20.0]}, index=pd.to_datetime(["2026-01-02"]))
        yf_ticker.history.return_value = df
        resultado = collector._baixar_historico("BBAS3.SA", "5d")
        assert not resultado.empty
        yf_ticker.history.assert_called_with(period="5d", auto_adjust=False)

    def test_todas_tentativas_vazias_devolve_df_vazio(self, yf_ticker, monkeypatch):
        monkeypatch.setattr(collector, "RETRY_DELAY_SEG", 0)
        yf_ticker.history.return_value = pd.DataFrame()
        resultado = collector._baixar_historico("BBAS3.SA", "5d")
        assert resultado.empty
        assert yf_ticker.history.call_count == 3  # MAX_RETRIES

    def test_excecao_seguida_de_sucesso(self, yf_ticker, monkeypatch):
        monkeypatch.setattr(collector, "RETRY_DELAY_SEG", 0)
        df_ok = pd.DataFrame({"Close": [20.0]}, index=pd.to_datetime(["2026-01-02"]))
        yf_ticker.history.side_effect = [Exception("timeout"), df_ok]
        resultado = collector._baixar_historico("BBAS3.SA", "5d")
        assert not resultado.empty
        assert yf_ticker.history.call_count == 2

    def test_excecao_em_todas_tentativas_devolve_df_vazio(self, yf_ticker, monkeypatch):
        monkeypatch.setattr(collector, "RETRY_DELAY_SEG", 0)
        yf_ticker.history.side_effect = Exception("erro de rede")
        resultado = collector._baixar_historico("BBAS3.SA", "5d")
        assert resultado.empty


# ─── coletar_historico ──────────────────────────────────────────────────────

class TestColetarHistorico:
    def _df_historico(self, tz=None):
        idx = pd.date_range("2026-01-02", periods=3, freq="D", tz=tz)
        return pd.DataFrame({
            "Open":       [20.0, 20.5, 21.0],
            "High":       [20.6, 21.0, 21.4],
            "Low":        [19.8, 20.2, 20.8],
            "Close":      [20.4, 20.8, 21.2],
            "Adj Close":  [20.4, 20.8, 21.2],
            "Volume":     [1_000_000, 1_200_000, 900_000],
        }, index=idx)

    def test_df_vazio_retorna_zero_sem_inserir(self, yf_ticker, db_temp, monkeypatch):
        monkeypatch.setattr(collector, "RETRY_DELAY_SEG", 0)
        yf_ticker.history.return_value = pd.DataFrame()
        assert collector.coletar_historico("BBAS3.SA") == 0
        assert db_temp.consultar_cotacoes("BBAS3.SA") == []

    def test_insercao_basica(self, yf_ticker, db_temp, monkeypatch):
        monkeypatch.setattr(collector, "RETRY_DELAY_SEG", 0)
        yf_ticker.history.return_value = self._df_historico()
        novos = collector.coletar_historico("BBAS3.SA")
        assert novos == 3
        rows = db_temp.consultar_cotacoes("BBAS3.SA")
        assert len(rows) == 3
        fechamentos = {round(r["fechamento"], 1) for r in rows}
        assert fechamentos == {20.4, 20.8, 21.2}

    def test_indice_com_timezone_e_normalizado(self, yf_ticker, db_temp, monkeypatch):
        """Índice tz-aware (comum no yfinance) precisa ser convertido para naive."""
        monkeypatch.setattr(collector, "RETRY_DELAY_SEG", 0)
        yf_ticker.history.return_value = self._df_historico(tz="America/Sao_Paulo")
        novos = collector.coletar_historico("BBAS3.SA")
        assert novos == 3

    def test_reinsercao_idempotente(self, yf_ticker, db_temp, monkeypatch):
        monkeypatch.setattr(collector, "RETRY_DELAY_SEG", 0)
        yf_ticker.history.return_value = self._df_historico()
        collector.coletar_historico("BBAS3.SA")
        segunda = collector.coletar_historico("BBAS3.SA")
        assert segunda == 0


class TestColetarHistoricoTodos:
    def test_isola_falha_por_ticker(self, db_temp, monkeypatch):
        monkeypatch.setattr(collector, "TICKERS", ["AAAA3.SA", "BBBB4.SA"])

        def _fake(ticker, periodo="2y"):
            if ticker == "AAAA3.SA":
                raise RuntimeError("falha simulada")
            return 5

        monkeypatch.setattr(collector, "coletar_historico", _fake)
        resultado = collector.coletar_historico_todos()
        assert resultado == {"AAAA3.SA": 0, "BBBB4.SA": 5}


# ─── cotacao_atual ──────────────────────────────────────────────────────────

class TestCotacaoAtual:
    def test_sucesso(self, yf_ticker):
        yf_ticker.fast_info.last_price = 20.5
        yf_ticker.fast_info.previous_close = 20.0
        resultado = collector.cotacao_atual("BBAS3.SA")
        assert resultado["fonte"] == "yfinance"
        assert resultado["preco"] == 20.5
        assert abs(resultado["variacao_pct"] - 2.5) < 1e-6

    def test_erro_retorna_fonte_erro(self, monkeypatch):
        import collector as _c
        monkeypatch.setattr(_c, "yf", None)  # yf.Ticker(...) -> AttributeError
        resultado = collector.cotacao_atual("BBAS3.SA")
        assert resultado["fonte"] == "erro"
        assert resultado["preco"] == 0.0

    def test_previous_close_zero_nao_gera_divisao_por_zero(self, yf_ticker):
        yf_ticker.fast_info.last_price = 20.5
        yf_ticker.fast_info.previous_close = 0
        resultado = collector.cotacao_atual("BBAS3.SA")
        assert resultado["variacao_pct"] == 0.0


# ─── _coletar_opcoes_yfinance / coletar_opcoes ─────────────────────────────

class TestColetarOpcoesYfinance:
    def _chain(self, calls_df=None, puts_df=None):
        chain = MagicMock()
        chain.calls = calls_df if calls_df is not None else pd.DataFrame()
        chain.puts = puts_df if puts_df is not None else pd.DataFrame()
        return chain

    def test_sem_vencimentos_disponiveis(self, yf_ticker):
        yf_ticker.options = ()
        assert collector._coletar_opcoes_yfinance("BBAS3.SA") == []

    def test_erro_ao_obter_vencimentos(self, yf_ticker):
        # Cada MagicMock() tem a sua própria subclasse dinâmica — configurar
        # a propriedade em type(yf_ticker) não vaza para outros testes.
        type(yf_ticker).options = PropertyMock(side_effect=Exception("boom"))
        assert collector._coletar_opcoes_yfinance("BBAS3.SA") == []

    def test_mapeamento_de_campos_calls_e_puts(self, yf_ticker):
        yf_ticker.options = ("2027-01-16",)
        calls = pd.DataFrame([{
            "contractSymbol": "BBAS3C200", "strike": 20.0, "lastPrice": 1.5,
            "percentChange": 3.2, "lastTradeDate": "2026-06-17 14:00:00",
            "volume": 150, "impliedVolatility": 0.30,
        }])
        puts = pd.DataFrame([{
            "contractSymbol": "BBAS3P200", "strike": 20.0, "lastPrice": 0.8,
            "percentChange": -1.1, "lastTradeDate": "2026-06-17 14:00:00",
            "volume": 90, "impliedVolatility": 0.28,
        }])
        chain = self._chain(calls, puts)
        yf_ticker.option_chain.return_value = chain

        resultado = collector._coletar_opcoes_yfinance("BBAS3.SA")
        assert len(resultado) == 2
        call_reg = next(r for r in resultado if r["tipo"] == "CALL")
        assert call_reg["codigo"] == "BBAS3C200"
        assert call_reg["strike"] == 20.0
        assert call_reg["vol_financeiro"] == round(150 * 1.5, 2)
        assert call_reg["fonte"] == "yfinance"

    def test_vencimento_individual_com_erro_nao_aborta_os_demais(self, yf_ticker):
        yf_ticker.options = ("2027-01-16", "2027-04-17")
        calls = pd.DataFrame([{
            "contractSymbol": "X", "strike": 20.0, "lastPrice": 1.0,
            "percentChange": 0.0, "lastTradeDate": "2026-06-17 14:00:00",
            "volume": 10, "impliedVolatility": 0.3,
        }])

        def _side_effect(venc):
            if venc == "2027-01-16":
                raise RuntimeError("falha neste vencimento")
            return self._chain(calls, pd.DataFrame())

        yf_ticker.option_chain.side_effect = _side_effect
        resultado = collector._coletar_opcoes_yfinance("BBAS3.SA")
        assert len(resultado) == 1  # só o vencimento que não falhou


class TestColetarOpcoes:
    def test_fallback_para_yfinance_quando_nao_configurado(self, yf_ticker, db_temp, monkeypatch):
        """auth.autenticacao_configurada() == False -> cai para yfinance."""
        import auth
        monkeypatch.setattr(auth, "autenticacao_configurada", lambda: False)
        yf_ticker.options = ()
        assert collector.coletar_opcoes("BBAS3.SA") == 0

    def test_fallback_para_yfinance_quando_autenticado_falha(self, yf_ticker, db_temp, monkeypatch):
        """Mesmo com autenticação 'configurada', qualquer exceção cai para yfinance."""
        import auth
        monkeypatch.setattr(auth, "autenticacao_configurada", lambda: True)
        monkeypatch.setattr(
            auth, "coletar_opcoes_autenticado",
            lambda ticker: (_ for _ in ()).throw(RuntimeError("sessão expirada"))
        )
        yf_ticker.options = ()
        assert collector.coletar_opcoes("BBAS3.SA") == 0

    def test_usa_fonte_autenticada_quando_disponivel(self, db_temp, monkeypatch):
        """Quando a autenticação está configurada e funciona, yfinance nem é chamado."""
        import auth
        monkeypatch.setattr(auth, "autenticacao_configurada", lambda: True)
        registros_fake = [{
            "ticker_ativo": "BBAS3.SA", "codigo": "T200", "tipo": "CALL",
            "modelo": "Americano", "strike": 20.0, "vencimento": "2027-06-18",
            "ultimo": 1.5, "variacao_pct": 1.0, "data_hora": "2026-06-17 14:00:00",
            "num_negocios": 100, "vol_financeiro": 150.0, "vol_implícita": 0.3,
            "iq": None, "coberto": None, "descoberto": None, "travado": None,
            "titulares": None, "lancadores": None, "fonte": "opcoes.net.br",
        }]
        monkeypatch.setattr(auth, "coletar_opcoes_autenticado", lambda ticker: registros_fake)
        novos = collector.coletar_opcoes("BBAS3.SA")
        assert novos == 1
        rows = db_temp.consultar_opcoes("BBAS3.SA")
        assert rows[0]["fonte"] == "opcoes.net.br"


class TestColetarOpcoesTodos:
    def test_isola_falha_por_ticker(self, monkeypatch):
        monkeypatch.setattr(collector, "TICKERS", ["AAAA3.SA", "BBBB4.SA"])

        def _fake(ticker):
            if ticker == "AAAA3.SA":
                raise RuntimeError("falha simulada")
            return 7

        monkeypatch.setattr(collector, "coletar_opcoes", _fake)
        resultado = collector.coletar_opcoes_todos()
        assert resultado == {"AAAA3.SA": 0, "BBBB4.SA": 7}


# ─── calcular_greeks_ticker / calcular_greeks_todos ────────────────────────

class TestCalcularGreeksTicker:
    def _inserir_opcao(self, db_temp, codigo="T200", vencimento="2027-06-18", tipo="CALL"):
        db_temp.upsert_opcoes([{
            "ticker_ativo": "BBAS3.SA", "codigo": codigo, "tipo": tipo,
            "modelo": None, "strike": 20.0, "vencimento": vencimento,
            "ultimo": 1.5, "variacao_pct": 1.0, "data_hora": "2026-06-17 14:00:00",
            "num_negocios": 100, "vol_financeiro": 150_000.0, "vol_implícita": 0.30,
            "iq": None, "coberto": None, "descoberto": None, "travado": None,
            "titulares": None, "lancadores": None, "fonte": "test",
        }])

    def test_sem_opcoes_no_banco_retorna_zero(self, yf_ticker, db_temp):
        yf_ticker.fast_info.last_price = 20.0
        yf_ticker.fast_info.previous_close = 20.0
        assert collector.calcular_greeks_ticker("BBAS3.SA") == 0

    def test_preco_ativo_invalido_nao_calcula(self, yf_ticker, db_temp):
        self._inserir_opcao(db_temp)
        yf_ticker.fast_info.last_price = 0
        yf_ticker.fast_info.previous_close = 0
        assert collector.calcular_greeks_ticker("BBAS3.SA") == 0

    def test_opcao_vencida_e_ignorada(self, yf_ticker, db_temp):
        self._inserir_opcao(db_temp, vencimento="2020-01-01")
        yf_ticker.fast_info.last_price = 20.0
        yf_ticker.fast_info.previous_close = 20.0
        assert collector.calcular_greeks_ticker("BBAS3.SA") == 0

    def test_fluxo_completo_salva_greeks_e_historico(self, yf_ticker, db_temp, no_network):
        self._inserir_opcao(db_temp)
        yf_ticker.fast_info.last_price = 20.0
        yf_ticker.fast_info.previous_close = 19.8
        total = collector.calcular_greeks_ticker("BBAS3.SA")
        assert total == 1
        historico = db_temp.consultar_greeks_historico("BBAS3.SA", "T200", limite=None)
        assert len(historico) == 1


class TestCalcularGreeksTodos:
    def test_itera_todos_os_tickers(self, monkeypatch):
        monkeypatch.setattr(collector, "TICKERS", ["AAAA3.SA", "BBBB4.SA"])
        monkeypatch.setattr(collector, "calcular_greeks_ticker", lambda t: 3)
        resultado = collector.calcular_greeks_todos()
        assert resultado == {"AAAA3.SA": 3, "BBBB4.SA": 3}


# ─── ciclo_tempo_real ───────────────────────────────────────────────────────

class TestCicloTempoReal:
    def test_ciclo_ok_registra_snapshot(self, yf_ticker, db_temp, monkeypatch):
        yf_ticker.fast_info.last_price = 20.5
        yf_ticker.fast_info.previous_close = 20.0
        monkeypatch.setattr(collector, "coletar_opcoes", lambda t: 2)
        monkeypatch.setattr(collector, "calcular_greeks_ticker", lambda t: 1)

        resultado = collector.ciclo_tempo_real("BBAS3.SA")
        assert resultado["status"] == "ok"
        assert resultado["n_opcoes"] == 2
        assert resultado["n_greeks"] == 1

        snaps = db_temp.consultar_snapshots("BBAS3.SA")
        assert len(snaps) == 1
        assert snaps[0]["status"] == "ok"

    def test_ciclo_com_erro_ainda_registra_snapshot(self, yf_ticker, db_temp, monkeypatch):
        yf_ticker.fast_info.last_price = 20.5
        yf_ticker.fast_info.previous_close = 20.0

        def _falha(ticker):
            raise RuntimeError("falha na coleta de opções")

        monkeypatch.setattr(collector, "coletar_opcoes", _falha)

        resultado = collector.ciclo_tempo_real("BBAS3.SA")
        assert resultado["status"] == "erro"
        assert resultado["n_opcoes"] == 0

        snaps = db_temp.consultar_snapshots("BBAS3.SA")
        assert len(snaps) == 1
        assert snaps[0]["status"] == "erro"


class TestCicloTempoRealTodos:
    def test_itera_todos_os_tickers(self, monkeypatch):
        monkeypatch.setattr(collector, "TICKERS", ["AAAA3.SA", "BBBB4.SA"])
        monkeypatch.setattr(
            collector, "ciclo_tempo_real",
            lambda t: {"ticker": t, "status": "ok"}
        )
        resultado = collector.ciclo_tempo_real_todos()
        assert [r["ticker"] for r in resultado] == ["AAAA3.SA", "BBBB4.SA"]


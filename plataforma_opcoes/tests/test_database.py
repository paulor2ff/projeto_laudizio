"""Testes para database.py — upsert, validação, UNIQUE constraint, histórico, purga."""

import pytest
from datetime import date, timedelta


class TestCotacoes:
    def test_insercao_e_leitura(self, db_temp):
        db_temp.upsert_cotacoes([
            ("BBAS3.SA", "2025-01-02", 20.0, 21.0, 19.5, 20.5, 20.5, 1_000_000)
        ])
        rows = db_temp.consultar_cotacoes("BBAS3.SA")
        assert len(rows) == 1
        assert abs(rows[0]["fechamento"] - 20.5) < 1e-6

    def test_reinsercao_idempotente(self, db_temp):
        reg = [("BBAS3.SA", "2025-01-02", 20.0, 21.0, 19.5, 20.5, 20.5, 1_000_000)]
        n1 = db_temp.upsert_cotacoes(reg)
        n2 = db_temp.upsert_cotacoes(reg)
        assert n1 == 1
        assert n2 == 0

    def test_campos_none_preservados_como_null(self, db_temp):
        db_temp.upsert_cotacoes([
            ("BBAS3.SA", "2025-01-02", None, None, None, None, None, None)
        ])
        rows = db_temp.consultar_cotacoes("BBAS3.SA")
        assert rows[0]["abertura"] is None
        assert rows[0]["volume"] is None

    def test_filtro_data_inicio(self, db_temp):
        base = date(2025, 1, 1)
        regs = [
            ("BBAS3.SA", (base + timedelta(days=i)).isoformat(), 20.0, 21.0, 19.0, 20.5, 20.5, 1000)
            for i in range(60)
        ]
        db_temp.upsert_cotacoes(regs)
        filtrado = db_temp.consultar_cotacoes(
            "BBAS3.SA", data_inicio=(base + timedelta(days=30)).isoformat()
        )
        assert len(filtrado) == 30

    def test_ticker_inexistente_retorna_vazio(self, db_temp):
        assert db_temp.consultar_cotacoes("NAOEXISTE.SA") == []


class TestValidarData:
    @pytest.mark.parametrize("valor,deve_passar", [
        ("2025-01-01", True),
        ("2024-02-29", True),    # ano bissexto
        ("2025-1-1",   False),   # sem zero à esquerda
        ("2025-01-1",  False),
        ("2025-1-01",  False),
        ("2023-02-29", False),   # não-bissexto
        ("2025-00-01", False),
        ("2025-13-01", False),
        ("2025-01-32", False),
        ("",           False),
        ("invalido",   False),
    ])
    def test_formatos_de_data(self, db_temp, valor, deve_passar):
        if deve_passar:
            assert db_temp._validar_data(valor) == valor
        else:
            with pytest.raises(ValueError):
                db_temp._validar_data(valor)

    def test_none_e_aceito(self, db_temp):
        assert db_temp._validar_data(None) is None


class TestOpcoes:
    def _opcao_exemplo(self, codigo, vencimento, data_hora="2026-06-17 14:00:00"):
        return {
            "ticker_ativo": "BBAS3.SA", "codigo": codigo, "tipo": "CALL",
            "modelo": None, "strike": 20.0, "vencimento": vencimento,
            "ultimo": 1.8, "variacao_pct": 1.0, "data_hora": data_hora,
            "num_negocios": 150, "vol_financeiro": 225_000.0,
            "vol_implícita": 0.30, "iq": None, "coberto": None,
            "descoberto": None, "travado": None, "titulares": None,
            "lancadores": None, "fonte": "test",
        }

    def test_insercao_basica(self, db_temp):
        n = db_temp.upsert_opcoes([self._opcao_exemplo("T200", "2027-06-18")])
        assert n == 1

    def test_unique_constraint_inclui_vencimento(self, db_temp):
        """
        Regressão do bug estrutural: UNIQUE(ticker,codigo,data_hora) sem
        vencimento causava perda silenciosa de dados quando o mesmo código
        aparecia em vencimentos diferentes com o mesmo timestamp.
        """
        mesmo_codigo_dois_vencimentos = [
            self._opcao_exemplo("BBAS3C02000", "2027-01-16"),
            self._opcao_exemplo("BBAS3C02000", "2027-04-17"),
        ]
        n = db_temp.upsert_opcoes(mesmo_codigo_dois_vencimentos)
        assert n == 2
        rows = db_temp.consultar_opcoes("BBAS3.SA")
        assert len(rows) == 2
        # SQLite com detect_types=PARSE_DECLTYPES e coluna 'vencimento DATE'
        # devolve datetime.date (conversor deprecated desde Python 3.12,
        # mas ainda funcional) — normalizar para string torna o teste
        # válido independentemente do tipo exacto devolvido.
        vencimentos = {str(r["vencimento"]) for r in rows}
        assert vencimentos == {"2027-01-16", "2027-04-17"}

    def test_filtro_tipo_call_put(self, db_temp):
        opcoes = []
        for venc in ["2027-01-16", "2027-04-17"]:
            for strike in [18.0, 19.0, 20.0]:
                for tipo in ["CALL", "PUT"]:
                    o = self._opcao_exemplo(f"C{strike}{tipo}{venc}", venc)
                    o["tipo"] = tipo
                    o["strike"] = strike
                    opcoes.append(o)
        db_temp.upsert_opcoes(opcoes)
        calls = db_temp.consultar_opcoes("BBAS3.SA", tipo="CALL")
        puts  = db_temp.consultar_opcoes("BBAS3.SA", tipo="PUT")
        assert len(calls) == 6
        assert len(puts) == 6

    def test_tipo_invalido_levanta_valueerror(self, db_temp):
        with pytest.raises(ValueError):
            db_temp.consultar_opcoes("BBAS3.SA", tipo="INVALIDO")

    def test_tipo_minusculo_normalizado(self, db_temp):
        db_temp.upsert_opcoes([self._opcao_exemplo("T200", "2027-06-18")])
        # não deve levantar exceção — normaliza para CALL internamente
        resultado = db_temp.consultar_opcoes("BBAS3.SA", tipo="call")
        assert len(resultado) == 1

    def test_filtro_vencimento(self, db_temp):
        db_temp.upsert_opcoes([
            self._opcao_exemplo("A", "2027-01-16"),
            self._opcao_exemplo("B", "2027-04-17"),
        ])
        resultado = db_temp.consultar_opcoes("BBAS3.SA", vencimento="2027-01-16")
        assert len(resultado) == 1
        assert resultado[0]["codigo"] == "A"

    def test_reinsercao_idempotente(self, db_temp):
        opc = [self._opcao_exemplo("T200", "2027-06-18")]
        db_temp.upsert_opcoes(opc)
        n_antes = db_temp.resumo_geral()["opcoes"]
        db_temp.upsert_opcoes(opc)
        n_depois = db_temp.resumo_geral()["opcoes"]
        assert n_antes == n_depois


class TestGreeksHistorico:
    def _greek_exemplo(self, codigo="T200", data_hora="2026-06-17 14:00:00"):
        return {
            "opcao_codigo": codigo, "ticker_ativo": "BBAS3.SA",
            "data_hora": data_hora, "delta": 0.5, "gamma": 0.01,
            "theta": -0.5, "vega": 0.1, "rho": 0.05, "otm_atm_itm": "ATM",
            "dist_strike": 0.0, "iq_calc": 50.0, "modelo_usado": "binomial",
            "preco_ativo": 20.0, "taxa_cdi": 0.1075, "div_yield": 0.0,
        }

    def test_acumula_sem_sobrescrever(self, db_temp):
        db_temp.salvar_greeks_historico([self._greek_exemplo()])
        db_temp.salvar_greeks_historico([self._greek_exemplo()])
        hist = db_temp.consultar_greeks_historico("BBAS3.SA", "T200", limite=None)
        assert len(hist) == 2

    def test_consulta_filtra_por_codigo(self, db_temp):
        db_temp.salvar_greeks_historico([self._greek_exemplo("T200")])
        db_temp.salvar_greeks_historico([self._greek_exemplo("T300")])
        hist = db_temp.consultar_greeks_historico("BBAS3.SA", "T200", limite=None)
        assert len(hist) == 1
        assert hist[0]["opcao_codigo"] == "T200"

    def test_retencao_remove_antigos_preserva_recentes(self, db_temp):
        hoje = date.today()
        antigo  = (hoje - timedelta(days=200)).isoformat() + " 14:00:00"
        recente = (hoje - timedelta(days=10)).isoformat()  + " 14:00:00"
        db_temp.salvar_greeks_historico([self._greek_exemplo(data_hora=antigo)])
        db_temp.salvar_greeks_historico([self._greek_exemplo(data_hora=recente)])

        removidos = db_temp.purgar_greeks_historico(dias_retencao=180)
        assert removidos == 1

        restantes = db_temp.consultar_greeks_historico("BBAS3.SA", "T200", limite=None)
        assert len(restantes) == 1
        assert restantes[0]["data_hora"] == recente


class TestPurgaVencimentos:
    def test_remove_vencidas_preserva_validas(self, db_temp):
        hoje_str = date.today().isoformat()
        venc_passado = (date.today() - timedelta(days=400)).isoformat()
        venc_futuro  = (date.today() + timedelta(days=400)).isoformat()

        db_temp.upsert_opcoes([{
            "ticker_ativo": "BBAS3.SA", "codigo": "VENCIDA", "tipo": "CALL",
            "modelo": None, "strike": 20.0, "vencimento": venc_passado,
            "ultimo": 0.5, "variacao_pct": 0.0, "data_hora": f"{hoje_str} 10:00:00",
            "num_negocios": 10, "vol_financeiro": 1000.0, "vol_implícita": 0.30,
            "iq": None, "coberto": None, "descoberto": None, "travado": None,
            "titulares": None, "lancadores": None, "fonte": "test",
        }, {
            "ticker_ativo": "BBAS3.SA", "codigo": "VALIDA", "tipo": "CALL",
            "modelo": None, "strike": 20.0, "vencimento": venc_futuro,
            "ultimo": 1.5, "variacao_pct": 0.0, "data_hora": f"{hoje_str} 10:00:00",
            "num_negocios": 100, "vol_financeiro": 10000.0, "vol_implícita": 0.30,
            "iq": None, "coberto": None, "descoberto": None, "travado": None,
            "titulares": None, "lancadores": None, "fonte": "test",
        }])

        removidos = db_temp.purgar_vencimentos_expirados(dias_graca=5)
        assert removidos["opcoes"] == 1

        restantes = db_temp.consultar_opcoes("BBAS3.SA")
        assert len(restantes) == 1
        assert restantes[0]["codigo"] == "VALIDA"


class TestResumoGeral:
    def test_chaves_obrigatorias_presentes(self, db_temp):
        resumo = db_temp.resumo_geral()
        for chave in ["cotacoes", "opcoes", "greeks", "greeks_historico",
                      "snapshots", "tickers"]:
            assert chave in resumo

    def test_contagens_corretas(self, db_temp):
        db_temp.upsert_cotacoes([
            ("BBAS3.SA", "2025-01-01", 20, 21, 19, 20.5, 20.5, 1000),
            ("PETR4.SA", "2025-01-01", 35, 36, 34, 35.5, 35.5, 2000),
        ])
        resumo = db_temp.resumo_geral()
        assert resumo["cotacoes"]["total"] == 2
        assert len(resumo["tickers"]) == 2

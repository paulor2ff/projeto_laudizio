"""
Testes para auth.py — módulo "esqueleto preparado" para opcoes.net.br.

O endpoint real ainda não está mapeado (ver README), então
coletar_opcoes_autenticado() e a parte de rede de _autenticar() não podem
ser exercitados de ponta-a-ponta. Mas autenticacao_configurada(), o guard
de obter_sessao(), encerrar_sessao(), _mapear_campos() e os parsers
_safe_float/_safe_int/_normalizar_tipo já estão 100% implementados e são
testáveis hoje — e é exatamente aí que vale a pena ter uma rede de
segurança antes do dia em que o endpoint for wireado.
"""

import pytest

import auth


class TestAutenticacaoConfigurada:
    def test_falso_quando_nada_configurado(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "AUTH_LOGIN_URL", "")
        monkeypatch.setattr(config, "AUTH_OPCOES_URL", "")
        monkeypatch.setattr(config, "AUTH_USERNAME", "")
        monkeypatch.setattr(config, "AUTH_PASSWORD", "")
        assert auth.autenticacao_configurada() is False

    @pytest.mark.parametrize("campo_vazio", [
        "AUTH_LOGIN_URL", "AUTH_OPCOES_URL", "AUTH_USERNAME", "AUTH_PASSWORD",
    ])
    def test_falso_quando_falta_um_campo(self, monkeypatch, campo_vazio):
        import config
        cheios = {
            "AUTH_LOGIN_URL": "https://x/login", "AUTH_OPCOES_URL": "https://x/opcoes",
            "AUTH_USERNAME": "user@x.com", "AUTH_PASSWORD": "senha",
        }
        for campo, valor in cheios.items():
            monkeypatch.setattr(config, campo, "" if campo == campo_vazio else valor)
        assert auth.autenticacao_configurada() is False

    def test_verdadeiro_quando_tudo_configurado(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "AUTH_LOGIN_URL", "https://x/login")
        monkeypatch.setattr(config, "AUTH_OPCOES_URL", "https://x/opcoes")
        monkeypatch.setattr(config, "AUTH_USERNAME", "user@x.com")
        monkeypatch.setattr(config, "AUTH_PASSWORD", "senha")
        assert auth.autenticacao_configurada() is True


class TestObterSessao:
    def test_levanta_notimplementederror_sem_configuracao(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "AUTH_LOGIN_URL", "")
        with pytest.raises(NotImplementedError):
            auth.obter_sessao()


class TestEncerrarSessao:
    def test_seguro_chamar_sem_sessao_ativa(self, monkeypatch):
        monkeypatch.setattr(auth, "_sessao", None)
        monkeypatch.setattr(auth, "_autenticado", False)
        auth.encerrar_sessao()  # não deve levantar exceção
        assert auth._sessao is None
        assert auth._autenticado is False

    def test_fecha_e_limpa_sessao_existente(self, monkeypatch):
        import requests
        sessao_fake = requests.Session()
        monkeypatch.setattr(auth, "_sessao", sessao_fake)
        monkeypatch.setattr(auth, "_autenticado", True)
        auth.encerrar_sessao()
        assert auth._sessao is None
        assert auth._autenticado is False


class TestColetarOpcoesAutenticado:
    def test_levanta_notimplementederror_endpoint_nao_mapeado(self, monkeypatch):
        """
        Estado atual e esperado: mesmo com autenticacao_configurada()==True,
        a URL de dados ainda não foi mapeada — a função deve continuar
        levantando NotImplementedError (é isso que faz collector.py cair
        para yfinance em vez de propagar o erro).
        """
        import config
        monkeypatch.setattr(config, "AUTH_LOGIN_URL", "https://x/login")
        monkeypatch.setattr(config, "AUTH_OPCOES_URL", "https://x/opcoes")
        monkeypatch.setattr(config, "AUTH_USERNAME", "user@x.com")
        monkeypatch.setattr(config, "AUTH_PASSWORD", "senha")
        # Evita que obter_sessao() tente autenticar de verdade pela rede:
        import requests
        monkeypatch.setattr(auth, "_sessao", requests.Session())
        monkeypatch.setattr(auth, "_autenticado", True)
        with pytest.raises(NotImplementedError):
            auth.coletar_opcoes_autenticado("BBAS3.SA")


class TestNormalizarTipo:
    @pytest.mark.parametrize("valor,esperado", [
        ("CALL", "CALL"), ("call", "CALL"), ("C", "CALL"), ("c", "CALL"),
        ("COMPRA", "CALL"), ("compra", "CALL"),
        ("PUT", "PUT"), ("put", "PUT"), ("P", "PUT"), ("p", "PUT"),
        ("VENDA", "PUT"), ("venda", "PUT"),
        ("  call  ", "CALL"),  # espaços nas pontas
    ])
    def test_variantes_conhecidas(self, valor, esperado):
        assert auth._normalizar_tipo(valor) == esperado

    def test_valor_desconhecido_cai_para_call(self):
        """
        Comportamento atual: qualquer valor não reconhecido vira 'CALL'
        por omissão (em vez de, por exemplo, levantar ou marcar como
        inválido). Hoje isto é código morto — _mapear_campos() só é
        chamado depois que o endpoint real for mapeado — mas fica
        registrado aqui para quando essa hora chegar (ver relatório).
        """
        assert auth._normalizar_tipo("XYZ") == "CALL"
        assert auth._normalizar_tipo("") == "CALL"


class TestSafeFloat:
    @pytest.mark.parametrize("valor,esperado", [
        (None,          None),
        ("225.000",     225000.0),
        ("1.234.567",   1234567.0),
        ("1.500,75",    1500.75),
        ("1,50",        1.5),
        ("R$ 20,50",    20.5),
        ("0.32",        0.32),
        ("  0.32  ",    0.32),
        ("",            None),
        ("abc",         None),
    ])
    def test_formatos_documentados(self, valor, esperado):
        resultado = auth._safe_float(valor)
        if esperado is None:
            assert resultado is None
        else:
            assert abs(resultado - esperado) < 1e-6


class TestSafeInt:
    @pytest.mark.parametrize("valor,esperado", [
        (None,      None),
        (1500,      1500),
        (1500.0,    1500),
        ("1.500",   1500),
        ("150",     150),
        ("150,00",  150),   # vírgula = decimal BR (consistente com _safe_float), não milhar
        ("abc",     None),
    ])
    def test_formatos_documentados(self, valor, esperado):
        assert auth._safe_int(valor) == esperado

    def test_virgula_isolada_e_decimal_nao_milhar(self):
        """
        Uma vírgula sozinha (sem ponto) é tratada como separador decimal BR
        neste código — igual a _safe_float — não como separador de milhar.
        '1,500' vira 1,5 (trunca para 1), não 1500. Documentado aqui porque
        é fácil de ler ao contrário se vier de fora acostumado ao padrão
        americano (onde vírgula = milhar).
        """
        assert auth._safe_int("1,500") == 1


class TestMapearCampos:
    def test_mapeamento_basico(self):
        brutos = [{
            "codigo": "BBAS3C200", "tipo": "CALL", "modelo": "Americano",
            "strike": "20,00", "vencimento": "2027-06-18", "ultimo": "1,50",
            "variacao": "3,2", "dataHora": "2026-06-17 14:00:00",
            "numNegocios": "150", "volFinanceiro": "225.000",
            "volImpl": "0,30",
        }]
        resultado = auth._mapear_campos(brutos, "BBAS3.SA")
        assert len(resultado) == 1
        reg = resultado[0]
        assert reg["ticker_ativo"] == "BBAS3.SA"
        assert reg["codigo"] == "BBAS3C200"
        assert reg["tipo"] == "CALL"
        assert reg["strike"] == 20.0
        assert reg["num_negocios"] == 150
        assert reg["vol_financeiro"] == 225000.0
        assert reg["fonte"] == "opcoes.net.br"

    def test_item_malformado_nao_aborta_lote(self):
        """Um item que levanta exceção ao mapear não deve derrubar os demais."""
        brutos = [
            {"codigo": "OK1", "tipo": "CALL", "strike": "20,00",
             "vencimento": "2027-06-18", "ultimo": "1,00"},
            None,  # item inválido — .get() vai falhar
            {"codigo": "OK2", "tipo": "PUT", "strike": "22,00",
             "vencimento": "2027-06-18", "ultimo": "0,50"},
        ]
        resultado = auth._mapear_campos(brutos, "BBAS3.SA")
        codigos = {r["codigo"] for r in resultado}
        assert codigos == {"OK1", "OK2"}

    def test_lista_vazia_retorna_lista_vazia(self):
        assert auth._mapear_campos([], "BBAS3.SA") == []

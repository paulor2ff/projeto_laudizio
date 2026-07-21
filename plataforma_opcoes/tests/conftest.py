"""
conftest.py — Configuração partilhada do pytest
==================================================
Mocka yfinance ANTES de qualquer módulo do projecto ser importado, pois
collector.py e auth.py fazem 'import yfinance' a nível de módulo, e este
pacote não está disponível em todos os ambientes de teste (incluindo o
sandbox onde esta suite foi originalmente escrita). Os testes validam a
LÓGICA do sistema — parsing, cálculos, banco de dados, concorrência — não
o comportamento real da rede, que só pode ser confirmado em produção.
"""

import sys
import os
from unittest.mock import MagicMock

# Mock global de yfinance — necessário antes de qualquer import do projecto
if "yfinance" not in sys.modules:
    sys.modules["yfinance"] = MagicMock()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tempfile
import pytest


@pytest.fixture
def db_temp(monkeypatch):
    """
    Fornece um banco SQLite temporário e isolado para cada teste.
    Garante que testes não interferem entre si nem com o banco real
    do utilizador (opcoes_b3.db). Limpa o ficheiro ao final do teste.
    """
    import config
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    caminho = tmp.name
    tmp.close()

    monkeypatch.setattr(config, "DB_PATH", caminho)

    import database
    import importlib
    importlib.reload(database)
    database.inicializar()

    yield database

    try:
        os.unlink(caminho)
    except FileNotFoundError:
        pass


@pytest.fixture
def alertas_temp(monkeypatch, tmp_path):
    """
    Fornece um ficheiro alertas.json isolado em diretório temporário
    para cada teste — evita interferência entre testes e com o ficheiro
    real do utilizador.
    """
    import alertas
    import importlib
    importlib.reload(alertas)

    arquivo_teste = tmp_path / "alertas_teste.json"
    lock_teste    = tmp_path / "alertas_teste.lock"
    monkeypatch.setattr(alertas, "ALERTAS_FILE", arquivo_teste)
    monkeypatch.setattr(alertas, "_LOCK_FILE", lock_teste)

    yield alertas

    for f in (arquivo_teste, lock_teste):
        try:
            f.unlink()
        except FileNotFoundError:
            pass


@pytest.fixture
def licenca_temp(monkeypatch, tmp_path):
    """
    Fornece um ficheiro licenca.json isolado em diretório temporário,
    mais um par de chaves Ed25519 gerado especificamente para o teste
    (não usa a chave de dev_tools/, para isolar completamente). Devolve
    um objecto com .modulo (o módulo licenca recarregado) e .emitir()
    (função de conveniência para gerar tokens assinados de teste).
    """
    import importlib
    import json
    import base64
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization
    from datetime import datetime, timedelta

    chave_privada = Ed25519PrivateKey.generate()
    pub_bytes = chave_privada.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    chave_publica_b64 = base64.b64encode(pub_bytes).decode("ascii")

    import config
    monkeypatch.setattr(config, "LICENCA_CHAVE_PUBLICA", chave_publica_b64)
    monkeypatch.setattr(config, "LICENCA_URL_RENOVACAO", "")

    import licenca
    importlib.reload(licenca)

    arquivo_teste = tmp_path / "licenca_teste.json"
    lock_teste    = tmp_path / "licenca_teste.lock"
    monkeypatch.setattr(licenca, "LICENCA_FILE", arquivo_teste)
    monkeypatch.setattr(licenca, "_LOCK_FILE", lock_teste)
    monkeypatch.setattr(licenca, "LICENCA_CHAVE_PUBLICA", chave_publica_b64)
    licenca.invalidar_cache_licenca()

    def _mensagem_canonica(payload):
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")

    def _emitir(cliente_id="cli_teste", plano="manutencao",
                dias_validade=30, vencido_ha=None, corromper=False):
        agora = datetime.now()
        valido_ate = (agora - timedelta(days=vencido_ha)) if vencido_ha is not None \
                     else (agora + timedelta(days=dias_validade))
        payload = {
            "cliente_id": cliente_id, "plano": plano,
            "emitido_em": agora.isoformat(timespec="seconds"),
            "valido_ate": valido_ate.isoformat(timespec="seconds"),
        }
        assinatura = chave_privada.sign(_mensagem_canonica(payload))
        assinatura_b64 = base64.b64encode(assinatura).decode("ascii")
        if corromper:
            ass_bytes = bytearray(base64.b64decode(assinatura_b64))
            ass_bytes[0] ^= 0xFF
            assinatura_b64 = base64.b64encode(bytes(ass_bytes)).decode("ascii")
        return {"payload": payload, "assinatura": assinatura_b64}

    class _Fixture:
        modulo = licenca
        emitir = staticmethod(_emitir)

    yield _Fixture()

    licenca.invalidar_cache_licenca()
    for f in (arquivo_teste, lock_teste):
        try:
            f.unlink()
        except FileNotFoundError:
            pass


@pytest.fixture
def yf_ticker(monkeypatch):
    """
    Substitui collector.yf inteiro por um MagicMock fresco e devolve a
    instância que collector.py vai receber de 'obj = yf.Ticker(ticker)'.
    Cada teste configura diretamente o que precisar, por ex.:

        yf_ticker.history.return_value = df_exemplo
        yf_ticker.fast_info.last_price = 20.5
        yf_ticker.options = ("2027-01-16",)

    Isolado por teste (monkeypatch reverte sozinho) — não usa o mock
    global de sys.modules['yfinance'] partilhado por toda a sessão,
    evitando vazamento de configuração entre testes.
    """
    import collector
    instancia = MagicMock()
    yf_mock = MagicMock()
    yf_mock.Ticker = MagicMock(return_value=instancia)
    monkeypatch.setattr(collector, "yf", yf_mock)
    return instancia


@pytest.fixture
def api_client(monkeypatch, db_temp, tmp_path):
    """
    Fornece um TestClient da API FastAPI (api.py) já ligado ao banco
    temporário isolado (db_temp) e com:
      - o scheduler neutralizado (iniciar/parar viram no-op — evita subir
        threads reais de agendamento durante os testes);
      - um ficheiro de token isolado, com valor conhecido, em vez do
        api_token.txt real do repositório.

    Devolve uma tupla (client, token). O 'token' já pode ser usado no
    cabeçalho: {"Authorization": f"Bearer {token}"}.
    """
    import scheduler
    monkeypatch.setattr(scheduler, "iniciar", lambda: None)
    monkeypatch.setattr(scheduler, "parar",   lambda: None)

    import config
    monkeypatch.setattr(config, "API_TOKEN", "")

    token = "token-de-teste-fixo"
    token_path = tmp_path / "api_token_teste.txt"
    token_path.write_text(token)

    import api
    import importlib
    importlib.reload(api)
    monkeypatch.setattr(api, "_TOKEN_FILE", str(token_path))

    from fastapi.testclient import TestClient
    with TestClient(api.app) as client:
        yield client, token


@pytest.fixture
def cli_env(monkeypatch, db_temp, tmp_path):
    """
    Ambiente isolado para testar cli.py de ponta-a-ponta via main().

    cli.py importa DB_PATH/LOG_PATH/TICKER_PADRAO/TICKERS de config.py
    com 'from config import ...' — são valores simples (str/Path), não
    lookups dinâmicos, então precisam que cli.py seja recarregado depois
    que db_temp troca config.DB_PATH, ou o módulo continuaria a apontar
    para o LOG_PATH real do projecto. Também muda o cwd para tmp_path,
    já que --exportar/--exportar-opcoes escrevem CSV no diretório actual.
    """
    import config
    monkeypatch.setattr(config, "LOG_PATH", tmp_path / "plataforma_teste.log")

    import cli
    import importlib
    importlib.reload(cli)

    monkeypatch.chdir(tmp_path)
    return cli


@pytest.fixture
def no_network(monkeypatch):
    """
    Garante que chamadas de rede (CDI via BCB, dividend yield via
    yfinance) falham de forma controlada, forçando o uso dos valores
    de fallback já testados — em vez de tentar uma rede que pode não
    existir no ambiente de teste.
    """
    import greeks
    monkeypatch.setattr(
        greeks.requests, "get",
        MagicMock(side_effect=Exception("rede desabilitada no teste"))
    )
    greeks.invalidar_cache_cdi()
    yield

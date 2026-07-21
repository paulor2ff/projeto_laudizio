"""
conftest.py — Fixtures partilhadas para a suite do servidor de licenças.

Design deliberado: NENHUMA fixture usa importlib.reload(). Em vez disso,
monkeypatch.setattr() é aplicado directamente nos módulos já importados
(ex: monkeypatch.setattr(database, "DB_PATH", ...)). Isto evita um
problema real de reload em cascata: emissor.py faz
'from chaves import obter_chave_privada' — uma referência directa à
função. Se chaves.py fosse recarregado via importlib.reload(), essa nova
função substituiria chaves.obter_chave_privada, mas a referência já
capturada dentro de emissor.py continuaria a apontar para o objecto
função ANTIGO, com o lru_cache antigo — dessincronizando os dois módulos.
Como Python resolve nomes globais via o __dict__ do próprio módulo em
tempo de chamada, fazer setattr directamente no módulo (sem nunca criar
um novo objecto de módulo) mantém todas as referências consistentes.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


@pytest.fixture
def db_temp(monkeypatch, tmp_path):
    """Banco SQLite isolado e temporário para cada teste."""
    import database
    caminho = tmp_path / "teste.db"
    monkeypatch.setattr(database, "DB_PATH", caminho)
    database.inicializar()
    yield database


@pytest.fixture
def chave_temp(monkeypatch, tmp_path):
    """Par de chaves Ed25519 isolado por teste — nunca usa a chave real do servidor."""
    import chaves
    chaves.obter_chave_privada.cache_clear()

    caminho_pem = tmp_path / "chave_teste.pem"
    pub_b64 = chaves.gerar_novo_par(caminho_pem)  # já grava o .pem e limpa o cache

    monkeypatch.setattr(chaves, "CHAVE_PRIVADA_PEM_PATH", caminho_pem)
    monkeypatch.setattr(chaves, "CHAVE_PRIVADA_PEM_ENV", "")
    chaves.obter_chave_privada.cache_clear()

    yield {"modulo": chaves, "chave_publica_b64": pub_b64, "caminho_pem": caminho_pem}

    chaves.obter_chave_privada.cache_clear()


@pytest.fixture
def app_cliente(monkeypatch, db_temp, chave_temp, tmp_path):
    """
    FastAPI TestClient com banco e chave isolados — testa os endpoints de
    ponta a ponta sem precisar de um servidor real a correr.
    """
    from fastapi.testclient import TestClient

    import admin
    admin_token_file = tmp_path / "admin_token_teste.txt"
    monkeypatch.setattr(admin, "_TOKEN_FILE", admin_token_file)
    monkeypatch.setattr(admin, "ADMIN_TOKEN", "token-admin-teste")

    import main

    with TestClient(main.app) as client:
        yield client


@pytest.fixture
def headers_admin():
    return {"Authorization": "Bearer token-admin-teste"}

"""Testes para emissor.py — emissão de tokens e compatibilidade com o cliente real."""

import json
import os
from datetime import datetime, timedelta

import pytest


class TestEmitirToken:
    def test_token_tem_campos_esperados(self, chave_temp):
        import emissor
        valido_ate = (datetime.now() + timedelta(days=30)).isoformat(timespec="seconds")
        token = emissor.emitir_token("cli_001", "manutencao", valido_ate)
        assert token["payload"]["cliente_id"] == "cli_001"
        assert token["payload"]["plano"] == "manutencao"
        assert token["payload"]["valido_ate"] == valido_ate
        assert "assinatura" in token

    def test_plano_invalido_levanta_valueerror(self, chave_temp):
        import emissor
        valido_ate = (datetime.now() + timedelta(days=30)).isoformat(timespec="seconds")
        with pytest.raises(ValueError):
            emissor.emitir_token("cli_001", "plano_inexistente", valido_ate)

    def test_emitir_token_para_cliente_existente(self, chave_temp, db_temp):
        valido_ate = (datetime.now() + timedelta(days=30)).isoformat(timespec="seconds")
        db_temp.upsert_cliente("cli_001", "a@b.com", None, "manutencao", valido_ate)
        import emissor
        token = emissor.emitir_token_para_cliente("cli_001")
        assert token["payload"]["cliente_id"] == "cli_001"
        assert token["payload"]["valido_ate"] == valido_ate

    def test_cliente_inexistente_levanta_erro_especifico(self, chave_temp, db_temp):
        import emissor
        with pytest.raises(emissor.ClienteNaoEncontradoError):
            emissor.emitir_token_para_cliente("nao_existe")

    def test_cliente_nao_activo_levanta_erro_especifico(self, chave_temp, db_temp):
        valido_ate = (datetime.now() + timedelta(days=30)).isoformat(timespec="seconds")
        db_temp.upsert_cliente("cli_001", None, None, "manutencao", valido_ate, status="cancelado")
        import emissor
        with pytest.raises(emissor.ClienteNaoActivoError):
            emissor.emitir_token_para_cliente("cli_001")


class TestCompatibilidadeComClienteReal:
    """
    O teste mais importante desta suite: confirma que um token assinado
    pelo SERVIDOR é de facto aceite pelo módulo CLIENTE real
    (plataforma_opcoes/licenca.py) — não uma réplica da lógica de
    verificação, mas o código exacto que vai correr na máquina do cliente.
    Qualquer divergência de serialização entre os dois lados seria
    invisível nos testes unitários de cada lado isoladamente, mas
    quebraria tudo em produção. Este teste fecha esse risco.

    Carregamos licenca.py e config.py do cliente directamente por caminho
    de ficheiro (importlib.util.spec_from_file_location) em vez de via
    sys.path — evita o conflito de nomes entre servidor_licencas/config.py
    e plataforma_opcoes/config.py quando ambos correm no mesmo processo.
    """

    def _carregar_modulo_cliente(self, nome, caminho):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            f"_cliente_{nome}", caminho
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def _preparar_licenca_cliente(self, tmp_path, chave_publica_b64, monkeypatch):
        """
        Carrega licenca.py do cliente isoladamente, trocando temporariamente
        sys.modules["config"] pelo config do cliente durante a carga.
        Isto é necessário porque ambos os projectos têm um módulo chamado
        "config" — e quando licenca.py faz 'from config import ...', Python
        resolve pelo sys.modules, que normalmente aponta para o config do servidor.
        """
        import importlib.util
        import sys as _sys

        raiz_cliente = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "plataforma_opcoes",
        )

        # Carregar o config.py do CLIENTE com um nome único para não colidir
        spec_cfg = importlib.util.spec_from_file_location(
            "_cliente_config_isolado",
            os.path.join(raiz_cliente, "config.py"),
        )
        config_cliente = importlib.util.module_from_spec(spec_cfg)
        spec_cfg.loader.exec_module(config_cliente)

        # Sobrepor constantes de licença com os valores de teste
        config_cliente.LICENCA_CHAVE_PUBLICA  = chave_publica_b64
        config_cliente.LICENCA_DIAS_CARENCIA  = 7
        config_cliente.LICENCA_DIAS_DEGRADADO = 23
        config_cliente.LICENCA_URL_RENOVACAO  = ""
        config_cliente.BASE_DIR               = tmp_path

        # Trocar sys.modules["config"] durante a carga do licenca.py do cliente
        config_servidor_original = _sys.modules.get("config")
        _sys.modules["config"] = config_cliente
        try:
            spec_lic = importlib.util.spec_from_file_location(
                "_cliente_licenca_isolada",
                os.path.join(raiz_cliente, "licenca.py"),
            )
            licenca_mod = importlib.util.module_from_spec(spec_lic)
            spec_lic.loader.exec_module(licenca_mod)
        finally:
            # Restaurar sempre — mesmo em caso de erro durante a carga
            if config_servidor_original is not None:
                _sys.modules["config"] = config_servidor_original
            elif "config" in _sys.modules:
                del _sys.modules["config"]

        # Sobrescrever os atributos que licenca.py leu do config durante a carga
        licenca_mod.LICENCA_CHAVE_PUBLICA = chave_publica_b64
        licenca_mod.LICENCA_FILE          = tmp_path / "licenca.json"
        licenca_mod._LOCK_FILE            = tmp_path / "licenca.lock"
        licenca_mod.invalidar_cache_licenca()
        return licenca_mod

    def test_token_do_servidor_e_aceite_pelo_cliente_real(
        self, chave_temp, monkeypatch, tmp_path
    ):
        import emissor
        valido_ate = (datetime.now() + timedelta(days=30)).isoformat(timespec="seconds")
        token = emissor.emitir_token("cli_real_001", "manutencao", valido_ate)

        licenca_cliente = self._preparar_licenca_cliente(
            tmp_path, chave_temp["chave_publica_b64"], monkeypatch
        )

        arq_token = tmp_path / "token_recebido_do_servidor.json"
        arq_token.write_text(json.dumps(token))

        estado = licenca_cliente.importar_licenca(str(arq_token))
        assert estado.estagio == "ok"
        assert estado.cliente_id == "cli_real_001"
        assert estado.plano == "manutencao"

    def test_token_com_chave_publica_errada_e_rejeitado_pelo_cliente(
        self, chave_temp, monkeypatch, tmp_path
    ):
        """Confirma que o cliente rejeita tokens de um servidor diferente do esperado."""
        import emissor
        valido_ate = (datetime.now() + timedelta(days=30)).isoformat(timespec="seconds")
        token = emissor.emitir_token("cli_001", "manutencao", valido_ate)

        # Chave pública DIFERENTE — simula cliente configurado para outro servidor
        chave_errada = "4SpGfgcgSUNNfBVAXjPNk0O9iWgWDpv8hFlw8wzb3+A="
        licenca_cliente = self._preparar_licenca_cliente(
            tmp_path, chave_errada, monkeypatch
        )

        arq_token = tmp_path / "token_chave_errada.json"
        arq_token.write_text(json.dumps(token))

        with pytest.raises(ValueError, match="inválida"):
            licenca_cliente.importar_licenca(str(arq_token))

"""Testes para chaves.py — geração, carregamento e assinatura Ed25519."""

import pytest


class TestChaves:
    def test_chave_publica_e_string_base64_valida(self, chave_temp):
        import base64
        pub = chave_temp["chave_publica_b64"]
        decodificado = base64.b64decode(pub)
        assert len(decodificado) == 32  # Ed25519 raw public key = 32 bytes

    def test_obter_chave_privada_e_cacheada(self, chave_temp):
        chaves = chave_temp["modulo"]
        c1 = chaves.obter_chave_privada()
        c2 = chaves.obter_chave_privada()
        assert c1 is c2  # mesmo objecto — veio do cache

    def test_chave_publica_corresponde_a_privada(self, chave_temp):
        chaves = chave_temp["modulo"]
        chaves.obter_chave_privada()  # garante a chave carregada/cacheada
        pub_recalculada = chaves.chave_publica_base64()
        assert pub_recalculada == chave_temp["chave_publica_b64"]

    def test_assinatura_e_verificavel_com_a_chave_publica(self, chave_temp):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        import base64
        chaves = chave_temp["modulo"]
        chave_privada = chaves.obter_chave_privada()
        mensagem = b"teste de assinatura"
        assinatura = chave_privada.sign(mensagem)

        pub_bytes = base64.b64decode(chave_temp["chave_publica_b64"])
        chave_publica = Ed25519PublicKey.from_public_bytes(pub_bytes)
        # Não levanta excepção = assinatura válida
        chave_publica.verify(assinatura, mensagem)

    def test_erro_se_nenhuma_chave_configurada(self, monkeypatch, tmp_path):
        import chaves
        chaves.obter_chave_privada.cache_clear()
        monkeypatch.setattr(chaves, "CHAVE_PRIVADA_PEM_PATH", tmp_path / "nao_existe.pem")
        monkeypatch.setattr(chaves, "CHAVE_PRIVADA_PEM_ENV", "")
        with pytest.raises(RuntimeError, match="Nenhuma chave"):
            chaves.obter_chave_privada()
        chaves.obter_chave_privada.cache_clear()

    def test_carregar_via_variavel_de_ambiente(self, monkeypatch, tmp_path):
        import chaves
        chaves.obter_chave_privada.cache_clear()

        # Gerar uma chave e extrair o PEM para simular a variável de ambiente
        caminho_temp = tmp_path / "para_env.pem"
        chaves.gerar_novo_par(caminho_temp)
        pem_conteudo = caminho_temp.read_text()
        chaves.obter_chave_privada.cache_clear()

        monkeypatch.setattr(chaves, "CHAVE_PRIVADA_PEM_PATH", tmp_path / "nao_existe.pem")
        monkeypatch.setattr(chaves, "CHAVE_PRIVADA_PEM_ENV", pem_conteudo)

        chave = chaves.obter_chave_privada()
        assert chave is not None
        chaves.obter_chave_privada.cache_clear()

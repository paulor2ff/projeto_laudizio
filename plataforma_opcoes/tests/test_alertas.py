"""Testes para alertas.py — CRUD, cooldown, notificações, locking, concorrência."""

import threading
import time
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

import pytest


class TestCRUD:
    def test_adicionar_alerta_basico(self, alertas_temp):
        a = alertas_temp.adicionar_alerta("BBAS3.SA", "delta", ">", 0.5, codigo="T200")
        assert a["id"] == 1
        assert a["ticker"] == "BBAS3.SA"
        assert a["ultimo_disparo"] is None

    def test_ticker_sem_sufixo_normalizado(self, alertas_temp):
        a = alertas_temp.adicionar_alerta("BBAS3", "preco", ">", 20.0)
        assert a["ticker"] == "BBAS3.SA"

    def test_ids_incrementais(self, alertas_temp):
        a1 = alertas_temp.adicionar_alerta("BBAS3.SA", "preco", ">", 20.0)
        a2 = alertas_temp.adicionar_alerta("PETR4.SA", "preco", ">", 30.0)
        assert a2["id"] == a1["id"] + 1

    @pytest.mark.parametrize("tipo", ["delta", "preco", "variacao", "iq", "vol_impl"])
    def test_tipos_validos_aceitos(self, alertas_temp, tipo):
        a = alertas_temp.adicionar_alerta("BBAS3.SA", tipo, ">", 1.0)
        assert a["tipo"] == tipo

    def test_tipo_invalido_levanta_valueerror(self, alertas_temp):
        with pytest.raises(ValueError):
            alertas_temp.adicionar_alerta("BBAS3.SA", "invalido", ">", 1.0)

    def test_operador_invalido_levanta_valueerror(self, alertas_temp):
        with pytest.raises(ValueError):
            alertas_temp.adicionar_alerta("BBAS3.SA", "delta", "!=", 1.0)

    def test_cooldown_negativo_levanta_valueerror(self, alertas_temp):
        with pytest.raises(ValueError):
            alertas_temp.adicionar_alerta("BBAS3.SA", "delta", ">", 0.5, cooldown_min=-1)

    def test_listar_filtra_por_ticker(self, alertas_temp):
        alertas_temp.adicionar_alerta("BBAS3.SA", "preco", ">", 20.0)
        alertas_temp.adicionar_alerta("PETR4.SA", "preco", ">", 30.0)
        lst = alertas_temp.listar_alertas("BBAS3.SA")
        assert len(lst) == 1
        assert lst[0]["ticker"] == "BBAS3.SA"

    def test_listar_sem_filtro_devolve_todos(self, alertas_temp):
        alertas_temp.adicionar_alerta("BBAS3.SA", "preco", ">", 20.0)
        alertas_temp.adicionar_alerta("PETR4.SA", "preco", ">", 30.0)
        assert len(alertas_temp.listar_alertas()) == 2

    def test_remover_alerta_existente(self, alertas_temp):
        a = alertas_temp.adicionar_alerta("BBAS3.SA", "preco", ">", 20.0)
        assert alertas_temp.remover_alerta(a["id"]) is True
        assert alertas_temp.listar_alertas() == []

    def test_remover_alerta_inexistente_devolve_false(self, alertas_temp):
        assert alertas_temp.remover_alerta(999) is False

    def test_desactivar_alerta(self, alertas_temp):
        a = alertas_temp.adicionar_alerta("BBAS3.SA", "preco", ">", 20.0)
        assert alertas_temp.desactivar_alerta(a["id"]) is True
        estado = alertas_temp.listar_alertas()[0]
        assert estado["activo"] is False


class TestCooldown:
    def _preparar_contrato(self, db_temp, no_network):
        with patch("greeks.requests.get") as mg, \
             patch("greeks.obter_dividend_yield", return_value=0.0):
            mg.side_effect = Exception("sem rede")
            from greeks import calcular_contrato
            res = calcular_contrato(
                "BBAS3.SA", "T200", "CALL", 20.0, "2027-06-18",
                21.0, 1.8, 225_000, 150, 0.8, "binomial"
            )
        db_temp.upsert_opcoes([{
            "ticker_ativo": "BBAS3.SA", "codigo": "T200", "tipo": "CALL",
            "modelo": None, "strike": 20.0, "vencimento": "2027-06-18",
            "ultimo": 1.8, "variacao_pct": 1.0, "data_hora": "2026-06-17 14:00:00",
            "num_negocios": 150, "vol_financeiro": 225_000.0,
            "vol_implícita": 0.30, "iq": None, "coberto": None,
            "descoberto": None, "travado": None, "titulares": None,
            "lancadores": None, "fonte": "test",
        }])
        db_temp.upsert_greeks([res])
        return res

    def test_dispara_na_primeira_verificacao(self, alertas_temp, db_temp, no_network):
        self._preparar_contrato(db_temp, no_network)
        alertas_temp.adicionar_alerta("BBAS3.SA", "delta", ">", 0.5, codigo="T200")
        with patch("collector.cotacao_atual", return_value={"preco": 21.0, "variacao_pct": 1.0}), \
             patch.object(alertas_temp, "_enviar_email", return_value=False), \
             patch.object(alertas_temp, "_enviar_webhook", return_value=False):
            disparados = alertas_temp.verificar_alertas("BBAS3.SA")
        assert len(disparados) == 1

    def test_suprime_segunda_verificacao_em_cooldown(self, alertas_temp, db_temp, no_network):
        self._preparar_contrato(db_temp, no_network)
        alertas_temp.adicionar_alerta("BBAS3.SA", "delta", ">", 0.5, codigo="T200", cooldown_min=10.0)
        with patch("collector.cotacao_atual", return_value={"preco": 21.0, "variacao_pct": 1.0}), \
             patch.object(alertas_temp, "_enviar_email", return_value=False), \
             patch.object(alertas_temp, "_enviar_webhook", return_value=False):
            alertas_temp.verificar_alertas("BBAS3.SA")
            d2 = alertas_temp.verificar_alertas("BBAS3.SA")
        assert len(d2) == 0

    def test_dispara_de_novo_apos_cooldown_expirar(self, alertas_temp, db_temp, no_network):
        self._preparar_contrato(db_temp, no_network)
        alertas_temp.adicionar_alerta("BBAS3.SA", "delta", ">", 0.5, codigo="T200", cooldown_min=10.0)
        with patch("collector.cotacao_atual", return_value={"preco": 21.0, "variacao_pct": 1.0}), \
             patch.object(alertas_temp, "_enviar_email", return_value=False), \
             patch.object(alertas_temp, "_enviar_webhook", return_value=False):
            alertas_temp.verificar_alertas("BBAS3.SA")

            # Simular que o cooldown já passou
            estado = alertas_temp._carregar()
            estado[0]["ultimo_disparo"] = (datetime.now() - timedelta(minutes=15)).isoformat()
            alertas_temp._guardar(estado)

            d_depois = alertas_temp.verificar_alertas("BBAS3.SA")
        assert len(d_depois) == 1

    def test_merge_por_id_preserva_outros_alertas(self, alertas_temp, db_temp, no_network):
        self._preparar_contrato(db_temp, no_network)
        alertas_temp.adicionar_alerta("BBAS3.SA", "delta", ">", 0.5, codigo="T200")
        a2 = alertas_temp.adicionar_alerta("BBAS3.SA", "preco", ">", 100.0)  # nunca dispara
        with patch("collector.cotacao_atual", return_value={"preco": 21.0, "variacao_pct": 1.0}), \
             patch.object(alertas_temp, "_enviar_email", return_value=False), \
             patch.object(alertas_temp, "_enviar_webhook", return_value=False):
            alertas_temp.verificar_alertas("BBAS3.SA")
        estado_a2 = [a for a in alertas_temp.listar_alertas() if a["id"] == a2["id"]][0]
        assert estado_a2["valor"] == 100.0
        assert len(alertas_temp.listar_alertas()) == 2


class TestNotificacoes:
    def test_email_nao_enviado_quando_desactivado(self, alertas_temp):
        import config
        config.NOTIF_EMAIL_ATIVO = False
        alerta = {"id": 1, "ticker": "BBAS3.SA", "tipo": "delta", "codigo": "T200",
                  "operador": ">", "valor": 0.5, "valor_actual": 0.7}
        assert alertas_temp._enviar_email(alerta) is False

    def test_webhook_nao_enviado_sem_url(self, alertas_temp):
        import config
        config.NOTIF_WEBHOOK_URL = ""
        alerta = {"id": 1, "ticker": "BBAS3.SA", "tipo": "delta", "codigo": "T200",
                  "operador": ">", "valor": 0.5, "valor_actual": 0.7}
        assert alertas_temp._enviar_webhook(alerta) is False

    def test_email_enviado_com_smtp_mockado(self, alertas_temp):
        import config
        config.NOTIF_EMAIL_ATIVO     = True
        config.NOTIF_EMAIL_SMTP_HOST = "smtp.gmail.com"
        config.NOTIF_EMAIL_SMTP_PORT = 587
        config.NOTIF_EMAIL_USER      = "remetente@gmail.com"
        config.NOTIF_EMAIL_PASS      = "senha"
        config.NOTIF_EMAIL_PARA      = "destino@gmail.com"
        alerta = {"id": 1, "ticker": "BBAS3.SA", "tipo": "delta", "codigo": "T200",
                  "operador": ">", "valor": 0.5, "valor_actual": 0.7}
        with patch("smtplib.SMTP"):
            resultado = alertas_temp._enviar_email(alerta)
        assert resultado is True
        config.NOTIF_EMAIL_ATIVO = False

    def test_webhook_discord_payload_correto(self, alertas_temp):
        import config
        config.NOTIF_WEBHOOK_URL = "https://discord.com/api/webhooks/exemplo"
        config.NOTIF_WEBHOOK_FORMATO = "discord"
        alerta = {"id": 1, "ticker": "BBAS3.SA", "tipo": "delta", "codigo": "T200",
                  "operador": ">", "valor": 0.5, "valor_actual": 0.7}
        with patch("requests.post") as mock_post:
            mock_post.return_value.raise_for_status = MagicMock()
            alertas_temp._enviar_webhook(alerta)
        assert "content" in mock_post.call_args[1]["json"]
        config.NOTIF_WEBHOOK_URL = ""

    def test_falha_de_rede_nao_propaga_excecao(self, alertas_temp):
        import config
        config.NOTIF_WEBHOOK_URL = "https://exemplo.com/webhook"
        alerta = {"id": 1, "ticker": "BBAS3.SA", "tipo": "delta", "codigo": "T200",
                  "operador": ">", "valor": 0.5, "valor_actual": 0.7}
        with patch("requests.post", side_effect=Exception("timeout")):
            resultado = alertas_temp._enviar_webhook(alerta)
        assert resultado is False
        config.NOTIF_WEBHOOK_URL = ""


class TestFileLocking:
    def test_lock_removido_apos_operacao(self, alertas_temp):
        alertas_temp.adicionar_alerta("BBAS3.SA", "preco", ">", 20.0)
        assert not alertas_temp._LOCK_FILE.exists()

    def test_concorrencia_20_threads_sem_perda(self, alertas_temp):
        erros = []
        trava = threading.Lock()

        def worker(i):
            try:
                alertas_temp.adicionar_alerta("BBAS3.SA", "preco", ">", float(i), codigo=f"C{i}")
            except Exception as exc:
                with trava:
                    erros.append(str(exc))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
        for t in threads: t.start()
        for t in threads: t.join()

        assert erros == []
        todos = alertas_temp.listar_alertas("BBAS3.SA")
        assert len(todos) == 20
        ids = {a["id"] for a in todos}
        assert len(ids) == 20  # nenhuma colisão de ID

    def test_lock_preso_e_recuperado(self, alertas_temp):
        import os
        alertas_temp._LOCK_TIMEOUT_SEG = 0.3
        fd = os.open(str(alertas_temp._LOCK_FILE), os.O_CREAT | os.O_EXCL | os.O_RDWR)
        os.close(fd)

        t0 = time.monotonic()
        a = alertas_temp.adicionar_alerta("BBAS3.SA", "preco", ">", 20.0)
        decorrido = time.monotonic() - t0

        assert a["id"] is not None
        assert decorrido >= 0.25
        assert not alertas_temp._LOCK_FILE.exists()
        alertas_temp._LOCK_TIMEOUT_SEG = 5.0

    def test_escrita_atomica_sem_tmp_residual(self, alertas_temp):
        alertas_temp.adicionar_alerta("BBAS3.SA", "preco", ">", 20.0)
        tmp_path = alertas_temp.ALERTAS_FILE.with_suffix(".json.tmp")
        assert not tmp_path.exists()

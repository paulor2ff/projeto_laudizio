"""Testes para database.py — clientes e auditoria de eventos."""

from datetime import datetime, timedelta


class TestClientes:
    def test_criar_e_obter_cliente(self, db_temp):
        valido_ate = (datetime.now() + timedelta(days=30)).isoformat(timespec="seconds")
        db_temp.upsert_cliente("cli_001", "a@b.com", "Fulano", "manutencao", valido_ate)
        cliente = db_temp.obter_cliente("cli_001")
        assert cliente is not None
        assert cliente["email"] == "a@b.com"
        assert cliente["plano"] == "manutencao"
        assert cliente["status"] == "activo"

    def test_upsert_actualiza_em_vez_de_duplicar(self, db_temp):
        valido_ate1 = (datetime.now() + timedelta(days=30)).isoformat(timespec="seconds")
        valido_ate2 = (datetime.now() + timedelta(days=60)).isoformat(timespec="seconds")
        db_temp.upsert_cliente("cli_001", "a@b.com", "Fulano", "manutencao", valido_ate1)
        db_temp.upsert_cliente("cli_001", None, None, "manutencao", valido_ate2)

        cliente = db_temp.obter_cliente("cli_001")
        assert cliente["valido_ate"] == valido_ate2
        assert cliente["email"] == "a@b.com"  # preservado via COALESCE

        todos = db_temp.listar_clientes()
        assert len(todos) == 1  # não duplicou

    def test_obter_cliente_inexistente_devolve_none(self, db_temp):
        assert db_temp.obter_cliente("nao_existe") is None

    def test_buscar_por_stripe_subscription(self, db_temp):
        valido_ate = (datetime.now() + timedelta(days=30)).isoformat(timespec="seconds")
        db_temp.upsert_cliente(
            "cli_001", "a@b.com", None, "manutencao", valido_ate,
            stripe_subscription_id="sub_xyz",
        )
        cliente = db_temp.obter_cliente_por_stripe_subscription("sub_xyz")
        assert cliente is not None
        assert cliente["id"] == "cli_001"

    def test_buscar_por_mp_subscription(self, db_temp):
        valido_ate = (datetime.now() + timedelta(days=30)).isoformat(timespec="seconds")
        db_temp.upsert_cliente(
            "cli_002", "c@d.com", None, "manutencao", valido_ate,
            mp_subscription_id="mp_xyz",
        )
        cliente = db_temp.obter_cliente_por_mp_subscription("mp_xyz")
        assert cliente is not None
        assert cliente["id"] == "cli_002"

    def test_listar_filtra_por_status(self, db_temp):
        valido_ate = (datetime.now() + timedelta(days=30)).isoformat(timespec="seconds")
        db_temp.upsert_cliente("cli_a", None, None, "manutencao", valido_ate, status="activo")
        db_temp.upsert_cliente("cli_b", None, None, "manutencao", valido_ate, status="cancelado")

        activos = db_temp.listar_clientes(status="activo")
        assert len(activos) == 1
        assert activos[0]["id"] == "cli_a"

    def test_actualizar_status(self, db_temp):
        valido_ate = (datetime.now() + timedelta(days=30)).isoformat(timespec="seconds")
        db_temp.upsert_cliente("cli_001", None, None, "manutencao", valido_ate)
        assert db_temp.actualizar_status("cli_001", "cancelado") is True
        cliente = db_temp.obter_cliente("cli_001")
        assert cliente["status"] == "cancelado"

    def test_actualizar_status_inexistente_devolve_false(self, db_temp):
        assert db_temp.actualizar_status("nao_existe", "cancelado") is False


class TestEventos:
    def test_registar_e_listar_eventos(self, db_temp):
        db_temp.registar_evento("cli_001", "checkout.session.completed", "stripe",
                                {"id": "evt_1"})
        db_temp.registar_evento("cli_001", "invoice.paid", "stripe", {"id": "evt_2"})

        eventos = db_temp.listar_eventos("cli_001")
        assert len(eventos) == 2

    def test_listar_eventos_sem_filtro_de_cliente(self, db_temp):
        db_temp.registar_evento("cli_a", "tipo1", "stripe", {})
        db_temp.registar_evento("cli_b", "tipo2", "mercadopago", {})
        eventos = db_temp.listar_eventos()
        assert len(eventos) == 2

    def test_payload_bruto_preservado_como_json(self, db_temp):
        db_temp.registar_evento("cli_001", "tipo1", "stripe", {"chave": "valor", "n": 42})
        eventos = db_temp.listar_eventos("cli_001")
        import json
        payload = json.loads(eventos[0]["payload_bruto"])
        assert payload == {"chave": "valor", "n": 42}

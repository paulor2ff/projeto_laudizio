"""Testes para main.py — endpoints FastAPI do servidor de licenças."""

import hashlib
import hmac
import json
import os
import time
from datetime import datetime, timedelta


# ══════════════════════════════════════════════════════════════════════════════
# Saúde e chave pública
# ══════════════════════════════════════════════════════════════════════════════

class TestEndpointsPublicos:
    def test_saude_retorna_ok(self, app_cliente):
        r = app_cliente.get("/saude")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_chave_publica_acessivel_sem_token(self, app_cliente, chave_temp):
        r = app_cliente.get("/chave-publica")
        assert r.status_code == 200
        assert r.json()["chave_publica_base64"] == chave_temp["chave_publica_b64"]


# ══════════════════════════════════════════════════════════════════════════════
# Endpoint de renovação do cliente
# ══════════════════════════════════════════════════════════════════════════════

class TestValidarLicenca:
    def test_cliente_activo_recebe_token(self, app_cliente, db_temp, chave_temp):
        valido_ate = (datetime.now() + timedelta(days=30)).isoformat(timespec="seconds")
        db_temp.upsert_cliente("cli_001", "a@b.com", None, "manutencao", valido_ate)

        r = app_cliente.post("/licencas/validar?cliente_id=cli_001")
        assert r.status_code == 200
        data = r.json()
        assert data["payload"]["cliente_id"] == "cli_001"
        assert "assinatura" in data

    def test_token_recebido_e_verificavel_com_chave_publica(
        self, app_cliente, db_temp, chave_temp, monkeypatch, tmp_path
    ):
        """Confirma de ponta a ponta que o token vindo do endpoint é aceite pelo cliente real."""
        valido_ate = (datetime.now() + timedelta(days=30)).isoformat(timespec="seconds")
        db_temp.upsert_cliente("cli_002", "b@c.com", None, "manutencao", valido_ate)

        r = app_cliente.post("/licencas/validar?cliente_id=cli_002")
        assert r.status_code == 200
        token = r.json()

        # Carregar licenca.py do cliente sem conflito de nomes com config do servidor
        import importlib.util, sys as _sys, json as _json
        raiz_cliente = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "plataforma_opcoes",
        )
        spec_cfg = importlib.util.spec_from_file_location(
            "_api_test_config", os.path.join(raiz_cliente, "config.py")
        )
        config_cliente = importlib.util.module_from_spec(spec_cfg)
        spec_cfg.loader.exec_module(config_cliente)
        config_cliente.LICENCA_CHAVE_PUBLICA  = chave_temp["chave_publica_b64"]
        config_cliente.LICENCA_URL_RENOVACAO  = ""
        config_cliente.BASE_DIR               = tmp_path

        config_servidor_orig = _sys.modules.get("config")
        _sys.modules["config"] = config_cliente
        try:
            spec_lic = importlib.util.spec_from_file_location(
                "_api_test_licenca", os.path.join(raiz_cliente, "licenca.py")
            )
            licenca_mod = importlib.util.module_from_spec(spec_lic)
            spec_lic.loader.exec_module(licenca_mod)
        finally:
            if config_servidor_orig is not None:
                _sys.modules["config"] = config_servidor_orig
            elif "config" in _sys.modules:
                del _sys.modules["config"]

        licenca_mod.LICENCA_CHAVE_PUBLICA = chave_temp["chave_publica_b64"]
        licenca_mod.LICENCA_FILE          = tmp_path / "licenca_api_test.json"
        licenca_mod._LOCK_FILE            = tmp_path / "licenca_api_test.lock"
        licenca_mod.invalidar_cache_licenca()

        arq_token = tmp_path / "token_do_endpoint.json"
        arq_token.write_text(_json.dumps(token))
        estado = licenca_mod.importar_licenca(str(arq_token))
        assert estado.estagio == "ok"

    def test_cliente_inexistente_retorna_404(self, app_cliente):
        r = app_cliente.post("/licencas/validar?cliente_id=nao_existe")
        assert r.status_code == 404

    def test_cliente_cancelado_retorna_403(self, app_cliente, db_temp):
        valido_ate = (datetime.now() + timedelta(days=30)).isoformat(timespec="seconds")
        db_temp.upsert_cliente("cli_003", None, None, "manutencao", valido_ate, status="cancelado")
        r = app_cliente.post("/licencas/validar?cliente_id=cli_003")
        assert r.status_code == 403


class TestRateLimitValidarLicenca:
    """
    _RATE_LIMIT_REQ = 10 por _RATE_LIMIT_JAN = 60s por IP. O TestClient
    reporta sempre o mesmo IP interno para todos os pedidos, então cada
    teste começa por zerar _validar_timestamps explicitamente — caso
    contrário, chamadas de OUTROS testes neste mesmo ficheiro (ex.:
    TestValidarLicenca) partilhariam o mesmo balde e o resultado dependeria
    da ordem de execução dos testes.
    """

    def test_dentro_do_limite_continua_liberado(self, app_cliente, monkeypatch):
        import main
        monkeypatch.setattr(main, "_validar_timestamps", {})

        respostas = [
            app_cliente.post("/licencas/validar?cliente_id=nao_existe").status_code
            for _ in range(10)
        ]
        assert respostas == [404] * 10  # nenhum bloqueado — todos abaixo do limite

    def test_decimo_primeiro_pedido_seguido_e_bloqueado(self, app_cliente, monkeypatch):
        import main
        monkeypatch.setattr(main, "_validar_timestamps", {})

        for _ in range(10):
            app_cliente.post("/licencas/validar?cliente_id=nao_existe")
        r = app_cliente.post("/licencas/validar?cliente_id=nao_existe")
        assert r.status_code == 429

    def test_janela_e_por_ip_isolada(self, app_cliente, monkeypatch):
        """Um IP diferente não deve ser afetado pelo histórico de outro."""
        import main
        monkeypatch.setattr(main, "_validar_timestamps", {"1.2.3.4": [time.time()] * 10})

        r = app_cliente.post("/licencas/validar?cliente_id=nao_existe")
        assert r.status_code == 404  # bloqueado seria 429; este IP (testclient) está limpo


# ══════════════════════════════════════════════════════════════════════════════
# Endpoints de administração
# ══════════════════════════════════════════════════════════════════════════════

class TestAdminProtegido:
    def test_sem_token_retorna_401(self, app_cliente):
        r = app_cliente.get("/admin/clientes")
        assert r.status_code == 401

    def test_token_errado_retorna_401(self, app_cliente):
        r = app_cliente.get("/admin/clientes",
                            headers={"Authorization": "Bearer token-errado"})
        assert r.status_code == 401


class TestAdminClientes:
    def test_listar_clientes_vazio(self, app_cliente, headers_admin):
        r = app_cliente.get("/admin/clientes", headers=headers_admin)
        assert r.status_code == 200
        assert r.json()["clientes"] == []

    def test_emitir_manual_cria_cliente(self, app_cliente, headers_admin, db_temp):
        r = app_cliente.post(
            "/admin/clientes/cli_novo/emitir",
            params={"plano": "manutencao", "dias": 30, "email": "novo@teste.com"},
            headers=headers_admin,
        )
        assert r.status_code == 200
        assert r.json()["id"] == "cli_novo"
        assert db_temp.obter_cliente("cli_novo") is not None

    def test_emitir_plano_invalido_retorna_400(self, app_cliente, headers_admin):
        r = app_cliente.post(
            "/admin/clientes/cli_x/emitir",
            params={"plano": "plano_invalido", "dias": 30},
            headers=headers_admin,
        )
        assert r.status_code == 400

    def test_obter_cliente_especifico(self, app_cliente, headers_admin, db_temp):
        valido_ate = (datetime.now() + timedelta(days=30)).isoformat(timespec="seconds")
        db_temp.upsert_cliente("cli_a", "a@b.com", "Ana", "manutencao", valido_ate)
        r = app_cliente.get("/admin/clientes/cli_a", headers=headers_admin)
        assert r.status_code == 200
        assert r.json()["email"] == "a@b.com"

    def test_obter_cliente_inexistente_retorna_404(self, app_cliente, headers_admin):
        r = app_cliente.get("/admin/clientes/nao_existe", headers=headers_admin)
        assert r.status_code == 404

    def test_revogar_cliente(self, app_cliente, headers_admin, db_temp):
        valido_ate = (datetime.now() + timedelta(days=30)).isoformat(timespec="seconds")
        db_temp.upsert_cliente("cli_b", None, None, "manutencao", valido_ate)

        r = app_cliente.post("/admin/clientes/cli_b/revogar", headers=headers_admin)
        assert r.status_code == 200
        assert db_temp.obter_cliente("cli_b")["status"] == "cancelado"

    def test_revogar_inexistente_retorna_404(self, app_cliente, headers_admin):
        r = app_cliente.post("/admin/clientes/nao_existe/revogar", headers=headers_admin)
        assert r.status_code == 404

    def test_listar_eventos_auditoria(self, app_cliente, headers_admin, db_temp):
        db_temp.registar_evento("cli_audit", "tipo1", "stripe", {"id": 1})
        r = app_cliente.get("/admin/eventos", headers=headers_admin)
        assert r.status_code == 200
        assert len(r.json()["eventos"]) >= 1

    def test_listar_clientes_filtra_por_status(self, app_cliente, headers_admin, db_temp):
        valido_ate = (datetime.now() + timedelta(days=30)).isoformat(timespec="seconds")
        db_temp.upsert_cliente("cli_c1", None, None, "manutencao", valido_ate, status="activo")
        db_temp.upsert_cliente("cli_c2", None, None, "manutencao", valido_ate, status="cancelado")
        r = app_cliente.get("/admin/clientes?status=activo", headers=headers_admin)
        assert r.status_code == 200
        ids = [c["id"] for c in r.json()["clientes"]]
        assert "cli_c1" in ids
        assert "cli_c2" not in ids


# ══════════════════════════════════════════════════════════════════════════════
# Webhooks via API (assinatura válida)
# ══════════════════════════════════════════════════════════════════════════════

class TestWebhookStripeViaAPI:
    SEG = "whsec_teste_123"

    def _fazer_request_stripe(self, app_cliente, evento, monkeypatch):
        import webhooks_stripe
        monkeypatch.setattr(webhooks_stripe, "STRIPE_WEBHOOK_SECRET", self.SEG)
        import config
        monkeypatch.setattr(config, "STRIPE_WEBHOOK_SECRET", self.SEG)
        corpo = json.dumps(evento, separators=(",", ":")).encode("utf-8")
        ts = int(time.time())
        mensagem = f"{ts}.".encode("utf-8") + corpo
        sig = hmac.new(self.SEG.encode("utf-8"), mensagem, hashlib.sha256).hexdigest()
        cabecalho = f"t={ts},v1={sig}"
        return app_cliente.post(
            "/webhooks/stripe",
            content=corpo,
            headers={"stripe-signature": cabecalho, "content-type": "application/json"},
        )

    def test_webhook_checkout_aceito(self, app_cliente, db_temp, monkeypatch):
        evento = {
            "type": "checkout.session.completed",
            "data": {"object": {
                "customer": "cus_via_api",
                "subscription": "sub_via_api",
                "customer_email": "via@api.com",
                "metadata": {"plano": "manutencao"},
            }},
        }
        r = self._fazer_request_stripe(app_cliente, evento, monkeypatch)
        assert r.status_code == 200, r.text
        assert r.json()["recebido"] is True
        assert db_temp.obter_cliente("stripe_cus_via_api") is not None

    def test_webhook_sem_assinatura_retorna_400(self, app_cliente, monkeypatch):
        import webhooks_stripe
        monkeypatch.setattr(webhooks_stripe, "STRIPE_WEBHOOK_SECRET", self.SEG)
        r = app_cliente.post(
            "/webhooks/stripe",
            content=b'{"type":"invoice.paid"}',
            headers={"stripe-signature": "invalida", "content-type": "application/json"},
        )
        assert r.status_code == 400

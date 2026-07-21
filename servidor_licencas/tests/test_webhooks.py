"""Testes para webhooks_stripe.py e webhooks_mercadopago.py."""

import hashlib
import hmac
import json
import time
from datetime import datetime, timezone


# ══════════════════════════════════════════════════════════════════════════════
# Stripe
# ══════════════════════════════════════════════════════════════════════════════

def _assinar_payload_stripe(payload: dict, segredo: str, timestamp: int = None) -> str:
    """Reconstrói o cabeçalho Stripe-Signature para testes."""
    if timestamp is None:
        timestamp = int(time.time())
    corpo = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    mensagem = f"{timestamp}.".encode("utf-8") + corpo
    assinatura = hmac.new(segredo.encode("utf-8"), mensagem, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={assinatura}", corpo


class TestVerificacaoAssinaturaStripe:
    SEG = "whsec_segredo_de_teste_1234567890"

    def test_assinatura_valida_aceita(self, db_temp):
        from webhooks_stripe import verificar_assinatura_stripe
        payload = {"type": "invoice.paid", "data": {}}
        ts = int(time.time())
        cabecalho, corpo = _assinar_payload_stripe(payload, self.SEG, ts)
        assert verificar_assinatura_stripe(corpo, cabecalho, self.SEG, 300, float(ts)) is True

    def test_assinatura_com_segredo_errado_rejeitada(self, db_temp):
        from webhooks_stripe import verificar_assinatura_stripe
        payload = {"type": "invoice.paid", "data": {}}
        ts = int(time.time())
        cabecalho, corpo = _assinar_payload_stripe(payload, "segredo_correcto", ts)
        assert verificar_assinatura_stripe(corpo, cabecalho, "segredo_errado", 300, float(ts)) is False

    def test_replay_alem_da_tolerancia_rejeitado(self, db_temp):
        from webhooks_stripe import verificar_assinatura_stripe
        payload = {"type": "invoice.paid", "data": {}}
        ts_antigo = int(time.time()) - 400  # > 300s de tolerância
        cabecalho, corpo = _assinar_payload_stripe(payload, self.SEG, ts_antigo)
        agora = float(int(time.time()))
        assert verificar_assinatura_stripe(corpo, cabecalho, self.SEG, 300, agora) is False

    def test_sem_segredo_configurado_rejeita(self, db_temp):
        from webhooks_stripe import verificar_assinatura_stripe
        assert verificar_assinatura_stripe(b"{}", "t=1,v1=abc", "", 300) is False

    def test_cabecalho_malformado_rejeitado(self, db_temp):
        from webhooks_stripe import verificar_assinatura_stripe
        assert verificar_assinatura_stripe(b"{}", "malformado", "segredo", 300) is False


class TestProcessarEventosStripe:
    def test_checkout_session_completed_cria_cliente(self, db_temp):
        from webhooks_stripe import processar_evento_stripe
        evento = {
            "type": "checkout.session.completed",
            "data": {"object": {
                "customer": "cus_abc",
                "subscription": "sub_abc",
                "customer_email": "x@y.com",
                "metadata": {"plano": "manutencao"},
            }},
        }
        resultado = processar_evento_stripe(evento)
        assert resultado["acao"] == "cliente_activado"
        cliente = db_temp.obter_cliente("stripe_cus_abc")
        assert cliente is not None
        assert cliente["status"] == "activo"
        assert cliente["plano"] == "manutencao"

    def test_invoice_paid_renova_cliente(self, db_temp):
        from webhooks_stripe import processar_evento_stripe
        from datetime import timedelta

        valido_ate = (datetime.now() + timedelta(days=5)).isoformat(timespec="seconds")
        db_temp.upsert_cliente(
            "stripe_cus_001", "a@b.com", None, "manutencao", valido_ate,
            stripe_subscription_id="sub_001",
        )

        periodo_fim = int(time.time()) + 30 * 86400
        evento = {
            "type": "invoice.paid",
            "data": {"object": {
                "subscription": "sub_001",
                "lines": {"data": [{"period": {"end": periodo_fim}}]},
            }},
        }
        resultado = processar_evento_stripe(evento)
        assert resultado["acao"] == "renovado"

        cliente_renovado = db_temp.obter_cliente("stripe_cus_001")
        nova_validade = datetime.fromtimestamp(periodo_fim, tz=timezone.utc) \
                               .replace(tzinfo=None).isoformat(timespec="seconds")
        assert cliente_renovado["valido_ate"] == nova_validade

    def test_subscription_deleted_cancela_cliente(self, db_temp):
        from webhooks_stripe import processar_evento_stripe
        from datetime import timedelta

        valido_ate = (datetime.now() + timedelta(days=30)).isoformat(timespec="seconds")
        db_temp.upsert_cliente(
            "stripe_cus_002", None, None, "manutencao", valido_ate,
            stripe_subscription_id="sub_002",
        )
        evento = {
            "type": "customer.subscription.deleted",
            "data": {"object": {"id": "sub_002"}},
        }
        resultado = processar_evento_stripe(evento)
        assert resultado["acao"] == "cancelado"
        assert db_temp.obter_cliente("stripe_cus_002")["status"] == "cancelado"

    def test_subscription_updated_suspende_por_past_due(self, db_temp):
        from webhooks_stripe import processar_evento_stripe
        from datetime import timedelta

        valido_ate = (datetime.now() + timedelta(days=30)).isoformat(timespec="seconds")
        periodo_fim = int(time.time()) + 7 * 86400
        db_temp.upsert_cliente(
            "stripe_cus_003", None, None, "manutencao", valido_ate,
            stripe_subscription_id="sub_003",
        )
        evento = {
            "type": "customer.subscription.updated",
            "data": {"object": {
                "id": "sub_003",
                "status": "past_due",
                "current_period_end": periodo_fim,
            }},
        }
        resultado = processar_evento_stripe(evento)
        assert resultado["acao"] == "actualizado"
        assert db_temp.obter_cliente("stripe_cus_003")["status"] == "suspenso"

    def test_tipo_desconhecido_e_ignorado(self, db_temp):
        from webhooks_stripe import processar_evento_stripe
        resultado = processar_evento_stripe({"type": "tipo.nao.tratado", "data": {"object": {}}})
        assert resultado["acao"] == "ignorado"

    def test_invoice_paid_subscription_desconhecida_e_ignorada(self, db_temp):
        from webhooks_stripe import processar_evento_stripe
        evento = {
            "type": "invoice.paid",
            "data": {"object": {
                "subscription": "sub_desconhecida",
                "lines": {"data": []},
            }},
        }
        resultado = processar_evento_stripe(evento)
        assert resultado["acao"] == "ignorado"

    def test_evento_stripe_registado_em_auditoria(self, db_temp):
        from webhooks_stripe import processar_evento_stripe
        evento = {"type": "invoice.payment_failed", "data": {"object": {"subscription": "sub_x"}}}
        processar_evento_stripe(evento)
        eventos = db_temp.listar_eventos()
        assert len(eventos) >= 1
        assert any(e["tipo"] == "invoice.payment_failed" for e in eventos)


# ══════════════════════════════════════════════════════════════════════════════
# Mercado Pago
# ══════════════════════════════════════════════════════════════════════════════

def _assinar_mp(data_id: str, x_request_id: str, segredo: str, ts: str = None) -> str:
    """Reconstrói o cabeçalho x-signature do Mercado Pago para testes."""
    if ts is None:
        ts = str(int(time.time()))
    manifest = f"id:{data_id.lower()};request-id:{x_request_id};ts:{ts};"
    assinatura = hmac.new(
        segredo.encode("utf-8"), manifest.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return f"ts={ts},v1={assinatura}"


class TestVerificacaoAssinaturaMercadoPago:
    SEG = "mp_segredo_teste_1234567890"

    def test_assinatura_valida_aceita(self, db_temp):
        from webhooks_mercadopago import verificar_assinatura_mp
        sig = _assinar_mp("12345", "req-abc", self.SEG)
        assert verificar_assinatura_mp("12345", sig, "req-abc", self.SEG) is True

    def test_assinatura_com_segredo_errado_rejeitada(self, db_temp):
        from webhooks_mercadopago import verificar_assinatura_mp
        sig = _assinar_mp("12345", "req-abc", "segredo_correcto")
        assert verificar_assinatura_mp("12345", sig, "req-abc", "segredo_errado") is False

    def test_sem_segredo_configurado_rejeita(self, db_temp):
        from webhooks_mercadopago import verificar_assinatura_mp
        assert verificar_assinatura_mp("12345", "ts=1,v1=abc", "req", "") is False

    def test_data_id_case_insensitive(self, db_temp):
        """O Mercado Pago documenta que o data_id deve ser tratado em minúsculas."""
        from webhooks_mercadopago import verificar_assinatura_mp
        # Assinatura feita com "ABC" em minúscula (conforme spec)
        sig = _assinar_mp("abc", "req-1", self.SEG)
        # Enviar "ABC" em maiúscula — deve normalizar e aceitar
        assert verificar_assinatura_mp("ABC", sig, "req-1", self.SEG) is True


class TestProcessarNotificacoesMercadoPago:
    def _recurso_autorizado(self, subscription_id, plano="manutencao", email="a@b.com"):
        return {
            "id": subscription_id, "status": "authorized",
            "payer_email": email, "external_reference": plano,
        }

    def test_authorized_cria_ou_renova_cliente(self, db_temp):
        from webhooks_mercadopago import processar_notificacao_mp

        def buscar(tipo, data_id):
            return self._recurso_autorizado(data_id)

        resultado = processar_notificacao_mp("subscription_preapproval", "mp_sub_01", buscar)
        assert resultado["acao"] == "activado_ou_renovado"
        cliente = db_temp.obter_cliente("mp_mp_sub_01")
        assert cliente is not None
        assert cliente["status"] == "activo"

    def test_cancelled_cancela_cliente(self, db_temp):
        from webhooks_mercadopago import processar_notificacao_mp
        from datetime import timedelta

        valido_ate = (datetime.now() + timedelta(days=30)).isoformat(timespec="seconds")
        db_temp.upsert_cliente("mp_sub_02", None, None, "manutencao", valido_ate,
                                mp_subscription_id="sub_02")

        def buscar(tipo, data_id):
            return {"id": data_id, "status": "cancelled", "payer_email": None, "external_reference": None}

        resultado = processar_notificacao_mp("subscription_preapproval", "sub_02", buscar)
        assert resultado["acao"] == "cancelled"
        assert db_temp.obter_cliente("mp_sub_02")["status"] == "cancelado"

    def test_tipo_desconhecido_e_ignorado(self, db_temp):
        from webhooks_mercadopago import processar_notificacao_mp
        resultado = processar_notificacao_mp("outro_tipo", "123", lambda t, d: {})
        assert resultado["acao"] == "ignorado"

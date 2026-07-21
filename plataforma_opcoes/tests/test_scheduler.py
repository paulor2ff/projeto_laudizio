"""
Testes para scheduler.py — guard de pregão (_dentro_do_pregao).

Usa apenas a função pura _dentro_do_pregao(agora), que recebe a data/hora
como parâmetro em vez de ler o relógio do sistema. Isto evita qualquer
necessidade de monkeypatch em datetime.datetime — patchear o tipo nativo
imutável é arriscado em processos com pandas/numpy carregados (pode causar
segfault por interação com extensões C), por isso o guard foi desenhado
desde o início para ser testável sem essa técnica.
"""

import json
import pytest
from datetime import datetime

import scheduler


@pytest.fixture(autouse=True)
def _limpar_scheduler_global():
    """
    _scheduler_global é uma variável de módulo (estado global). Sem esta
    limpeza, um scheduler real deixado 'rodando' por um teste vazaria para
    o próximo (iniciar() vira no-op se já achar algo em execução).
    """
    yield
    scheduler.parar()
    scheduler._scheduler_global = None


class TestCriarScheduler:
    def test_cria_scheduler_parado_com_3_jobs(self):
        sched = scheduler.criar_scheduler()
        assert sched.running is False
        ids = {j.id for j in sched.get_jobs()}
        assert ids == {"tempo_real", "historico_diario", "invalidar_cdi"}
        # Nota: não chamar sched.shutdown() aqui — a instância nunca foi
        # .start()ada (criar_scheduler() devolve-a propositalmente parada),
        # e o próprio APScheduler levanta SchedulerNotRunningError nesse caso.

    def test_levanta_importerror_sem_apscheduler(self, monkeypatch):
        monkeypatch.setattr(scheduler, "APSCHEDULER_OK", False)
        with pytest.raises(ImportError):
            scheduler.criar_scheduler()


class TestStatus:
    def test_status_antes_de_qualquer_inicio(self):
        assert scheduler.status() == {"rodando": False, "jobs": []}


class TestIniciarPararComLicenca:
    def _instalar_licenca(self, licenca_temp, **kwargs):
        dados = licenca_temp.emitir(**kwargs)
        licenca_temp.modulo.LICENCA_FILE.write_text(json.dumps(dados))
        licenca_temp.modulo.invalidar_cache_licenca()

    def test_bloqueia_sem_nenhuma_licenca_instalada(self, licenca_temp):
        with pytest.raises(licenca_temp.modulo.LicencaError) as exc_info:
            scheduler.iniciar()
        assert exc_info.value.estado.estagio == "sem_licenca"
        assert scheduler.status()["rodando"] is False

    def test_inicia_com_licenca_valida_ok(self, licenca_temp):
        self._instalar_licenca(licenca_temp, dias_validade=10)
        scheduler.iniciar()
        st = scheduler.status()
        assert st["rodando"] is True
        assert {j["id"] for j in st["jobs"]} == {
            "tempo_real", "historico_diario", "invalidar_cdi"
        }

    def test_inicia_ainda_em_estagio_degradado(self, licenca_temp):
        """degradado está dentro do mínimo exigido (<= 'degradado') — deve iniciar."""
        self._instalar_licenca(licenca_temp, vencido_ha=15)  # 7 < 15 <= 30 -> degradado
        scheduler.iniciar()
        assert scheduler.status()["rodando"] is True

    def test_bloqueia_em_estagio_bloqueado(self, licenca_temp):
        """Vencida há mais de 30 dias (carência + degradado) -> 'bloqueado', acima do mínimo."""
        self._instalar_licenca(licenca_temp, vencido_ha=45)
        with pytest.raises(licenca_temp.modulo.LicencaError) as exc_info:
            scheduler.iniciar()
        assert exc_info.value.estado.estagio == "bloqueado"

    def test_segunda_chamada_a_iniciar_e_idempotente(self, licenca_temp):
        self._instalar_licenca(licenca_temp, dias_validade=10)
        scheduler.iniciar()
        primeiro = scheduler._scheduler_global
        scheduler.iniciar()  # não deve substituir a instância nem levantar erro
        assert scheduler._scheduler_global is primeiro
        assert scheduler.status()["rodando"] is True

    def test_parar_encerra_o_scheduler(self, licenca_temp):
        self._instalar_licenca(licenca_temp, dias_validade=10)
        scheduler.iniciar()
        scheduler.parar()
        assert scheduler.status()["rodando"] is False

    def test_parar_sem_nunca_ter_iniciado_e_seguro(self):
        scheduler.parar()  # não deve levantar
        assert scheduler.status()["rodando"] is False


class TestJobColetaTempoReal:
    def test_fora_do_pregao_nao_executa_ciclo(self, monkeypatch):
        monkeypatch.setattr(scheduler, "_dentro_do_pregao", lambda agora: False)
        chamado = {}
        monkeypatch.setattr(
            "collector.ciclo_tempo_real_todos",
            lambda: chamado.setdefault("sim", True)
        )
        scheduler._job_coleta_tempo_real()
        assert "sim" not in chamado

    def test_dentro_do_pregao_executa_ciclo_e_verifica_alertas(self, monkeypatch):
        monkeypatch.setattr(scheduler, "_dentro_do_pregao", lambda agora: True)
        monkeypatch.setattr(
            "collector.ciclo_tempo_real_todos",
            lambda: [{"status": "ok"}, {"status": "erro"}]
        )
        disparos = {}
        monkeypatch.setattr(
            "alertas.verificar_todos_alertas",
            lambda: disparos.setdefault("chamado", True) or []
        )
        scheduler._job_coleta_tempo_real()  # não deve levantar
        assert disparos.get("chamado") is True

    def test_erro_no_ciclo_e_isolado(self, monkeypatch):
        monkeypatch.setattr(scheduler, "_dentro_do_pregao", lambda agora: True)

        def _falha():
            raise RuntimeError("falha simulada")

        monkeypatch.setattr("collector.ciclo_tempo_real_todos", _falha)
        scheduler._job_coleta_tempo_real()  # não deve propagar a exceção


class TestJobHistoricoDiario:
    def test_executa_sem_levantar(self, monkeypatch):
        monkeypatch.setattr(
            "collector.coletar_historico_todos",
            lambda periodo="5d": {"BBAS3.SA": 2}
        )
        scheduler._job_historico_diario()

    def test_erro_e_isolado(self, monkeypatch):
        def _falha(periodo="5d"):
            raise RuntimeError("falha simulada")
        monkeypatch.setattr("collector.coletar_historico_todos", _falha)
        scheduler._job_historico_diario()  # não deve propagar


class TestJobInvalidarCdi:
    def test_executa_sem_levantar(self, monkeypatch):
        chamado = {}
        monkeypatch.setattr(
            "greeks.invalidar_cache_cdi",
            lambda: chamado.setdefault("sim", True)
        )
        scheduler._job_invalidar_cdi()
        assert chamado.get("sim") is True

    def test_erro_e_isolado(self, monkeypatch):
        def _falha():
            raise RuntimeError("falha simulada")
        monkeypatch.setattr("greeks.invalidar_cache_cdi", _falha)
        scheduler._job_invalidar_cdi()  # não deve propagar


@pytest.mark.parametrize("dt,esperado,descricao", [
    (datetime(2026, 12, 25, 14, 0, 0), False, "Natal (sexta, feriado fixo)"),
    (datetime(2026, 6, 16, 14, 0, 0),  True,  "terça comum dentro do pregão"),
    (datetime(2026, 2, 17, 11, 0, 0),  False, "Carnaval terça (feriado móvel)"),
    (datetime(2026, 2, 16, 11, 0, 0),  False, "Carnaval segunda (feriado móvel)"),
    (datetime(2026, 6, 20, 14, 0, 0),  False, "sábado (fim de semana)"),
    (datetime(2026, 6, 21, 14, 0, 0),  False, "domingo (fim de semana)"),
    (datetime(2026, 6, 19, 14, 0, 0),  True,  "sexta comum dentro do pregão"),
    (datetime(2026, 6, 16, 9, 0, 0),   False, "terça comum, antes das 10h"),
    (datetime(2026, 6, 16, 18, 0, 0),  False, "terça comum, às 18h (fim exclusive)"),
    (datetime(2026, 6, 16, 17, 59, 0), True,  "terça comum, 17h59 (último minuto)"),
    (datetime(2026, 6, 16, 3, 0, 0),   False, "terça de madrugada"),
    (datetime(2026, 4, 3, 11, 0, 0),   False, "Sexta-feira Santa (feriado móvel)"),
    (datetime(2026, 6, 4, 11, 0, 0),   False, "Corpus Christi (feriado móvel)"),
    (datetime(2026, 9, 7, 11, 0, 0),   False, "Independência (feriado fixo)"),
])
def test_dentro_do_pregao(dt, esperado, descricao):
    assert scheduler._dentro_do_pregao(dt) == esperado, descricao

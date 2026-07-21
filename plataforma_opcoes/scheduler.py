"""
scheduler.py — Agendador de coleta durante o pregão B3
=======================================================
Executa coleta automática de segunda a sexta durante o horário do pregão.
Pode ser iniciado standalone ou integrado ao servidor FastAPI.
"""

import logging
import signal
import sys

from config import (
    INTERVALO_SEG, PREGAO_FIM, PREGAO_INICIO,
    TIMEZONE,
)
from licenca import requer_licenca

log = logging.getLogger(__name__)

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron         import CronTrigger
    from apscheduler.triggers.interval     import IntervalTrigger
    APSCHEDULER_OK = True
except ImportError:
    APSCHEDULER_OK = False
    log.warning("APScheduler não instalado. Execute: pip install apscheduler")


def _dentro_do_pregao(agora) -> bool:
    """
    Função pura que decide se 'agora' (datetime) está dentro do pregão B3:
    dia de semana, não-feriado, e dentro de PREGAO_INICIO–PREGAO_FIM.
    Extraída do job para ser testável sem precisar de mock de tempo real
    (monkeypatch de datetime.datetime é arriscado em processos com pandas
    carregado — pode causar segfault por interação com extensões C).
    """
    # Fim de semana (5=sábado, 6=domingo)
    if agora.weekday() >= 5:
        log.debug("Fora do pregão (fim de semana) — coleta ignorada.")
        return False

    # Feriado da B3 (nacional fixo ou móvel — Carnaval, Sexta-feira Santa, etc.)
    try:
        from feriados import eh_feriado_b3
        if eh_feriado_b3(agora.date()):
            log.debug("Feriado da B3 (%s) — coleta ignorada.", agora.date())
            return False
    except Exception as exc:
        log.debug("Verificação de feriado indisponível: %s", exc)

    # Converter PREGAO_INICIO / PREGAO_FIM para horas inteiras
    h_ini = int(PREGAO_INICIO.split(":")[0])
    h_fim = int(PREGAO_FIM.split(":")[0])
    if not (h_ini <= agora.hour < h_fim):
        log.debug("Fora do pregão (%02d:%02d) — coleta ignorada.", agora.hour, agora.minute)
        return False

    return True


def _job_coleta_tempo_real():
    """
    Job executado a cada INTERVALO_SEG.
    Guard interno garante execução apenas durante o pregão B3
    (segunda a sexta, não-feriado, PREGAO_INICIO–PREGAO_FIM, fuso
    America/Sao_Paulo). Protege contra execução em madrugadas, fins de
    semana e feriados mesmo se o IntervalTrigger não tiver filtro próprio.
    """
    from datetime import datetime as _dt
    try:
        import pytz as _pytz
        tz    = _pytz.timezone(TIMEZONE)
        agora = _dt.now(tz)
    except ImportError:
        # pytz não instalado — usar horário local sem fuso
        agora = _dt.now()

    if not _dentro_do_pregao(agora):
        return

    from collector import ciclo_tempo_real_todos
    try:
        resultados = ciclo_tempo_real_todos()
        ok  = sum(1 for r in resultados if r["status"] == "ok")
        err = len(resultados) - ok
        log.info("Ciclo tempo real: %d/%d tickers OK, %d erros", ok, len(resultados), err)
    except Exception as exc:
        log.error("Erro no job de tempo real: %s", exc)
        return

    # Verificar alertas activos após a coleta — os dados acabaram de ser actualizados
    try:
        from alertas import verificar_todos_alertas
        disparados = verificar_todos_alertas()
        if disparados:
            log.warning("%d alerta(s) disparado(s) neste ciclo.", len(disparados))
    except Exception as exc:
        log.debug("Verificação de alertas indisponível: %s", exc)


def _job_historico_diario():
    """Job diário (após fechamento do pregão) para atualizar histórico."""
    from collector import coletar_historico_todos
    try:
        log.info("Iniciando coleta histórica diária...")
        resultado = coletar_historico_todos(periodo="5d")  # últimos 5 dias (suficiente para update)
        total = sum(resultado.values())
        log.info("Histórico diário: %d novos registros em %d tickers", total, len(resultado))
    except Exception as exc:
        log.error("Erro na coleta histórica diária: %s", exc)


def _job_invalidar_cdi():
    """Invalida cache do CDI uma vez ao dia para buscar valor atualizado."""
    try:
        from greeks import invalidar_cache_cdi
        invalidar_cache_cdi()
        log.info("Cache do CDI invalidado.")
    except Exception as exc:
        log.error("Erro ao invalidar CDI: %s", exc)


def criar_scheduler() -> "BackgroundScheduler":
    """
    Cria e configura o scheduler com todos os jobs.
    Retorna a instância (não iniciada).
    """
    if not APSCHEDULER_OK:
        raise ImportError("APScheduler necessário. Execute: pip install apscheduler")

    scheduler = BackgroundScheduler(timezone=TIMEZONE)

    # Extrai hora/minuto de início e fim do pregão
    h_ini, m_ini = PREGAO_INICIO.split(":")
    h_fim, m_fim = PREGAO_FIM.split(":")

    # ── Coleta em tempo real durante o pregão ─────────────────────────────────
    # Executa a cada INTERVALO_SEG segundos, apenas nos dias/horários do pregão
    scheduler.add_job(
        func    = _job_coleta_tempo_real,
        trigger = IntervalTrigger(
            seconds  = INTERVALO_SEG,
            timezone = TIMEZONE,
        ),
        id              = "tempo_real",
        name            = "Coleta tempo real (pregão)",
        replace_existing= True,
        misfire_grace_time = 15,
    )

    # ── Histórico diário — 18h30 (após fechamento) ────────────────────────────
    scheduler.add_job(
        func    = _job_historico_diario,
        trigger = CronTrigger(
            day_of_week = "mon-fri",
            hour        = 18,
            minute      = 30,
            timezone    = TIMEZONE,
        ),
        id              = "historico_diario",
        name            = "Atualização histórica diária",
        replace_existing= True,
    )

    # ── Invalidação do CDI — 9h (antes do pregão) ────────────────────────────
    scheduler.add_job(
        func    = _job_invalidar_cdi,
        trigger = CronTrigger(
            day_of_week = "mon-fri",
            hour        = 9,
            minute      = 0,
            timezone    = TIMEZONE,
        ),
        id              = "invalidar_cdi",
        name            = "Atualizar CDI",
        replace_existing= True,
    )

    return scheduler


_scheduler_global = None


@requer_licenca(minimo="degradado")
def iniciar() -> None:
    """
    Inicia o scheduler global. Requer licença em estágio 'degradado' ou
    melhor — automação é uma funcionalidade paga. Em estágio bloqueado
    ou sem licença, levanta LicencaError em vez de iniciar silenciosamente.
    """
    global _scheduler_global
    if not APSCHEDULER_OK:
        log.error("APScheduler não disponível.")
        return
    if _scheduler_global and _scheduler_global.running:
        log.warning("Scheduler já está em execução.")
        return
    _scheduler_global = criar_scheduler()
    _scheduler_global.start()
    log.info(
        "Scheduler iniciado | Intervalo: %ds | Pregão: %s–%s (%s)",
        INTERVALO_SEG, PREGAO_INICIO, PREGAO_FIM, TIMEZONE,
    )


def parar() -> None:
    """Para o scheduler global."""
    global _scheduler_global
    if _scheduler_global and _scheduler_global.running:
        _scheduler_global.shutdown(wait=False)
        log.info("Scheduler encerrado.")


def status() -> dict:
    """Retorna status dos jobs agendados."""
    if not _scheduler_global:
        return {"rodando": False, "jobs": []}
    jobs = []
    for job in _scheduler_global.get_jobs():
        jobs.append({
            "id":            job.id,
            "nome":          job.name,
            "proxima_exec":  str(job.next_run_time) if job.next_run_time else "—",
        })
    return {"rodando": _scheduler_global.running, "jobs": jobs}


# ─── Execução standalone ──────────────────────────────────────────────────────

if __name__ == "__main__":
    import time as _time
    from database import inicializar

    logging.basicConfig(
        level   = logging.INFO,
        format  = "%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt = "%Y-%m-%d %H:%M:%S",
    )

    inicializar()
    iniciar()

    def _sair(sig, frame):
        log.info("Encerrando scheduler...")
        parar()
        sys.exit(0)

    signal.signal(signal.SIGINT,  _sair)
    signal.signal(signal.SIGTERM, _sair)

    log.info("Scheduler rodando. Ctrl+C para encerrar.")
    while True:
        _time.sleep(60)

"""
api.py — Backend FastAPI (REST + WebSocket)
============================================
Endpoints REST:
  GET  /cotacoes/{ticker}      — histórico de cotações
  GET  /opcoes/{ticker}        — cadeia de opções com Greeks
  GET  /greeks/{ticker}        — Greeks detalhados
  GET  /tickers                — lista de tickers disponíveis
  GET  /resumo                 — estatísticas do banco
  GET  /status                 — status do sistema e scheduler
  POST /coletar/{ticker}       — dispara coleta manual
  GET  /cotacao-atual/{ticker} — preço atual (yfinance)

WebSocket:
  WS   /ws/{ticker}            — stream de atualizações em tempo real

Iniciar:
  uvicorn api:app --host 0.0.0.0 --port 8000
  ou via CLI: python cli.py --dashboard
"""

import asyncio
import os
import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from typing   import Optional, Set
from contextlib import asynccontextmanager
from fastapi                    import FastAPI, WebSocket, WebSocketDisconnect, Query, Request, HTTPException
from fastapi.security           import HTTPBearer, HTTPAuthorizationCredentials
from fastapi                    import Depends, status
from fastapi.middleware.cors    import CORSMiddleware
from fastapi.responses          import HTMLResponse, FileResponse, Response
from fastapi.staticfiles        import StaticFiles
from pathlib                    import Path

from config   import API_HOST, API_PORT, TICKERS, INTERVALO_SEG, BASE_DIR
import exportadores
from database import (
    inicializar, resumo_geral,
    consultar_cotacoes, consultar_opcoes, consultar_snapshots,
)
from collector import (
    cotacao_atual, coletar_historico,
    coletar_opcoes, calcular_greeks_ticker,
)

log = logging.getLogger(__name__)

TZ = ZoneInfo("America/Sao_Paulo")

# ─── Autenticação Bearer Token ────────────────────────────────────────────────
_security = HTTPBearer(auto_error=False)
# Ancorado a BASE_DIR (não um caminho relativo) — no executável Nuitka, o
# cwd do processo pode não ser a pasta do executável, o que faria o token
# "sumir" e ser regenerado a cada início a partir de um cwd diferente.
# Mesmo bug de fundo que o antigo BASE_DIR do config.py, aplicado aqui.
_TOKEN_FILE = str(BASE_DIR / "api_token.txt")

def _obter_token_activo() -> str:
    import config as _cfg
    t = _cfg.API_TOKEN
    if t: return t
    if os.path.exists(_TOKEN_FILE):
        return open(_TOKEN_FILE).read().strip()
    return ""

def inicializar_token() -> str:
    t = _obter_token_activo()
    if t: return t
    import secrets as _s
    novo = _s.token_urlsafe(32)
    with open(_TOKEN_FILE, "w") as f: f.write(novo)
    log.warning("=" * 60)
    log.warning("TOKEN DA API GERADO: %s", novo)
    log.warning("Guarde este token — é necessário para aceder à API.")
    log.warning("Ficheiro: %s", os.path.abspath(_TOKEN_FILE))
    log.warning("=" * 60)
    return novo

def verificar_token(
    credentials: HTTPAuthorizationCredentials = Depends(_security),
    request: Request = None,
) -> bool:

    token_activo = _obter_token_activo()
    if not token_activo:
        return True   # token não configurado → modo aberto (localhost)
    if credentials and credentials.credentials == token_activo:
        return True
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Token inválido ou ausente. Use: Authorization: Bearer <token>",
        headers={"WWW-Authenticate": "Bearer"},
    )

# ─── App ─────────────────────────────────────────────────────────────────────

import time as _time
_coleta_timestamps: dict = {}   # {ip: [timestamps]}
_RATE_LIMIT_REQ = 5             # máximo de requisições
_RATE_LIMIT_JAN = 60            # por janela de N segundos

def _checar_rate_limit(ip: str) -> bool:
    agora = _time.time()
    historico = _coleta_timestamps.setdefault(ip, [])
    historico[:] = [t for t in historico if agora - t < _RATE_LIMIT_JAN]
    if len(historico) >= _RATE_LIMIT_REQ:
        return False
    historico.append(agora)
    return True

# ─── WebSocket Manager ────────────────────────────────────────────────────────

class WebSocketManager:
    def __init__(self):
        # {ticker: set of WebSocket connections}
        self._conexoes: dict[str, Set[WebSocket]] = {}

    async def conectar(self, ws: WebSocket, ticker: str):
        await ws.accept()
        self._conexoes.setdefault(ticker, set()).add(ws)
        log.info("WS conectado: %s (%d clientes)", ticker,
                 len(self._conexoes.get(ticker, set())))

    def desconectar(self, ws: WebSocket, ticker: str):
        self._conexoes.get(ticker, set()).discard(ws)

    def desconectar_todos(self):
        self._conexoes.clear()

    async def broadcast(self, ticker: str, dados: dict):
        """Envia dados para todos os clientes conectados ao ticker."""
        conexoes = list(self._conexoes.get(ticker, set()))
        mortos   = []
        payload  = json.dumps(dados, ensure_ascii=False, default=str)
        for ws in conexoes:
            try:
                await ws.send_text(payload)
            except Exception:
                mortos.append(ws)
        for ws in mortos:
            self.desconectar(ws, ticker)

    def total_conexoes(self) -> int:
        return sum(len(v) for v in self._conexoes.values())

_ws_manager = WebSocketManager()

# ─── Ciclo de vida da aplicação ───────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    inicializar()
    inicializar_token()

    try:
        from scheduler import iniciar
        iniciar()
        log.info("Scheduler iniciado junto com a API.")
    except Exception as exc:
        log.warning("Scheduler não iniciado: %s", exc)

    yield

    # Shutdown
    try:
        from scheduler import parar
        parar()
    except Exception as exc:
        log.debug("Scheduler já estava parado ou indisponível: %s", exc)

    _ws_manager.desconectar_todos()


# ─── Aplicação FastAPI ────────────────────────────────────────────────────────

app = FastAPI(
    lifespan=lifespan,
    title="Plataforma de Opções B3",
    description="API REST + WebSocket para dados de opções da B3",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8000", "http://SEU-IP:8000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

DASHBOARD_DIR = Path(__file__).parent / "dashboard"
if DASHBOARD_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(DASHBOARD_DIR)), name="static")

# ─── WebSocket endpoint ───────────────────────────────────────────────────────

@app.websocket("/ws/{ticker}")
async def ws_ticker(ws: WebSocket, ticker: str, token: str = ""):
    """
    Stream em tempo real. Token pode ser passado como query para:
      ws://localhost:8000/ws/BBAS3?token=SEU_TOKEN
    """
    token_activo = _obter_token_activo()
    if token_activo and token != token_activo:
        await ws.close(code=4401)
        return

    ticker_fmt = _normalizar_ticker(ticker)
    await _ws_manager.conectar(ws, ticker_fmt)
    try:
        while True:
            # Usar asyncio.to_thread para não bloquear o event loop
            # cotacao_atual e consultar_opcoes são síncronas (I/O de rede e banco)
            cot    = await asyncio.to_thread(cotacao_atual, ticker_fmt)
            opcoes = await asyncio.to_thread(consultar_opcoes, ticker_fmt, None, None, None, None, None, None, 50)
            payload = {
                "tipo":      "update",
                "ticker":    ticker_fmt,
                "timestamp": datetime.now(TZ).isoformat(),
                "cotacao":   cot,
                "opcoes":    [_row_to_dict(o) for o in opcoes],
                "banco_vazio": len(opcoes) == 0,
            }
            await _ws_manager.broadcast(ticker_fmt, payload)
            await asyncio.sleep(INTERVALO_SEG)
    except WebSocketDisconnect:
        _ws_manager.desconectar(ws, ticker_fmt)
        log.info("WS desconectado: %s", ticker_fmt)
    except Exception as exc:
        log.error("Erro no WebSocket [%s]: %s", ticker_fmt, exc)
        _ws_manager.desconectar(ws, ticker_fmt)
        # Tentar reconectar automaticamente após erro inesperado
        try:
            await ws.close()
        except Exception as _exc:
            log.debug("WS já fechado ou indisponível: %s", _exc)


# ─── REST endpoints ───────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def raiz():
    """Redireciona para o dashboard."""
    index = DASHBOARD_DIR / "index.html"
    if index.exists():
        return FileResponse(str(index))
    return HTMLResponse("""
        <h2>Plataforma de Opções B3</h2>
        <p><a href="/docs">Documentação da API</a></p>
        <p><a href="/redoc">ReDoc</a></p>
    """)


@app.get("/tickers", summary="Lista de tickers monitorados")
def get_tickers(_: bool = Depends(verificar_token)):
    return {"tickers": TICKERS, "total": len(TICKERS)}


@app.get("/resumo", summary="Estatísticas gerais do banco")
def get_resumo(_: bool = Depends(verificar_token)):
    return resumo_geral()


@app.get("/status", summary="Status do sistema")
def get_status(_: bool = Depends(verificar_token)):
    try:
        from scheduler import status as sched_status
        sched = sched_status()
    except Exception:
        sched = {"rodando": False, "jobs": []}
    from auth import autenticacao_configurada
    return {
        "versao":            "1.0.0",
        "timestamp":         datetime.now(TZ).isoformat(),
        "ws_clientes":       _ws_manager.total_conexoes(),
        "scheduler":         sched,
        "auth_configurada":  autenticacao_configurada(),
    }


@app.get("/cotacao-atual/{ticker}", summary="Cotação atual do ativo")
def get_cotacao_atual(ticker: str, _: bool = Depends(verificar_token)):
    ticker_fmt = _normalizar_ticker(ticker)
    return cotacao_atual(ticker_fmt)


@app.get("/cotacoes/{ticker}", summary="Histórico de cotações")
def get_cotacoes(
    ticker:      str,
    _:           bool    = Depends(verificar_token),
    de:          Optional[str] = Query(None, description="Data inicial AAAA-MM-DD"),
    ate:         Optional[str] = Query(None, description="Data final AAAA-MM-DD"),
    limite:      Optional[int] = Query(252,  description="Máximo de registros"),
):
    ticker_fmt = _normalizar_ticker(ticker)
    rows = consultar_cotacoes(ticker_fmt, de, ate, limite)
    return {
        "ticker": ticker_fmt,
        "total":  len(rows),
        "dados":  [_row_to_dict(r) for r in rows],
    }


@app.get("/snapshots/{ticker}", summary="Snapshots recentes (evolução intraday)")
def get_snapshots(
    ticker:      str,
    _:           bool    = Depends(verificar_token),
    limite:      Optional[int] = Query(100, description="Máximo de registros"),
):
    ticker_fmt = _normalizar_ticker(ticker)
    rows = consultar_snapshots(ticker_fmt, limite)
    return {
        "ticker": ticker_fmt,
        "total":  len(rows),
        "snapshots": [_row_to_dict(r) for r in rows],
    }


@app.get("/opcoes/{ticker}", summary="Cadeia de opções com Greeks")
def get_opcoes(
    ticker:     str,
    _:          bool    = Depends(verificar_token),
    tipo:       Optional[str]   = Query(None, description="CALL ou PUT"),
    vencimento: Optional[str]   = Query(None, description="Data AAAA-MM-DD"),
    strike_min: Optional[float] = Query(None),
    strike_max: Optional[float] = Query(None),
    de:         Optional[str]   = Query(None),
    ate:        Optional[str]   = Query(None),
    limite:     Optional[int]   = Query(500),
):
    ticker_fmt = _normalizar_ticker(ticker)
    rows = consultar_opcoes(
        ticker_fmt, tipo, vencimento,
        strike_min, strike_max, de, ate, limite,
    )
    return {
        "ticker":      ticker_fmt,
        "total":       len(rows),
        "opcoes":      [_row_to_dict(r) for r in rows],
    }


@app.get("/exportar/cotacoes/{ticker}", summary="Exporta cotações (csv, xlsx ou pdf)")
def get_exportar_cotacoes(
    ticker:  str,
    _:       bool = Depends(verificar_token),
    formato: str  = Query("xlsx", pattern="^(csv|xlsx|pdf)$"),
    de:      Optional[str] = Query(None),
    ate:     Optional[str] = Query(None),
):
    ticker_fmt = _normalizar_ticker(ticker)
    conteudo, _contagem = exportadores.COTACOES_BYTES[formato](ticker_fmt, de, ate)
    if conteudo is None:
        raise HTTPException(status_code=404, detail="Nenhum dado para exportar.")
    nome = exportadores._nome_exportacao("cotacoes", ticker_fmt, formato)
    return Response(
        content=conteudo,
        media_type=exportadores.MEDIA_TYPES[formato],
        headers={"Content-Disposition": f'attachment; filename="{nome}"'},
    )


@app.get("/exportar/opcoes/{ticker}", summary="Exporta cadeia de opções (csv, xlsx ou pdf)")
def get_exportar_opcoes(
    ticker:     str,
    _:          bool = Depends(verificar_token),
    formato:    str  = Query("xlsx", pattern="^(csv|xlsx|pdf)$"),
    tipo:       Optional[str] = Query(None, description="CALL ou PUT"),
    vencimento: Optional[str] = Query(None, description="Data AAAA-MM-DD"),
    de:         Optional[str] = Query(None),
    ate:        Optional[str] = Query(None),
):
    ticker_fmt = _normalizar_ticker(ticker)
    conteudo, _contagem = exportadores.OPCOES_BYTES[formato](ticker_fmt, tipo, vencimento, de, ate)
    if conteudo is None:
        raise HTTPException(status_code=404, detail="Nenhuma opção para exportar.")
    nome = exportadores._nome_exportacao("opcoes", ticker_fmt, formato)
    return Response(
        content=conteudo,
        media_type=exportadores.MEDIA_TYPES[formato],
        headers={"Content-Disposition": f'attachment; filename="{nome}"'},
    )


@app.post("/coletar/{ticker}", summary="Dispara coleta manual de um ticker")
def post_coletar(
    ticker:  str,
    periodo: str = Query("5d", description="Período histórico (1mo, 3mo, 1y, 2y, 5y, max)"),
    opcoes:  bool= Query(True, description="Coletar cadeia de opções também"),
    greeks:  bool= Query(True, description="Calcular Greeks após coleta"),
    request: Request = None,
):
    ip = (request.client.host if request and request.client else "local")
    if not _checar_rate_limit(ip):
        raise HTTPException(status_code=429,
            detail=f"Rate limit: máx {_RATE_LIMIT_REQ} coletas/{_RATE_LIMIT_JAN}s por IP")
    ticker_fmt = _normalizar_ticker(ticker)
    resultado  = {"ticker": ticker_fmt}
    resultado["historico"] = coletar_historico(ticker_fmt, periodo)
    if opcoes:
        resultado["opcoes"]  = coletar_opcoes(ticker_fmt)
    if greeks:
        resultado["greeks"]  = calcular_greeks_ticker(ticker_fmt)
    resultado["timestamp"] = datetime.now(TZ).isoformat()
    return resultado


@app.post("/coletar-todos", summary="Dispara coleta para todos os tickers")
def post_coletar_todos(
    opcoes:  bool    = Query(True),
    greeks:  bool    = Query(True),
    request: Request = None,
):
    ip = (request.client.host if request and request.client else "local")
    if not _checar_rate_limit(ip):
        from fastapi import HTTPException
        raise HTTPException(status_code=429,
            detail=f"Rate limit: máx {_RATE_LIMIT_REQ} coletas/{_RATE_LIMIT_JAN}s por IP")
    from collector import coletar_historico_todos, coletar_opcoes_todos, calcular_greeks_todos
    resultado = {}
    resultado["historico"] = coletar_historico_todos()
    if opcoes:
        resultado["opcoes"] = coletar_opcoes_todos()
    if greeks:
        resultado["greeks"] = calcular_greeks_todos()
    resultado["timestamp"] = datetime.now(TZ).isoformat()
    return resultado


@app.get("/greeks-historico/{ticker}", summary="Evolução histórica dos Greeks")
def get_greeks_historico(
    ticker:  str,
    _:       bool          = Depends(verificar_token),
    codigo:  Optional[str] = Query(None, description="Código do contrato"),
    de:      Optional[str] = Query(None),
    ate:     Optional[str] = Query(None),
    limite:  Optional[int] = Query(500),
):
    from database import consultar_greeks_historico
    ticker_fmt = _normalizar_ticker(ticker)
    rows = consultar_greeks_historico(ticker_fmt, codigo, de, ate, limite)
    return {
        "ticker":  ticker_fmt,
        "codigo":  codigo,
        "total":   len(rows),
        "historico": [_row_to_dict(r) for r in rows],
    }


@app.get("/alertas", summary="Lista alertas configurados")
def get_alertas(
    _:      bool          = Depends(verificar_token),
    ticker: Optional[str] = Query(None, description="Filtrar por ticker"),
):
    from alertas import listar_alertas
    ticker_fmt = ticker if not ticker else _normalizar_ticker(ticker)
    alertas = listar_alertas(ticker_fmt)
    return {"total": len(alertas), "alertas": alertas}


@app.post("/alertas", summary="Adiciona um novo alerta")
def post_alertas(
    ticker:       str,
    tipo:         str,
    operador:     str,
    valor:        float,
    codigo:       Optional[str] = Query(None),
    cooldown_min: float         = Query(15.0, description="Minutos entre disparos consecutivos"),
    _:            bool          = Depends(verificar_token),
):
    from alertas import adicionar_alerta
    try:
        alerta = adicionar_alerta(ticker, tipo, operador, valor, codigo, cooldown_min)
        return alerta
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/alertas/{id_alerta}", summary="Remove um alerta por ID")
def delete_alerta(
    id_alerta: int,
    _:         bool = Depends(verificar_token),
):
    from alertas import remover_alerta
    if remover_alerta(id_alerta):
        return {"removido": True, "id": id_alerta}
    raise HTTPException(status_code=404, detail=f"Alerta #{id_alerta} não encontrado.")


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _row_to_dict(row) -> dict:
    try:
        return dict(row)
    except Exception:
        return {}


def _normalizar_ticker(ticker: str) -> str:
    t = ticker.upper()
    return t if t.endswith(".SA") else f"{t}.SA"


# ─── Execução direta ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    logging.basicConfig(
        level   = logging.INFO,
        format  = "%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt = "%Y-%m-%d %H:%M:%S",
    )
    uvicorn.run("api:app", host=API_HOST, port=API_PORT, reload=False)

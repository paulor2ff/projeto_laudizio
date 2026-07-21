"""
licenca.py — Verificação de licença por token assinado (Ed25519)
====================================================================
Protege funcionalidades pagas (automação via scheduler, notificações de
alertas, etc.) com um token assinado pelo servidor de licenças. Verificação
é local — não requer rede a cada execução, apenas para renovar o token
periodicamente quando o servidor de licenças existir.

Conceito central: o cliente nunca tem a chave privada, só a pública (que
serve apenas para VERIFICAR assinaturas, nunca para criá-las). Mesmo que
alguém leia este ficheiro inteiro, não consegue forjar um token válido
sem a chave privada — que fica apenas no servidor de licenças.

Limite honesto: se esta aplicação for distribuída como código-fonte Python
puro (não compilado), nada impede alguém com conhecimento técnico de abrir
este ficheiro e remover a chamada à verificação — essa camada seria só de
boa-fé, protegendo a receita de quem esquece de renovar, não uma defesa
contra adulteração deliberada.

Distribuída como executável Nuitka (ver build/BUILD.md), a situação é
melhor — não sobra um .py editável, e o binário resultante exige
engenharia reversa real (não "abrir e editar uma linha") para ser alterado.
Ainda não é inquebrável: Nuitka compila para código nativo de verdade, mas
por padrão preserva nomes de função/variável e deixa strings/constantes
legíveis no binário (a versão paga, "Nuitka Commercial", cifra isso) — um
atacante com ferramentas de engenharia reversa e tempo suficiente ainda
pode, em princípio, localizar e neutralizar esta verificação, porque o
código que a executa roda inteiramente numa máquina que o atacante
controla. Isto não é uma fraqueza específica desta implementação — é o
limite de qualquer verificação de licença executada localmente, em
qualquer linguagem. Para proteção robusta contra isso, a alternativa é o
modelo de plataforma gerida (servidor próprio, cliente nunca recebe este
módulo).

Ciclo de vida do token:
  válido        → Now <= valido_ate                                    → estágio "ok"
  carência      → valido_ate < Now <= valido_ate + DIAS_CARENCIA        → estágio "carencia"
  degradado     → ... <= valido_ate + DIAS_CARENCIA + DIAS_DEGRADADO    → estágio "degradado"
  bloqueado     → depois disso                                          → estágio "bloqueado"

Dados já coletados pelo utilizador NUNCA são apagados ou ficam inacessíveis
em nenhum estágio — apenas funcionalidades pagas (gateadas explicitamente
via @requer_licenca) ficam indisponíveis nos estágios mais restritivos.
"""

import json
import os
import time
import base64
import functools
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, Callable

from config import (
    BASE_DIR, LICENCA_CHAVE_PUBLICA, LICENCA_DIAS_CARENCIA,
    LICENCA_DIAS_DEGRADADO, LICENCA_URL_RENOVACAO,
)

log = logging.getLogger(__name__)

LICENCA_FILE = BASE_DIR / "licenca.json"
_LOCK_FILE   = BASE_DIR / "licenca.lock"
_LOCK_TIMEOUT_SEG     = 5.0
_LOCK_RETRY_INTERVALO = 0.05

# Estágios em ordem crescente de restrição — usado para comparações de
# "mínimo exigido" no decorator requer_licenca().
ORDEM_ESTAGIOS = {
    "ok":          0,
    "carencia":    1,
    "degradado":   2,
    "bloqueado":   3,
    "sem_licenca": 4,
}


# ─── Estado ──────────────────────────────────────────────────────────────────

@dataclass
class EstadoLicenca:
    estagio:                str
    plano:                  Optional[str]      = None
    cliente_id:              Optional[str]      = None
    valido_ate:              Optional[datetime] = None
    dias_desde_vencimento:    Optional[float]    = None
    motivo:                  str                = ""

    def permite(self, minimo: str) -> bool:
        """True se este estado satisfaz o nível mínimo exigido."""
        return ORDEM_ESTAGIOS[self.estagio] <= ORDEM_ESTAGIOS[minimo]


class LicencaError(Exception):
    """Levantado por @requer_licenca quando o estágio actual é insuficiente."""
    def __init__(self, mensagem: str, estado: EstadoLicenca):
        super().__init__(mensagem)
        self.estado = estado


# ─── Lock de ficheiro (mesmo padrão de alertas.py) ────────────────────────────

class _FileLock:
    def __enter__(self):
        inicio = time.monotonic()
        while True:
            try:
                fd = os.open(str(_LOCK_FILE), os.O_CREAT | os.O_EXCL | os.O_RDWR)
                os.close(fd)
                return self
            except FileExistsError:
                if time.monotonic() - inicio > _LOCK_TIMEOUT_SEG:
                    log.warning(
                        "Lock de licenca.json preso há mais de %.1fs — forçando remoção.",
                        _LOCK_TIMEOUT_SEG
                    )
                    try: _LOCK_FILE.unlink()
                    except FileNotFoundError: pass
                    continue
                time.sleep(_LOCK_RETRY_INTERVALO)

    def __exit__(self, exc_type, exc_val, exc_tb):
        try: _LOCK_FILE.unlink()
        except FileNotFoundError: pass
        return False


def _licenca_lock() -> _FileLock:
    return _FileLock()


# ─── Verificação criptográfica ────────────────────────────────────────────────

def _mensagem_canonica(payload: dict) -> bytes:
    """
    Serialização determinística do payload para assinar/verificar.
    Chaves ordenadas e sem espaços — o servidor de licenças DEVE usar
    exactamente o mesmo formato ao assinar, ou a verificação falha.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _verificar_assinatura(payload: dict, assinatura_b64: str) -> bool:
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        from cryptography.exceptions import InvalidSignature

        chave_bytes = base64.b64decode(LICENCA_CHAVE_PUBLICA)
        chave = Ed25519PublicKey.from_public_bytes(chave_bytes)
        mensagem = _mensagem_canonica(payload)
        assinatura = base64.b64decode(assinatura_b64)
        chave.verify(assinatura, mensagem)
        return True
    except InvalidSignature:
        return False
    except Exception as exc:
        log.debug("Erro ao verificar assinatura de licença: %s", exc)
        return False


# ─── Cache em memória (evita reler/reverificar a cada chamada) ───────────────

_estado_cache: Optional[EstadoLicenca] = None
_estado_cache_em: float = 0.0
_CACHE_TTL_SEG = 60.0   # reavaliar no máximo 1x/minuto


def invalidar_cache_licenca() -> None:
    """Força a próxima verificar_licenca() a reler o ficheiro do disco."""
    global _estado_cache, _estado_cache_em
    _estado_cache = None
    _estado_cache_em = 0.0


# ─── Renovação online (stub — activa quando o servidor de licenças existir) ──

def _tentar_renovar_online() -> bool:
    """
    Busca um token actualizado no servidor de licenças, se configurado.
    Inactivo enquanto LICENCA_URL_RENOVACAO estiver vazio — o sistema usa
    apenas o token em cache até a carência expirar. Quando o servidor for
    implantado, esta função fará um POST com o cliente_id actual e
    substituirá o token em cache pelo novo (estendendo valido_ate
    automaticamente enquanto o pagamento estiver em dia).
    """
    if not LICENCA_URL_RENOVACAO:
        return False
    try:
        if not LICENCA_FILE.exists():
            return False
        dados_actuais = json.loads(LICENCA_FILE.read_text(encoding="utf-8"))
        cliente_id = dados_actuais.get("payload", {}).get("cliente_id")
        if not cliente_id:
            return False

        import requests
        resp = requests.post(
            LICENCA_URL_RENOVACAO,
            json={"cliente_id": cliente_id},
            timeout=10,
        )
        resp.raise_for_status()
        novo_token = resp.json()

        payload = novo_token.get("payload", {})
        assinatura = novo_token.get("assinatura", "")
        if not _verificar_assinatura(payload, assinatura):
            log.warning("Token renovado online tinha assinatura inválida — ignorado.")
            return False

        with _licenca_lock():
            tmp = LICENCA_FILE.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(novo_token, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(str(tmp), str(LICENCA_FILE))

        log.info("Licença renovada online — válida até %s", payload.get("valido_ate"))
        return True
    except Exception as exc:
        log.debug("Renovação online indisponível: %s", exc)
        return False


# ─── Verificação principal ────────────────────────────────────────────────────

def verificar_licenca(forcar_reverificacao: bool = False) -> EstadoLicenca:
    """
    Devolve o estado actual da licença. Resultado é cacheado em memória
    por _CACHE_TTL_SEG segundos para evitar I/O de disco repetido quando
    chamado com frequência (ex: dentro de um decorator aplicado a um
    job que corre a cada 30s).
    """
    global _estado_cache, _estado_cache_em

    if not forcar_reverificacao and _estado_cache is not None:
        if time.monotonic() - _estado_cache_em < _CACHE_TTL_SEG:
            return _estado_cache

    if not LICENCA_FILE.exists():
        estado = EstadoLicenca(estagio="sem_licenca", motivo="Nenhuma licença instalada")
        _estado_cache, _estado_cache_em = estado, time.monotonic()
        return estado

    try:
        with _licenca_lock():
            dados = json.loads(LICENCA_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        estado = EstadoLicenca(estagio="sem_licenca", motivo=f"Licença ilegível: {exc}")
        _estado_cache, _estado_cache_em = estado, time.monotonic()
        return estado

    payload    = dados.get("payload", {})
    assinatura = dados.get("assinatura", "")

    if not _verificar_assinatura(payload, assinatura):
        log.warning("Assinatura de licença inválida — possível adulteração ou corrupção.")
        estado = EstadoLicenca(estagio="sem_licenca", motivo="Assinatura inválida")
        _estado_cache, _estado_cache_em = estado, time.monotonic()
        return estado

    try:
        valido_ate = datetime.fromisoformat(payload["valido_ate"])
    except (KeyError, ValueError) as exc:
        estado = EstadoLicenca(estagio="sem_licenca", motivo=f"Campo valido_ate inválido: {exc}")
        _estado_cache, _estado_cache_em = estado, time.monotonic()
        return estado

    # Tentar renovação online antes de avaliar o estágio — se configurada e
    # bem-sucedida, reler o token (agora possivelmente actualizado).
    if LICENCA_URL_RENOVACAO and _tentar_renovar_online():
        dados = json.loads(LICENCA_FILE.read_text(encoding="utf-8"))
        payload = dados.get("payload", {})
        valido_ate = datetime.fromisoformat(payload["valido_ate"])

    agora = datetime.now()
    dias_desde_vencimento = (agora - valido_ate).total_seconds() / 86400.0

    if dias_desde_vencimento <= 0:
        estagio = "ok"
        motivo  = "Licença activa"
    elif dias_desde_vencimento <= LICENCA_DIAS_CARENCIA:
        restante = LICENCA_DIAS_CARENCIA - dias_desde_vencimento
        estagio  = "carencia"
        motivo   = f"Em carência — renovar nos próximos {restante:.1f} dia(s)"
    elif dias_desde_vencimento <= LICENCA_DIAS_CARENCIA + LICENCA_DIAS_DEGRADADO:
        estagio = "degradado"
        motivo  = "Funcionalidades pagas desactivadas — renovar para reactivar"
    else:
        estagio = "bloqueado"
        motivo  = "Licença vencida há mais de 30 dias — renovação necessária"

    estado = EstadoLicenca(
        estagio=estagio,
        plano=payload.get("plano"),
        cliente_id=payload.get("cliente_id"),
        valido_ate=valido_ate,
        dias_desde_vencimento=round(dias_desde_vencimento, 2),
        motivo=motivo,
    )
    _estado_cache, _estado_cache_em = estado, time.monotonic()
    return estado


# ─── Importação de um token recebido após pagamento ──────────────────────────

def importar_licenca(caminho_arquivo: str) -> EstadoLicenca:
    """
    Importa um ficheiro de token de licença (recebido do servidor de
    licenças após confirmação de pagamento) para o cache local. Valida a
    assinatura antes de aceitar — um ficheiro adulterado é rejeitado.
    """
    origem = Path(caminho_arquivo)
    if not origem.exists():
        raise FileNotFoundError(f"Ficheiro de licença não encontrado: {caminho_arquivo}")

    dados = json.loads(origem.read_text(encoding="utf-8"))
    payload    = dados.get("payload", {})
    assinatura = dados.get("assinatura", "")

    if not _verificar_assinatura(payload, assinatura):
        raise ValueError("Assinatura inválida — ficheiro de licença corrompido ou adulterado")

    if "valido_ate" not in payload:
        raise ValueError("Token de licença não contém o campo 'valido_ate'")

    with _licenca_lock():
        tmp = LICENCA_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(str(tmp), str(LICENCA_FILE))

    invalidar_cache_licenca()
    log.info(
        "Licença importada: cliente=%s plano=%s válido até=%s",
        payload.get("cliente_id"), payload.get("plano"), payload.get("valido_ate"),
    )
    return verificar_licenca(forcar_reverificacao=True)


# ─── Decorator de gating ───────────────────────────────────────────────────────

def requer_licenca(minimo: str = "degradado"):
    """
    Decorator que impede a execução da função decorada se o estágio actual
    da licença for mais restritivo que `minimo`. Levanta LicencaError com
    o estado completo anexado, permitindo ao chamador decidir como reagir
    (mostrar aviso no dashboard, bloquear silenciosamente, etc.).

    Uso:
        @requer_licenca(minimo="degradado")
        def iniciar_automacao():
            ...
    """
    if minimo not in ORDEM_ESTAGIOS:
        raise ValueError(f"Nível mínimo inválido: '{minimo}'. Use: {list(ORDEM_ESTAGIOS)}")

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            estado = verificar_licenca()
            if not estado.permite(minimo):
                msg = (
                    f"'{func.__name__}' requer licença nível '{minimo}' ou superior "
                    f"(estado actual: '{estado.estagio}' — {estado.motivo})"
                )
                log.warning(msg)
                raise LicencaError(msg, estado)
            return func(*args, **kwargs)
        return wrapper
    return decorator

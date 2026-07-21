"""
admin.py — Autenticação dos endpoints administrativos
==========================================================
Token separado do que os clientes usam — protege operações sensíveis
(emissão manual, revogação, consulta de todos os clientes). Nunca deve
ser o mesmo token distribuído a clientes.
"""

import logging
import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from config import ADMIN_TOKEN, BASE_DIR

log = logging.getLogger(__name__)

_security = HTTPBearer(auto_error=False)
_TOKEN_FILE = BASE_DIR / "admin_token.txt"


def _obter_token_activo() -> str:
    if ADMIN_TOKEN:
        return ADMIN_TOKEN
    if _TOKEN_FILE.exists():
        return _TOKEN_FILE.read_text().strip()
    return ""


def inicializar_admin_token() -> str:
    t = _obter_token_activo()
    if t:
        return t
    novo = secrets.token_urlsafe(32)
    _TOKEN_FILE.write_text(novo)
    log.warning("=" * 60)
    log.warning("TOKEN DE ADMIN GERADO: %s", novo)
    log.warning("Guarde-o com segurança — dá acesso total à base de clientes.")
    log.warning("Ficheiro: %s", _TOKEN_FILE.resolve())
    log.warning("=" * 60)
    return novo


def verificar_admin(
    credentials: HTTPAuthorizationCredentials = Depends(_security),
) -> bool:
    token_activo = _obter_token_activo()
    if not token_activo:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Servidor sem token de admin configurado.",
        )
    if credentials and credentials.credentials == token_activo:
        return True
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Token de admin inválido ou ausente.",
        headers={"WWW-Authenticate": "Bearer"},
    )

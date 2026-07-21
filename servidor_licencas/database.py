"""
database.py — Banco de dados do servidor de licenças
=========================================================
SQLite, consistente com o resto do ecossistema do projecto. Para o volume
esperado (dezenas a poucas centenas de clientes), é suficiente — migrar
para PostgreSQL só se a escala justificar.
"""

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Optional

from config import DB_PATH

log = logging.getLogger(__name__)


@contextmanager
def conexao():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def inicializar() -> None:
    with conexao() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS clientes (
                id                      TEXT PRIMARY KEY,
                email                   TEXT UNIQUE,
                nome                    TEXT,
                plano                   TEXT NOT NULL,
                status                  TEXT NOT NULL DEFAULT 'activo',
                valido_ate              TEXT NOT NULL,
                stripe_customer_id      TEXT,
                stripe_subscription_id  TEXT,
                mp_subscription_id      TEXT,
                criado_em               TEXT NOT NULL,
                atualizado_em           TEXT NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_clientes_email ON clientes(email)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_clientes_stripe_sub ON clientes(stripe_subscription_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_clientes_mp_sub ON clientes(mp_subscription_id)")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS eventos_pagamento (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                cliente_id      TEXT,
                tipo            TEXT NOT NULL,
                fonte           TEXT NOT NULL,
                payload_bruto   TEXT,
                processado_em   TEXT NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_eventos_cliente ON eventos_pagamento(cliente_id)")
    log.info("Banco de dados do servidor de licenças inicializado: %s", DB_PATH)


def upsert_cliente(
    cliente_id: str,
    email: Optional[str],
    nome: Optional[str],
    plano: str,
    valido_ate: str,
    status: str = "activo",
    stripe_customer_id: Optional[str] = None,
    stripe_subscription_id: Optional[str] = None,
    mp_subscription_id: Optional[str] = None,
) -> dict:
    agora = datetime.now().isoformat(timespec="seconds")
    with conexao() as conn:
        existente = conn.execute(
            "SELECT * FROM clientes WHERE id = ?", (cliente_id,)
        ).fetchone()
        if existente:
            conn.execute("""
                UPDATE clientes SET
                    email = COALESCE(?, email),
                    nome = COALESCE(?, nome),
                    plano = ?,
                    status = ?,
                    valido_ate = ?,
                    stripe_customer_id = COALESCE(?, stripe_customer_id),
                    stripe_subscription_id = COALESCE(?, stripe_subscription_id),
                    mp_subscription_id = COALESCE(?, mp_subscription_id),
                    atualizado_em = ?
                WHERE id = ?
            """, (email, nome, plano, status, valido_ate,
                  stripe_customer_id, stripe_subscription_id, mp_subscription_id,
                  agora, cliente_id))
        else:
            conn.execute("""
                INSERT INTO clientes
                    (id, email, nome, plano, status, valido_ate,
                     stripe_customer_id, stripe_subscription_id, mp_subscription_id,
                     criado_em, atualizado_em)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """, (cliente_id, email, nome, plano, status, valido_ate,
                  stripe_customer_id, stripe_subscription_id, mp_subscription_id,
                  agora, agora))
    return obter_cliente(cliente_id)


def obter_cliente(cliente_id: str) -> Optional[dict]:
    with conexao() as conn:
        row = conn.execute("SELECT * FROM clientes WHERE id = ?", (cliente_id,)).fetchone()
        return dict(row) if row else None


def obter_cliente_por_stripe_subscription(subscription_id: str) -> Optional[dict]:
    with conexao() as conn:
        row = conn.execute(
            "SELECT * FROM clientes WHERE stripe_subscription_id = ?", (subscription_id,)
        ).fetchone()
        return dict(row) if row else None


def obter_cliente_por_mp_subscription(subscription_id: str) -> Optional[dict]:
    with conexao() as conn:
        row = conn.execute(
            "SELECT * FROM clientes WHERE mp_subscription_id = ?", (subscription_id,)
        ).fetchone()
        return dict(row) if row else None


def listar_clientes(status: Optional[str] = None) -> list:
    with conexao() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM clientes WHERE status = ? ORDER BY criado_em DESC", (status,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM clientes ORDER BY criado_em DESC").fetchall()
        return [dict(r) for r in rows]


def actualizar_status(cliente_id: str, status: str) -> bool:
    with conexao() as conn:
        cur = conn.execute(
            "UPDATE clientes SET status = ?, atualizado_em = ? WHERE id = ?",
            (status, datetime.now().isoformat(timespec="seconds"), cliente_id),
        )
        return cur.rowcount > 0


def registar_evento(
    cliente_id: Optional[str], tipo: str, fonte: str, payload_bruto: dict
) -> None:
    with conexao() as conn:
        conn.execute("""
            INSERT INTO eventos_pagamento (cliente_id, tipo, fonte, payload_bruto, processado_em)
            VALUES (?,?,?,?,?)
        """, (
            cliente_id, tipo, fonte,
            json.dumps(payload_bruto, ensure_ascii=False),
            datetime.now().isoformat(timespec="seconds"),
        ))


def listar_eventos(cliente_id: Optional[str] = None, limite: int = 100) -> list:
    with conexao() as conn:
        if cliente_id:
            rows = conn.execute(
                "SELECT * FROM eventos_pagamento WHERE cliente_id = ? "
                "ORDER BY processado_em DESC LIMIT ?",
                (cliente_id, limite),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM eventos_pagamento ORDER BY processado_em DESC LIMIT ?",
                (limite,),
            ).fetchall()
        return [dict(r) for r in rows]

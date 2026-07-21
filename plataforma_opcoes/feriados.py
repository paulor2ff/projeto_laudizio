"""
feriados.py — Calendário de feriados da B3
============================================
Calcula feriados nacionais brasileiros (fixos e móveis baseados na Páscoa)
sem depender de API externa ou ficheiro mantido manualmente — funciona
para qualquer ano automaticamente.

Feriados fixos: Ano Novo, Tiradentes, Dia do Trabalho, Independência,
N. Sra. Aparecida, Finados, Proclamação da República, Consciência Negra
(nacional desde 2024), Natal.

Feriados móveis (calculados a partir da Páscoa via algoritmo de
Meeus/Jones/Butcher): Carnaval (segunda e terça), Sexta-feira Santa,
Corpus Christi — todos observados pela B3 como pregão fechado.

Não inclui pontos facultativos nem véspera de Natal/Ano Novo (B3 tem
pregão normal ou encurtado nesses dias, não fechamento total).
"""

from datetime import date, timedelta
from functools import lru_cache


def calcular_pascoa(ano: int) -> date:
    """
    Calcula a data da Páscoa (domingo) para o ano informado.
    Algoritmo de Meeus/Jones/Butcher (Gregoriano) — válido para qualquer
    ano no calendário Gregoriano, sem necessidade de tabela ou API externa.
    """
    a = ano % 19
    b = ano // 100
    c = ano % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mes = (h + l - 7 * m + 114) // 31
    dia = ((h + l - 7 * m + 114) % 31) + 1
    return date(ano, mes, dia)


@lru_cache(maxsize=32)
def feriados_b3(ano: int) -> frozenset:
    """
    Devolve o conjunto de datas de feriados da B3 para o ano informado.
    Resultado em cache (lru_cache) — o cálculo só ocorre uma vez por ano.
    """
    pascoa = calcular_pascoa(ano)

    fixos = {
        date(ano, 1, 1),    # Ano Novo
        date(ano, 4, 21),   # Tiradentes
        date(ano, 5, 1),    # Dia do Trabalho
        date(ano, 9, 7),    # Independência
        date(ano, 10, 12),  # N. Sra. Aparecida
        date(ano, 11, 2),   # Finados
        date(ano, 11, 15),  # Proclamação da República
        date(ano, 11, 20),  # Consciência Negra (feriado nacional desde 2024)
        date(ano, 12, 25),  # Natal
    }

    moveis = {
        pascoa - timedelta(days=48),  # Carnaval — segunda-feira
        pascoa - timedelta(days=47),  # Carnaval — terça-feira
        pascoa - timedelta(days=2),   # Sexta-feira Santa
        pascoa + timedelta(days=60),  # Corpus Christi
    }

    return frozenset(fixos | moveis)


def eh_feriado_b3(d: date) -> bool:
    """Verifica se a data informada é feriado de B3 (pregão fechado)."""
    return d in feriados_b3(d.year)


def proximo_pregao(d: date) -> date:
    """
    Devolve a próxima data de pregão a partir de 'd' (inclusive),
    pulando fins de semana e feriados da B3.
    """
    atual = d
    while atual.weekday() >= 5 or eh_feriado_b3(atual):
        atual += timedelta(days=1)
    return atual

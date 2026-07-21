"""Testes para feriados.py — cálculo da Páscoa e calendário de feriados B3."""

import pytest
from datetime import date

from feriados import calcular_pascoa, feriados_b3, eh_feriado_b3, proximo_pregao


class TestCalcularPascoa:
    @pytest.mark.parametrize("ano,esperado", [
        (2023, date(2023, 4, 9)),
        (2024, date(2024, 3, 31)),
        (2025, date(2025, 4, 20)),
        (2026, date(2026, 4, 5)),
    ])
    def test_datas_conhecidas(self, ano, esperado):
        assert calcular_pascoa(ano) == esperado


class TestFeriadosFixos:
    @pytest.mark.parametrize("mes,dia", [
        (1, 1), (4, 21), (5, 1), (9, 7), (10, 12),
        (11, 2), (11, 15), (11, 20), (12, 25),
    ])
    def test_feriados_fixos_presentes(self, mes, dia):
        assert date(2026, mes, dia) in feriados_b3(2026)


class TestFeriadosMoveis:
    def test_carnaval_segunda_e_terca(self):
        # Páscoa 2026 = 5 de abril
        fer = feriados_b3(2026)
        assert date(2026, 2, 16) in fer  # segunda de carnaval
        assert date(2026, 2, 17) in fer  # terça de carnaval

    def test_sexta_feira_santa(self):
        assert date(2026, 4, 3) in feriados_b3(2026)

    def test_corpus_christi(self):
        assert date(2026, 6, 4) in feriados_b3(2026)


class TestDiasComuns:
    @pytest.mark.parametrize("d", [
        date(2026, 6, 17), date(2026, 3, 10), date(2026, 8, 5),
    ])
    def test_dia_comum_nao_e_feriado(self, d):
        assert not eh_feriado_b3(d)


class TestProximoPregao:
    def test_pula_fim_de_semana_e_feriado(self):
        prox = proximo_pregao(date(2026, 12, 25))  # Natal
        assert prox.weekday() < 5
        assert not eh_feriado_b3(prox)

    def test_dia_comum_devolve_o_mesmo_dia(self):
        comum = date(2026, 6, 17)
        assert proximo_pregao(comum) == comum

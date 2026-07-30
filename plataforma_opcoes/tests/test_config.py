"""
Testes para config.py — focado em BASE_DIR, a única lógica não-trivial
deste módulo (o resto são apenas constantes). Ver o comentário em
config.py e o relatório de julho/2026 para o porquê: sys.executable
resolve para um caminho efémero (muda a cada execução) num binário Nuitka
--onefile, o que quebrava silenciosamente a persistência do banco de
dados — confirmado compilando de facto o projeto, não só por leitura.
"""

import importlib
import sys
from pathlib import Path


class TestBaseDir:
    def test_nao_compilado_usa_file(self):
        import config
        assert config.BASE_DIR == Path(config.__file__).resolve().parent

    def test_compilado_usa_argv0_nao_sys_executable(self, monkeypatch, tmp_path):
        """
        Simula o cenário compilado: __compiled__ é injectado no namespace
        do módulo ANTES do reload, para que 'IS_COMPILED = "__compiled__"
        in globals()' veja a branch certa ao re-executar. sys.argv[0] deve
        ser usado — não sys.executable, que aponta para a pasta de extração
        temporária efémera do Nuitka --onefile e mudaria a cada execução
        (era exatamente o bug original).
        """
        import config
        exe_falso = tmp_path / "PlataformaOpcoesB3.exe"
        exe_falso.touch()
        monkeypatch.setattr(sys, "argv", [str(exe_falso)])
        monkeypatch.setattr(sys, "executable",
                             "/tmp/onefile_1234_hash/python")  # o caminho errado/efémero
        config.__dict__["__compiled__"] = True
        try:
            importlib.reload(config)
            assert config.IS_COMPILED is True
            assert config.BASE_DIR == tmp_path
            assert config.DB_PATH == tmp_path / "opcoes_b3.db"
        finally:
            del config.__dict__["__compiled__"]
            importlib.reload(config)  # restaura o estado normal para os próximos testes
            assert config.IS_COMPILED is False

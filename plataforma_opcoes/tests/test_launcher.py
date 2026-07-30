"""
Testes para launcher.py — o ponto de entrada automático (sem menu, sem
flags) para o utilizador final. launcher.py só usa stdlib de propósito
(precisa correr antes de qualquer dependência estar instalada), então
estes testes não dependem de db_temp/conftest — só mocks e sockets reais
(a espera de porta é testada com um socket de verdade, não simulado).
"""

import socket
import sys
import threading
import time

import pytest

import launcher


class TestEncontrarExecutavelCompilado:
    def test_nenhum_presente_devolve_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(launcher, "BASE_DIR", tmp_path)
        assert launcher._encontrar_executavel_compilado() is None

    def test_encontra_em_dist(self, tmp_path, monkeypatch):
        monkeypatch.setattr(launcher, "BASE_DIR", tmp_path)
        (tmp_path / "dist").mkdir()
        alvo = tmp_path / "dist" / launcher.NOME_EXECUTAVEL_COMPILADO
        alvo.write_text("fake exe")
        assert launcher._encontrar_executavel_compilado() == alvo

    def test_encontra_ao_lado_do_launcher(self, tmp_path, monkeypatch):
        monkeypatch.setattr(launcher, "BASE_DIR", tmp_path)
        alvo = tmp_path / launcher.NOME_EXECUTAVEL_COMPILADO
        alvo.write_text("fake exe")
        assert launcher._encontrar_executavel_compilado() == alvo

    def test_prioriza_dist_sobre_ao_lado(self, tmp_path, monkeypatch):
        monkeypatch.setattr(launcher, "BASE_DIR", tmp_path)
        (tmp_path / "dist").mkdir()
        em_dist = tmp_path / "dist" / launcher.NOME_EXECUTAVEL_COMPILADO
        em_dist.write_text("fake exe dist")
        (tmp_path / launcher.NOME_EXECUTAVEL_COMPILADO).write_text("fake exe ao lado")
        assert launcher._encontrar_executavel_compilado() == em_dist


class TestCaminhoPythonVenv:
    def test_windows(self, monkeypatch, tmp_path):
        monkeypatch.setattr(launcher.sys, "platform", "win32")
        assert launcher._caminho_python_venv(tmp_path) == tmp_path / "Scripts" / "python.exe"

    def test_linux_ou_mac(self, monkeypatch, tmp_path):
        monkeypatch.setattr(launcher.sys, "platform", "linux")
        assert launcher._caminho_python_venv(tmp_path) == tmp_path / "bin" / "python"


class TestPortaEmUso:
    def test_porta_livre_devolve_false(self):
        assert launcher._porta_em_uso("127.0.0.1", 58234) is False

    def test_porta_ocupada_devolve_true(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        porta = srv.getsockname()[1]
        srv.listen(1)
        try:
            assert launcher._porta_em_uso("127.0.0.1", porta) is True
        finally:
            srv.close()


class TestAguardarServidor:
    def test_servidor_ja_no_ar_retorna_rapido(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        porta = srv.getsockname()[1]
        srv.listen(1)
        try:
            assert launcher._aguardar_servidor("127.0.0.1", porta, timeout=2) is True
        finally:
            srv.close()

    def test_processo_morre_antes_da_porta_abrir(self):
        class ProcessoFalso:
            def poll(self):
                return 1  # já terminou

        assert launcher._aguardar_servidor(
            "127.0.0.1", 58235, processo=ProcessoFalso(), timeout=2, intervalo=0.1
        ) is False

    def test_timeout_sem_porta_nem_processo(self):
        assert launcher._aguardar_servidor(
            "127.0.0.1", 58236, timeout=0.6, intervalo=0.2
        ) is False

    def test_porta_abre_durante_a_espera(self):
        """Simula um servidor que demora um pouco para começar a escutar."""
        srv_temp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv_temp.bind(("127.0.0.1", 0))
        porta = srv_temp.getsockname()[1]
        srv_temp.close()

        def abrir_depois():
            time.sleep(0.4)
            s2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s2.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s2.bind(("127.0.0.1", porta))
            s2.listen(1)
            time.sleep(1.5)
            s2.close()

        t = threading.Thread(target=abrir_depois, daemon=True)
        t.start()
        assert launcher._aguardar_servidor("127.0.0.1", porta, timeout=3, intervalo=0.1) is True


class TestPythonDoSistema:
    def test_nao_compilado_usa_sys_executable(self, monkeypatch):
        """Rodando como script normal (não compilado), sys.executable é confiável."""
        assert launcher.IS_COMPILED is False
        assert launcher._python_do_sistema() == sys.executable

    def test_compilado_usa_shutil_which(self, monkeypatch):
        """
        Simula o cenário compilado (Nuitka onefile): sys.executable não
        serve (aponta para dentro da pasta temporária de extração — bug
        real encontrado ao compilar de facto este ficheiro), então precisa
        cair para shutil.which() em busca de um Python real do sistema.
        """
        monkeypatch.setattr(launcher, "IS_COMPILED", True)
        monkeypatch.setattr(launcher.shutil, "which",
                             lambda nome: "/usr/bin/python3" if nome == "python3" else None)
        assert launcher._python_do_sistema() == "/usr/bin/python3"

    def test_compilado_sem_python_no_sistema_sai_com_erro(self, monkeypatch, capsys):
        monkeypatch.setattr(launcher, "IS_COMPILED", True)
        monkeypatch.setattr(launcher.shutil, "which", lambda nome: None)
        with pytest.raises(SystemExit):
            launcher._python_do_sistema()
        assert "não foi encontrado" in capsys.readouterr().out.lower()


class TestPrepararAmbienteFonte:
    def test_venv_ja_existe_pula_criacao(self, tmp_path, monkeypatch):
        monkeypatch.setattr(launcher, "BASE_DIR", tmp_path)
        python_venv = launcher._caminho_python_venv(tmp_path / ".venv")
        python_venv.parent.mkdir(parents=True)
        python_venv.write_text("fake")

        chamadas = []

        def _fake_run(cmd, **kwargs):
            chamadas.append(cmd)
            class R:
                returncode = 0
            return R()

        monkeypatch.setattr(launcher.subprocess, "run", _fake_run)
        resultado = launcher._preparar_ambiente_fonte()
        assert resultado == python_venv
        assert len(chamadas) == 1          # só o pip install — o venv já existia
        assert "pip" in chamadas[0]

    def test_venv_nao_existe_cria_e_instala(self, tmp_path, monkeypatch):
        monkeypatch.setattr(launcher, "BASE_DIR", tmp_path)
        (tmp_path / "requirements.txt").write_text("pytest")
        chamadas = []

        def _fake_run(cmd, **kwargs):
            chamadas.append(cmd)
            if "venv" in cmd:
                python_venv = launcher._caminho_python_venv(tmp_path / ".venv")
                python_venv.parent.mkdir(parents=True, exist_ok=True)
                python_venv.write_text("fake")
            class R:
                returncode = 0
            return R()

        monkeypatch.setattr(launcher.subprocess, "run", _fake_run)
        launcher._preparar_ambiente_fonte()
        assert len(chamadas) == 2          # cria venv + instala dependências

    def test_falha_no_pip_install_sai_com_erro(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(launcher, "BASE_DIR", tmp_path)
        python_venv = launcher._caminho_python_venv(tmp_path / ".venv")
        python_venv.parent.mkdir(parents=True)
        python_venv.write_text("fake")

        def _fake_run(cmd, **kwargs):
            class R:
                returncode = 1
            return R()

        monkeypatch.setattr(launcher.subprocess, "run", _fake_run)
        with pytest.raises(SystemExit):
            launcher._preparar_ambiente_fonte()
        assert "internet" in capsys.readouterr().out.lower()


class TestMontarComandoBase:
    def test_usa_executavel_compilado_quando_existe(self, tmp_path, monkeypatch):
        monkeypatch.setattr(launcher, "BASE_DIR", tmp_path)
        exe = tmp_path / launcher.NOME_EXECUTAVEL_COMPILADO
        exe.write_text("fake")
        assert launcher._montar_comando_base() == [str(exe)]

    def test_usa_venv_quando_nao_ha_compilado(self, tmp_path, monkeypatch):
        monkeypatch.setattr(launcher, "BASE_DIR", tmp_path)
        monkeypatch.setattr(launcher, "_preparar_ambiente_fonte",
                             lambda: tmp_path / "fake_python")
        comando = launcher._montar_comando_base()
        assert comando == [str(tmp_path / "fake_python"), str(tmp_path / "cli.py")]


class TestMain:
    def test_porta_ja_em_uso_so_abre_navegador(self, monkeypatch):
        monkeypatch.setattr(launcher, "_porta_em_uso", lambda h, p: True)
        chamadas = {}
        monkeypatch.setattr(launcher.webbrowser, "open",
                             lambda url: chamadas.setdefault("url", url))
        assert launcher.main() == 0
        assert chamadas["url"] == f"http://{launcher.HOST}:{launcher.PORT}"

    def test_fluxo_completo_com_sucesso(self, monkeypatch):
        monkeypatch.setattr(launcher, "_porta_em_uso", lambda h, p: False)
        monkeypatch.setattr(launcher, "_montar_comando_base", lambda: ["python", "cli.py"])
        monkeypatch.setattr(launcher, "_executar_coleta_inicial", lambda comando: None)
        monkeypatch.setattr(launcher, "_aguardar_servidor", lambda *a, **kw: True)

        class ProcessoFalso:
            def wait(self, timeout=None):
                pass
            def poll(self):
                return None
            def terminate(self):
                pass

        monkeypatch.setattr(launcher.subprocess, "Popen", lambda *a, **kw: ProcessoFalso())
        abertos = {}
        monkeypatch.setattr(launcher.webbrowser, "open",
                             lambda url: abertos.setdefault("url", url))

        assert launcher.main() == 0
        assert abertos["url"] == f"http://{launcher.HOST}:{launcher.PORT}"

    def test_servidor_nao_sobe_retorna_erro_e_encerra_processo(self, monkeypatch, capsys):
        monkeypatch.setattr(launcher, "_porta_em_uso", lambda h, p: False)
        monkeypatch.setattr(launcher, "_montar_comando_base", lambda: ["python", "cli.py"])
        monkeypatch.setattr(launcher, "_executar_coleta_inicial", lambda comando: None)
        monkeypatch.setattr(launcher, "_aguardar_servidor", lambda *a, **kw: False)

        eventos = []
        class ProcessoFalso:
            def poll(self):
                return None
            def terminate(self):
                eventos.append("terminate")

        monkeypatch.setattr(launcher.subprocess, "Popen", lambda *a, **kw: ProcessoFalso())
        assert launcher.main() == 1
        assert "não respondeu a tempo" in capsys.readouterr().out
        assert "terminate" in eventos

    def test_ctrl_c_encerra_processo_com_calma(self, monkeypatch):
        monkeypatch.setattr(launcher, "_porta_em_uso", lambda h, p: False)
        monkeypatch.setattr(launcher, "_montar_comando_base", lambda: ["python", "cli.py"])
        monkeypatch.setattr(launcher, "_executar_coleta_inicial", lambda comando: None)
        monkeypatch.setattr(launcher, "_aguardar_servidor", lambda *a, **kw: True)
        monkeypatch.setattr(launcher.webbrowser, "open", lambda url: None)

        eventos = []
        class ProcessoFalso:
            def wait(self, timeout=None):
                if timeout is None:
                    eventos.append("wait_bloqueante")
                    raise KeyboardInterrupt
                eventos.append("wait_com_timeout")
            def poll(self):
                return None
            def terminate(self):
                eventos.append("terminate")

        monkeypatch.setattr(launcher.subprocess, "Popen", lambda *a, **kw: ProcessoFalso())
        assert launcher.main() == 0
        assert eventos == ["wait_bloqueante", "terminate", "wait_com_timeout"]

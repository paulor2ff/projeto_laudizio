"""
launcher.py — Ponto de entrada único e totalmente automático.

Sem menus, sem flags para decorar: dá duplo-clique (ou corre
`python launcher.py` / o `.exe` compilado a partir deste ficheiro) e o
programa, sozinho:

  1. Confere se já existe um executável compilado ao lado (dist/
     PlataformaOpcoesB3) — se sim, usa-o directamente, sem instalar nada.
  2. Caso contrário (a correr a partir do código-fonte), prepara um
     ambiente virtual (.venv) e instala requirements.txt automaticamente.
  3. Coleta os dados iniciais (histórico, opções e Greeks) para todos os
     tickers configurados, para o dashboard não abrir vazio.
  4. Inicia o dashboard e abre o navegador em http://localhost:8000.
  5. Fica à escuta até a janela ser fechada ou Ctrl+C, e encerra tudo de
     forma limpa (não deixa o servidor "órfão" rodando).

IMPORTANTE — este ficheiro só pode usar a biblioteca padrão do Python.
É a própria razão de ele existir: precisa correr ANTES de qualquer
dependência (fastapi, pandas...) estar instalada, e precisa continuar
trivial e rápido de compilar com Nuitka (ver build/BUILD.md) — um
executável só com stdlib compila em segundos, não em minutos.
"""

from __future__ import annotations

import socket
import shutil
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from typing import Optional

# Mesma cautela do config.py com Nuitka — e a mesma correção real, validada
# compilando de facto este ficheiro (ver relatório): em modo --onefile,
# __file__ aponta para uma pasta temporária recriada a cada execução, e
# sys.executable NÃO é uma alternativa válida — aponta para dentro dessa
# MESMA pasta temporária efémera, mudando a cada execução. sys.argv[0] é o
# que o bootstrap onefile do Nuitka resolve para o caminho real e estável
# do executável.
IS_COMPILED = "__compiled__" in globals()

if IS_COMPILED:
    BASE_DIR = Path(sys.argv[0]).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent

HOST = "127.0.0.1"
PORT = 8000
NOME_EXECUTAVEL_COMPILADO = (
    "PlataformaOpcoesB3.exe" if sys.platform == "win32" else "PlataformaOpcoesB3"
)


def _linha(char: str = "─", tam: int = 64) -> None:
    print(char * tam)


def _banner() -> None:
    _linha("═")
    print("  📈  Plataforma de Opções B3 — Iniciando")
    _linha("═")
    print()


def _encontrar_executavel_compilado() -> Optional[Path]:
    """Procura um build Nuitka (build/BUILD.md) já pronto ao lado deste launcher."""
    candidatos = [
        BASE_DIR / "dist" / NOME_EXECUTAVEL_COMPILADO,
        BASE_DIR / NOME_EXECUTAVEL_COMPILADO,
    ]
    for candidato in candidatos:
        if candidato.exists():
            return candidato
    return None


def _caminho_python_venv(venv_dir: Path) -> Path:
    if sys.platform == "win32":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def _python_do_sistema() -> str:
    """
    Devolve um interpretador Python real do sistema, para criar o venv.

    sys.executable NÃO serve para isto quando o próprio launcher está
    compilado (Nuitka --onefile): dentro do binário compilado, ele aponta
    para um caminho dentro da pasta temporária de extração do onefile
    (ex.: /tmp/onefile_XXXX/python), que não é um Python executável de
    verdade — só existe enquanto o processo compilado está a correr.
    Descoberto ao compilar e testar de facto este ficheiro (ver relatório).
    """
    if not IS_COMPILED:
        return sys.executable
    for candidato in ("python3", "python"):
        encontrado = shutil.which(candidato)
        if encontrado:
            return encontrado
    print("❌ Não foi encontrado um Python instalado neste computador.")
    print("   Instale o Python 3.11+ (https://python.org) e tente novamente,")
    print("   ou use a versão compilada completa da plataforma (não precisa de Python).")
    sys.exit(1)


def _preparar_ambiente_fonte() -> Path:
    """
    Sem executável compilado por perto: garante um venv local com as
    dependências de requirements.txt instaladas e devolve o caminho do
    python desse venv. Idempotente — seguro chamar em toda execução.
    """
    venv_dir = BASE_DIR / ".venv"
    python_venv = _caminho_python_venv(venv_dir)

    if not python_venv.exists():
        print("🔧 Preparando ambiente (primeira execução, só acontece uma vez)...")
        subprocess.run([_python_do_sistema(), "-m", "venv", str(venv_dir)], check=True)

    print("📦 Verificando dependências (pode levar alguns minutos na primeira vez)...")
    resultado = subprocess.run([
        str(python_venv), "-m", "pip", "install", "-q",
        "-r", str(BASE_DIR / "requirements.txt"),
    ])
    if resultado.returncode != 0:
        print()
        print("❌ Não foi possível instalar as dependências automaticamente.")
        print("   Verifique sua conexão com a internet e tente novamente.")
        print("   Se o problema persistir, entre em contato com o suporte.")
        sys.exit(1)

    return python_venv


def _montar_comando_base() -> list[str]:
    """Devolve o comando a usar para chamar a plataforma — exe compilado ou
    'python cli.py' dentro do venv, dependendo do que já existe."""
    exe = _encontrar_executavel_compilado()
    if exe is not None:
        print(f"✅ Executável compilado encontrado: {exe.name} (nenhuma instalação necessária)")
        return [str(exe)]
    python_venv = _preparar_ambiente_fonte()
    return [str(python_venv), str(BASE_DIR / "cli.py")]


def _porta_em_uso(host: str, porta: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, porta)) == 0


def _aguardar_servidor(
    host: str, porta: int,
    processo: Optional[subprocess.Popen] = None,
    timeout: float = 30.0,
    intervalo: float = 0.5,
) -> bool:
    """Espera até a porta responder, o processo morrer, ou o timeout esgotar."""
    inicio = time.time()
    while time.time() - inicio < timeout:
        if _porta_em_uso(host, porta):
            return True
        if processo is not None and processo.poll() is not None:
            return False  # o processo terminou antes de abrir a porta
        time.sleep(intervalo)
    return False


def _executar_coleta_inicial(comando: list[str]) -> None:
    print()
    print("📊 Coletando dados de mercado (histórico, opções e Greeks)...")
    print("   (isto pode levar alguns instantes na primeira vez)")
    for flag, rotulo in [
        ("--coletar-todos",         "histórico"),
        ("--coletar-opcoes-todos",  "opções"),
        ("--calcular-greeks-todos", "Greeks"),
    ]:
        resultado = subprocess.run(comando + [flag], cwd=str(BASE_DIR))
        if resultado.returncode != 0:
            print(f"   ⚠️  Coleta de {rotulo} não completou — o dashboard "
                  f"abrirá mesmo assim com os dados já disponíveis.")


def main() -> int:
    _banner()

    if _porta_em_uso(HOST, PORT):
        print(f"ℹ️  Já existe um dashboard em execução em http://{HOST}:{PORT}")
        print("   Abrindo o navegador...")
        webbrowser.open(f"http://{HOST}:{PORT}")
        return 0

    comando = _montar_comando_base()
    _executar_coleta_inicial(comando)

    print()
    print("🚀 Iniciando o dashboard...")
    processo = subprocess.Popen(comando + ["--dashboard"], cwd=str(BASE_DIR))

    if not _aguardar_servidor(HOST, PORT, processo):
        print()
        print("❌ O dashboard não respondeu a tempo.")
        print(f"   Verifique se a porta {PORT} já não está sendo usada por outro programa.")
        if processo.poll() is None:
            processo.terminate()
        return 1

    webbrowser.open(f"http://{HOST}:{PORT}")

    print()
    _linha()
    print(f"✅ Tudo pronto! Dashboard aberto em http://{HOST}:{PORT}")
    print("   Feche esta janela ou pressione Ctrl+C para encerrar o programa.")
    _linha()

    try:
        processo.wait()
    except KeyboardInterrupt:
        print()
        print("Encerrando...")
        processo.terminate()
        try:
            processo.wait(timeout=10)
        except subprocess.TimeoutExpired:
            processo.kill()
    return 0


if __name__ == "__main__":
    sys.exit(main())

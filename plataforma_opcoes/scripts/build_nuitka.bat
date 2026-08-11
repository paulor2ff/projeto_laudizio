@echo off
REM build_nuitka.bat — Compila a plataforma num executavel autocontido (Windows)
REM ================================================================================
REM Requisitos:
REM   - Python 3.11+ com as dependencias de requirements.txt ja instaladas
REM   - Um compilador C: MSVC (Visual Studio Build Tools) ou MinGW64
REM     O Nuitka detecta e usa automaticamente o que estiver disponivel;
REM     se nenhum estiver instalado, ele oferece descarregar o MinGW64
REM     automaticamente na primeira execucao (por isso --assume-yes-for-downloads)
REM
REM Uso:
REM   cd plataforma_opcoes
REM   build\build_nuitka.bat
REM
REM Resultado: dist\PlataformaOpcoesB3.exe
REM
REM NOTA DE TEMPO: esta compilacao envolve fastapi, uvicorn, pandas, numpy
REM e as dependencias transitivas delas — normalmente 3-8 minutos num
REM computador com 4+ nucleos.
REM
REM scipy E DELIBERADAMENTE EXCLUIDO do build (--nofollow-import-to=scipy),
REM nao esquecido. greeks.py ja tem um fallback funcional (aproximacao
REM polinomial de Hart, erro < 7.5e-8) para quando scipy nao esta disponivel.
REM scipy foi identificado como causa provavel de o build do Windows falhar
REM apos ~4h de compilacao no GitHub Actions — suas dependencias Fortran/
REM LAPACK sao notoriamente lentas/frageis para compilar com Nuitka+MSVC
REM nesse runner especificamente. Simplesmente tirar "--include-package=
REM scipy" NAO basta: o Nuitka segue automaticamente qualquer import que
REM encontra no codigo (o "from scipy.stats import norm" em greeks.py
REM continua la) — e preciso --nofollow-import-to para excluir de verdade.

cd /d "%~dp0\.."

pip install nuitka -q

echo Iniciando compilacao — isto pode demorar varios minutos...

python -m nuitka ^
  --standalone ^
  --output-dir=dist ^
  --include-package=fastapi ^
  --include-package=starlette ^
  --include-package=uvicorn ^
  --include-package=apscheduler ^
  --include-package=yfinance ^
  --include-package=pandas ^
  --include-package=numpy ^
  --include-package=requests ^
  --include-package=cryptography ^
  --include-package=pytz ^
  --include-package=openpyxl ^
  --include-package=reportlab ^
  --include-package=multitasking ^
  --nofollow-import-to=scipy ^
  --include-data-dir=dashboard=dashboard ^
  --assume-yes-for-downloads ^
  --company-name="Plataforma Opcoes B3" ^
  --product-name="Plataforma de Opcoes B3" ^
  --file-version=11.0.0 ^
  --product-version=11.0.0 ^
  cli.py

if errorlevel 1 (
    echo [ERRO] Build Nuitka falhou.
    exit /b 1
)

if exist dist\PlataformaOpcoesB3 (
    rmdir /s /q dist\PlataformaOpcoesB3
)

move dist\cli.dist dist\PlataformaOpcoesB3

if exist dist\PlataformaOpcoesB3\cli.exe (
    move dist\PlataformaOpcoesB3\cli.exe dist\PlataformaOpcoesB3\PlataformaOpcoesB3.exe
) else (
    echo [ERRO] Executavel cli.exe nao encontrado.
    exit /b 1
)

echo.
echo ==========================================
echo Build concluido com sucesso.
echo ==========================================
echo Executavel:
echo dist\PlataformaOpcoesB3\PlataformaOpcoesB3.exe

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
REM NOTA DE TEMPO: esta compilacao envolve fastapi, uvicorn, pandas, numpy,
REM scipy e as dependencias transitivas delas — normalmente 3-8 minutos
REM num computador com 4+ nucleos.

cd /d "%~dp0\.."

pip install nuitka -q

echo Iniciando compilacao — isto pode demorar varios minutos...

python -m nuitka ^
  --onefile ^
  --output-dir=dist ^
  --output-filename=PlataformaOpcoesB3.exe ^
  --windows-console-mode=force ^
  --include-package=fastapi ^
  --include-package=starlette ^
  --include-package=uvicorn ^
  --include-package=apscheduler ^
  --include-package=yfinance ^
  --include-package=pandas ^
  --include-package=numpy ^
  --include-package=scipy ^
  --include-package=requests ^
  --include-package=cryptography ^
  --include-package=pytz ^
  --include-data-dir=dashboard=dashboard ^
  --assume-yes-for-downloads ^
  --company-name="Plataforma Opcoes B3" ^
  --product-name="Plataforma de Opcoes B3" ^
  --file-version=11.0.0 ^
  --product-version=11.0.0 ^
  cli.py

echo.
echo Build concluido: dist\PlataformaOpcoesB3.exe
echo.
echo Antes de distribuir, teste localmente:
echo   cd dist ^&^& PlataformaOpcoesB3.exe --status
echo   cd dist ^&^& PlataformaOpcoesB3.exe --dashboard

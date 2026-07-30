#!/usr/bin/env bash
#
# build_nuitka.sh — Compila a plataforma num executável autocontido (Linux/macOS)
# ===================================================================================
# Requisitos:
#   - Python 3.11+ com as dependências de requirements.txt já instaladas
#   - Um compilador C (gcc/clang) — normalmente já presente em Linux/macOS
#   - Linux: patchelf instalado (apt install patchelf / dnf install patchelf)
#
# Uso:
#   cd plataforma_opcoes
#   bash scripts/build_nuitka.sh
#
# Resultado: dist/PlataformaOpcoesB3 (Linux) ou dist/PlataformaOpcoesB3.app (macOS)
#
# NOTA DE HARDWARE: esta compilação envolve fastapi, uvicorn, pandas, numpy
# e as suas dependências transitivas (jinja2, pygments, websockets, etc.)
# — em máquinas com 1 núcleo de CPU a fase de compilação C pode levar bem mais
# de 10 minutos. Com 4+ núcleos, tipicamente 3-6 minutos.
#
# scipy É DELIBERADAMENTE EXCLUÍDO do build (--nofollow-import-to=scipy), não
# esquecido. greeks.py já tem um fallback funcional (aproximação polinomial
# de Hart para N(x), erro < 7.5e-8) para quando scipy não está disponível —
# ver o try/except em greeks.py:25-40. scipy foi identificado como a causa
# provável do build do Windows falhar após ~4h de compilação (exit code 1);
# suas dependências Fortran/LAPACK são notoriamente lentas e frágeis para
# compilar com Nuitka+MSVC no runner do GitHub Actions especificamente —
# Linux e macOS compilam normalmente mesmo com scipy incluído. Simplesmente
# remover "--include-package=scipy" (tentativa anterior) NÃO resolve isto:
# Nuitka segue automaticamente qualquer import que encontra no código
# (`from scipy.stats import norm` em greeks.py continua lá), a flag
# --include-package é só uma garantia adicional para casos que a detecção
# automática não pegaria sozinha — para excluir de verdade, é preciso
# --nofollow-import-to. Isto faz o import de scipy falhar em runtime (por
# design), o que aciona o fallback já existente em greeks.py.

set -e

cd "$(dirname "$0")/.."   # Garante que roda a partir da raiz do projeto.

echo "Iniciando compilação — isto pode demorar vários minutos..."
echo ""

echo "========== DIAGNÓSTICO DO AMBIENTE =========="
echo ""

echo "Python utilizado:"
python --version

echo ""
echo "Executável:"
python -c "import sys; print(sys.executable)"

echo ""
echo "Pip:"
python -m pip --version

echo ""
echo "FastAPI:"
python -c "import fastapi; print(f'FastAPI {fastapi.__version__}')"

echo ""
echo "Nuitka:"
python -m nuitka --version

echo ""
echo "============================================="
echo ""

python -m nuitka \
  --onefile \
  --output-dir=dist \
  --output-filename=PlataformaOpcoesB3 \
  --include-package=fastapi \
  --include-package=starlette \
  --include-package=uvicorn \
  --include-package=apscheduler \
  --include-package=yfinance \
  --include-package=pandas \
  --include-package=numpy \
  --include-package=requests \
  --include-package=cryptography \
  --include-package=pytz \
  --include-package=openpyxl \
  --include-package=reportlab \
  --nofollow-import-to=scipy \
  --include-data-dir=dashboard=dashboard \
  --assume-yes-for-downloads \
  --company-name="Plataforma Opcoes B3" \
  --product-name="Plataforma de Opcoes B3" \
  --file-version=11.0.0 \
  --product-version=11.0.0 \
  cli.py

echo ""
echo "✅ Build concluído: dist/PlataformaOpcoesB3"
echo ""
echo "Antes de distribuir, teste localmente:"
echo "  cd dist && ./PlataformaOpcoesB3 --status"
echo "  cd dist && ./PlataformaOpcoesB3 --dashboard"
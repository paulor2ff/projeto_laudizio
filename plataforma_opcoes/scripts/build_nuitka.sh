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
#   bash build/build_nuitka.sh
#
# Resultado: dist/PlataformaOpcoesB3 (Linux) ou dist/PlataformaOpcoesB3.app (macOS)
#
# NOTA DE HARDWARE: esta compilação envolve fastapi, uvicorn, pandas, numpy,
# scipy e as suas dependências transitivas (jinja2, pygments, websockets, etc.)
# — em máquinas com 1 núcleo de CPU a fase de compilação C pode levar bem mais
# de 10 minutos. Com 4+ núcleos, tipicamente 3-6 minutos.

set -e

cd "$(dirname "$0")/.."   # garante que roda a partir da raiz do projecto

pip install nuitka --break-system-packages -q 2>/dev/null || pip install nuitka -q

echo "Iniciando compilação — isto pode demorar vários minutos..."

python3 -m nuitka \
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
  --include-package=scipy \
  --include-package=requests \
  --include-package=cryptography \
  --include-package=pytz \
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

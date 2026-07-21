# Guia de Apresentação — Plataforma de Opções B3

Sequência para rodar o sistema do zero em ~5 minutos.

## 1. Instalar dependências

```bash
pip install -r requirements.txt
```

## 2. Importar a licença de demonstração

```bash
python cli.py --licenca-importar dev_tools/licenca_demo.json
```

Resultado esperado:
```
✅ Licença importada com sucesso — estágio: ok
   Válido até: 2026-07-25
```

## 3. Coletar dados iniciais

```bash
# Histórico de preços (2 anos)
python cli.py --coletar --ticker BBAS3 --periodo 2y

# Cadeia de opções
python cli.py --coletar-opcoes --ticker BBAS3

# Greeks (Delta, Gamma, Theta, Vega, Rho)
python cli.py --calcular-greeks --ticker BBAS3
```

## 4. Abrir o dashboard

```bash
python cli.py --dashboard
```

Aceder em: **http://localhost:8000**

## 5. Autenticar o dashboard

- Clicar em **🔑 Token** no canto superior
- Abrir o ficheiro `api_token.txt` na pasta do programa
- Colar o token no campo

## 6. Pontos de demonstração sugeridos

- Tabela de opções com Greeks calculados localmente
- Filtros por CALL/PUT, vencimento, strike, IQ mínimo
- Painel **🔔 Alertas** — criar alerta de Delta > 0.7
- Exportação CSV
- Endpoint Swagger UI: http://localhost:8000/docs
- Estado da licença: `python cli.py --licenca`

## Licença de demonstração válida até

2026-07-25 — 30 dias a partir da geração.

# Plataforma de Análise de Opções B3

Sistema completo de coleta, armazenamento e análise de opções da B3.

---

## Instalação

```bash
pip install -r requirements.txt
```

---

## Uso rápido

**Sem querer decorar comandos?** `python launcher.py` (ou o `.exe` gerado a
partir dele — ver `build/BUILD.md`) faz tudo sozinho: instala dependências,
coleta os dados, sobe o dashboard e abre o navegador. Nenhum passo manual.

Os comandos abaixo são para quem quer controlo fino sobre cada etapa:

```bash
# 1. Primeira coleta (cria o banco e baixa 2 anos de histórico)
python cli.py --coletar --ticker BBAS3

# 2. Coletar cadeia de opções
python cli.py --coletar-opcoes --ticker BBAS3

# 3. Calcular Greeks
python cli.py --calcular-greeks --ticker BBAS3

# 4. Iniciar dashboard web (http://localhost:8000)
python cli.py --dashboard
```

---

## Todos os comandos

| Comando | Descrição |
|---|---|
| `--coletar` | Histórico OHLCV do ticker |
| `--coletar --periodo 5y` | Período customizado (1mo 3mo 6mo 1y 2y 5y max) |
| `--coletar-opcoes` | Cadeia de opções do ticker |
| `--coletar-todos` | Histórico de todos os tickers configurados |
| `--coletar-opcoes-todos` | Cadeia de opções de todos os tickers configurados |
| `--calcular-greeks-todos` | Greeks de todos os tickers configurados |
| `--calcular-greeks` | Delta, Gamma, Theta, Vega, Rho, IQ |
| `--tempo-real` | Ciclo completo (cotação + opções + greeks) |
| `--consultar` | Histórico de cotações |
| `--opcoes` | Cadeia de opções com Greeks |
| `--opcoes --tipo CALL` | Filtrar por tipo |
| `--opcoes --vencimento 2025-01-17` | Filtrar por vencimento |
| `--resumo` | Estatísticas gerais do banco |
| `--exportar` | Exporta cotações (padrão CSV — ver `--formato`) |
| `--exportar-opcoes` | Exporta opções com Greeks (padrão CSV — ver `--formato`) |
| `--exportar --formato xlsx\|pdf` | Mesmo dado, formatado para Excel ou como relatório PDF |
| `--dashboard` | Servidor web + dashboard em tempo real |
| `--status` | Status do sistema e scheduler |

### Filtros disponíveis em qualquer consulta

```bash
--ticker PETR4        # Ativo (padrão: BBAS3)
--de 2025-01-01       # Data inicial
--ate 2025-12-31      # Data final
--limite 100          # Máximo de registros
--tipo CALL           # CALL ou PUT
--vencimento AAAA-MM-DD
```

---

## Estrutura do projeto

```
plataforma_opcoes/
├── config.py       # Configurações, tickers, credenciais (editar aqui)
├── database.py     # Banco SQLite — 4 tabelas, índices, upsert
├── greeks.py       # Black-Scholes, Binomial, IQ, volatilidade implícita
├── auth.py         # Módulo de autenticação opcoes.net.br (configurar quando pronto)
├── collector.py    # Coleta yfinance + fallback autenticado
├── scheduler.py    # Agendador de coleta durante o pregão
├── api.py          # FastAPI: REST + WebSocket
├── cli.py          # Interface de linha de comando
├── dashboard/
│   └── index.html  # Dashboard web em tempo real
└── requirements.txt
```

---

## Configuração de tickers

Edite `config.py`:

```python
TICKERS = [
    "BBAS3.SA", "PETR4.SA", "VALE3.SA",
    # adicione quantos quiser
]
```

---

## Ativar autenticação (opcoes.net.br)

Quando mapear os endpoints via inspeção de rede (F12 → Network → XHR, logado):

```python
# Em config.py:
AUTH_LOGIN_URL  = "https://opcoes.net.br/..."   # URL de login (POST)
AUTH_OPCOES_URL = "https://opcoes.net.br/..."   # URL de opções (GET, com {ticker})
AUTH_USERNAME   = "seu@email.com"
AUTH_PASSWORD   = "suasenha"
```

O sistema passa automaticamente a usar a fonte autenticada (dados em tempo real completos).
Enquanto não configurado, usa yfinance com 15 min de atraso — sem nenhuma alteração necessária no restante do código.

---

## Agendamento automático

```bash
# Linux/macOS — crontab -e
0 19 * * 1-5 python /caminho/cli.py --coletar-todos

# Ou iniciar o scheduler junto com o dashboard:
python cli.py --dashboard   # scheduler sobe automaticamente com a API
```

---

## API REST

Com o dashboard rodando (`--dashboard`), os endpoints ficam disponíveis:

```
GET  /cotacoes/{ticker}      — histórico de cotações
GET  /opcoes/{ticker}        — cadeia de opções com Greeks
GET  /cotacao-atual/{ticker} — preço atual
GET  /tickers                — lista de tickers monitorados
GET  /resumo                 — estatísticas do banco
GET  /status                 — status do sistema
POST /coletar/{ticker}       — dispara coleta manual
WS   /ws/{ticker}            — stream em tempo real (WebSocket)

Documentação interativa: http://localhost:8000/docs
```

---

## Campos disponíveis

| Campo | Fonte | Disponibilidade |
|---|---|---|
| Strike, Tipo, Vencimento | yfinance / autenticado | ✅ |
| Último, Var.%, Núm. Neg., Vol. Fin. | yfinance / autenticado | ✅ |
| Vol. Implícita | yfinance / calculado | ✅ |
| Delta, Gamma, Theta, Vega, Rho | Black-Scholes / Binomial local | ✅ |
| A/I/OTM, Dist. Strike | calculado | ✅ |
| IQ | aproximação local | ⚠️ similar ao opcoes.net.br |
| Coberto, Descoberto, Travado, Tit., Lanç. | custódia B3 | ❌ (NULL reservado) |

---

## Dependências

```
yfinance, pandas, numpy, scipy, fastapi, uvicorn, APScheduler, requests
```

# Plataforma Financeira B3

Sistema completo para coleta, armazenamento, análise e monitoramento de opções da B3, desenvolvido em Python.

A plataforma integra coleta automatizada de dados, cálculo de indicadores quantitativos, API REST, dashboard web, servidor de licenciamento e mecanismos de segurança para distribuição comercial.

---

## Principais funcionalidades

- Coleta automática de cotações da B3
- Coleta da cadeia de opções
- Armazenamento em banco SQLite
- Cálculo dos Greeks utilizando Black-Scholes e modelo Binomial
- Cálculo de volatilidade implícita
- Dashboard Web em tempo real
- API REST desenvolvida em FastAPI
- WebSocket para atualização em tempo real
- Sistema de alertas
- Exportação para CSV
- Scheduler para atualização automática durante o pregão
- Sistema de autenticação
- Servidor de licenciamento
- Build protegida para distribuição
- Documentação técnica e operacional

---

# Arquitetura

```
                   ┌─────────────────────────┐
                   │      CLI / Scheduler    │
                   └─────────────┬───────────┘
                                 │
                     Coleta automática
                                 │
                ┌────────────────▼──────────────┐
                │         Collector             │
                └────────────────┬──────────────┘
                                 │
                 yfinance / Fonte autenticada
                                 │
                ┌────────────────▼──────────────┐
                │        Banco SQLite           │
                └────────────────┬──────────────┘
                                 │
                ┌────────────────▼──────────────┐
                │          FastAPI              │
                └──────────────┬────────────────┘
                               │
                 REST API      │     WebSocket
                               │
                  ┌────────────▼────────────┐
                  │     Dashboard Web       │
                  └─────────────────────────┘
```

---

# Tecnologias

- Python 3
- FastAPI
- SQLite
- Uvicorn
- yfinance
- Pandas
- NumPy
- SciPy
- APScheduler
- WebSockets
- JWT / Token Authentication
- Nuitka (build protegido)
- GitHub Actions

---

# Estrutura do projeto

```
plataforma_opcoes/

├── api.py                 # API REST
├── auth.py                # Autenticação
├── collector.py           # Coleta de dados
├── database.py            # Persistência
├── greeks.py              # Modelos matemáticos
├── scheduler.py           # Agendamento automático
├── alertas.py             # Sistema de alertas
├── licenca.py             # Validação de licenças
├── cli.py                 # Interface de linha de comando
│
├── dashboard/
│   └── index.html
│
├── build/
│   ├── build_nuitka.bat
│   └── build_nuitka.sh
│
├── tests/
│
└── requirements.txt
```

---

# Instalação

Clone o repositório:

```bash
git clone https://github.com/paulor2ff/finance_project.git

cd finance_project/plataforma_opcoes
```

Instale as dependências:

```bash
pip install -r requirements.txt
```

---

# Primeiros passos

## Coletar histórico

```bash
python cli.py --coletar --ticker BBAS3
```

## Coletar opções

```bash
python cli.py --coletar-opcoes --ticker BBAS3
```

## Calcular Greeks

```bash
python cli.py --calcular-greeks --ticker BBAS3
```

## Dashboard

```bash
python cli.py --dashboard
```

A aplicação ficará disponível em

```
http://localhost:8000
```

---

# API REST

Quando o dashboard estiver em execução:

| Método | Endpoint |
|---------|----------|
| GET | /cotacoes/{ticker} |
| GET | /opcoes/{ticker} |
| GET | /cotacao-atual/{ticker} |
| GET | /tickers |
| GET | /status |
| GET | /resumo |
| POST | /coletar/{ticker} |
| WS | /ws/{ticker} |

Documentação automática:

```
http://localhost:8000/docs
```

---

# Sistema de Licenciamento

O projeto possui um sistema próprio de proteção comercial, incluindo:

- emissão de licenças digitais;
- validação criptográfica;
- importação de licenças;
- verificação automática de validade;
- integração transparente com a aplicação.

---

# Segurança

Entre os mecanismos implementados encontram-se:

- autenticação por token;
- controle de acesso à API;
- proteção da distribuição executável;
- validação de licenças;
- logs de auditoria;
- tratamento de exceções;
- limitação de requisições (rate limiting);
- pipeline de build automatizado.

---

# Testes

Executar todos os testes:

```bash
pytest
```

---

# Build

O projeto possui scripts de compilação utilizando Nuitka.

Linux

```bash
build/build_nuitka.sh
```

Windows

```bat
build\build_nuitka.bat
```

---

# Documentação

O projeto acompanha documentação complementar:

- Manual do Usuário
- Manual do Operador
- Guia de Apresentação
- Documentação da API (Swagger)

---

# Aplicações

A plataforma pode ser utilizada para:

- monitoramento de opções da B3;
- estudos quantitativos;
- análise de volatilidade;
- cálculo dos Greeks;
- desenvolvimento de estratégias;
- integração com aplicações externas por meio da API.

---

# Roadmap

- Interface gráfica expandida
- Novos indicadores quantitativos
- Novas fontes de dados
- Integração com provedores em tempo real
- Novos modelos de precificação
- Relatórios automatizados

---

# Licença

Este projeto é disponibilizado exclusivamente mediante licenciamento.

Todos os direitos reservados.

---

# Autor

**Paulo Rogério Fernandes Filho**

MBA em Segurança da Informação

Desenvolvedor da Plataforma Financeira B3.

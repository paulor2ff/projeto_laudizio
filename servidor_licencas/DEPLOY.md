# DEPLOY.md — Hospedar o Servidor de Licenças

Guia para colocar o servidor de licenças online. Testado neste pacote
com um processo `uvicorn` real (não apenas testes simulados) — o fluxo
completo (emitir → token → cliente importa → estado "ok") foi confirmado
de ponta a ponta antes desta entrega.

## Antes de começar

Escolha um provedor. Railway e Render têm planos gratuitos suficientes
para começar. Fly.io é uma terceira opção via Dockerfile (já incluído).

| Provedor | Ficheiro de configuração já incluído | Disco persistente |
|---|---|---|
| Railway | `railway.json` + `Procfile` | Volumes (pago a partir de uso) |
| Render | `render.yaml` | Disco incluído no blueprint |
| Fly.io / VPS | `Dockerfile` | Configurar volume manualmente |

## ⚠️ O aviso mais importante deste guia

SQLite em disco efémero é apagado a cada redeploy ou reinício do
container. Sem um volume persistente configurado, a sua base de
clientes desaparece sempre que o serviço reiniciar — o que acontece
rotineiramente em planos gratuitos (idle timeout) e em todo redeploy.

**Configure o volume ANTES de ter o primeiro cliente real.** As
instruções de cada provedor abaixo já cobrem isto.

## Passo 1 — Gerar a chave de produção (uma única vez)

```bash
cd servidor_licencas
python3 gerar_chave_producao.py
```

Isto cria `chave_privada_SERVIDOR.pem` e imprime a chave pública
correspondente. **Guarde a chave pública** — vai precisar dela no passo 5.

Nunca repita este comando depois de ter clientes activos sem usar
`--forcar` conscientemente — gerar uma nova chave invalida todas as
licenças já emitidas.

## Passo 2 — Deploy no Railway

1. Crie um repositório Git com o conteúdo desta pasta (`servidor_licencas/`)
2. No painel Railway: New Project → Deploy from GitHub repo
3. Railway detecta `railway.json` e `requirements.txt` automaticamente
4. Adicionar um Volume: Settings → Volumes → New Volume, mount path `/data`
5. Configurar variáveis de ambiente (Settings → Variables):

```
LICENCA_CHAVE_PRIVADA_PEM=<conteúdo completo do .pem gerado no Passo 1>
LICENCA_ADMIN_TOKEN=<gerar com: python3 -c "import secrets; print(secrets.token_urlsafe(32))">
LICENCA_DB_PATH=/data/servidor_licencas.db
STRIPE_WEBHOOK_SECRET=<se for usar Stripe>
MERCADOPAGO_WEBHOOK_SECRET=<se for usar Mercado Pago>
MERCADOPAGO_ACCESS_TOKEN=<se for usar Mercado Pago>
```

6. Deploy. Railway atribui uma URL pública (`https://SEU-PROJETO.up.railway.app`)

## Passo 2 (alternativa) — Deploy no Render

1. Crie um repositório Git com o conteúdo desta pasta
2. No painel Render: New → Blueprint → conectar o repositório
3. Render lê `render.yaml` automaticamente — já inclui o disco persistente
4. Preencher as variáveis marcadas `sync: false` no painel (mesmas do Passo 1 acima)
5. Deploy. Render atribui `https://SEU-SERVICO.onrender.com`

## Passo 3 — Verificar que está no ar

```bash
curl https://SEU-DOMINIO/saude
# {"status":"ok","tempo":"..."}

curl https://SEU-DOMINIO/chave-publica
# {"chave_publica_base64":"..."} — deve corresponder à do Passo 1
```

## Passo 4 — Testar a emissão manual (fluxo PIX)

Use `admin_cli.py` a partir da sua máquina local, apontando para o
servidor já implantado:

```bash
export LICENCA_SERVIDOR_URL="https://SEU-DOMINIO"
export LICENCA_ADMIN_TOKEN="o-mesmo-token-que-configurou-no-passo-1"

python3 admin_cli.py saude
python3 admin_cli.py emitir --cliente-id teste_001 --plano manutencao --dias 31
python3 admin_cli.py listar
```

Se `emitir` funcionar e gerar `licenca_teste_001.json`, o servidor está
operacional.

## Passo 5 — Configurar a plataforma dos clientes

Em `plataforma_opcoes/config.py`, antes de distribuir a qualquer cliente:

```python
LICENCA_CHAVE_PUBLICA  = "<a chave pública impressa no Passo 1>"
LICENCA_URL_RENOVACAO  = "https://SEU-DOMINIO/licencas/validar"
```

Com isto, a renovação automática de tokens (`_tentar_renovar_online()`
em `licenca.py`) passa a funcionar sem intervenção manual a cada ciclo.

## Passo 6 — Fluxo do dia a dia (cobrança manual via PIX)

```bash
# 1. Cliente paga na sua chave PIX
# 2. Você confirma o pagamento no extrato
# 3. Emitir a licença:
python3 admin_cli.py emitir --cliente-id nome_do_cliente \
                             --plano manutencao --dias 31 \
                             --email cliente@email.com --nome "Nome Completo"

# 4. O comando já gera licenca_nome_do_cliente.json — enviar por e-mail
# 5. O cliente activa do lado dele:
python cli.py --licenca-importar licenca_nome_do_cliente.json
```

## Configurar webhooks (quando quiser automatizar)

Só necessário quando decidir parar de emitir manualmente.

| Processador | URL do webhook | Eventos |
|---|---|---|
| Stripe | `https://SEU-DOMINIO/webhooks/stripe` | checkout.session.completed, invoice.paid, customer.subscription.updated, customer.subscription.deleted, invoice.payment_failed |
| Mercado Pago | `https://SEU-DOMINIO/webhooks/mercadopago` | subscription_preapproval |

Os segredos gerados em cada painel vão para `STRIPE_WEBHOOK_SECRET` /
`MERCADOPAGO_WEBHOOK_SECRET` nas variáveis de ambiente do servidor.

## O que este guia não cobre

Criar a conta no Railway/Render/Stripe/Mercado Pago é uma acção que só
você pode fazer — requer os seus dados e decisões de negócio. Este guia
cobre tudo o que é técnico a partir do momento em que a conta existe.

# BUILD.md — Compilar a Plataforma num Executável Autocontido

Este documento explica como gerar um executável (`.exe` no Windows, binário
nativo no Linux/macOS) a partir do código-fonte, usando o [Nuitka](https://nuitka.net/),
um compilador que transforma Python em C e depois em código de máquina real —
não sobra bytecode Python legível, ao contrário do PyInstaller.

## O que foi corrigido no código-fonte para isto funcionar

Duas mudanças foram necessárias e já estão aplicadas nesta versão:

1. **`cli.py`** — `uvicorn.run("api:app", ...)` (import por string) foi trocado
   por `uvicorn.run(fastapi_app, ...)` (import directo do objecto). A forma por
   string exige resolução dinâmica de módulo em runtime, que falha em binários
   compilados — é um problema documentado do próprio projecto Nuitka.

2. **`config.py`** — `BASE_DIR` deixou de depender só de `Path(__file__)`. Em
   modo `--onefile`, `__file__` aponta para uma pasta temporária que é criada e
   apagada a cada execução — sem esta correcção, o banco de dados seria
   recriado do zero toda vez que o utilizador fechasse e reabrisse o programa,
   perdendo todo o histórico coletado silenciosamente. Agora, quando compilado
   (`"__compiled__" in globals()`, a forma documentada de detectar isso), usa
   `Path(sys.executable).parent` — a pasta real onde o `.exe` está.

Ambas as correcções foram confirmadas com a suite de testes completa (150/150)
e com o dashboard a correr de facto (scheduler real, APScheduler real,
respostas HTTP 200) antes de qualquer tentativa de compilação.

## O que foi validado neste ambiente de desenvolvimento, e o que não foi

Sendo directo sobre uma limitação real: este ambiente de desenvolvimento tem
**apenas 1 núcleo de CPU** e um limite de tempo por comando. A fase de
compilação C do Nuitka (que envolve compilar centenas de ficheiros gerados a
partir de fastapi, pandas, numpy, scipy e as suas dependências transitivas)
não termina dentro desse limite — não porque haja um erro, mas por restrição
pura de hardware.

O que **foi** confirmado aqui, correndo o processo real (não simulado):
- A fase de análise Python do Nuitka completa sem nenhum erro de "módulo em
  falta" ou import não resolvido, com a lista de `--include-package` usada
  nos scripts abaixo.
- O `dashboard --status` e o servidor real (`--dashboard`) funcionam
  correctamente em Python normal após as duas correcções acima.
- O comando de build gera ficheiros `.c` e inicia a compilação corretamente
  (confirmado via inspecção directa dos artefactos parciais gerados).

O que **não foi** confirmado aqui, por limitação de hardware do ambiente:
- A conclusão da compilação C completa.
- A execução do binário final resultante.

Isto precisa de ser confirmado numa máquina com mais recursos — a sua, ou
(recomendado) via GitHub Actions, que é gratuito para isto e resolve o
problema de raiz por rodar em runners com múltiplos núcleos.

## Três formas de gerar o executável

### Opção 1 — GitHub Actions (recomendado, sem precisar de máquina própria)

> **⚠️ Nota (revisão de julho/2026):** este documento descreve o workflow
> abaixo como "já configurado", mas o ficheiro
> `.github/workflows/build-executavel.yml` não está presente no projecto
> como recebido — não existe pasta `.github/` nesta cópia. Ou ficou de fora
> do zip exportado, ou ainda precisa de ser criado. Vale confirmar antes de
> depender desta opção; posso criar esse workflow se for útil.

Já está configurado em `.github/workflows/build-executavel.yml`. Compila para
Windows, Linux e macOS ao mesmo tempo, em runners com recursos adequados.

```bash
git init   # se ainda não for um repositório
git add .
git commit -m "Plataforma de Opções B3 v11"
git remote add origin https://github.com/SEU-USUARIO/SEU-REPOSITORIO.git
git push -u origin main

# Disparar o build manualmente:
# GitHub → aba "Actions" → "Build Executáveis" → "Run workflow"
#
# Ou disparar por tag de versão:
git tag v11.0.0
git push --tags
```

Após 5-10 minutos por sistema operativo, os três executáveis ficam
disponíveis para download em "Artifacts", no resumo da execução do workflow.
Um repositório privado no GitHub é gratuito e mantém o código fora do
alcance público.

### Opção 2 — Compilar na sua própria máquina

```bash
# Linux / macOS
cd plataforma_opcoes
pip install -r requirements-build.txt
bash build/build_nuitka.sh

# Windows (PowerShell ou CMD)
cd plataforma_opcoes
pip install -r requirements-build.txt
build\build_nuitka.bat
```

Tempo esperado: 3-10 minutos em hardware com 4+ núcleos. Se a sua máquina
tiver poucos núcleos, esperar proporcionalmente mais.

### Opção 3 — Comando manual (para ajustar flags)

Ver o conteúdo de `build/build_nuitka.sh` ou `build/build_nuitka.bat` — os
comandos completos estão documentados linha a linha ali, caso precise
adicionar ou remover alguma flag (por exemplo, um ícone personalizado via
`--windows-icon-from-ico=caminho.ico`).

## Testar o executável depois de compilado

```bash
# Linux/macOS
cd dist
./PlataformaOpcoesB3 --status
./PlataformaOpcoesB3 --licenca-importar caminho/para/licenca.json
./PlataformaOpcoesB3 --dashboard

# Windows
cd dist
PlataformaOpcoesB3.exe --status
PlataformaOpcoesB3.exe --dashboard
```

Confirme que `opcoes_b3.db` aparece **ao lado do executável** depois de
qualquer comando de coleta — não numa pasta temporária. Isto é exatamente o
que a correção em `config.py` garante; vale a pena confirmar uma vez.

## Avisos práticos

**Antivírus e SmartScreen no Windows**: executáveis gerados por Nuitka (e por
ferramentas semelhantes como PyInstaller) por vezes disparam avisos falsos de
antivírus ou do SmartScreen do Windows — não porque haja algo errado, mas
porque o padrão de empacotamento de um executável Python compilado se parece,
para uma heurística automática, com o de alguns programas maliciosos que usam
a mesma técnica de empacotamento. Isto é uma fricção conhecida e comum neste
tipo de distribuição, não um sinal de bug. A forma definitiva de resolver é
assinar digitalmente o executável com um certificado de assinatura de código
(processo e custo à parte, envolve verificação de identidade junto de uma
autoridade certificadora) — mencionado aqui para que a decisão seja sua, não
foi implementado nesta entrega.

**`--onefile` vs pasta `--standalone`**: os scripts usam `--onefile` (um único
ficheiro `.exe`, mais simples de entregar a um cliente). Isto tem um pequeno
custo: o executável extrai-se para uma pasta temporária a cada arranque, o
que atrasa o início em cerca de 1-2 segundos. Se preferir eliminar esse atraso
à custa de distribuir uma pasta inteira em vez de um único ficheiro, troque
`--onefile` por `--standalone` nos scripts.

## `launcher.py` — iniciar sem terminal (adicionado em julho/2026)

Para o utilizador final que não quer saber de flags de CLI: `launcher.py`
é um ponto de entrada único e totalmente automático — dá duplo-clique
(ou corre) e ele sozinho detecta o que precisa, colhe dados e abre o
dashboard no navegador. Sem menu, sem perguntas.

**O que ele faz, em ordem:**
1. Procura um executável já compilado (`dist/PlataformaOpcoesB3(.exe)`) ao
   lado dele. Se encontrar, usa-o directamente — nenhuma instalação.
2. Senão (a correr a partir do código-fonte), cria um `.venv` local (se
   ainda não existir) e instala `requirements.txt` — automático, sem pedir
   confirmação.
3. Coleta histórico, opções e Greeks para todos os tickers configurados.
4. Sobe o dashboard e abre `http://localhost:8000` no navegador padrão.
5. Fica à escuta: fechar a janela ou Ctrl+C encerra tudo de forma limpa
   (incluindo o scheduler em segundo plano — testado com o processo real,
   não só simulado).

**Porque é um ficheiro à parte, e não uma flag do `cli.py`:** ele só pode
depender da biblioteca padrão do Python — precisa correr *antes* de
`fastapi`/`pandas`/etc. estarem instalados. Isso também o torna trivial de
compilar (não há nada pesado para o Nuitka empacotar):

```bash
# Linux/macOS
python3 -m nuitka --onefile --output-dir=dist \
  --output-filename=IniciarPlataforma \
  --company-name="Plataforma Opcoes B3" \
  --product-name="Iniciar Plataforma de Opcoes B3" \
  launcher.py

# Windows
python -m nuitka --onefile --output-dir=dist ^
  --output-filename=IniciarPlataforma.exe ^
  --windows-console-mode=force ^
  --company-name="Plataforma Opcoes B3" ^
  --product-name="Iniciar Plataforma de Opcoes B3" ^
  launcher.py
```

Isto deve terminar em segundos, não minutos — se demorar tanto quanto o
build principal, algo foi importado que não devia (verifique se nenhuma
dependência de `requirements.txt` foi importada por engano em `launcher.py`).

**Como distribuir:** o mais simples é colocar `IniciarPlataforma.exe` ao
lado de `dist/PlataformaOpcoesB3.exe` (o build completo deste mesmo
documento) — o launcher detecta e usa o segundo automaticamente. Também
funciona sozinho, apontando para o código-fonte + `requirements.txt`, para
quem já tem Python instalado e prefere não esperar pelo build completo.

**Não testado neste ambiente:** a compilação real deste ficheiro com
Nuitka (só a lógica em Python puro foi validada, incluindo com um
subprocesso real do dashboard subindo e sendo encerrado de forma limpa).

## Actualizações futuras

Sempre que o código-fonte for alterado, é necessário recompilar — o
executável não se actualiza sozinho. Para clientes já com o executável
instalado, o caminho mais simples é distribuir o novo `.exe` e pedir que
substituam o antigo (o `opcoes_b3.db`, `licenca.json` e `alertas.json` ficam
ao lado do executável e são preservados na substituição, desde que o nome da
pasta não mude).

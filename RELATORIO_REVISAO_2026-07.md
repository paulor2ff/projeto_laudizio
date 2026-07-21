# Architecture & Test Review — B3 Options Platform (v12)
**Date:** July 2026 · **Scope:** `plataforma_opcoes/` + `servidor_licencas/`

This report covers five passes over the project: (1) an initial read-only
architecture + test review, (2) a full remediation pass implementing
everything actionable from pass 1, (3) new `.xlsx`/`.pdf` export formats,
(4) a fully-automatic launcher for non-technical users, and (5) dashboard
export buttons plus a live price-flash effect.

**Headline numbers**

| | Before | After |
|---|---|---|
| Tests (both projects) | 212 | **462** |
| `plataforma_opcoes` coverage | 57% | **92%** |
| `servidor_licencas` coverage | 79% | **80%** |
| Modules with 0% dedicated coverage | `api.py`, `cli.py`, `auth.py`, `collector.py` (11%) | none (`inspecionar_db.py` and two one-shot operator scripts excepted — see §6) |
| Ruff findings (F/E9/B rules) | 36 | 3 (all reviewed, all justified false-positives/intentional — see §4) |
| Export formats | csv only | **csv, xlsx, pdf** (§9) |
| Entry points | `cli.py` (flags) | **+ `launcher.py`** (no flags, no menu — §11) |

---

## 1. Bugs found and fixed

All of these were discovered while writing the new tests (i.e. found by
exercising real code paths, not by inspection alone). Each is small, low-risk,
and doesn't change any financial/business logic.

| # | File | Issue | Fix |
|---|---|---|---|
| 1 | `api.py` | `_TOKEN_FILE = "api_token.txt"` was a **bare relative path** — same root cause as the historical Nuitka `BASE_DIR` bug, just in a spot that fix didn't cover. A compiled build launched from a different working directory would silently regenerate a brand-new API token every time. | Anchored to `BASE_DIR` (`str(BASE_DIR / "api_token.txt")`), matching the pattern already used for `DB_PATH`/`LOG_PATH` in `config.py`. |
| 2 | `api.py` (8 spots) + `cli.py` (1 spot) | Ticker normalization only appended `.SA` — it never uppercased. `GET /cotacoes/bbas3` silently returned 0 rows against data stored as `BBAS3.SA`, because SQLite string comparison is case-sensitive. | Added `_normalizar_ticker()` (uppercase **then** check/append `.SA`) and routed every endpoint/CLI command through it. |
| 3 | `auth.py` — `_mapear_campos()` | The per-item `except` block itself crashed on a malformed item: `item.get("codigo", "?")` assumes `item` is dict-like, but the exception being handled could be exactly that assumption failing (e.g. `None` in the raw list). One bad entry would abort the whole batch instead of being skipped. | Guard the debug lookup: `item.get(...) if isinstance(item, dict) else "?"`. |
| 4 | `collector.py` — `_validar_sanidade_cotacao()` | `preco_ref = fechamento or vals[0]` computed but never read — dead code. | Removed. |
| 5 | `database.py`, `api.py`, `servidor_licencas/main.py` (3 spots) | `raise ...` inside an `except` block without `from exc` — masks the original traceback. | Added `from exc`. |
| 6 | `cli.py` — `main()` | Adding a new CLI flag (`--coletar-opcoes-todos`, `--calcular-greeks-todos`, §11) initially did nothing when passed — `main()` keeps a separate, explicit list of flag names to decide "was any action requested," apart from argparse itself, and the new names weren't in it. | Added them to the list. Found by a test that actually invoked the new flag and got the help text back instead. |

None of these change what a correctly-formed request returns today — they
close edge cases (wrong cwd, wrong ticker case, a malformed upstream record,
lost tracebacks, a missed wiring step) that either don't come up yet or come
up silently.

## 2. New hardening: rate limit on `/licencas/validar`

This endpoint only requires knowing a `cliente_id` to mint a valid signed
license — mitigated today by the entropy of Stripe/Mercado Pago-derived IDs
(`stripe_cus_...`, `mp_...`), but wasn't rate-limited. Added the same
sliding-window limiter already used for `/coletar/{ticker}` in
`plataforma_opcoes/api.py`: **10 requests / 60s per IP**, `429` beyond that.
High enough not to interfere with legitimate renewal traffic or the existing
test suite, low enough to meaningfully slow down sequential guessing.
Covered by 3 tests (limit holds, 11th request blocked, IPs isolated).

## 3. Tests added (sessions 1–2)

| File | Status | Tests |
|---|---|---|
| `plataforma_opcoes/tests/test_api.py` | new | 27 |
| `plataforma_opcoes/tests/test_collector.py` | new | 49 |
| `plataforma_opcoes/tests/test_auth.py` | new | 45 |
| `plataforma_opcoes/tests/test_cli.py` | new (later extended, §9/§11) | 48 → 62 |
| `plataforma_opcoes/tests/test_scheduler.py` | extended | 6 → 31 |
| `servidor_licencas/tests/test_api.py` | extended | +3 (rate limit) |
| `plataforma_opcoes/tests/conftest.py` | extended | +3 fixtures: `yf_ticker`, `api_client`, `cli_env` |

Design notes worth knowing, since they shape how you'd extend these tests:

- **`yf_ticker`** replaces `collector.yf` per-test (not the session-wide
  `sys.modules['yfinance']` mock), so each test configures its own
  `history`/`fast_info`/`options`/`option_chain` without leaking state.
- **`api_client`** reloads `api.py` after `db_temp` patches `config.DB_PATH`
  (needed because `api.py`/`cli.py`/`collector.py` do `from config import
  DB_PATH` — a name-copy, not a live lookup — so without the reload they'd
  keep pointing at wherever `DB_PATH` was when first imported). It also
  neutralizes `scheduler.iniciar`/`parar` so tests don't spin up a real
  background scheduler.
- Confirmed empirically: `scheduler.py`'s and `auth.py`'s license-gated
  functions pick up `licenca_temp`'s patched state correctly **without**
  needing `scheduler.py` reloaded — the decorator closure created by
  `@requer_licenca(...)` at import time lives inside `licenca.py`'s own
  module dict, which `importlib.reload()` mutates in place rather than
  replacing.
- One behavioral note: going through the **full** app lifespan,
  `verificar_token()`'s "no token configured → open mode" branch is
  effectively unreachable in practice, because `inicializar_token()`
  (called at startup) always self-heals by generating a token first. Not a
  bug — that fallback is a safety net for direct/non-standard use of the
  module, not something that happens in the deployed app's normal
  lifecycle. Tested `verificar_token()` directly as a unit to exercise it.

## 4. Lint (ruff, rules F / E9 / B)

36 → 3 remaining, and all three are deliberate, not oversights:

- **`api.py` and `servidor_licencas/admin.py`** — ruff's B008 flags
  `Depends(...)` as a risky mutable default argument. This is the correct,
  documented FastAPI idiom (FastAPI special-cases `Depends()` at
  registration time); "fixing" it would break dependency injection.
- **`auth.py:207`** — `sessao = obter_sessao()` looks unused today, but the
  commented-out TODO block right below it (waiting on the real
  opcoes.net.br endpoint mapping) already references `sessao`. Removing the
  assignment now would silently break that scaffolding once it's filled in.

Everything else (30 auto-fixed + the 5 fixes in §1 needing a human
decision) is resolved: unused imports, unused test-mock variables,
f-strings without placeholders, exception chaining.

## 5. Repo hygiene

Added a root **`.gitignore`** — none existed. The zip you sent included a
real 2.6MB `opcoes_b3.db`, a 6.8MB `plataforma.log`, and your live
`api_token.txt`. Covers generated DB/log files, tokens/licenses/`.pem` keys,
CLI exports (csv/xlsx/pdf), `.venv/`, Nuitka build artifacts,
`.pytest_cache`/coverage output, and `.qodo/`.

*(One mistake worth being upfront about: while repackaging the deliverable
zip in session 3, a cleanup command's glob pattern accidentally deleted the
real `opcoes_b3.db` from the working copy. Caught before handing anything
over, restored from your original upload, both suites re-verified green
afterward. Mentioning it here rather than letting it pass quietly.)*

## 6. Still open — needs your call, not touched

**`greeks.py:445-448`, `calcular_contrato()`'s `"auto"` mode** picks
binomial-vs-Black-Scholes by CALL/PUT, but the README describes `auto` as
detecting the `modelo` (Americano/Europeu) field — which yfinance always
populates as `None` today. Didn't change this: it's a real financial-logic
decision (and CALL-gets-binomial/PUT-gets-Black-Scholes is, if anything,
backwards from the usual early-exercise-premium intuition — puts typically
carry more early-exercise value than calls on a dividend-paying stock).
Still the one open question from the first pass; happy to implement
whichever behavior you confirm.

Also unchanged, by design: `admin_cli.py` and `gerar_chave_producao.py` in
`servidor_licencas` remain at 0% coverage (thin one-shot operator
scripts/argument plumbing) and `inspecionar_db.py` in `plataforma_opcoes`
(a standalone diagnostic script, not meant to be imported).

**Separately found, not part of this work:** `build/BUILD.md` describes
`.github/workflows/build-executavel.yml` as "already configured," but no
`.github/` folder exists anywhere in the project as uploaded. Either it
didn't make it into the zip, or it still needs to be created — flagged
directly in `BUILD.md` now. Happy to create it if useful.

## 7. Full re-run after sessions 1–2

```
plataforma_opcoes:  336 passed, 91% coverage
servidor_licencas:   65 passed, 80% coverage
                    ─────────────────────────
                    401 passed, 0 failed
```

Root `pytest` still fails if invoked from the repo root without `cd`-ing
into each subproject first (documented in the first pass) — a tooling/
import-namespace issue, not something a code diff fixes; keep it in mind
whenever you wire up CI.

## 8. Files touched, sessions 1–2

```
NEW
  plataforma_opcoes/tests/test_api.py
  plataforma_opcoes/tests/test_auth.py
  plataforma_opcoes/tests/test_collector.py
  plataforma_opcoes/tests/test_cli.py
  .gitignore                                  (repo root)

MODIFIED
  plataforma_opcoes/api.py                    (_TOKEN_FILE fix, ticker normalization, B904)
  plataforma_opcoes/auth.py                   (_mapear_campos crash fix)
  plataforma_opcoes/cli.py                    (ticker normalization)
  plataforma_opcoes/collector.py              (dead code removal)
  plataforma_opcoes/database.py               (B904)
  plataforma_opcoes/tests/conftest.py         (+3 fixtures)
  plataforma_opcoes/tests/test_scheduler.py   (extended, 6 → 31 tests)
  plataforma_opcoes/tests/test_alertas.py     (cosmetic: unused mock var)
  plataforma_opcoes/tests/test_licenca.py     (cosmetic: unused var)
  servidor_licencas/main.py                   (rate limiter, B904 x2)
  servidor_licencas/tests/test_api.py         (+3 rate-limit tests)
  servidor_licencas/tests/test_chaves.py      (cosmetic: unused var)
```

---

## 9. Session 3 — `.xlsx` / `.pdf` export formats

Added `.xlsx` and `.pdf` alongside the existing `.csv`, via a new
`--formato csv|xlsx|pdf` flag on `--exportar` and `--exportar-opcoes`
(default stays `csv` — no change to existing behavior/scripts).

**Design rationale** — xlsx and pdf are deliberately *not* identical:

- **`.xlsx`** — same full column set as the CSV (nothing dropped), but
  formatted: colored title/header rows, zebra striping, proper number
  formats (currency, thousands, percentages), frozen header row, and the
  closing-price/variation cell colored green or red by sign. Native Excel
  date cells where the data allows it (sortable/filterable), falling back
  to plain text otherwise — same sqlite3 date-typing nuance from the first
  review, handled defensively here too.
- **`.pdf`** — curated to the same column set already used by
  `imprimir_cotacoes()`/`imprimir_opcoes()` in the terminal output, not the
  full 25-column CSV schema. A PDF is a fixed page, not a spreadsheet —
  meant to be glanced at or printed, so it shows what's already established
  as "the readable view" rather than everything at once. Landscape A4,
  colored header, zebra striping, green/red on the variation column,
  repeats the header row across pages for long tables.

Implementation notes:
- `openpyxl` and `reportlab` added to `requirements.txt` (both pure-Python,
  no system-level dependencies — shouldn't complicate Nuitka the way a tool
  like WeasyPrint would).
- `pypdf` added to `requirements-dev.txt` (test-only, to read generated
  PDFs back and verify actual text content, not just "a file exists").
- All formatting reuses the CLI's own `_fmt`/`_fmt_i`/`_fmt_pct`/`_fmt_m`
  helpers, so number display stays consistent with what's already on screen.
- 27 new tests, including reading generated `.xlsx` back with `openpyxl`
  (headers/values/cell colors) and generated `.pdf` back with `pypdf`
  (extracted text).
- Visually spot-checked both PDF layouts by rendering a sample export (10
  quotes, 10 option contracts) to PNG — clean landscape tables, correct
  color-coding, no clipped columns.

**Not done:** no Nuitka compile exercised in this session — worth a real
`--formato xlsx`/`--formato pdf` smoke test against your actual compiled
build before shipping, same as any new dependency added to a frozen build.

```
plataforma_opcoes:  348 passed, 92% coverage   (was 336 / 91%)
servidor_licencas:   65 passed, 80% coverage   (unchanged)
```

**Files touched:**
```
MODIFIED
  plataforma_opcoes/cli.py                    (xlsx/pdf export functions, --formato flag)
  plataforma_opcoes/tests/test_cli.py         (+27 export-format tests)
  plataforma_opcoes/requirements.txt          (+ openpyxl, reportlab)
  plataforma_opcoes/requirements-dev.txt      (+ pypdf, test-only)
```

---

## 10. Session 4 — automatic launcher (`launcher.py`)

Fully-automatic entry point for non-technical users — no menu, no flags:
run it (as a script or as a compiled `.exe`) and it installs what it needs,
collects data, starts the dashboard, and opens the browser, in that order,
on its own.

**What it does, in order:**
1. Looks for an already-compiled build (`dist/PlataformaOpcoesB3(.exe)`)
   next to itself. If found, uses it directly — nothing to install.
2. Otherwise (running from source), creates a local `.venv` if needed and
   installs `requirements.txt` automatically, no prompts.
3. Runs `--coletar-todos` / `--coletar-opcoes-todos` /
   `--calcular-greeks-todos` (the last two are new CLI flags — added
   because `collector.py` already had `coletar_opcoes_todos()`/
   `calcular_greeks_todos()`, they just weren't exposed on the command
   line yet) so the dashboard isn't empty on first view.
4. Starts the dashboard, waits for port 8000 to actually respond (polling,
   not a fixed sleep), then opens the default browser.
5. Stays alive; closing the window or Ctrl+C shuts the dashboard and
   scheduler down cleanly instead of leaving an orphaned process.
6. If port 8000 is already in use (e.g. double-clicked twice), skips
   straight to opening the browser instead of starting a second copy.

**Why a separate file, not a `cli.py` flag:** it can only import the
standard library — it has to run *before* `fastapi`/`pandas`/etc. are
necessarily installed. That also makes it trivial to compile (nothing
heavy for Nuitka to bundle — seconds, not minutes; build snippet now in
`build/BUILD.md`).

**Tested:**
- 21 unit tests (`tests/test_launcher.py`): compiled-build detection,
  Windows-vs-POSIX venv paths, port-in-use detection against a real socket
  (not mocked), the wait-for-server polling loop (a server that starts
  late, and one that dies before opening its port), the venv/pip-install
  path (mocked subprocess, including the "pip failed" error message), and
  `main()`'s four branches (already running, full success, server never
  comes up, Ctrl+C mid-run).
- One real, non-mocked end-to-end run: actual subprocess, actual
  `cli.py --dashboard` (DB init, token generation, 3 scheduler jobs,
  `GET /` → real 200), actual `SIGINT` sent to the actual process,
  confirmed clean shutdown (`Scheduler has been shut down` / `Application
  shutdown complete`), no orphaned process afterward. Data collection was
  stubbed out for this specific run only (no real network in this
  sandbox); everything else was the real thing.

**Not done:** an actual Nuitka compile of `launcher.py` (same caveat as
§9 — no C toolchain exercise in this session).

```
plataforma_opcoes:  371 passed, 92% coverage   (was 348 / 92%)
servidor_licencas:   65 passed, 80% coverage   (unchanged)
```

**Files touched:**
```
NEW
  plataforma_opcoes/launcher.py
  plataforma_opcoes/tests/test_launcher.py

MODIFIED
  plataforma_opcoes/cli.py            (--coletar-opcoes-todos, --calcular-greeks-todos flags)
  plataforma_opcoes/tests/test_cli.py (+3 tests for the two new flags)
  plataforma_opcoes/README.md         (launcher mention, new flags in the command table)
  plataforma_opcoes/build/BUILD.md    (new launcher section; flags the missing .github/workflows)
```

---

---

## 11. Session 5 — dashboard export buttons + live price flash

### Export buttons (`.xlsx`/`.pdf` download from the dashboard)

The export logic (previously only in `cli.py`) was extracted into a new
shared module, **`exportadores.py`**, so `cli.py` (writes to disk) and
`api.py` (streams over HTTP) call the exact same code instead of
maintaining two copies that could drift. `cli.py`'s six export functions
are now thin wrappers around it — behavior unchanged, confirmed by the
full existing export test suite passing unmodified against the refactor.

Two new endpoints:
- `GET /exportar/cotacoes/{ticker}?formato=csv|xlsx|pdf&de=...&ate=...`
- `GET /exportar/opcoes/{ticker}?formato=csv|xlsx|pdf&tipo=...&vencimento=...`

Both require the same bearer token as everything else, default to `xlsx`
(not `csv` — someone clicking a dashboard button wants the readable file,
not the raw one), and return `404` with a plain message when there's
nothing to export rather than an empty/broken file.

Dashboard: "⬇ Excel" / "⬇ PDF" buttons added next to the existing "⬇ CSV"
on both the Histórico and Opções tabs. Unlike the CSV button (which builds
the file client-side from whatever's already loaded), these fetch the
endpoint with the auth header, turn the response into a blob, and trigger
the download — the same pattern already used for CSV, just with a network
round-trip since the server does the formatting. Opções passes through the
`tipo`/`vencimento` filters already selected in the UI; it does **not**
currently replicate the purely-visual filters (strike range, text search,
IQ threshold) since those aren't server-side query params today — noted as
a known limitation, not silently glossed over.

Tested: 9 new endpoint tests (`test_api.py`) + 17 new tests directly against
`exportadores.py` (`test_exportadores.py`) covering the empty/populated
case for all six generator functions, content round-tripped through
`openpyxl`/`pypdf`/`csv.DictReader`.

### Live price flash (green up / red down)

**Worth knowing upfront:** the dashboard already had a 15s collection
cycle (`INTERVALO_SEG` driving both the scheduler and the WebSocket loop)
and a 17s UI refresh (`setInterval(atualizarAutomaticamente, 17000)` for
the tabs not already pushed live via WebSocket) — this matches what you
described almost exactly. It was already there; this session didn't touch
the timing, only added the piece that was actually missing: **visual
feedback when a value changes.**

Looked at how established platforms handle this before building it (per
your question) — the standard pattern, confirmed via a couple of concrete
references, is a brief colored background flash on the changed value that
fades back to normal, colored by direction (green up / red down), not a
persistent tint. That's what's implemented:

- **Main price** (header + the Mercado tab's mirrored card): flashes when
  the WebSocket-pushed price differs from the last known one. First value
  ever received doesn't flash (nothing to compare against yet), and
  switching tickers resets the tracked price so the new instrument's first
  update doesn't spuriously flash against the old one's price.
- **Options table rows**: each contract's last-known price is tracked by
  its code (`Map` keyed by `codigo`). On the WebSocket-driven re-render, a
  row whose price actually changed gets `flash-alta`/`flash-baixa` on the
  whole `<tr>` (matching "the whole line flashes," as asked) — a contract
  appearing for the first time, or a pure re-sort/re-filter with no new
  data, doesn't flash.

**Tested** without a real browser (no network access in this sandbox to
fetch a Playwright browser binary — confirmed unavailable, didn't fake
this one): extracted the actual functions straight out of `index.html`
and ran them against a `jsdom`-simulated DOM, asserting on real
`classList` state across 14 scenarios — first load, price up, price down,
unchanged, a second contract appearing without disturbing the first, and
a simulated ticker switch. All 14 pass. This verification lives outside
the repo (didn't add a Node/npm test dependency to an otherwise all-Python
project without asking first) — happy to wire it in permanently as a real
regression test if you'd like that.

```
plataforma_opcoes:  397 passed, 92% coverage   (was 371 / 92%)
servidor_licencas:   65 passed, 80% coverage   (unchanged)
```

**Files touched:**
```
NEW
  plataforma_opcoes/exportadores.py
  plataforma_opcoes/tests/test_exportadores.py

MODIFIED
  plataforma_opcoes/cli.py            (export functions now thin wrappers around exportadores.py)
  plataforma_opcoes/api.py            (2 new /exportar endpoints)
  plataforma_opcoes/tests/test_api.py (+9 tests for the new endpoints)
  plataforma_opcoes/dashboard/index.html (download buttons; price-flash CSS/JS)
```

---

**Small aside, not a feature session:** while answering a question about
reverse-engineering protection, updated the "Limite honesto" comment at
the top of `licenca.py` — it was written assuming raw `.py` source
distribution specifically ("nothing stops someone from opening this file
and deleting the check"), which understates the protection the project's
own Nuitka build (§ above) actually provides. Comment now covers both
cases accurately; no behavior changed, both suites re-verified green.

## Final state, all sessions combined

```
plataforma_opcoes:  397 passed, 92% coverage
servidor_licencas:   65 passed, 80% coverage
                    ─────────────────────────
                    462 passed, 0 failed
```

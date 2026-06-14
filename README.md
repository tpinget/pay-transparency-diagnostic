# EU Pay Transparency Diagnostic
**Directive EU 2023/970 | Anthropic Paris Candidacy Portfolio**  
Author: Timothée Pinget | Model: claude-sonnet-4-6

---

## Why this prototype

The EU Pay Transparency Directive (2023/970) requires all European organisations to
disclose salary ranges at recruitment, grant employees the right to access pay
criteria on request, and report gender pay gaps — with the first hard deadline
falling on **7 June 2026**.

Most CHROs I work with in HR transformation consulting (Mercer) know this deadline
exists. Very few have a clear picture of where their organisation actually stands.
Traditional compliance audits cost €20–50K and take weeks. This tool delivers a
structured maturity snapshot in under 15 minutes.

The diagnostic maps 27 questions across 5 dimensions (governance, job classification,
data & reporting, employee rights, remediation) onto a weighted score, then uses
Claude to generate an actionable report tailored to the organisation's country
footprint and headcount thresholds. The output is what a CHRO needs to walk into
a board meeting: a risk level, a calendar of obligations by country, and three
concrete next steps.

This prototype was built as part of my candidacy for the Solutions Architect,
Applied AI role at Anthropic Paris. It is designed to demonstrate that AI can
deliver genuine enterprise value — not as a chatbot layer, but as the analytical
core of a decision-support tool.

---

## Architecture

```
.env                  ← API key (never committed)
serve.py              ← Local server: reads .env, proxies Claude API
index.html            ← Browser client: auto-detects server key
diagnostic_cli.py     ← Terminal version (standalone)
locales/              ← i18n JSON files (fr, en, de, es, nl)
```

### How the key detection works

```
Browser loads index.html
    │
    ├─ GET /api/config ──► serve.py ──► { has_key: true }
    │       ↓
    │   Key found → skip manual entry, go straight to diagnostic
    │
    └─ No server / no key → show manual key entry form (standalone mode)
```

The API key **never leaves the server**. The browser calls `/api/diagnostic`,
which serve.py forwards to Anthropic with the key injected server-side.

---

## Quick start — recommended (server mode)

```bash
# 1. Clone
git clone https://github.com/tpinget/pay-transparency-diagnostic
cd pay-transparency-diagnostic

# 2. Set your API key
cp .env.example .env
# Edit .env → ANTHROPIC_API_KEY=sk-ant-...

# 3. Install dependency
pip install anthropic

# 4. Start
python serve.py
# → http://localhost:8080
```

The browser detects the server key automatically and skips the key entry screen.

---

## Standalone mode (demo only — no server)

Open `index.html` directly in a browser (`file://`).
The app detects it is running without a server and shows a manual key entry field.
The key is stored in memory only and cleared on page reload.

> **Security note:** Standalone mode is for local demo purposes only.
> Do not share the HTML file with a key pre-filled.
> For any public deployment, use server mode with the proxy.

---

## CLI version

```bash
export ANTHROPIC_API_KEY="sk-ant-..."   # macOS/Linux
$env:ANTHROPIC_API_KEY = "sk-ant-..."  # Windows PowerShell

python diagnostic_cli.py [--lang fr|en|de|es|nl] [--out report.json]
```

Walks through the same 27-question / 5-dimension assessment as the browser
version, using the questions and weights from `locales/<lang>.json`. Scope
(countries, headcounts) is resolved live via web search — no hardcoded
deadlines — and the resulting report is printed to the terminal and exported
as JSON.

---

## Features

- 27-question Likert maturity assessment across 5 EU 2023/970 dimensions
- Scope definition: organisation, covered countries, headcounts
- Regulatory calendar engine: deadlines computed by country × headcount threshold
- Streaming analysis with adaptive follow-up questions per dimension
- 5 languages: French, English, German, Spanish, Dutch (auto-detected from browser)
- Claude-powered report: executive summary, weighted scoring, 3 priority recommendations, sector benchmark
- JSON export (CLI) / Print-to-PDF (HTML)

---

## Security

See [SECURITY.md](SECURITY.md) for the full security audit.

- API key read from `.env` server-side — never exposed to the browser
- No personal data sent to Claude (organisation-level aggregates only)
- GDPR scope: no consent banner required (no personal data collected)
- `.env` and JSON exports excluded from git via `.gitignore`

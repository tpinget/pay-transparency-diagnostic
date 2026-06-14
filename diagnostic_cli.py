"""
EU Pay Transparency Diagnostic — CLI version
Terminal counterpart to index.html: the same 27-question / 5-dimension
maturity assessment, the same live web-search scope resolution (no
hardcoded regulatory dates), and the same Claude-generated structured
report — driven entirely from the terminal, with JSON export.

Usage:
    export ANTHROPIC_API_KEY="sk-ant-..."   # macOS/Linux
    $env:ANTHROPIC_API_KEY = "sk-ant-..."  # Windows PowerShell

    python diagnostic_cli.py [--lang fr|en|de|es|nl] [--out report.json]

Reads the same locales/*.json files as index.html, so the 27 questions,
options and dimension weights stay in sync with the browser version.
"""

import os
import sys
import json
import argparse
from pathlib import Path
from datetime import date
from concurrent.futures import ThreadPoolExecutor
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError

# Avoid UnicodeEncodeError on Windows terminals (cp1252) for accented
# question text in fr/de/es/nl locales.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR          = Path(__file__).parent
ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
MODEL             = "claude-sonnet-4-6"
TODAY_ISO         = date.today().isoformat()

# ─── Load .env if present (same loader as serve.py) ─────────────────
ENV_FILE = BASE_DIR / ".env"
if ENV_FILE.exists():
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# Country names — same set and translations as PAYS_NAMES in index.html.
COUNTRIES = {
    "FR": {"fr": "France",     "en": "France",      "de": "Frankreich",   "es": "Francia",      "nl": "Frankrijk"},
    "DE": {"fr": "Allemagne",  "en": "Germany",     "de": "Deutschland",  "es": "Alemania",     "nl": "Duitsland"},
    "BE": {"fr": "Belgique",   "en": "Belgium",     "de": "Belgien",      "es": "Bélgica",      "nl": "België"},
    "NL": {"fr": "Pays-Bas",   "en": "Netherlands", "de": "Niederlande",  "es": "Países Bajos", "nl": "Nederland"},
    "ES": {"fr": "Espagne",    "en": "Spain",       "de": "Spanien",      "es": "España",       "nl": "Spanje"},
    "IT": {"fr": "Italie",     "en": "Italy",       "de": "Italien",      "es": "Italia",       "nl": "Italië"},
    "PL": {"fr": "Pologne",    "en": "Poland",      "de": "Polen",        "es": "Polonia",      "nl": "Polen"},
    "SE": {"fr": "Suède",      "en": "Sweden",      "de": "Schweden",     "es": "Suecia",       "nl": "Zweden"},
    "PT": {"fr": "Portugal",   "en": "Portugal",    "de": "Portugal",     "es": "Portugal",     "nl": "Portugal"},
    "AT": {"fr": "Autriche",   "en": "Austria",     "de": "Österreich",   "es": "Austria",      "nl": "Oostenrijk"},
    "DK": {"fr": "Danemark",   "en": "Denmark",     "de": "Dänemark",     "es": "Dinamarca",    "nl": "Denemarken"},
    "FI": {"fr": "Finlande",   "en": "Finland",     "de": "Finnland",     "es": "Finlandia",    "nl": "Finland"},
    "LU": {"fr": "Luxembourg", "en": "Luxembourg",  "de": "Luxemburg",    "es": "Luxemburgo",   "nl": "Luxemburg"},
    "IE": {"fr": "Irlande",    "en": "Ireland",     "de": "Irland",       "es": "Irlanda",      "nl": "Ierland"},
    "CZ": {"fr": "Tchéquie",   "en": "Czechia",     "de": "Tschechien",   "es": "Chequia",      "nl": "Tsjechië"},
    "RO": {"fr": "Roumanie",   "en": "Romania",     "de": "Rumänien",     "es": "Rumanía",      "nl": "Roemenië"},
    "HU": {"fr": "Hongrie",    "en": "Hungary",     "de": "Ungarn",       "es": "Hungría",      "nl": "Hongarije"},
    "SK": {"fr": "Slovaquie",  "en": "Slovakia",    "de": "Slowakei",     "es": "Eslovaquia",   "nl": "Slowakije"},
    "HR": {"fr": "Croatie",    "en": "Croatia",     "de": "Kroatien",     "es": "Croacia",      "nl": "Kroatië"},
    "SI": {"fr": "Slovénie",   "en": "Slovenia",    "de": "Slowenien",    "es": "Eslovenia",    "nl": "Slovenië"},
    "GR": {"fr": "Grèce",      "en": "Greece",      "de": "Griechenland", "es": "Grecia",       "nl": "Griekenland"},
    "EE": {"fr": "Estonie",    "en": "Estonia",     "de": "Estland",      "es": "Estonia",      "nl": "Estland"},
    "LV": {"fr": "Lettonie",   "en": "Latvia",      "de": "Lettland",     "es": "Letonia",      "nl": "Letland"},
    "LT": {"fr": "Lituanie",   "en": "Lithuania",   "de": "Litauen",      "es": "Lituania",     "nl": "Litouwen"},
}


# ─── Anthropic API call ───────────────────────────────────────────────
def call_claude(system, messages, max_tokens=1500, tools=None):
    payload = {
        "model":      MODEL,
        "max_tokens": max_tokens,
        "system":     system,
        "messages":   messages,
    }
    if tools:
        payload["tools"] = tools

    req = Request(
        ANTHROPIC_API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type":      "application/json",
            "x-api-key":         API_KEY,
            "anthropic-version": ANTHROPIC_VERSION,
        },
        method="POST",
    )
    with urlopen(req, timeout=90) as resp:
        return json.loads(resp.read())


def extract_json(raw):
    """Strip ```json fences (if any) and parse the JSON object."""
    raw = raw.strip()
    if raw.startswith("```"):
        parts = raw.split("```")
        raw = parts[1] if len(parts) > 1 else parts[0]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())


# ─── Organisational scope — resolved live via web search ─────────────
# Mirrors resolveScopeContext() in index.html: no transposition date or
# deadline is ever hardcoded — Claude is asked to verify current facts
# via web_search for each country/headcount pair.
def resolve_scope_context(country_name, headcount, lang_instr):
    fallback = {
        "transposition_status":   "—",
        "applicable_deadline":    "—",
        "country_specific_rules": "—",
        "status":                 "NOT_YET_IN_SCOPE",
    }

    system_prompt = (
        "You are a regulatory research assistant specialised in the EU Pay Transparency Directive (2023/970).\n"
        "Use the web_search tool to verify current, factual information before answering — do not rely on memorised dates.\n"
        + lang_instr + "\n\n"
        "ABSOLUTE RULES:\n"
        "- Respond ONLY with a valid JSON object. No markdown, no code fences, no text before or after.\n"
        "- The JSON must be directly parsable by json.loads() and have exactly these fields:\n"
        '  {"transposition_status": "string", "applicable_deadline": "string", "country_specific_rules": "string", "status": "IN_SCOPE|ANTICIPATION|NOT_YET_IN_SCOPE"}\n'
        "- \"status\" reflects whether the organisation is currently within the obligation's scope:\n"
        "  IN_SCOPE = the applicable deadline has already passed or falls within the next 6 months,\n"
        "  ANTICIPATION = the deadline is more than 6 months but less than 3 years away,\n"
        "  NOT_YET_IN_SCOPE = no deadline currently applies (e.g. headcount threshold not met, or 3+ years away)."
    )

    user_prompt = (
        f"Today's date is {TODAY_ISO}.\n"
        f"For the EU Pay Transparency Directive 2023/970, what is the transposition status in {country_name} "
        "as of today (transposed, in progress, deadline missed, etc.)? What is the applicable compliance "
        f"deadline for a company with {headcount} employees in {country_name} under the directive's "
        "thresholds (>250 / 100-249 / <100 employees)? Are there any country-specific rules stricter than "
        "the directive (earlier deadlines, lower thresholds, sector exceptions)? Use web search to confirm "
        "current information before answering."
    )

    try:
        data = call_claude(
            system_prompt,
            [{"role": "user", "content": user_prompt}],
            max_tokens=1500,
            tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 3}],
        )
        # With web_search enabled, content[] also contains server_tool_use /
        # web_search_tool_result blocks — the JSON answer is in the last text block.
        text_blocks = [b for b in data.get("content", []) if b.get("type") == "text"]
        if not text_blocks:
            raise ValueError("No text content in response")

        raw = text_blocks[-1]["text"].strip()
        start, end = raw.find("{"), raw.rfind("}")
        if start != -1 and end != -1 and end > start:
            raw = raw[start:end + 1]
        parsed = json.loads(raw.strip())

        return {
            "transposition_status":   parsed.get("transposition_status")   or fallback["transposition_status"],
            "applicable_deadline":    parsed.get("applicable_deadline")    or fallback["applicable_deadline"],
            "country_specific_rules": parsed.get("country_specific_rules") or fallback["country_specific_rules"],
            "status": parsed.get("status") if parsed.get("status") in
                ("IN_SCOPE", "ANTICIPATION", "NOT_YET_IN_SCOPE") else fallback["status"],
        }
    except (HTTPError, URLError, ValueError, json.JSONDecodeError, KeyError) as err:
        print(f"  ! Scope resolution failed for {country_name}: {err}", file=sys.stderr)
        return fallback


def resolve_all_scope_contexts(pays_effectifs, lang_instr, country_name_fn):
    """Resolve scope context for every selected country in parallel."""
    scope_context = {}
    with ThreadPoolExecutor(max_workers=max(1, len(pays_effectifs))) as pool:
        futures = {
            code: pool.submit(resolve_scope_context, country_name_fn(code), headcount, lang_instr)
            for code, headcount in pays_effectifs.items()
        }
        for code, future in futures.items():
            scope_context[code] = future.result()
    return scope_context


# Plain-language description of the IN_SCOPE/ANTICIPATION/NOT_YET_IN_SCOPE
# enum used internally (see resolve_scope_context). Used in build_scope_block()
# so Claude reasons about a sentence rather than an enum constant — without
# this, Claude could echo the raw "NOT_YET_IN_SCOPE"-style code verbatim into
# the executive summary / recommendations.
def describe_scope_status(status):
    return {
        "IN_SCOPE":         "in scope — the applicable deadline has already passed or falls within the next 6 months",
        "ANTICIPATION":     "anticipation phase — the applicable deadline is more than 6 months but less than 3 years away",
        "NOT_YET_IN_SCOPE": "not yet in scope — no compliance deadline currently applies for this country/headcount",
    }.get(status, "—")


def build_scope_block(pays_effectifs, scope_context, country_name_fn):
    """Mirrors buildScopeBlock() — same "--- ORGANIZATIONAL SCOPE ---" blocks
    injected into the system prompt for the final report."""
    blocks = []
    for code, headcount in pays_effectifs.items():
        ctx = scope_context.get(code, {})
        blocks.append(
            "--- ORGANIZATIONAL SCOPE ---\n"
            f"Country: {country_name_fn(code)}\n"
            f"Headcount: {headcount}\n"
            f"Transposition status: {ctx.get('transposition_status', '—')}\n"
            f"Applicable deadline: {ctx.get('applicable_deadline', '—')}\n"
            f"Country-specific rules: {ctx.get('country_specific_rules', '—')}\n"
            f"Status: {describe_scope_status(ctx.get('status'))}\n"
            "----------------------------"
        )
    return "\n\n".join(blocks)


# Mirrors computeNearestDeadlineLabel() / resolveQuestionText() in index.html:
# the last "remediation" question references the actual nearest applicable
# deadline for the selected countries instead of a hardcoded date.
def compute_nearest_deadline_label(pays_effectifs, scope_context, country_name_fn, generic_label):
    order = {"IN_SCOPE": 0, "ANTICIPATION": 1}
    best = None
    for code in pays_effectifs:
        ctx = scope_context.get(code, {})
        deadline = ctx.get("applicable_deadline")
        if not deadline or deadline == "—":
            continue
        rank = order.get(ctx.get("status"), 2)
        if best is None or rank < best["rank"]:
            best = {"code": code, "deadline": deadline, "rank": rank}
    if best is None:
        return generic_label
    if len(pays_effectifs) > 1:
        return f"{best['deadline']} ({country_name_fn(best['code'])})"
    return best["deadline"]


def resolve_question_text(text, pays_effectifs, scope_context, country_name_fn, generic_label):
    if "{deadline}" not in text:
        return text
    return text.replace("{deadline}", compute_nearest_deadline_label(pays_effectifs, scope_context, country_name_fn, generic_label))


# ─── Scoring ───────────────────────────────────────────────────────────
def compute_scores(dimensions, answers):
    """Mirrors computeScores() — weighted average of per-dimension scores."""
    global_score = 0.0
    dims = {}
    for dim in dimensions:
        reps = [r for r in answers[dim["id"]] if r is not None]
        score = round(sum(reps) / (len(reps) * 3) * 1000) / 10 if reps else 0
        dims[dim["id"]] = {"label": dim["label"], "score": score, "weight": dim["weight"]}
        global_score += score * dim["weight"]
    return {"global": round(global_score * 10) / 10, "dimensions": dims}


# ─── Final report prompt — mirrors runAnalysis() in index.html ────────
def build_report_prompts(perimetre, scores, dimensions, answers, lang_instr, scope_block):
    system_prompt = (
        (scope_block + "\n\n" if scope_block else "") +
        "You are an expert in HR regulatory compliance, specialised in EU Directive 2023/970 on pay transparency.\n"
        "You analyse organisational maturity diagnostic results and generate a structured JSON report.\n"
        + lang_instr + "\n"
        "ABSOLUTE RULES:\n"
        "- Respond ONLY with a valid JSON object. No markdown, no code fences, no text before or after.\n"
        "- The JSON must be directly parsable by json.loads().\n"
        "- Be factual, actionable, and CHRO-oriented.\n"
        "- The executive summary, dimension framing, and every recommendation must explicitly reference\n"
        "  the organisational scope above (applicable deadlines, transposition status, country-specific\n"
        "  rules, and compliance status) — never invent or assume dates not provided there.\n"
        "- Describe compliance status in plain language for a CHRO audience — never write internal\n"
        "  labels such as \"in scope\", \"anticipation phase\" or \"not yet in scope\" as standalone jargon;\n"
        "  explain what it means for the organisation instead."
    )

    dim_details = "\n".join(
        "- {label}: {score}% (weight {weight}%) | Answers: {answers}".format(
            label=scores["dimensions"][dim["id"]]["label"],
            score=scores["dimensions"][dim["id"]]["score"],
            weight=round(dim["weight"] * 100),
            answers=",".join(str(a) for a in answers[dim["id"]]),
        )
        for dim in dimensions
    )

    user_prompt = (
        f"EU PAY TRANSPARENCY DIAGNOSTIC — {perimetre['nom']}\n"
        f"Date: {perimetre['date']} | Sector: {perimetre['secteur']}\n"
        f"Countries: {', '.join(perimetre['paysEffectifs'].keys())} | Total headcount: {perimetre['effectifTotal']}\n\n"
        f"SCORES:\nGlobal weighted score: {scores['global']}%\n{dim_details}\n\n"
        'Generate a JSON report with exactly this structure:\n'
        '{\n'
        '  "executive_summary": "max 120 words, CHRO tone, mention most exposed country and nearest deadline",\n'
        '  "maturity_level": "Exposed|Early stage|Progressing|Advanced (or local equivalent)",\n'
        '  "regulatory_risk": "Critical|High|Moderate|Low (or local equivalent)",\n'
        '  "strengths": ["string","string"],\n'
        '  "recommendations": [\n'
        '    {"rank":1,"dimension":"string","action":"string","impact":"High|Medium","timeline":"0-3 months|3-6 months|6-12 months"},\n'
        '    {"rank":2,"dimension":"string","action":"string","impact":"High|Medium","timeline":"0-3 months|3-6 months|6-12 months"},\n'
        '    {"rank":3,"dimension":"string","action":"string","impact":"High|Medium","timeline":"0-3 months|3-6 months|6-12 months"}\n'
        '  ],\n'
        '  "sector_benchmark": {\n'
        '    "comment": "max 60 words, sector positioning"\n'
        '  }\n'
        '}'
    )
    return system_prompt, user_prompt


# ─── Interactive prompts ───────────────────────────────────────────────
def ask_int(prompt_text, minimum=1, hint=""):
    while True:
        raw = input(prompt_text).strip()
        try:
            value = int(raw)
            if value >= minimum:
                return value
        except ValueError:
            pass
        print(f"  {hint}" if hint else f"  Please enter an integer >= {minimum}.")


def ask_text(prompt_text):
    while True:
        raw = input(prompt_text).strip()
        if raw:
            return raw
        print("  This field is required.")


def ask_from_list(prompt_text, options, skip_first=False):
    """Numbered single-choice prompt. With skip_first, option 0 (a
    placeholder like "Select…") is hidden and not selectable."""
    print(prompt_text)
    start = 1 if skip_first else 0
    for i in range(start, len(options)):
        print(f"  {i}. {options[i]}")
    while True:
        raw = input("> ").strip()
        try:
            idx = int(raw)
            if idx >= start and idx < len(options):
                return options[idx]
        except ValueError:
            pass
        print("  Invalid choice, try again.")


def ask_countries(lang):
    codes = list(COUNTRIES.keys())
    print("\nCountries covered by the diagnostic (comma-separated numbers, e.g. 1,3):")
    for i, code in enumerate(codes, 1):
        name = COUNTRIES[code].get(lang, COUNTRIES[code]["en"])
        print(f"  {i:2d}. {name} ({code})")
    while True:
        raw = input("> ").strip()
        try:
            idxs = [int(x.strip()) for x in raw.split(",") if x.strip()]
            if idxs and all(1 <= i <= len(codes) for i in idxs):
                return [codes[i - 1] for i in idxs]
        except ValueError:
            pass
        print("  Invalid selection, try again.")


def ask_likert(question_text, options):
    print("\n" + question_text)
    for i, opt in enumerate(options):
        print(f"  {i}. {opt}")
    while True:
        raw = input("> ").strip()
        if raw in ("0", "1", "2", "3"):
            return int(raw)
        print("  Please enter a number from 0 to 3.")


# ─── Main flow ──────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="EU Pay Transparency Diagnostic — CLI")
    parser.add_argument("--lang", default="fr", choices=["fr", "en", "de", "es", "nl"],
                         help="Language for questions and the generated report (default: fr)")
    parser.add_argument("--out", default=None,
                         help="Output JSON file (default: diagnostic_<org>_<date>.json)")
    args = parser.parse_args()

    if not API_KEY:
        print("ANTHROPIC_API_KEY not set. Export it or add it to .env.", file=sys.stderr)
        sys.exit(1)

    locale = json.loads((BASE_DIR / "locales" / f"{args.lang}.json").read_text(encoding="utf-8"))
    dimensions = locale["dimensions"]
    options    = locale["options"]
    lang_instr = locale.get("claude_lang_instruction", "Respond in English.")
    country_name_fn = lambda code: COUNTRIES.get(code, {}).get(args.lang, code)  # noqa: E731

    print("=" * 60)
    print("  EU Pay Transparency Diagnostic — CLI")
    print("=" * 60)

    # ── Organisational scope ──
    nom = ask_text("\nOrganisation name: ")
    secteur = ask_from_list("\nSector:", locale["sectors"], skip_first=True)

    pays = ask_countries(args.lang)
    pays_effectifs = {}
    for code in pays:
        headcount = ask_int(f"  Headcount in {country_name_fn(code)}: ")
        pays_effectifs[code] = headcount
    country_sum = sum(pays_effectifs.values())
    effectif_total = ask_int(
        "\nTotal group headcount: ",
        minimum=country_sum,
        hint=f"Total must be >= the sum of individual country headcounts ({country_sum}).",
    )

    perimetre = {
        "nom": nom,
        "secteur": secteur,
        "paysEffectifs": pays_effectifs,
        "effectifTotal": effectif_total,
        "date": TODAY_ISO,
        "lang": args.lang,
    }

    # ── Live scope resolution (web search) ──
    print("\nResolving regulatory scope for selected countries (web search)...")
    scope_context = resolve_all_scope_contexts(pays_effectifs, lang_instr, country_name_fn)
    for code, ctx in scope_context.items():
        print(f"  {country_name_fn(code)}: {ctx['status']} — {ctx['applicable_deadline']}")

    # ── 27-question Likert assessment ──
    answers = {}
    for dim in dimensions:
        print(f"\n--- {dim['label']} ---")
        answers[dim["id"]] = []
        for q in dim["questions"]:
            text = resolve_question_text(q, pays_effectifs, scope_context, country_name_fn, locale.get("deadline_generic", "the applicable regulatory deadline"))
            answers[dim["id"]].append(ask_likert(text, options))

    # ── Scoring ──
    scores = compute_scores(dimensions, answers)

    # ── Final report ──
    print("\nGenerating report...")
    scope_block = build_scope_block(pays_effectifs, scope_context, country_name_fn)
    system_prompt, user_prompt = build_report_prompts(perimetre, scores, dimensions, answers, lang_instr, scope_block)

    try:
        data = call_claude(system_prompt, [{"role": "user", "content": user_prompt}], max_tokens=1500)
        report = extract_json(data["content"][0]["text"])
    except (HTTPError, URLError, ValueError, json.JSONDecodeError, KeyError) as err:
        print(f"\nReport generation failed: {err}", file=sys.stderr)
        sys.exit(1)

    # ── Terminal summary ──
    print("\n" + "=" * 60)
    print(f"  Global maturity score: {scores['global']}%")
    print(f"  Maturity level: {report.get('maturity_level', '—')}")
    print(f"  Regulatory risk: {report.get('regulatory_risk', '—')}")
    print("=" * 60)

    print("\nScores by dimension:")
    for dim in dimensions:
        d = scores["dimensions"][dim["id"]]
        print(f"  - {d['label']}: {d['score']}%")

    print("\nExecutive summary:")
    print(f"  {report.get('executive_summary', '—')}")

    print("\nStrengths:")
    for s in report.get("strengths", []):
        print(f"  - {s}")

    print("\nTop recommendations:")
    for r in report.get("recommendations", []):
        print(f"  {r.get('rank')}. [{r.get('dimension')}] {r.get('action')} "
              f"(impact: {r.get('impact')}, timeline: {r.get('timeline')})")

    print("\nRegulatory calendar:")
    for code, headcount in pays_effectifs.items():
        ctx = scope_context.get(code, {})
        print(f"  - {country_name_fn(code)} ({headcount} emp.): "
              f"{ctx.get('status', '—')} — deadline {ctx.get('applicable_deadline', '—')}")
        if ctx.get("country_specific_rules") and ctx["country_specific_rules"] != "—":
            print(f"    Country-specific rules: {ctx['country_specific_rules']}")

    bench = report.get("sector_benchmark", {})
    if bench.get("comment"):
        print(f"\nSector benchmark:\n  {bench['comment']}")

    # ── JSON export ──
    export = {
        "perimetre": perimetre,
        "scores": scores,
        "answers": answers,
        "scope_context": scope_context,
        "report": report,
    }
    out_path = Path(args.out) if args.out else BASE_DIR / f"diagnostic_{nom.lower().replace(' ', '_')}_{TODAY_ISO}.json"
    out_path.write_text(json.dumps(export, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nFull report exported to {out_path}")


if __name__ == "__main__":
    main()

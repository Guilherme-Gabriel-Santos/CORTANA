"""CyberSentry OWASP — Sub-módulo de testes OWASP Top 10.

Testa CSRF (proteção em formulários), Open Redirect, padrões IDOR,
CORS avançado com origens maliciosas, extração de segredos em JavaScript,
enumeração de métodos HTTP e detecção de debug mode.

⚠️  Use APENAS em alvos dos quais você tenha autorização explícita.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any
from urllib.parse import urlencode, urlparse, parse_qs, urljoin

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Padrões de Segredos em JavaScript
# ---------------------------------------------------------------------------

JS_SECRET_PATTERNS: list[dict[str, str]] = [
    {"name": "AWS Access Key", "pattern": r"(?:AKIA|ASIA)[0-9A-Z]{16}"},
    {"name": "AWS Secret Key", "pattern": r"(?:aws_secret_access_key|AWS_SECRET_ACCESS_KEY)\s*[=:]\s*['\"]?([A-Za-z0-9/+=]{40})"},
    {"name": "Google API Key", "pattern": r"AIza[0-9A-Za-z\-_]{35}"},
    {"name": "Google OAuth", "pattern": r"[0-9]+-[0-9A-Za-z_]{32}\.apps\.googleusercontent\.com"},
    {"name": "Firebase", "pattern": r"(?:firebase[a-zA-Z]*)\s*[=:]\s*['\"]([A-Za-z0-9\-_]+)['\"]"},
    {"name": "Stripe API Key", "pattern": r"(?:sk_live|pk_live|sk_test|pk_test)_[0-9a-zA-Z]{24,}"},
    {"name": "Slack Token", "pattern": r"xox[baprs]-[0-9a-zA-Z\-]{10,}"},
    {"name": "Slack Webhook", "pattern": r"https://hooks\.slack\.com/services/T[a-zA-Z0-9_]{8,}/B[a-zA-Z0-9_]{8,}/[a-zA-Z0-9_]{24,}"},
    {"name": "GitHub Token", "pattern": r"(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{36,}"},
    {"name": "JWT Token", "pattern": r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"},
    {"name": "Bearer Token", "pattern": r"[Bb]earer\s+[A-Za-z0-9\-_\.]{20,}"},
    {"name": "Private Key", "pattern": r"-----BEGIN (?:RSA |EC |DSA )?PRIVATE KEY-----"},
    {"name": "SendGrid API Key", "pattern": r"SG\.[A-Za-z0-9\-_]{22}\.[A-Za-z0-9\-_]{43}"},
    {"name": "Twilio", "pattern": r"(?:AC[a-z0-9]{32}|SK[a-z0-9]{32})"},
    {"name": "Mailgun", "pattern": r"key-[0-9a-zA-Z]{32}"},
    {"name": "PayPal/Braintree", "pattern": r"access_token\$production\$[0-9a-z]{16}\$[0-9a-f]{32}"},
    {"name": "Square OAuth", "pattern": r"sq0csp-[0-9A-Za-z\-_]{43}"},
    {"name": "Heroku API Key", "pattern": r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"},
    {"name": "Generic API Key", "pattern": r"(?:api[_-]?key|apikey|api[_-]?secret)\s*[=:]\s*['\"]([A-Za-z0-9\-_]{16,})['\"]"},
    {"name": "Generic Secret", "pattern": r"(?:secret|password|passwd|pwd|token|credentials?)\s*[=:]\s*['\"]([^'\"]{8,})['\"]"},
    {"name": "Database URL", "pattern": r"(?:mysql|postgres|postgresql|mongodb|redis|mssql)://[^\s'\"]+"},
    {"name": "Internal IP", "pattern": r"(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3})"},
    {"name": "Hardcoded Email (Corp)", "pattern": r"[a-zA-Z0-9._%+-]+@(?:gmail|hotmail|yahoo|outlook|empresa|corp|internal)\.[a-zA-Z]{2,}"},
]

# ---------------------------------------------------------------------------
# Padrões de Debug/Error Mode
# ---------------------------------------------------------------------------

DEBUG_PATTERNS: list[dict[str, str]] = [
    {"name": "Django DEBUG=True", "pattern": r"(?:Using the URLconf|You're seeing this error because|Django Version|Traceback \(most recent call last\))"},
    {"name": "Laravel Debug", "pattern": r"(?:Whoops!|ErrorException|Illuminate\\|laravel\.log)"},
    {"name": "Flask/Werkzeug Debugger", "pattern": r"(?:Werkzeug Debugger|Traceback \(most recent|The debugger caught an exception)"},
    {"name": "ASP.NET Yellow Screen", "pattern": r"(?:Server Error in|Stack Trace:|Version Information:.*ASP\.NET)"},
    {"name": "PHP Error", "pattern": r"(?:Fatal error:|Parse error:|Warning:.*on line \d+|Notice:.*on line \d+)"},
    {"name": "Java Stack Trace", "pattern": r"(?:java\.lang\.\w+Exception|at [\w.$]+\([\w.]+:\d+\))"},
    {"name": "Node.js Error", "pattern": r"(?:ReferenceError:|TypeError:.*is not a function|SyntaxError:.*Unexpected token)"},
    {"name": "Ruby on Rails Debug", "pattern": r"(?:ActionController::RoutingError|ActiveRecord::RecordNotFound|Completed \d+ .* in \d+ms)"},
    {"name": "Express.js Error", "pattern": r"(?:Cannot GET|Cannot POST|Error: ENOENT)"},
]

# ---------------------------------------------------------------------------
# Open Redirect payloads
# ---------------------------------------------------------------------------

REDIRECT_PAYLOADS: list[str] = [
    "https://evil.com",
    "//evil.com",
    "/\\evil.com",
    "https://evil.com/path",
    "//evil.com/%2f..",
    "///evil.com",
    "////evil.com",
    "https:evil.com",
    "http://evil.com",
    "https://evil.com@legitimate.com",
    "/redirect?url=https://evil.com",
]

REDIRECT_PARAMS: list[str] = [
    "redirect", "redirect_url", "redirect_uri", "return", "returnUrl",
    "return_url", "returnTo", "return_to", "next", "url", "goto",
    "target", "destination", "redir", "redirect_to", "continue",
    "forward", "forward_url", "callback", "callback_url", "ref",
]


# ---------------------------------------------------------------------------
# CSRF Checker
# ---------------------------------------------------------------------------

def test_csrf(forms: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Verifica se formulários POST possuem proteção CSRF."""
    findings: list[dict[str, Any]] = []
    csrf_token_names = {
        "csrf", "csrftoken", "csrf_token", "_csrf", "_token",
        "csrfmiddlewaretoken", "authenticity_token", "__requestverificationtoken",
        "xsrf", "xsrf-token", "_xsrf", "antiforgerytoken",
    }

    for form in forms:
        if form.get("method", "GET").upper() != "POST":
            continue

        fields = form.get("fields", [])
        field_names = {f.get("name", "").lower() for f in fields}
        has_csrf = bool(field_names & csrf_token_names)

        if not has_csrf:
            findings.append({
                "type": "CSRF",
                "subtype": "missing token",
                "severity": "HIGH",
                "param": "form",
                "method": "POST",
                "payload": "N/A",
                "evidence": (
                    f"Formulário POST em `{form.get('page', '?')}` "
                    f"(action=`{form.get('action', '')}`) sem token CSRF. "
                    f"Campos: {', '.join(f.get('name', '?') for f in fields[:6])}"
                ),
                "url": form.get("page", ""),
                "attack_scenario": (
                    "Um atacante pode criar uma página maliciosa que, ao ser visitada "
                    "por um usuário autenticado da empresa, executa ações em nome dele "
                    "sem consentimento: transferir fundos, alterar senha, mudar email de "
                    "recuperação, deletar conta ou conceder privilégios de administrador "
                    "a um perfil controlado pelo invasor."
                ),
            })

    return findings


# ---------------------------------------------------------------------------
# Open Redirect Tester
# ---------------------------------------------------------------------------

async def test_open_redirect(
    url: str,
    params: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Testa Open Redirect em parâmetros de URL."""
    findings: list[dict[str, Any]] = []
    session = requests.Session()
    session.headers["User-Agent"] = "CortanaSentry/2.0"
    session.verify = False
    loop = asyncio.get_event_loop()

    target_params = params or {}
    # Adiciona params detectados que parecem redirecionamento
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    for k in qs:
        if k.lower() in REDIRECT_PARAMS:
            target_params[k] = qs[k][0]

    for param_name in target_params:
        if param_name.lower() not in REDIRECT_PARAMS:
            continue
        for payload in REDIRECT_PAYLOADS[:5]:
            injected_qs = {**{k: v[0] if isinstance(v, list) else v for k, v in qs.items()}, param_name: payload}
            test_url = parsed._replace(query=urlencode(injected_qs)).geturl()
            try:
                resp = await loop.run_in_executor(
                    None,
                    lambda u=test_url: session.get(u, timeout=10, allow_redirects=False),
                )
                location = resp.headers.get("Location", "")
                if resp.status_code in (301, 302, 303, 307, 308) and "evil.com" in location:
                    findings.append({
                        "type": "Open Redirect",
                        "subtype": "unvalidated redirect",
                        "severity": "MEDIUM",
                        "param": param_name,
                        "method": "GET",
                        "payload": payload,
                        "evidence": f"Redirecionamento para `{location}` com status {resp.status_code}",
                        "url": test_url,
                        "attack_scenario": (
                            "Um atacante pode criar um link usando o domínio legítimo da empresa "
                            "que redireciona para uma página de phishing idêntica. Vítimas confiam "
                            "no domínio e inserem credenciais na página falsa. Também pode ser "
                            "encadeado com OAuth para roubar tokens de autenticação."
                        ),
                    })
                    break
            except Exception:
                continue

    session.close()
    return findings


# ---------------------------------------------------------------------------
# IDOR Pattern Detector
# ---------------------------------------------------------------------------

def detect_idor_patterns(routes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Detecta padrões de IDOR em URLs encontradas pelo crawler."""
    findings: list[dict[str, Any]] = []
    idor_patterns = [
        (r"/user[s]?/(\d+)", "User ID"),
        (r"/profile[s]?/(\d+)", "Profile ID"),
        (r"/account[s]?/(\d+)", "Account ID"),
        (r"/order[s]?/(\d+)", "Order ID"),
        (r"/invoice[s]?/(\d+)", "Invoice ID"),
        (r"/document[s]?/(\d+)", "Document ID"),
        (r"/file[s]?/(\d+)", "File ID"),
        (r"/download/(\d+)", "Download ID"),
        (r"/api/.*?/(\d+)", "API Resource ID"),
        (r"/ticket[s]?/(\d+)", "Ticket ID"),
        (r"/message[s]?/(\d+)", "Message ID"),
        (r"/comment[s]?/(\d+)", "Comment ID"),
        (r"/post[s]?/(\d+)", "Post ID"),
        (r"/product[s]?/(\d+)", "Product ID"),
        (r"/item[s]?/(\d+)", "Item ID"),
        (r"\?id=(\d+)", "ID Parameter"),
        (r"\?uid=(\d+)", "UID Parameter"),
        (r"\?user_id=(\d+)", "User ID Parameter"),
    ]

    seen_patterns: set[str] = set()
    for route in routes:
        url = route.get("url", "")
        for pattern, label in idor_patterns:
            match = re.search(pattern, url, re.IGNORECASE)
            if match and pattern not in seen_patterns:
                seen_patterns.add(pattern)
                findings.append({
                    "type": "IDOR",
                    "subtype": f"sequential {label}",
                    "severity": "MEDIUM",
                    "param": label,
                    "method": "GET",
                    "payload": f"Incrementar/decrementar o ID ({match.group(1)})",
                    "evidence": (
                        f"URL com {label} sequencial detectado: `{url}`. "
                        f"ID encontrado: `{match.group(1)}`. "
                        f"Testar com IDs adjacentes (ex: {int(match.group(1))-1}, {int(match.group(1))+1})"
                    ),
                    "url": url,
                    "attack_scenario": (
                        f"Se o sistema não validar que o {label} pertence ao usuário autenticado, "
                        "um atacante pode simplesmente alterar o número na URL para acessar "
                        "dados de outros clientes: faturas, pedidos, mensagens privadas, "
                        "documentos confidenciais e informações pessoais. Este tipo de falha "
                        "é extremamente comum e está no OWASP Top 10."
                    ),
                })

    return findings


# ---------------------------------------------------------------------------
# HTTP Method Enumeration
# ---------------------------------------------------------------------------

async def enumerate_http_methods(
    urls: list[str],
    max_urls: int = 20,
) -> list[dict[str, Any]]:
    """Testa quais métodos HTTP each URL aceita."""
    findings: list[dict[str, Any]] = []
    session = requests.Session()
    session.headers["User-Agent"] = "CortanaSentry/2.0"
    session.verify = False
    loop = asyncio.get_event_loop()
    dangerous_methods = {"PUT", "DELETE", "PATCH", "TRACE"}

    for url in urls[:max_urls]:
        try:
            resp = await loop.run_in_executor(
                None,
                lambda u=url: session.options(u, timeout=8),
            )
            allow = resp.headers.get("Allow", "")
            if not allow:
                continue

            methods = {m.strip().upper() for m in allow.split(",")}
            dangerous = methods & dangerous_methods

            if dangerous:
                findings.append({
                    "type": "HTTP Methods",
                    "subtype": "dangerous methods allowed",
                    "severity": "MEDIUM",
                    "param": "N/A",
                    "method": "OPTIONS",
                    "payload": "N/A",
                    "evidence": f"URL `{url}` aceita: {', '.join(sorted(methods))}. Perigosos: {', '.join(sorted(dangerous))}",
                    "url": url,
                    "attack_scenario": (
                        f"Os métodos {', '.join(sorted(dangerous))} podem permitir que "
                        "atacantes modifiquem (PUT/PATCH), deletem (DELETE) ou rastreiem "
                        "(TRACE) recursos no servidor. TRACE pode ser usado para Cross-Site "
                        "Tracing (XST), roubando cookies HttpOnly."
                    ),
                })
        except Exception:
            continue

    session.close()
    return findings


# ---------------------------------------------------------------------------
# CORS Advanced Tester
# ---------------------------------------------------------------------------

async def test_cors_advanced(url: str) -> list[dict[str, Any]]:
    """Testa CORS com origens maliciosas reais."""
    findings: list[dict[str, Any]] = []
    session = requests.Session()
    session.headers["User-Agent"] = "CortanaSentry/2.0"
    session.verify = False
    loop = asyncio.get_event_loop()

    parsed = urlparse(url)
    domain = parsed.netloc

    evil_origins = [
        "https://evil.com",
        f"https://{domain}.evil.com",
        f"https://evil{domain}",
        f"https://{domain}@evil.com",
        "null",
    ]

    for origin in evil_origins:
        try:
            resp = await loop.run_in_executor(
                None,
                lambda o=origin: session.get(
                    url, timeout=10,
                    headers={"Origin": o},
                ),
            )
            acao = resp.headers.get("Access-Control-Allow-Origin", "")
            acac = resp.headers.get("Access-Control-Allow-Credentials", "").lower()

            if acao and (acao == origin or acao == "*"):
                severity = "CRITICAL" if acac == "true" else "HIGH"
                findings.append({
                    "type": "CORS Misconfiguration",
                    "subtype": f"reflects origin ({origin})",
                    "severity": severity,
                    "param": "Origin header",
                    "method": "GET",
                    "payload": f"Origin: {origin}",
                    "evidence": (
                        f"O servidor reflete a origem maliciosa: `ACAO: {acao}`. "
                        f"Credentials: `{acac}`"
                    ),
                    "url": url,
                    "attack_scenario": (
                        "O servidor aceita requisições cross-origin de qualquer domínio. "
                        "Um atacante pode criar um site que faz requisições autenticadas "
                        "à API da empresa em nome da vítima, exfiltrando dados pessoais, "
                        "financeiros e tokens de sessão sem que o usuário perceba."
                    ),
                })
                break
        except Exception:
            continue

    session.close()
    return findings


# ---------------------------------------------------------------------------
# JavaScript Secrets Extractor
# ---------------------------------------------------------------------------

async def extract_js_secrets(
    js_urls: list[str],
    max_files: int = 30,
    callback=None,
) -> list[dict[str, Any]]:
    """Varre assets JavaScript por API keys, tokens e segredos hardcoded."""
    findings: list[dict[str, Any]] = []
    session = requests.Session()
    session.headers["User-Agent"] = "CortanaSentry/2.0"
    session.verify = False
    loop = asyncio.get_event_loop()
    total = min(len(js_urls), max_files)

    for idx, js_url in enumerate(js_urls[:max_files], 1):
        if callback and idx % 10 == 0:
            try:
                await callback(f"🔑 Extraindo segredos JS [{idx}/{total}]...")
            except Exception:
                pass

        try:
            resp = await loop.run_in_executor(
                None,
                lambda u=js_url: session.get(u, timeout=10),
            )
            if resp.status_code != 200:
                continue

            content = resp.text
            for secret_info in JS_SECRET_PATTERNS:
                matches = re.findall(secret_info["pattern"], content, re.IGNORECASE)
                if matches:
                    # Evitar duplicatas e falsos positivos óbvios
                    for match in matches[:3]:
                        match_str = match if isinstance(match, str) else str(match)
                        if len(match_str) < 8 or match_str.lower() in ("password", "12345678", "changeme"):
                            continue

                        findings.append({
                            "type": "JS Secret Leak",
                            "subtype": secret_info["name"],
                            "severity": "HIGH" if "key" in secret_info["name"].lower() or "token" in secret_info["name"].lower() else "MEDIUM",
                            "param": "JavaScript",
                            "method": "GET",
                            "payload": "N/A",
                            "evidence": (
                                f"**{secret_info['name']}** encontrado em `{js_url}`:\n"
                                f"`{match_str[:60]}{'...' if len(match_str) > 60 else ''}`"
                            ),
                            "url": js_url,
                            "attack_scenario": (
                                f"A chave/token `{secret_info['name']}` está exposta publicamente "
                                "no código JavaScript do site. Qualquer pessoa pode extraí-la "
                                "e usá-la para acessar serviços de terceiros em nome da empresa: "
                                "enviar emails, acessar armazenamento cloud, ler bancos de dados "
                                "ou realizar cobranças financeiras."
                            ),
                        })
        except Exception:
            continue

    session.close()
    return findings


# ---------------------------------------------------------------------------
# Debug Mode Detector
# ---------------------------------------------------------------------------

async def detect_debug_mode(
    urls: list[str],
    max_urls: int = 15,
) -> list[dict[str, Any]]:
    """Detecta modos de debug e stack traces expostos."""
    findings: list[dict[str, Any]] = []
    session = requests.Session()
    session.headers["User-Agent"] = "CortanaSentry/2.0"
    session.verify = False
    loop = asyncio.get_event_loop()

    # Testar URLs normais + provocar erros
    test_urls = list(urls[:max_urls])
    for url in list(urls[:5]):
        parsed = urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        test_urls.extend([
            base + "/this-page-does-not-exist-sentry-test",
            base + "/api/v1/../../../../etc/passwd",
            base + "/?id='",
        ])

    seen: set[str] = set()
    for url in test_urls:
        try:
            resp = await loop.run_in_executor(
                None,
                lambda u=url: session.get(u, timeout=10),
            )
            body = resp.text[:10000]
            for debug_info in DEBUG_PATTERNS:
                if debug_info["name"] in seen:
                    continue
                if re.search(debug_info["pattern"], body, re.IGNORECASE | re.DOTALL):
                    seen.add(debug_info["name"])
                    findings.append({
                        "type": "Debug Mode / Stack Trace",
                        "subtype": debug_info["name"],
                        "severity": "HIGH",
                        "param": "N/A",
                        "method": "GET",
                        "payload": "N/A",
                        "evidence": f"Padrão `{debug_info['name']}` detectado em `{url}`",
                        "url": url,
                        "attack_scenario": (
                            "O modo debug expõe informações internas do servidor: "
                            "caminhos de arquivos, variáveis de ambiente, queries SQL, "
                            "e stack traces completos. Atacantes usam essas informações "
                            "para mapear a arquitetura interna e direcionar exploits "
                            "com precisão cirúrgica."
                        ),
                    })
        except Exception:
            continue

    session.close()
    return findings


# ---------------------------------------------------------------------------
# HTML Comment Extractor
# ---------------------------------------------------------------------------

def extract_html_comments(html_content: str, url: str = "") -> list[dict[str, Any]]:
    """Extrai comentários HTML que podem conter informações sensíveis."""
    findings: list[dict[str, Any]] = []
    comments = re.findall(r"<!--(.*?)-->", html_content, re.DOTALL)

    sensitive_keywords = [
        "password", "passwd", "pwd", "secret", "token", "key", "api",
        "TODO", "FIXME", "HACK", "BUG", "XXX", "TEMP",
        "admin", "root", "debug", "test", "staging",
        "database", "db_", "mysql", "postgres",
        "BEGIN", "credentials", "auth",
    ]

    for comment in comments:
        comment_stripped = comment.strip()
        if len(comment_stripped) < 10:
            continue

        for keyword in sensitive_keywords:
            if re.search(keyword, comment_stripped, re.IGNORECASE):
                findings.append({
                    "type": "Information Disclosure",
                    "subtype": "HTML comment",
                    "severity": "LOW",
                    "param": "HTML",
                    "method": "GET",
                    "payload": "N/A",
                    "evidence": f"Comentário HTML sensível em `{url}`:\n```\n{comment_stripped[:300]}\n```",
                    "url": url,
                    "attack_scenario": (
                        "Desenvolvedores frequentemente deixam comentários com senhas "
                        "temporárias, endpoints internos, TODOs de segurança e informações "
                        "de infraestrutura. Atacantes vasculham o código-fonte para extrair "
                        "essas informações e usá-las como ponto de partida."
                    ),
                })
                break

    return findings

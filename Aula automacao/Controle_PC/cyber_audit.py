"""CyberSentry v2.0 — Motor de auditoria de segurança web da Cortana.

Pipeline completa de pentest automatizado com 8 fases:
  1. Crawling e inventário da superfície web (Playwright headed)
  2. Análise de headers, CSP e CORS
  3. Análise de cookies
  4. TLS/SSL e fingerprint de stack
  5. Detecção de arquivos sensíveis e sourcemaps
  6. Recon avançado (DNS, WHOIS, subdomínios, dirbusting)
  7. Fuzzing de injeções (SQLi, XSS, SSTI, CMDi, LFI)
  8. Testes OWASP (CSRF, IDOR, Open Redirect, JS Secrets, Debug Mode)

Sub-módulos:
  - cyber_recon.py  — DNS enum, WHOIS, subdomínios, dirbusting
  - cyber_fuzzer.py — SQLi, XSS, SSTI, CMDi, Path Traversal
  - cyber_owasp.py  — CSRF, IDOR, Open Redirect, JS Secrets, Debug

⚠️  Use APENAS em alvos dos quais você tenha autorização explícita.
    Os testes de Fase 7 enviam payloads ofensivos reais.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import socket
import ssl
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
import urllib3
from bs4 import BeautifulSoup

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Diretório padrão de relatórios
# ---------------------------------------------------------------------------
DEFAULT_REPORTS_DIR = Path(__file__).resolve().parent.parent.parent / "Audit_Reports"

# ---------------------------------------------------------------------------
# Constantes de verificação
# ---------------------------------------------------------------------------

SECURITY_HEADERS: dict[str, dict[str, str]] = {
    "Strict-Transport-Security": {
        "severity": "HIGH",
        "title": "HSTS (HTTP Strict Transport Security) ausente",
        "description": (
            "O header Strict-Transport-Security não está presente. "
            "O navegador pode aceitar conexões HTTP não criptografadas."
        ),
        "attack_scenario": "Um atacante na mesma rede (Wi-Fi de café, ex) pode realizar um ataque Man-in-the-Middle (MitM) fazendo um 'SSL Stripping'. Ele força a vítima a navegar na versão HTTP do site, conseguindo interceptar senhas, tokens de sessão e dados de cartão de crédito em texto claro.",
        "remediation": (
            "Adicionar o header:\n"
            "`Strict-Transport-Security: max-age=31536000; includeSubDomains; preload`"
        ),
    },
    "X-Content-Type-Options": {
        "severity": "MEDIUM",
        "title": "X-Content-Type-Options ausente",
        "description": (
            "Sem esse header o navegador pode fazer MIME-sniffing, permitindo "
            "que arquivos enviados como texto sejam interpretados como scripts."
        ),
        "attack_scenario": "Se o site permitir upload de arquivos ou refletir dados do usuário, um atacante pode fazer upload de um arquivo fingindo ser uma imagem, mas contendo código JavaScript malicioso. O navegador da vítima irá executar o script (XSS), roubando a conta do usuário.",
        "remediation": "Adicionar: `X-Content-Type-Options: nosniff`",
    },
    "X-Frame-Options": {
        "severity": "MEDIUM",
        "title": "X-Frame-Options ausente (Clickjacking)",
        "description": (
            "Sem X-Frame-Options, a página pode ser embutida em iframes "
            "maliciosos (clickjacking)."
        ),
        "attack_scenario": "O atacante cria um site falso com um botão 'Ganhe um prêmio'. Por trás desse botão, ele carrega o sistema da empresa invadida de forma invisível. Quando a vítima clica no botão, ela na verdade está clicando para 'Transferir fundos' ou 'Deletar conta' dentro do sistema da empresa, sem perceber.",
        "remediation": "Adicionar: `X-Frame-Options: DENY` ou `SAMEORIGIN`",
    },
    "Content-Security-Policy": {
        "severity": "MEDIUM",
        "title": "Content-Security-Policy (CSP) ausente",
        "description": (
            "Sem CSP, o navegador não tem restrições sobre quais recursos "
            "podem ser carregados, facilitando ataques XSS."
        ),
        "attack_scenario": "Sendo a última linha de defesa, a falta de CSP significa que se houver qualquer brecha de Injeção de Código (XSS) no site, o atacante poderá injetar um script externo (como um Keylogger invisível) ou usar o site da empresa para instalar malwares e minerar criptomoedas nas máquinas dos clientes.",
        "remediation": (
            "Implementar uma política CSP. Exemplo mínimo:\n"
            "`Content-Security-Policy: default-src 'self'; script-src 'self'`"
        ),
    },
    "Referrer-Policy": {
        "severity": "LOW",
        "title": "Referrer-Policy ausente",
        "description": (
            "O Referer completo pode vazar caminhos internos e tokens de query string "
            "para terceiros."
        ),
        "attack_scenario": "Se o sistema da empresa usar tokens na URL (ex: `site.com/reset-password?token=123`), ao clicar em qualquer link externo na página, o site de destino recebe essa URL completa. O dono desse outro site pode registrar o token e assumir imediatamente o controle da conta da vítima.",
        "remediation": "Adicionar: `Referrer-Policy: strict-origin-when-cross-origin`",
    },
    "Permissions-Policy": {
        "severity": "LOW",
        "title": "Permissions-Policy ausente",
        "description": (
            "Sem Permissions-Policy, o navegador não restringe acesso a câmera, "
            "microfone, geolocalização e outras APIs sensíveis."
        ),
        "attack_scenario": "Um atacante que consiga injetar um script leve em um portal de notícias ou blog corporativo poderia ativar sub-recursos (um componente de publicidade, por exemplo) para acessar as permissões concedidas pelo usuário (como câmera ou microfone) silenciosamente.",
        "remediation": (
            "Adicionar: `Permissions-Policy: camera=(), microphone=(), geolocation=()`"
        ),
    },
    "X-XSS-Protection": {
        "severity": "INFO",
        "title": "X-XSS-Protection ausente",
        "description": (
            "Header legado, mas ainda ajuda navegadores antigos a bloquear "
            "respostas que detectem reflexão de XSS."
        ),
        "attack_scenario": "-",
        "remediation": "Adicionar: `X-XSS-Protection: 1; mode=block`",
    },
}

SENSITIVE_PATHS: list[tuple[str, str, str]] = [
    ("/.git/HEAD", "CRITICAL", "Repositório Git exposto"),
    ("/.git/config", "CRITICAL", "Configuração Git exposta"),
    ("/.env", "CRITICAL", "Arquivo .env exposto"),
    ("/.env.local", "CRITICAL", "Arquivo .env.local exposto"),
    ("/.env.production", "CRITICAL", "Arquivo .env.production exposto"),
    ("/.env.backup", "CRITICAL", "Backup de .env exposto"),
    ("/wp-config.php", "CRITICAL", "Configuração do WordPress exposta"),
    ("/config.php", "HIGH", "Arquivo de configuração PHP exposto"),
    ("/config.yml", "HIGH", "Arquivo de configuração YAML exposto"),
    ("/config.json", "HIGH", "Arquivo config.json exposto"),
    ("/phpinfo.php", "HIGH", "phpinfo() acessível publicamente"),
    ("/.htpasswd", "CRITICAL", "Arquivo de senhas Apache exposto"),
    ("/.htaccess", "MEDIUM", "Configuração Apache exposta"),
    ("/web.config", "MEDIUM", "Configuração IIS exposta"),
    ("/server-status", "MEDIUM", "Apache server-status exposto"),
    ("/server-info", "MEDIUM", "Apache server-info exposto"),
    ("/robots.txt", "INFO", "robots.txt encontrado"),
    ("/sitemap.xml", "INFO", "Sitemap encontrado"),
    ("/crossdomain.xml", "LOW", "Política Flash crossdomain encontrada"),
    ("/security.txt", "INFO", "Arquivo security.txt encontrado"),
    ("/.well-known/security.txt", "INFO", "security.txt (.well-known) encontrado"),
    ("/package.json", "MEDIUM", "package.json exposto (dependências Node.js)"),
    ("/package-lock.json", "MEDIUM", "package-lock.json exposto"),
    ("/composer.json", "MEDIUM", "composer.json exposto (dependências PHP)"),
    ("/Gemfile", "MEDIUM", "Gemfile exposto (dependências Ruby)"),
    ("/requirements.txt", "LOW", "requirements.txt exposto (dependências Python)"),
    ("/Dockerfile", "MEDIUM", "Dockerfile exposto"),
    ("/docker-compose.yml", "MEDIUM", "docker-compose.yml exposto"),
    ("/.gitlab-ci.yml", "MEDIUM", "GitLab CI config exposta"),
    ("/swagger.json", "MEDIUM", "Documentação Swagger exposta"),
    ("/swagger-ui.html", "MEDIUM", "Swagger UI acessível"),
    ("/openapi.json", "MEDIUM", "Especificação OpenAPI exposta"),
    ("/api-docs", "LOW", "Endpoint de documentação de API encontrado"),
    ("/graphql", "MEDIUM", "Endpoint GraphQL encontrado"),
    ("/wp-json/", "INFO", "WordPress REST API exposta"),
    ("/wp-admin/", "INFO", "Painel admin do WordPress encontrado"),
    ("/admin/", "LOW", "Painel admin encontrado"),
    ("/administrator/", "LOW", "Painel admin encontrado"),
    ("/backup/", "HIGH", "Diretório de backup encontrado"),
    ("/backups/", "HIGH", "Diretório de backups encontrado"),
    ("/dump.sql", "CRITICAL", "Dump de banco de dados exposto"),
    ("/database.sql", "CRITICAL", "Dump database.sql exposto"),
    ("/db.sql", "CRITICAL", "Dump db.sql exposto"),
    ("/.DS_Store", "LOW", "Metadados macOS (.DS_Store) expostos"),
    ("/debug/", "MEDIUM", "Endpoint de debug encontrado"),
    ("/elmah.axd", "MEDIUM", "Log de erros ELMAH exposto"),
    ("/test/", "LOW", "Diretório de testes encontrado"),
    ("/temp/", "LOW", "Diretório temp encontrado"),
    ("/tmp/", "LOW", "Diretório tmp encontrado"),
]

TECH_SIGNATURES: dict[str, list[str]] = {
    "WordPress": [r"wp-content", r"wp-includes", r"wp-json"],
    "React": [r"_reactRoot", r"__REACT", r"react\.production"],
    "Next.js": [r"__NEXT_DATA__", r"/_next/"],
    "Nuxt.js": [r"__NUXT__", r"/_nuxt/"],
    "Vue.js": [r"__vue__", r"v-cloak", r"vue-router"],
    "Angular": [r"ng-version", r"ng-app", r"angular(?:\.min)?\.js"],
    "Svelte": [r"__svelte", r"svelte-"],
    "jQuery": [r"jquery[\.\-]", r"jQuery\.fn"],
    "Bootstrap": [r"bootstrap[\.\-]"],
    "Tailwind CSS": [r"tailwindcss", r"tw-"],
    "Laravel": [r"laravel_session", r"XSRF-TOKEN"],
    "Django": [r"csrfmiddlewaretoken", r"csrftoken"],
    "Express": [r"X-Powered-By.*Express"],
    "ASP.NET": [r"__VIEWSTATE", r"ASP\.NET_SessionId", r"X-AspNet-Version"],
    "PHP": [r"PHPSESSID", r"X-Powered-By.*PHP"],
    "Ruby on Rails": [r"_rails_", r"X-Runtime"],
    "Cloudflare": [r"cf-ray", r"cf-cache-status"],
    "Vercel": [r"x-vercel-", r"X-Vercel-Id"],
    "Netlify": [r"x-nf-request-id"],
    "Shopify": [r"shopify", r"Shopify\.theme"],
    "Wix": [r"X-Wix-"],
    "Squarespace": [r"squarespace"],
    "Nginx": [r"Server.*nginx"],
    "Apache": [r"Server.*Apache"],
    "AWS S3/CloudFront": [r"x-amz-", r"AmazonS3", r"CloudFront"],
    "Google Cloud": [r"x-goog-", r"via.*1\.1 google"],
}


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

SEVERITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "INFO": 4}


@dataclass
class Finding:
    severity: str
    category: str
    title: str
    description: str
    attack_scenario: str = ""
    evidence: str = ""
    remediation: str = ""
    references: list[str] = field(default_factory=list)
    screenshot_path: str | None = None


@dataclass
class AuditResult:
    target_url: str = ""
    domain: str = ""
    started_at: str = ""
    finished_at: str = ""
    routes: list[dict[str, Any]] = field(default_factory=list)
    forms: list[dict[str, Any]] = field(default_factory=list)
    js_assets: list[str] = field(default_factory=list)
    api_endpoints: list[dict[str, Any]] = field(default_factory=list)
    raw_headers: dict[str, str] = field(default_factory=dict)
    cookies: list[dict[str, Any]] = field(default_factory=list)
    tls_info: dict[str, Any] = field(default_factory=dict)
    technologies: list[str] = field(default_factory=list)
    sensitive_files: list[dict[str, Any]] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    screenshots: list[str] = field(default_factory=list)
    # v2.0 — Recon
    dns_records: dict[str, Any] = field(default_factory=dict)
    whois_data: dict[str, Any] = field(default_factory=dict)
    subdomains: list[dict[str, Any]] = field(default_factory=list)
    discovered_dirs: list[dict[str, Any]] = field(default_factory=list)
    # v2.0 — Injeções e OWASP
    injection_findings: list[dict[str, Any]] = field(default_factory=list)
    owasp_findings: list[dict[str, Any]] = field(default_factory=list)
    js_secrets: list[dict[str, Any]] = field(default_factory=list)
    html_comments: list[dict[str, Any]] = field(default_factory=list)

    def add(self, finding: Finding) -> None:
        self.findings.append(finding)

    def sorted_findings(self) -> list[Finding]:
        return sorted(self.findings, key=lambda f: SEVERITY_ORDER.get(f.severity, 99))


# ---------------------------------------------------------------------------
# Classe Principal
# ---------------------------------------------------------------------------


class CyberSentry:
    """Motor de auditoria de segurança web para a Cortana."""

    def __init__(
        self,
        reports_dir: Path | str = DEFAULT_REPORTS_DIR,
        headed: bool = True,
        request_delay: float = 0.4,
    ) -> None:
        self.reports_dir = Path(reports_dir)
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        self.headed = headed
        self.request_delay = request_delay
        self._cache: dict[str, AuditResult] = {}

    @staticmethod
    def _base_url(url: str) -> str:
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}"

    @staticmethod
    def _same_domain(url: str, domain: str) -> bool:
        try:
            return urlparse(url).netloc == domain
        except Exception:
            return False

    def _audit_dir(self, domain: str) -> Path:
        safe = re.sub(r"[^\w.\-]", "_", domain)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        d = self.reports_dir / f"{safe}_{ts}"
        d.mkdir(parents=True, exist_ok=True)
        return d

    async def crawl(self, url: str, max_depth: int = 3, max_pages: int = 60, callback=None) -> AuditResult:
        from playwright.async_api import async_playwright

        parsed = urlparse(url)
        domain = parsed.netloc
        base = self._base_url(url)
        audit_dir = self._audit_dir(domain)

        result = AuditResult(
            target_url=url,
            domain=domain,
            started_at=datetime.now(timezone.utc).isoformat(),
        )

        visited: set[str] = set()
        queue: list[tuple[str, int]] = [(url, 0)]
        network_log: list[dict[str, Any]] = []

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=not self.headed)
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36 CortanaSentry/1.0"
                ),
                ignore_https_errors=True,
            )
            await context.tracing.start(screenshots=True, snapshots=True, sources=False)
            page = await context.new_page()

            def _on_response(response):
                try:
                    req = response.request
                    entry = {
                        "url": req.url,
                        "method": req.method,
                        "resource_type": req.resource_type,
                        "status": response.status,
                        "content_type": response.headers.get("content-type", ""),
                    }
                    network_log.append(entry)
                    if req.resource_type == "script":
                        result.js_assets.append(req.url)
                    if req.resource_type in ("fetch", "xhr"):
                        result.api_endpoints.append(entry)
                except Exception:
                    pass

            page.on("response", _on_response)

            while queue and len(visited) < max_pages:
                current_url, depth = queue.pop(0)
                normalized = current_url.split("#")[0].rstrip("/")
                if normalized in visited:
                    continue
                visited.add(normalized)

                if callback:
                    try:
                        await callback(f"🔍 Crawling [{len(visited)}/{max_pages}]: {normalized}")
                    except Exception:
                        pass

                try:
                    resp = await page.goto(normalized, wait_until="domcontentloaded", timeout=15000)
                except Exception as exc:
                    logger.warning("[Crawler] Falha: %s — %s", normalized, exc)
                    continue

                if not resp:
                    continue

                if normalized == url.rstrip("/") or not result.raw_headers:
                    result.raw_headers = dict(resp.headers)

                result.routes.append({
                    "url": normalized,
                    "status": resp.status,
                    "content_type": resp.headers.get("content-type", ""),
                    "depth": depth,
                })

                try:
                    shot_name = hashlib.md5(normalized.encode()).hexdigest()[:10]
                    shot_path = audit_dir / f"page_{shot_name}.png"
                    await page.screenshot(path=str(shot_path), full_page=True)
                    result.screenshots.append(str(shot_path))
                except Exception:
                    pass

                try:
                    html = await page.content()
                    soup = BeautifulSoup(html, "html.parser")

                    for form in soup.find_all("form"):
                        form_data: dict[str, Any] = {
                            "page": normalized,
                            "action": form.get("action", ""),
                            "method": (form.get("method") or "GET").upper(),
                            "fields": [],
                        }
                        for inp in form.find_all(["input", "select", "textarea"]):
                            form_data["fields"].append({
                                "tag": inp.name,
                                "type": inp.get("type", "text"),
                                "name": inp.get("name", ""),
                                "id": inp.get("id", ""),
                                "value": inp.get("value", ""),
                                "hidden": inp.get("type") == "hidden",
                            })
                        result.forms.append(form_data)

                    if depth < max_depth:
                        for anchor in soup.find_all("a", href=True):
                            href = anchor["href"]
                            full = urljoin(normalized, href)
                            if self._same_domain(full, domain) and full.split("#")[0].rstrip("/") not in visited:
                                queue.append((full, depth + 1))
                except Exception as exc:
                    logger.warning("[Crawler] Parse error %s: %s", normalized, exc)

                await asyncio.sleep(self.request_delay)

            trace_path = audit_dir / "trace.zip"
            await context.tracing.stop(path=str(trace_path))
            await browser.close()

        result.js_assets = list(dict.fromkeys(result.js_assets))
        result.finished_at = datetime.now(timezone.utc).isoformat()
        self._cache[domain] = result
        return result

    def analyze_headers(self, result: AuditResult) -> None:
        headers = result.raw_headers
        if not headers:
            return
        headers_lower = {k.lower(): v for k, v in headers.items()}
        for header_name, meta in SECURITY_HEADERS.items():
            if header_name.lower() not in headers_lower:
                result.add(Finding(
                    severity=meta["severity"],
                    category="Headers de Segurança",
                    title=meta["title"],
                    description=meta["description"],
                    attack_scenario=meta.get("attack_scenario", ""),
                    evidence=f"Header `{header_name}` não encontrado na resposta.",
                    remediation=meta["remediation"],
                ))
        for leak_header in ("Server", "X-Powered-By", "X-AspNet-Version", "X-AspNetMvc-Version"):
            val = headers_lower.get(leak_header.lower())
            if val:
                result.add(Finding(
                    severity="LOW",
                    category="Information Disclosure",
                    title=f"Header `{leak_header}` revela informação de stack",
                    description=f"O header `{leak_header}: {val}` expõe detalhes da infraestrutura.",
                    attack_scenario="Atacantes utilizam essa informação para direcionar exploits específicos conhecidos contra as exatas versões da infraestrutura utilizada, economizando tempo no reconhecimento e aumentando o sucesso do ataque cibernético.",
                    evidence=f"`{leak_header}: {val}`",
                    remediation=f"Remover ou ofuscar o header `{leak_header}` no servidor.",
                ))

    async def analyze_cookies(self, url: str, result: AuditResult) -> None:
        from playwright.async_api import async_playwright
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            context = await browser.new_context(ignore_https_errors=True)
            page = await context.new_page()
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                await asyncio.sleep(1)
            except Exception:
                pass
            cookies = await context.cookies()
            await browser.close()

        for cookie in cookies:
            cookie_info: dict[str, Any] = {
                "name": cookie.get("name", ""),
                "domain": cookie.get("domain", ""),
                "path": cookie.get("path", "/"),
                "secure": cookie.get("secure", False),
                "httpOnly": cookie.get("httpOnly", False),
                "sameSite": cookie.get("sameSite", "None"),
                "expires": cookie.get("expires", -1),
            }
            result.cookies.append(cookie_info)
            issues: list[str] = []
            if not cookie_info["secure"]:
                issues.append("flag `Secure` ausente")
            if not cookie_info["httpOnly"]:
                issues.append("flag `HttpOnly` ausente")
            if cookie_info["sameSite"] == "None":
                issues.append("`SameSite=None` (enviado cross-site)")
            if issues:
                result.add(Finding(
                    severity="MEDIUM" if not cookie_info["httpOnly"] else "LOW",
                    category="Cookies",
                    title=f"Cookie `{cookie_info['name']}` com configuração insegura",
                    description=f"Problemas encontrados: {', '.join(issues)}.",
                    attack_scenario="A ausência de HttpOnly permite que qualquer vulnerabilidade XSS seja usada para extrair esse cookie. Se o cookie for a credencial de sessão de um cliente ou administrador, o hacker assumirá total controle de sua conta corporativa (Account Takeover). Sem a flag Secure, o cookie pode ser roubado em redes abertas antes do redirecionamento para HTTPS.",
                    evidence=json.dumps(cookie_info, indent=2),
                    remediation=f"Configurar o cookie `{cookie_info['name']}` com as flags `Secure; HttpOnly; SameSite=Strict`.",
                ))

    def analyze_csp(self, result: AuditResult) -> None:
        headers_lower = {k.lower(): v for k, v in result.raw_headers.items()}
        csp = headers_lower.get("content-security-policy", "")
        if not csp:
            return
        dangerous = []
        if "'unsafe-inline'" in csp:
            dangerous.append("`unsafe-inline` permite scripts inline (XSS)")
        if "'unsafe-eval'" in csp:
            dangerous.append("`unsafe-eval` permite eval() (code injection)")
        if "data:" in csp:
            dangerous.append("`data:` URI permite injeção via data scheme")
        if "*" in csp.split():
            dangerous.append("Wildcard `*` permite carregamento de qualquer origem")
        if dangerous:
            result.add(Finding(
                severity="MEDIUM",
                category="CSP (Content Security Policy)",
                title="CSP com diretivas perigosas",
                description="A política CSP contém diretivas que enfraquecem sua eficácia.",
                attack_scenario="Com 'unsafe-inline' ou 'unsafe-eval' liberados, qualquer mínimo defeito nos formulários ou campos de pesquisa do site permite a execução de JavaScript arbitrário. Criminosos cibernéticos podem explorar isso para criar falsas telas de login flutuantes na sua página real para roubar dados bancários de clientes, e os proteções do navegador não bloquearão isso.",
                evidence=f"CSP: `{csp[:300]}`\n\nProblemas:\n" + "\n".join(f"- {d}" for d in dangerous),
                remediation="Remover diretivas perigosas e usar nonces ou hashes para scripts inline.",
            ))

    def analyze_cors(self, result: AuditResult) -> None:
        headers_lower = {k.lower(): v for k, v in result.raw_headers.items()}
        acao = headers_lower.get("access-control-allow-origin", "")
        creds = headers_lower.get("access-control-allow-credentials", "").lower()
        if acao == "*":
            sev = "HIGH" if creds == "true" else "MEDIUM"
            result.add(Finding(
                severity=sev,
                category="CORS",
                title="CORS com origem wildcard (*)",
                description="O header `Access-Control-Allow-Origin: *` permite que qualquer site faça requisições cross-origin.",
                attack_scenario="Se um usuário da plataforma corporativa visitar um site externo criado por um atacante, esse site falso possuirá carta-branca para executar chamadas na API interna do cliente sob a identidade já autenticada da vítima. Ele poderia, por exemplo, furtar os dados privados lendo as respostas JSON do servidor como se fosse a aplicação legítima.",
                evidence=f"`Access-Control-Allow-Origin: {acao}`\n`Access-Control-Allow-Credentials: {creds}`",
                remediation="Restringir a origem a domínios confiáveis do escopo da própria empresa.",
            ))

    def analyze_tls(self, result: AuditResult) -> None:
        parsed = urlparse(result.target_url)
        hostname = parsed.hostname or result.domain
        port = parsed.port or 443

        if parsed.scheme != "https":
            result.add(Finding(
                severity="HIGH",
                category="TLS/SSL",
                title="Site não utiliza HTTPS",
                description="A conexão não é criptografada.",
                attack_scenario="Em conexões abertas, todos os dados de cartões de crédito e logins viajam descriptografados pelo roteador, provedor e backbones de telecomunicações, podendo ser rastreados por atacantes locais.",
                evidence=f"Esquema: `{parsed.scheme}`",
                remediation="Implementar HTTPS com certificado TLS válido.",
            ))
            return

        try:
            ctx = ssl.create_default_context()
            with ctx.wrap_socket(socket.socket(), server_hostname=hostname) as sock:
                sock.settimeout(10)
                sock.connect((hostname, port))
                cert = sock.getpeercert()
                cipher = sock.cipher()
                protocol = sock.version()
        except ssl.SSLCertVerificationError as exc:
            result.add(Finding(severity="CRITICAL", category="TLS/SSL", title="Certificado TLS inválido", description=str(exc), attack_scenario="Basta estar na mesma sub-rede ou forjar um DNS para que criminosos apontem o domínio para um servidor espelho e capturem clientes. Como o certificado está quebrado ou a raiz não é confiável, as defesas do navegador irão espantar visitantes honestos.", evidence=str(exc), remediation="Renovar e configurar adequadamente a cadeia do certificado."))
            return
        except Exception as exc:
            result.add(Finding(severity="HIGH", category="TLS/SSL", title="Não foi possível estabelecer conexão TLS", description=str(exc), attack_scenario="-", evidence=str(exc)))
            return

        not_after = cert.get("notAfter", "")
        result.tls_info = {
            "protocol": protocol,
            "cipher": cipher[0] if cipher else "unknown",
            "bits": cipher[2] if cipher and len(cipher) > 2 else 0,
            "issuer": dict(x[0] for x in cert.get("issuer", [])) if cert.get("issuer") else {},
            "subject": dict(x[0] for x in cert.get("subject", [])) if cert.get("subject") else {},
            "not_before": cert.get("notBefore", ""),
            "not_after": not_after,
            "san": [entry[1] for entry in cert.get("subjectAltName", [])],
        }

        if not_after:
            try:
                expiry = datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z")
                days_left = (expiry - datetime.now(timezone.utc).replace(tzinfo=None)).days
                if days_left < 0:
                    result.add(Finding(severity="CRITICAL", category="TLS/SSL", title="Certificado TLS expirado", description=f"Expirou há {abs(days_left)} dias.", attack_scenario="Clientes tentando acessar a loja/aplicação enfrentarão alertas vermelhos do navegador impedindo o acesso à ferramenta.", evidence=f"Expiração: `{not_after}`", remediation="Renovar o certificado TLS imediatamente."))
                elif days_left < 30:
                    result.add(Finding(severity="MEDIUM", category="TLS/SSL", title="Certificado TLS expirando em breve", description=f"Expira em {days_left} dias.", attack_scenario="-", evidence=f"Expiração: `{not_after}`", remediation="Agendar a renovação para impedir inatividade iminente da operação."))
            except Exception:
                pass

        if protocol and protocol in ("TLSv1", "TLSv1.1", "SSLv3", "SSLv2"):
            result.add(Finding(severity="HIGH", category="TLS/SSL", title=f"Protocolo TLS fraco ({protocol})", description=f"O servidor negociou `{protocol}`, considerado inseguro e vulnerável a downgrade attacks.", attack_scenario="Atacantes qualificados em posições MitM (provedores, interceptadores) podem rebaixar conexões criptografadas das sessões corporativas pelo fato da aplicação tolerar versões defasadas de handshakes.", evidence=f"Protocolo: `{protocol}`", remediation="Desabilitar TLSv1.0/1.1 e SSLv3. Usar apenas TLSv1.2 e TLSv1.3."))


    async def fingerprint(self, url: str, result: AuditResult) -> None:
        combined = json.dumps(result.raw_headers) + " "
        try:
            resp = requests.get(url, timeout=10, verify=False, headers={"User-Agent": "CortanaSentry/1.0"})
            combined += resp.text[:50000]
        except Exception:
            pass
        for cookie in result.cookies:
            combined += f" {cookie.get('name', '')}"

        detected: list[str] = []
        for tech, patterns in TECH_SIGNATURES.items():
            for pattern in patterns:
                if re.search(pattern, combined, re.IGNORECASE):
                    detected.append(tech)
                    break
        result.technologies = list(dict.fromkeys(detected))
        if result.technologies:
            result.add(Finding(
                severity="INFO",
                category="Fingerprint",
                title="Tecnologias detectadas na aplicação",
                description="As seguintes tecnologias, frameworks e serviços foram identificados.",
                evidence=", ".join(result.technologies),
                remediation="Remover headers de informação e ofuscar indicadores de stack quando possível.",
            ))

    async def probe_sensitive_files(self, url: str, result: AuditResult, callback=None) -> None:
        base = self._base_url(url)
        session = requests.Session()
        session.headers["User-Agent"] = "CortanaSentry/1.0"
        total = len(SENSITIVE_PATHS)

        for idx, (path, severity, title) in enumerate(SENSITIVE_PATHS, 1):
            if callback and idx % 10 == 0:
                try:
                    await callback(f"🔎 Probing arquivos sensíveis [{idx}/{total}]...")
                except Exception:
                    pass
            probe_url = base + path
            try:
                resp = session.get(probe_url, timeout=8, verify=False, allow_redirects=False)
            except Exception:
                continue
            if resp.status_code == 200 and len(resp.content) > 0:
                is_html = "text/html" in resp.headers.get("content-type", "")
                body_snippet = resp.text[:500].strip()
                if is_html and re.search(r"(404|not\s*found|page\s*not)", body_snippet, re.IGNORECASE):
                    continue
                result.sensitive_files.append({"path": path, "status": resp.status_code, "size": len(resp.content), "snippet": body_snippet[:200]})
                
                # Gerar um cenario dinamico
                vuln_impact = "Um atacante pode baixar o backup do banco de dados, o que resulta na exfiltração imediata de toda a base de clientes, hashes de senhas e dados financeiros confidenciais." if ".sql" in path.lower() or "backup" in path.lower() else (
                    "Esse vazamento compromete chaves privadas e credenciais de nuvem em plain-text, dando ao atacante acesso direto às nuvens da corporação e permitindo execução remota de código e sequestro dos clusters." if "env" in path or "config" in path else (
                        "O invasor faz o download integral do código fonte reservado da companhia e da arquitetura do servidor, podendo realizar engenharia reversa das defesas para aplicar zero-days locais sem interagir cegamente de fora." if ".git" in path else (
                            "Atacantes podem enumerar todas as dependências internas do projeto buscando versões antigas de componentes com CVEs públicos prontos para ser explorados via Injeção."
                        )
                    )
                )

                result.add(Finding(
                    severity=severity,
                    category="Arquivos Sensíveis e Vazamentos de Dados (Data Leak)",
                    title=f"Vazamento grave: {title}",
                    description=f"O diretório ou arquivo `{path}` sigiloso foi detectado exposto à internet de forma pública, o que burla as barreiras do sistema da empresa.",
                    attack_scenario=vuln_impact,
                    evidence=f"URL: `{probe_url}`\nStatus: `{resp.status_code}`\nTamanho: `{len(resp.content)}` bytes\n\nSnippet Visto:\n```\n{body_snippet[:200]}\n```",
                    remediation=f"Restringir privilégios HTTP publicamente sobre arquivos críticos do workspace e blindar a raiz web bloqueando requisições direta para `{path}` no servidor.",
                ))
            await asyncio.sleep(0.15)

        for js_url in result.js_assets[:30]:
            map_url = js_url + ".map"
            try:
                resp = session.head(map_url, timeout=5, verify=False, allow_redirects=False)
                if resp.status_code == 200:
                    result.sensitive_files.append({"path": map_url, "status": 200, "size": int(resp.headers.get("content-length", 0)), "type": "sourcemap"})
                    result.add(Finding(
                        severity="HIGH",
                        category="Sourcemaps e Engenharia Reversa",
                        title="Sourcemap JavaScript altamente exposto",
                        description=f"O sourcemap `{map_url}` está publicamente disponível e serve como um mapa para reverter a ofuscação do código.",
                        attack_scenario="Atacantes qualificados rastreiam o Front-end e realizam engenharia reversa perfeitamente em horas em busca de falhas invisíveis em lógicas obscuras na regra de negócio; lendo o código que deveria estar ofuscado como se vissem a máquina do Dev.",
                        evidence=f"O URL rastreado que retornou os diagramas do código foi:\n`{map_url}`",
                        remediation="Desativar `source-maps` do processo de build de produção (`npm run build`).",
                    ))
            except Exception:
                pass
        session.close()

    async def capture_screenshot(self, url: str, output_dir: Path | None = None, css_selector: str | None = None) -> str:
        from playwright.async_api import async_playwright
        if output_dir is None:
            output_dir = self._audit_dir(urlparse(url).netloc)
        slug = hashlib.md5(url.encode()).hexdigest()[:10]
        path = output_dir / f"evidence_{slug}.png"
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=not self.headed)
            page = await browser.new_page()
            await page.goto(url, wait_until="networkidle", timeout=20000)
            await asyncio.sleep(1)
            if css_selector:
                element = await page.query_selector(css_selector)
                if element:
                    await element.screenshot(path=str(path))
                else:
                    await page.screenshot(path=str(path), full_page=True)
            else:
                await page.screenshot(path=str(path), full_page=True)
            await browser.close()
        return str(path)

    async def full_audit(self, url: str, callback=None) -> AuditResult:
        """Pipeline completa de auditoria de segurança — 8 fases."""
        from cyber_recon import dns_enum, whois_lookup, subdomain_enum, dirbust
        from cyber_fuzzer import fuzz_all_inputs
        from cyber_owasp import (
            test_csrf, test_open_redirect, detect_idor_patterns,
            enumerate_http_methods, test_cors_advanced,
            extract_js_secrets, detect_debug_mode, extract_html_comments,
        )

        if callback:
            await callback("🚀 Iniciando auditoria de segurança CyberSentry v2.0...")

        # === FASE 1: Crawling ===
        if callback:
            await callback("📡 Fase 1/8 — Crawling e inventário da superfície web...")
        result = await self.crawl(url, callback=callback)

        # === FASE 2: Headers, CSP, CORS ===
        if callback:
            await callback("🛡️ Fase 2/8 — Análise de headers, CSP e CORS...")
        self.analyze_headers(result)
        self.analyze_csp(result)
        self.analyze_cors(result)

        # === FASE 3: Cookies ===
        if callback:
            await callback("🍪 Fase 3/8 — Análise de cookies...")
        await self.analyze_cookies(url, result)

        # === FASE 4: TLS/SSL + Fingerprint ===
        if callback:
            await callback("🔐 Fase 4/8 — TLS/SSL e fingerprint de stack...")
        self.analyze_tls(result)
        await self.fingerprint(url, result)

        # === FASE 5: Arquivos sensíveis ===
        if callback:
            await callback("🔎 Fase 5/8 — Detecção de arquivos sensíveis e sourcemaps...")
        await self.probe_sensitive_files(url, result, callback=callback)

        # === FASE 6: Recon Avançado ===
        if callback:
            await callback("🔍 Fase 6/8 — Recon avançado (DNS, WHOIS, subdomínios, dirbusting)...")
        try:
            result.dns_records = dns_enum(result.domain)
            result.whois_data = whois_lookup(result.domain)
            result.subdomains = await subdomain_enum(result.domain, callback=callback)
            if result.subdomains:
                result.add(Finding(
                    severity="INFO",
                    category="Recon — Subdomínios",
                    title=f"{len(result.subdomains)} subdomínios descobertos",
                    description="Subdomínios encontrados via DNS brute-force.",
                    evidence="\n".join(f"- `{s['subdomain']}` → {', '.join(s['ips'])}" for s in result.subdomains[:20]),
                    remediation="Verificar se subdomínios expostos são intencionais e se possuem as mesmas proteções do domínio principal.",
                ))
            result.discovered_dirs = await dirbust(url, callback=callback)
            new_dirs = [d for d in result.discovered_dirs if d["path"] not in {sf.get("path") for sf in result.sensitive_files}]
            for d in new_dirs:
                sev = "HIGH" if d["status"] == 200 and any(kw in d["path"].lower() for kw in ("admin", "debug", "backup", "dump", ".env", ".git")) else (
                    "MEDIUM" if d["status"] == 200 else "LOW"
                )
                result.add(Finding(
                    severity=sev,
                    category="Dirbusting",
                    title=f"Diretório/recurso encontrado: `{d['path']}`",
                    description=f"O recurso `{d['path']}` respondeu com status `{d['status']}`.",
                    attack_scenario="Diretórios e rotas ocultas expostos publicamente podem revelar painéis administrativos, backups, configurações e funcionalidades de debug que permitem acesso não autorizado.",
                    evidence=f"URL: `{d['url']}`\nStatus: `{d['status']}`\nTamanho: `{d['size']}` bytes",
                    remediation=f"Restringir o acesso a `{d['path']}` via autenticação ou bloqueio no servidor web.",
                ))
        except Exception as exc:
            logger.warning("[CyberSentry] Recon phase error: %s", exc)

        # === FASE 7: Fuzzing de Injeções ===
        if callback:
            await callback("💉 Fase 7/8 — Fuzzing de injeções (SQLi, XSS, SSTI, CMDi, LFI)...")
        try:
            # Coletar todos os parâmetros de todas as rotas
            all_params: dict[str, str] = {}
            for route in result.routes:
                from urllib.parse import urlparse as _up, parse_qs as _pq
                qs = _pq(_up(route["url"]).query)
                for k, v in qs.items():
                    if k not in all_params:
                        all_params[k] = v[0]

            urls_with_params = [r["url"] for r in result.routes if "?" in r["url"]]
            for test_url in urls_with_params[:10]:  # Limitar a 10 URLs para performance
                injection_results = await fuzz_all_inputs(
                    test_url,
                    forms=result.forms,
                    callback=callback,
                )
                for inj in injection_results:
                    result.injection_findings.append(inj)
                    result.add(Finding(
                        severity=inj.get("severity", "HIGH"),
                        category=f"Injeção — {inj['type']}",
                        title=f"{inj['type']} ({inj.get('subtype', '')}) em `{inj.get('param', '?')}`",
                        description=f"Payload: `{inj.get('payload', '?')}`",
                        attack_scenario=inj.get("attack_scenario", ""),
                        evidence=inj.get("evidence", ""),
                        remediation=f"Sanitizar e parametrizar todas as entradas do parâmetro `{inj.get('param', '?')}`.",
                    ))

            # Também testar forms sem query params
            if result.forms and not urls_with_params:
                injection_results = await fuzz_all_inputs(
                    url, forms=result.forms, callback=callback,
                )
                for inj in injection_results:
                    result.injection_findings.append(inj)
                    result.add(Finding(
                        severity=inj.get("severity", "HIGH"),
                        category=f"Injeção — {inj['type']}",
                        title=f"{inj['type']} ({inj.get('subtype', '')}) em `{inj.get('param', '?')}`",
                        description=f"Payload: `{inj.get('payload', '?')}`",
                        attack_scenario=inj.get("attack_scenario", ""),
                        evidence=inj.get("evidence", ""),
                        remediation=f"Sanitizar e parametrizar todas as entradas do parâmetro `{inj.get('param', '?')}`.",
                    ))
        except Exception as exc:
            logger.warning("[CyberSentry] Fuzzing phase error: %s", exc)

        # === FASE 8: OWASP Top 10 ===
        if callback:
            await callback("🏴 Fase 8/8 — Testes OWASP (CSRF, IDOR, Redirect, JS Secrets, Debug)...")
        try:
            # CSRF
            csrf_findings = test_csrf(result.forms)
            for f in csrf_findings:
                result.owasp_findings.append(f)
                result.add(Finding(
                    severity=f["severity"], category="OWASP — CSRF",
                    title=f"CSRF: {f.get('subtype', '')}",
                    description=f.get("evidence", ""),
                    attack_scenario=f.get("attack_scenario", ""),
                    remediation="Implementar tokens CSRF em todos os formulários POST.",
                ))

            # IDOR
            idor_findings = detect_idor_patterns(result.routes)
            for f in idor_findings:
                result.owasp_findings.append(f)
                result.add(Finding(
                    severity=f["severity"], category="OWASP — IDOR",
                    title=f"IDOR: {f.get('subtype', '')}",
                    description=f.get("evidence", ""),
                    attack_scenario=f.get("attack_scenario", ""),
                    remediation="Validar no backend se o recurso pertence ao usuário autenticado.",
                ))

            # HTTP Methods
            route_urls = [r["url"] for r in result.routes[:20]]
            method_findings = await enumerate_http_methods(route_urls)
            for f in method_findings:
                result.owasp_findings.append(f)
                result.add(Finding(
                    severity=f["severity"], category="OWASP — HTTP Methods",
                    title=f"Métodos perigosos: {f.get('subtype', '')}",
                    description=f.get("evidence", ""),
                    attack_scenario=f.get("attack_scenario", ""),
                    remediation="Desabilitar métodos HTTP desnecessários no servidor.",
                ))

            # CORS Avançado
            cors_findings = await test_cors_advanced(url)
            for f in cors_findings:
                result.owasp_findings.append(f)
                result.add(Finding(
                    severity=f["severity"], category="OWASP — CORS",
                    title=f"CORS: {f.get('subtype', '')}",
                    description=f.get("evidence", ""),
                    attack_scenario=f.get("attack_scenario", ""),
                    remediation="Validar origens no servidor e nunca refletir Origin arbitrário.",
                ))

            # JS Secrets
            if callback:
                await callback("🔑 Extraindo segredos do JavaScript...")
            js_findings = await extract_js_secrets(result.js_assets, callback=callback)
            for f in js_findings:
                result.js_secrets.append(f)
                result.add(Finding(
                    severity=f["severity"], category="OWASP — JS Secrets",
                    title=f"Segredo JS: {f.get('subtype', '')}",
                    description=f.get("evidence", ""),
                    attack_scenario=f.get("attack_scenario", ""),
                    remediation="Remover chaves e tokens do código JavaScript. Usar variáveis de ambiente no backend.",
                ))

            # Debug Mode
            debug_findings = await detect_debug_mode(route_urls)
            for f in debug_findings:
                result.owasp_findings.append(f)
                result.add(Finding(
                    severity=f["severity"], category="OWASP — Debug Mode",
                    title=f"Debug: {f.get('subtype', '')}",
                    description=f.get("evidence", ""),
                    attack_scenario=f.get("attack_scenario", ""),
                    remediation="Desabilitar modo debug em produção.",
                ))

            # Open Redirect
            redirect_findings = await test_open_redirect(url)
            for f in redirect_findings:
                result.owasp_findings.append(f)
                result.add(Finding(
                    severity=f["severity"], category="OWASP — Open Redirect",
                    title=f"Redirect: {f.get('subtype', '')}",
                    description=f.get("evidence", ""),
                    attack_scenario=f.get("attack_scenario", ""),
                    remediation="Validar URLs de redirecionamento contra uma whitelist de domínios autorizados.",
                ))

        except Exception as exc:
            logger.warning("[CyberSentry] OWASP phase error: %s", exc)

        # === FINALIZAÇÃO ===
        result.finished_at = datetime.now(timezone.utc).isoformat()
        self._cache[result.domain] = result
        if callback:
            findings = result.sorted_findings()
            crits = sum(1 for f in findings if f.severity == "CRITICAL")
            highs = sum(1 for f in findings if f.severity == "HIGH")
            meds = sum(1 for f in findings if f.severity == "MEDIUM")
            lows = sum(1 for f in findings if f.severity == "LOW")
            infos = sum(1 for f in findings if f.severity == "INFO")
            await callback(
                f"✅ Auditoria v2.0 completa! Total: {len(findings)} findings — "
                f"🔴 {crits} CRITICAL  🟠 {highs} HIGH  🟡 {meds} MEDIUM  🔵 {lows} LOW  ⚪ {infos} INFO"
            )
        return result

    async def quick_header_audit(self, url: str) -> AuditResult:
        parsed = urlparse(url)
        result = AuditResult(target_url=url, domain=parsed.netloc, started_at=datetime.now(timezone.utc).isoformat())
        try:
            resp = requests.get(url, timeout=10, verify=False, headers={"User-Agent": "CortanaSentry/1.0"})
            result.raw_headers = dict(resp.headers)
        except Exception as exc:
            result.add(Finding(severity="HIGH", category="Conectividade", title="Não foi possível acessar o alvo", description=str(exc)))
        self.analyze_headers(result)
        self.analyze_csp(result)
        self.analyze_cors(result)
        self.analyze_tls(result)
        await self.analyze_cookies(url, result)
        await self.fingerprint(url, result)
        result.finished_at = datetime.now(timezone.utc).isoformat()
        self._cache[result.domain] = result
        return result

    def generate_markdown_report(self, result: AuditResult) -> str:
        findings = result.sorted_findings()
        crits = sum(1 for f in findings if f.severity == "CRITICAL")
        highs = sum(1 for f in findings if f.severity == "HIGH")
        meds = sum(1 for f in findings if f.severity == "MEDIUM")
        lows = sum(1 for f in findings if f.severity == "LOW")
        infos = sum(1 for f in findings if f.severity == "INFO")
        risk_score = crits * 40 + highs * 20 + meds * 10 + lows * 3
        if risk_score >= 100:
            risk_label = "🔴 CRÍTICO"
        elif risk_score >= 60:
            risk_label = "🟠 ALTO"
        elif risk_score >= 30:
            risk_label = "🟡 MÉDIO"
        elif risk_score > 0:
            risk_label = "🔵 BAIXO"
        else:
            risk_label = "🟢 NENHUM"

        lines: list[str] = []
        lines += [f"# 🛡️ Relatório de Auditoria de Segurança Web", "",
                  f"**Alvo:** `{result.target_url}`  ", f"**Domínio:** `{result.domain}`  ",
                  f"**Data:** {result.started_at[:10]}  ", f"**Auditor:** Cortana CyberSentry v1.0  ",
                  f"**Classificação de Risco:** {risk_label} (score: {risk_score})", "", "---", "",
                  "## 📋 Resumo Executivo", "",
                  f"Esta auditoria cobriu crawling automatizado, inspeção de headers, cookies, políticas CSP/CORS, certificados TLS/SSL, fingerprint de tecnologias e detecção de arquivos sensíveis.",
                  "", f"Foram identificadas **{len(findings)} observações**:", "",
                  "| Severidade | Quantidade |", "|---|---|",
                  f"| 🔴 CRITICAL | **{crits}** |", f"| 🟠 HIGH | **{highs}** |",
                  f"| 🟡 MEDIUM | **{meds}** |", f"| 🔵 LOW | **{lows}** |",
                  f"| ⚪ INFO | **{infos}** |", "", "---", "", "## 🗺️ Superfície de Ataque", "",
                  f"- **Rotas descobertas:** {len(result.routes)}",
                  f"- **Formulários encontrados:** {len(result.forms)}",
                  f"- **Assets JavaScript:** {len(result.js_assets)}",
                  f"- **Chamadas de API (XHR/Fetch):** {len(result.api_endpoints)}",
                  f"- **Arquivos sensíveis detectados:** {len(result.sensitive_files)}", ""]

        if result.technologies:
            lines += ["### 🏗️ Stack Tecnológica Detectada", ""]
            for tech in result.technologies:
                lines.append(f"- {tech}")
            lines.append("")

        if result.tls_info:
            lines += ["### 🔒 Certificado TLS", "",
                      f"- **Protocolo:** `{result.tls_info.get('protocol', 'N/A')}`",
                      f"- **Cifra:** `{result.tls_info.get('cipher', 'N/A')}`",
                      f"- **Bits:** `{result.tls_info.get('bits', 'N/A')}`",
                      f"- **Válido até:** `{result.tls_info.get('not_after', 'N/A')}`", ""]

        lines += ["---", "", "## 🔍 Findings Detalhados", ""]
        for idx, finding in enumerate(findings, 1):
            badge = {"CRITICAL": "🔴 CRITICAL", "HIGH": "🟠 HIGH", "MEDIUM": "🟡 MEDIUM", "LOW": "🔵 LOW", "INFO": "⚪ INFO"}.get(finding.severity, finding.severity)
            lines += [f"### {idx}. {badge} — {finding.title}", "",
                      f"**Categoria:** {finding.category}  ", f"**Severidade:** {finding.severity}", "",
                      f"**Descrição:**  ", finding.description, ""]
            
            if finding.attack_scenario and finding.attack_scenario != "-":
                lines += ["**IMPACTO NOS NEGÓCIOS (Vetor de Ataque):**", "", finding.attack_scenario, ""]

            if finding.evidence:
                lines += ["**Evidência:**", "", finding.evidence, ""]
            if finding.remediation:
                lines += ["**Remediação:**  ", finding.remediation, ""]
            lines += ["---", ""]

        if result.routes:
            lines += ["## 📎 Apêndice A — Rotas Descobertas", "",
                      "| # | URL | Status | Content-Type |", "|---|---|---|---|"]
            for i, route in enumerate(result.routes[:100], 1):
                lines.append(f"| {i} | `{route['url'][:80]}` | {route.get('status', '?')} | {route.get('content_type', '')[:40]} |")
            lines.append("")

        if result.forms:
            lines += ["## 📎 Apêndice B — Formulários", ""]
            for i, form in enumerate(result.forms, 1):
                lines += [f"### Formulário {i}", f"- **Página:** `{form.get('page', '')}`",
                          f"- **Action:** `{form.get('action', '')}`", f"- **Método:** `{form.get('method', 'GET')}`",
                          f"- **Campos:** {len(form.get('fields', []))}", ""]
                if form.get("fields"):
                    lines += ["| Nome | Tipo | Hidden |", "|---|---|---|"]
                    for fld in form["fields"]:
                        lines.append(f"| `{fld.get('name', '')}` | `{fld.get('type', 'text')}` | {'✅' if fld.get('hidden') else '❌'} |")
                lines.append("")

        if result.api_endpoints:
            lines += ["## 📎 Apêndice C — Chamadas de API", "",
                      "| # | Método | URL | Status |", "|---|---|---|---|"]
            seen: set[str] = set()
            count = 0
            for ep in result.api_endpoints:
                key = f"{ep.get('method', 'GET')}:{ep.get('url', '')}"
                if key in seen or count > 50:
                    continue
                seen.add(key)
                count += 1
                lines.append(f"| {count} | `{ep.get('method', 'GET')}` | `{ep.get('url', '')[:80]}` | {ep.get('status', '?')} |")
            lines.append("")

        lines += ["---", "", "## ⚠️ Aviso Legal", "",
                  "Este relatório foi gerado automaticamente pelo **Cortana CyberSentry v1.0** e tem caráter puramente informativo. "
                  "A auditoria foi conduzida de forma passiva e não-destrutiva, limitando-se a verificações públicas e análise de resposta do servidor.",
                  "", f"*Relatório gerado em {result.finished_at or datetime.now(timezone.utc).isoformat()}*"]
        return "\n".join(lines)

    async def generate_pdf_report(self, result: AuditResult, output_dir: Path | None = None) -> str:
        from playwright.async_api import async_playwright
        md_content = self.generate_markdown_report(result)
        if output_dir is None:
            output_dir = self._audit_dir(result.domain)
        md_path = output_dir / "report.md"
        md_path.write_text(md_content, encoding="utf-8")
        html_content = self._md_to_styled_html(md_content, result)
        html_path = output_dir / "report.html"
        html_path.write_text(html_content, encoding="utf-8")
        pdf_path = output_dir / "report.pdf"
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(f"file:///{html_path.resolve()}", wait_until="networkidle")
            await page.pdf(path=str(pdf_path), format="A4", margin={"top": "20mm", "bottom": "20mm", "left": "15mm", "right": "15mm"}, print_background=True)
            await browser.close()
        return str(pdf_path)

    def _md_to_styled_html(self, md_text: str, result: AuditResult) -> str:
        import re as re_mod
        html_body = md_text
        html_body = re_mod.sub(r"^### (.+)$", r"<h3>\1</h3>", html_body, flags=re_mod.MULTILINE)
        html_body = re_mod.sub(r"^## (.+)$", r"<h2>\1</h2>", html_body, flags=re_mod.MULTILINE)
        html_body = re_mod.sub(r"^# (.+)$", r"<h1>\1</h1>", html_body, flags=re_mod.MULTILINE)
        html_body = re_mod.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", html_body)
        html_body = re_mod.sub(r"`(.+?)`", r"<code>\1</code>", html_body)
        html_body = re_mod.sub(r"```\n?(.*?)```", r"<pre><code>\1</code></pre>", html_body, flags=re_mod.DOTALL)
        def _convert_table(match):
            lines = match.group(0).strip().split("\n")
            if len(lines) < 2:
                return match.group(0)
            t = '<table class="report-table">\n<thead>\n<tr>'
            for h in [c.strip() for c in lines[0].split("|") if c.strip()]:
                t += f"<th>{h}</th>"
            t += "</tr>\n</thead>\n<tbody>\n"
            for line in lines[2:]:
                cells = [c.strip() for c in line.split("|") if c.strip()]
                if cells:
                    t += "<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>\n"
            t += "</tbody>\n</table>"
            return t
        html_body = re_mod.sub(r"(?:^\|.+\|$\n?)+", _convert_table, html_body, flags=re_mod.MULTILINE)
        html_body = re_mod.sub(r"^- (.+)$", r"<li>\1</li>", html_body, flags=re_mod.MULTILINE)
        html_body = re_mod.sub(r"^---$", "<hr>", html_body, flags=re_mod.MULTILINE)
        html_body = html_body.replace("\n\n", "</p><p>").replace("  \n", "<br>\n")
        return f"""<!DOCTYPE html><html lang="pt-BR"><head><meta charset="UTF-8">
<title>Relatório de Segurança — {result.domain}</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:'Inter',-apple-system,sans-serif;color:#1a1a2e;background:#fff;line-height:1.7;font-size:11pt}}
h1{{font-size:22pt;font-weight:700;color:#0f172a;margin:30px 0 10px;padding-bottom:8px;border-bottom:3px solid #3b82f6}}
h2{{font-size:16pt;font-weight:600;color:#1e293b;margin:28px 0 10px;padding-bottom:6px;border-bottom:2px solid #e2e8f0}}
h3{{font-size:13pt;font-weight:600;color:#334155;margin:20px 0 8px}}
p{{margin:6px 0}}
code{{background:#f1f5f9;padding:2px 6px;border-radius:4px;font-family:'Cascadia Code','Fira Code',monospace;font-size:9.5pt;color:#be185d}}
pre{{background:#0f172a;color:#e2e8f0;padding:14px 18px;border-radius:8px;margin:10px 0;font-size:9pt}}
pre code{{background:none;color:inherit;padding:0}}
.report-table{{width:100%;border-collapse:collapse;margin:12px 0;font-size:10pt}}
.report-table th{{background:#1e293b;color:#fff;padding:8px 12px;text-align:left;font-weight:600}}
.report-table td{{padding:7px 12px;border-bottom:1px solid #e2e8f0}}
.report-table tr:nth-child(even) td{{background:#f8fafc}}
li{{margin:3px 0 3px 20px;list-style:disc}}
hr{{border:none;border-top:1px solid #cbd5e1;margin:20px 0}}
strong{{font-weight:600}}
</style></head><body><p>{html_body}</p></body></html>"""

    async def save_report(self, result: AuditResult) -> dict[str, str]:
        output_dir = self._audit_dir(result.domain)
        md_content = self.generate_markdown_report(result)
        md_path = output_dir / "report.md"
        md_path.write_text(md_content, encoding="utf-8")
        json_path = output_dir / "audit_data.json"
        json_path.write_text(json.dumps(asdict(result), indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        try:
            pdf_path = await self.generate_pdf_report(result, output_dir)
        except Exception as exc:
            logger.warning("[CyberSentry] Falha ao gerar PDF: %s", exc)
            pdf_path = ""
        return {"directory": str(output_dir), "markdown": str(md_path), "json": str(json_path), "pdf": pdf_path}

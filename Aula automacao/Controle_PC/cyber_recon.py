"""CyberSentry Recon — Sub-módulo de reconhecimento avançado.

Fornece DNS enumeration, WHOIS lookup, brute-force de subdomínios
e directory busting com wordlists embutidas.

⚠️  Use APENAS em alvos dos quais você tenha autorização explícita.
"""

from __future__ import annotations

import asyncio
import logging
import socket
from typing import Any
from urllib.parse import urlparse

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Wordlists embutidas
# ---------------------------------------------------------------------------

COMMON_SUBDOMAINS: list[str] = [
    "www", "api", "admin", "app", "mail", "webmail", "smtp", "pop", "imap",
    "ftp", "ssh", "vpn", "dev", "staging", "stage", "test", "testing", "qa",
    "uat", "sandbox", "demo", "beta", "alpha", "preview", "pre", "prod",
    "production", "cdn", "static", "assets", "media", "img", "images",
    "files", "upload", "uploads", "download", "downloads", "docs", "doc",
    "help", "support", "status", "monitor", "health", "dashboard", "panel",
    "portal", "login", "auth", "sso", "oauth", "account", "accounts",
    "my", "me", "profile", "user", "users", "member", "members",
    "blog", "news", "forum", "community", "wiki", "kb", "knowledge",
    "shop", "store", "checkout", "cart", "pay", "payment", "billing",
    "api-v1", "api-v2", "api2", "v1", "v2", "v3", "rest", "graphql",
    "gql", "ws", "websocket", "socket", "realtime", "live", "stream",
    "git", "gitlab", "github", "bitbucket", "jenkins", "ci", "cd",
    "deploy", "build", "release", "docker", "k8s", "kubernetes",
    "db", "database", "mysql", "postgres", "postgresql", "mongo",
    "mongodb", "redis", "elastic", "elasticsearch", "kibana", "grafana",
    "prometheus", "nagios", "zabbix", "splunk", "sentry", "log", "logs",
    "analytics", "tracking", "metrics", "report", "reports",
    "backup", "backups", "bak", "old", "legacy", "archive",
    "internal", "intranet", "extranet", "corp", "corporate",
    "crm", "erp", "hr", "finance", "sales",
    "ns1", "ns2", "ns3", "dns", "dns1", "dns2",
    "mx", "mx1", "mx2", "relay", "gateway", "proxy", "reverse",
    "cache", "edge", "node", "worker", "queue", "job", "cron",
    "s3", "storage", "bucket", "blob", "cloud",
    "mobile", "m", "wap", "android", "ios",
    "www2", "www3", "web", "web2", "site", "home", "landing",
    "remote", "rdp", "citrix", "terminal", "console",
    "phpmyadmin", "adminer", "pgadmin", "webmin", "cpanel", "plesk",
    "wp", "wordpress", "joomla", "drupal", "magento",
    "exchange", "owa", "autodiscover", "lync", "skype",
    "chat", "slack", "teams", "meet", "zoom", "video", "call",
    "ticket", "tickets", "jira", "redmine", "trello",
    "survey", "feedback", "review", "reviews",
    "careers", "jobs", "apply", "recruit",
    "events", "calendar", "booking", "reserve", "schedule",
    "search", "find", "lookup", "query",
    "notify", "notification", "notifications", "alert", "alerts",
    "push", "webhook", "webhooks", "callback", "hook",
    "config", "configuration", "settings", "setup", "install",
    "debug", "trace", "test-api", "dev-api", "staging-api",
]

DIRBUST_PATHS: list[str] = [
    # Admin panels
    "/admin", "/admin/", "/administrator", "/administrator/",
    "/admin/login", "/admin/dashboard", "/admin/config",
    "/wp-admin", "/wp-admin/", "/wp-login.php",
    "/manager", "/manager/", "/manage", "/management",
    "/cpanel", "/cpanel/", "/plesk", "/webmail",
    "/phpmyadmin", "/phpmyadmin/", "/adminer", "/adminer.php",
    "/pgadmin", "/pgadmin/", "/phpinfo.php",
    # API & Docs
    "/api", "/api/", "/api/v1", "/api/v2", "/api/v3",
    "/api/v1/", "/api/v2/", "/api/v3/",
    "/api-docs", "/api-docs/", "/swagger", "/swagger/",
    "/swagger.json", "/swagger.yaml", "/swagger-ui.html",
    "/openapi.json", "/openapi.yaml", "/redoc",
    "/graphql", "/graphql/", "/graphiql",
    "/rest", "/rest/", "/rpc",
    "/docs", "/docs/", "/documentation",
    # Auth
    "/login", "/signin", "/signup", "/register",
    "/logout", "/signout", "/forgot-password",
    "/reset-password", "/change-password",
    "/oauth", "/oauth/", "/oauth/authorize",
    "/token", "/auth", "/auth/",
    "/sso", "/sso/login", "/saml",
    "/.well-known/openid-configuration",
    # Dev & Debug
    "/debug", "/debug/", "/trace", "/trace.axd",
    "/elmah.axd", "/elmah", "/error", "/errors",
    "/test", "/test/", "/testing", "/tests",
    "/console", "/console/", "/terminal",
    "/shell", "/cmd", "/command",
    "/_debug", "/_profiler", "/_debugbar",
    "/server-status", "/server-info",
    # Config & Env
    "/.env", "/.env.local", "/.env.production", "/.env.backup",
    "/.env.dev", "/.env.staging", "/.env.old",
    "/config", "/config/", "/configuration",
    "/config.php", "/config.yml", "/config.json", "/config.xml",
    "/settings", "/settings/", "/setup", "/install",
    "/web.config", "/wp-config.php", "/wp-config.php.bak",
    "/application.yml", "/application.properties",
    # VCS & CI
    "/.git", "/.git/", "/.git/config", "/.git/HEAD",
    "/.gitignore", "/.gitattributes",
    "/.svn", "/.svn/", "/.svn/entries",
    "/.hg", "/.hg/",
    "/.gitlab-ci.yml", "/.github", "/.github/",
    "/Jenkinsfile", "/Dockerfile", "/docker-compose.yml",
    "/.circleci/config.yml", "/.travis.yml",
    # Dependencies
    "/package.json", "/package-lock.json", "/yarn.lock",
    "/composer.json", "/composer.lock",
    "/Gemfile", "/Gemfile.lock",
    "/requirements.txt", "/Pipfile", "/Pipfile.lock",
    "/go.mod", "/go.sum", "/Cargo.toml",
    "/pom.xml", "/build.gradle",
    # Backups & Dumps
    "/backup", "/backup/", "/backups", "/backups/",
    "/dump", "/dump.sql", "/database.sql", "/db.sql",
    "/data.sql", "/backup.sql", "/backup.zip", "/backup.tar.gz",
    "/site.zip", "/site.tar.gz", "/www.zip",
    "/db-backup", "/db-backup/",
    "/.bak", "/old", "/old/", "/archive", "/archive/",
    "/temp", "/temp/", "/tmp", "/tmp/",
    # Info & Meta
    "/robots.txt", "/sitemap.xml", "/sitemap_index.xml",
    "/humans.txt", "/security.txt", "/.well-known/security.txt",
    "/crossdomain.xml", "/clientaccesspolicy.xml",
    "/favicon.ico", "/manifest.json", "/browserconfig.xml",
    "/version", "/version.txt", "/version.json",
    "/changelog", "/CHANGELOG.md", "/README.md",
    "/LICENSE", "/NOTICE",
    # Static & uploads
    "/uploads", "/uploads/", "/upload", "/upload/",
    "/files", "/files/", "/media", "/media/",
    "/static", "/static/", "/assets", "/assets/",
    "/public", "/public/", "/storage", "/storage/",
    "/images", "/img", "/css", "/js",
    # CMS specific
    "/wp-content", "/wp-content/", "/wp-includes",
    "/wp-json/", "/wp-json/wp/v2/users",
    "/feed", "/feed/", "/xmlrpc.php",
    "/joomla", "/drupal", "/magento",
    "/typo3", "/typo3/", "/sitecore", "/umbraco",
    # Misc
    "/status", "/health", "/healthcheck", "/health-check",
    "/ping", "/info", "/metrics",
    "/dashboard", "/portal", "/panel",
    "/.htaccess", "/.htpasswd",
    "/.DS_Store", "/Thumbs.db",
    "/cgi-bin", "/cgi-bin/",
    "/bin", "/bin/", "/scripts", "/scripts/",
]


# ---------------------------------------------------------------------------
# DNS Enumeration
# ---------------------------------------------------------------------------

def dns_enum(domain: str) -> dict[str, Any]:
    """Enumera registros DNS de um domínio (A, AAAA, MX, NS, TXT, CNAME, SOA)."""
    results: dict[str, Any] = {"domain": domain, "records": {}}

    try:
        import dns.resolver
        resolver = dns.resolver.Resolver()
        resolver.timeout = 5
        resolver.lifetime = 10

        for rtype in ("A", "AAAA", "MX", "NS", "TXT", "CNAME", "SOA"):
            try:
                answers = resolver.resolve(domain, rtype)
                records = []
                for rdata in answers:
                    if rtype == "MX":
                        records.append({"priority": rdata.preference, "host": str(rdata.exchange).rstrip(".")})
                    elif rtype == "SOA":
                        records.append({
                            "mname": str(rdata.mname).rstrip("."),
                            "rname": str(rdata.rname).rstrip("."),
                            "serial": rdata.serial,
                            "refresh": rdata.refresh,
                            "retry": rdata.retry,
                            "expire": rdata.expire,
                        })
                    else:
                        records.append(str(rdata).strip('"'))
                if records:
                    results["records"][rtype] = records
            except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN, dns.resolver.NoNameservers):
                pass
            except Exception:
                pass
    except ImportError:
        # Fallback sem dnspython — apenas A records via socket
        logger.warning("[Recon] dnspython não instalado; usando fallback socket.")
        try:
            ips = socket.getaddrinfo(domain, None)
            a_records = list({addr[4][0] for addr in ips if addr[0] == socket.AF_INET})
            aaaa_records = list({addr[4][0] for addr in ips if addr[0] == socket.AF_INET6})
            if a_records:
                results["records"]["A"] = a_records
            if aaaa_records:
                results["records"]["AAAA"] = aaaa_records
        except socket.gaierror:
            pass

    return results


# ---------------------------------------------------------------------------
# WHOIS Lookup
# ---------------------------------------------------------------------------

def whois_lookup(domain: str) -> dict[str, Any]:
    """Coleta informações WHOIS de um domínio."""
    try:
        import whois
        w = whois.whois(domain)
        data: dict[str, Any] = {}
        for key in ("domain_name", "registrar", "creation_date", "expiration_date",
                     "updated_date", "name_servers", "status", "emails",
                     "org", "country", "state", "city"):
            val = getattr(w, key, None)
            if val is not None:
                if isinstance(val, list):
                    data[key] = [str(v) for v in val]
                else:
                    data[key] = str(val)
        return {"domain": domain, "whois": data}
    except ImportError:
        logger.warning("[Recon] python-whois não instalado. Ignorando WHOIS.")
        return {"domain": domain, "whois": {}, "error": "python-whois não instalado"}
    except Exception as exc:
        return {"domain": domain, "whois": {}, "error": str(exc)}


# ---------------------------------------------------------------------------
# Subdomain Enumeration
# ---------------------------------------------------------------------------

async def subdomain_enum(
    domain: str,
    wordlist: list[str] | None = None,
    max_concurrent: int = 50,
    callback=None,
) -> list[dict[str, Any]]:
    """Descobre subdomínios via DNS brute-force paralelo."""
    words = wordlist or COMMON_SUBDOMAINS
    found: list[dict[str, Any]] = []
    semaphore = asyncio.Semaphore(max_concurrent)
    total = len(words)

    async def _check(idx: int, prefix: str) -> None:
        fqdn = f"{prefix}.{domain}"
        async with semaphore:
            try:
                loop = asyncio.get_event_loop()
                infos = await loop.getaddrinfo(fqdn, None)
                ips = list({addr[4][0] for addr in infos})
                if ips:
                    found.append({"subdomain": fqdn, "ips": ips})
                    logger.info("[Recon] Subdomínio encontrado: %s -> %s", fqdn, ips)
            except socket.gaierror:
                pass
            except Exception:
                pass

        if callback and idx % 40 == 0:
            try:
                await callback(f"🔍 Enumerando subdomínios [{idx}/{total}]...")
            except Exception:
                pass

    tasks = [_check(i, w) for i, w in enumerate(words, 1)]
    await asyncio.gather(*tasks)
    return sorted(found, key=lambda x: x["subdomain"])


# ---------------------------------------------------------------------------
# Directory Busting
# ---------------------------------------------------------------------------

async def dirbust(
    url: str,
    wordlist: list[str] | None = None,
    max_concurrent: int = 20,
    callback=None,
) -> list[dict[str, Any]]:
    """Brute-force de diretórios via HTTP. Retorna apenas recursos com status 200/301/302/403."""
    import re

    paths = wordlist or DIRBUST_PATHS
    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    found: list[dict[str, Any]] = []
    semaphore = asyncio.Semaphore(max_concurrent)
    session = requests.Session()
    session.headers["User-Agent"] = "CortanaSentry/2.0"
    session.verify = False
    total = len(paths)

    async def _probe(idx: int, path: str) -> None:
        async with semaphore:
            probe_url = base + path
            try:
                loop = asyncio.get_event_loop()
                resp = await loop.run_in_executor(
                    None,
                    lambda: session.get(probe_url, timeout=8, allow_redirects=False),
                )
            except Exception:
                return

            if resp.status_code in (200, 301, 302, 403):
                is_html = "text/html" in resp.headers.get("content-type", "")
                body = resp.text[:500].strip() if resp.status_code == 200 else ""
                # Evita falsos positivos com páginas 404 customizadas
                if is_html and re.search(r"(404|not\s*found|page\s*not)", body, re.IGNORECASE):
                    return
                found.append({
                    "path": path,
                    "url": probe_url,
                    "status": resp.status_code,
                    "size": len(resp.content),
                    "content_type": resp.headers.get("content-type", ""),
                })

            await asyncio.sleep(0.05)

        if callback and idx % 30 == 0:
            try:
                await callback(f"📂 Dirbusting [{idx}/{total}]...")
            except Exception:
                pass

    tasks = [_probe(i, p) for i, p in enumerate(paths, 1)]
    await asyncio.gather(*tasks)
    session.close()
    return sorted(found, key=lambda x: x["path"])

"""
whatsapp_controller.py - Kira v3.1
- SW_MINIMIZE em vez de SW_HIDE (renderer continua ativo)
- Aguarda campo de busca pronto antes de conectar
- Playwright locator API (fill/press) em vez de execCommand
- Dump do DOM para diagnóstico automático
"""

import re
import threading
import time
from collections import deque
from typing import Callable, Optional

OnProgress = Callable[[int, str], None]
OnLog = Callable[[str], None]
OnMessage = Callable[[str, str], None]
OnStatus = Callable[[str], None]
OnConnected = Callable[[], None]
OnSendDone = Callable[[bool, str], None]


class WhatsAppController:

    _SEARCH_SELECTORS = [
        'input[aria-label*="Pesquisar ou começar"]',
        'input[aria-label*="Search or start"]',
        'input[aria-label*="Pesquisar"]',
        'input[aria-label*="Search"]',
        'input[role="textbox"][aria-label*="Pesquisar"]',
        'input[role="textbox"][aria-label*="Search"]',
        'input[data-tab="3"]',
        'input[role="textbox"]',
        '#_r_b_',
        '#side input',
        'input[type="text"]',
        'div[data-testid="chat-list-search"]',
        'div[role="textbox"][aria-label*="Pesquisar"]',
        'div[contenteditable="true"][aria-label*="Pesquisar"]',
        '#side div[contenteditable="true"]',
        'div[contenteditable="true"]',
    ]
    _COMPOSE_SELECTORS = [
        'div[data-testid="conversation-compose-box-input"]',
        'div[aria-label="Digite uma mensagem"]',
        'div[aria-label="Type a message"]',
        'div[role="textbox"][aria-label*="mensagem"]',
        'div[role="textbox"][aria-label*="message"]',
        'input[aria-label*="mensagem"]',
        'input[aria-label*="message"]',
        'footer div[contenteditable="true"]',
        'footer input',
        'div[contenteditable="true"][data-tab="10"]',
        'div[contenteditable="true"][data-tab="6"]',
    ]
    _CHAT_LIST_SELECTORS = [
        '[aria-label="Lista de conversas"]',
        '[aria-label="Chat list"]',
        '#pane-side',
        'div[data-testid="chat-list"]',
    ]

    def __init__(self, on_progress=None, on_log=None, on_message=None,
                 on_status=None, on_connected=None, on_send_done=None):
        self._on_progress = on_progress or (lambda v, m: None)
        self._on_log = on_log or (lambda m: None)
        self._on_message = on_message or (lambda c, t: None)
        self._on_status = on_status or (lambda s: None)
        self._on_connected = on_connected or (lambda: None)
        self._on_send_done = on_send_done or (lambda ok, m: None)

        self._stop = threading.Event()
        self._connected = False
        self._browser = None
        self._page = None
        self._hwnd = None
        self._last_badge = 0
        self._notified: dict = {}
        self._existing_unread_contacts: set = set()
        self._snapshot_previews: dict = {}
        self._pending_sends = deque()
        self._send_lock = threading.Lock()

    def start(self):
        self._stop.clear()
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self):
        self._stop.set()
        self._connected = False
        with self._send_lock:
            self._pending_sends.clear()
        try:
            if self._browser:
                self._browser.close()
        except Exception:
            pass

    def request_send(self, contact: str, message: str,
                     on_done: Optional[OnSendDone] = None,
                     notify_global: bool = True):
        with self._send_lock:
            self._pending_sends.append((contact, message, on_done, notify_global))

    # conexão

    def _run(self):
        try:
            self._log("Iniciando Playwright...", progress=8)
            from pathlib import Path
            from playwright.sync_api import sync_playwright

            with sync_playwright() as pw:
                profile = Path(__file__).resolve().parent / "kira_session"
                profile.mkdir(exist_ok=True)

                self._log("Abrindo WhatsApp Web...", progress=15)
                ctx = pw.chromium.launch_persistent_context(
                    user_data_dir=str(profile),
                    headless=False,           # deve ficar False — headless quebra o WhatsApp Web
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--no-sandbox",
                        "--disable-dev-shm-usage",
                        "--disable-gpu",
                        "--window-size=1100,800",
                        # ── ocultar da barra de tarefas do Windows ──────────────────────────
                        "--app-shell-host-window-name=kira_wpp_hidden",
                        "--disable-features=MediaSessionService",
                        # ─────────────────────────────────────────────────────────────────────
                    ],
                    viewport={"width": 1100, "height": 800},
                    locale="pt-BR",
                )

                # Oculta a janela do Chromium no Windows — invisível mas funcional
                def _hide_chromium():
                    import time as _t
                    try:
                        import win32gui, win32con, win32process
                        # Aguarda a janela aparecer (até 8 segundos)
                        deadline = _t.time() + 8
                        hwnd_found = None
                        while _t.time() < deadline and not hwnd_found:
                            def _cb(hwnd, _):
                                nonlocal hwnd_found
                                try:
                                    _, pid = win32process.GetWindowThreadProcessId(hwnd)
                                    # Verifica se é uma janela do processo Chromium
                                    title = win32gui.GetWindowText(hwnd)
                                    if (win32gui.IsWindowVisible(hwnd)
                                            and ("WhatsApp" in title or "Chrome" in title or "Chromium" in title)):
                                        hwnd_found = hwnd
                                except Exception:
                                    pass
                                return True
                            win32gui.EnumWindows(_cb, None)
                            if not hwnd_found:
                                _t.sleep(0.4)
                        if hwnd_found:
                            # SW_HIDE = 0: janela some da tela E da barra de tarefas
                            win32gui.ShowWindow(hwnd_found, win32con.SW_HIDE)
                    except Exception as e:
                        pass  # silencioso — não quebra o fluxo

                import threading as _threading
                _threading.Thread(target=_hide_chromium, daemon=True).start()

                self._browser = ctx
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                self._page = page

                page.goto(
                    "https://web.whatsapp.com",
                    wait_until="domcontentloaded",
                    timeout=60000,
                )
                self._log("Aguardando autenticação...", progress=25)

                self._wait_login()
                if self._stop.is_set():
                    return

                self._log("Aguardando interface ficar pronta...", progress=70)
                self._wait_search_ready()
                if self._stop.is_set():
                    return

                self._minimize_window()

                self._log("✅ Kira conectada!", progress=100)
                self._on_status("WhatsApp conectado")
                self._connected = True
                self._on_connected()

                self._last_badge = self._get_badge()
                self._snapshot_existing_unreads()
                self._log(
                    f"Snapshot: {self._last_badge} badge, "
                    f"{len(self._existing_unread_contacts)} contatos já ignorados."
                )
                self._monitor_loop()

        except Exception as e:
            self._log(f"❌ Erro: {e}", progress=0)
            self._on_status(f"Erro: {e}")

    def _wait_login(self):
        deadline = time.time() + 300
        while time.time() < deadline and not self._stop.is_set():
            if self._is_logged_in():
                self._log("✅ Autenticado!", progress=60)
                return
            try:
                qr = self._page.locator(
                    'canvas[aria-label="Scan me!"], div[data-ref] canvas'
                ).first
                if qr.is_visible(timeout=500):
                    self._log("📱 Escaneie o QR Code pelo celular.", progress=35)
            except Exception:
                pass
            time.sleep(2)
        if not self._is_logged_in():
            raise RuntimeError("Timeout aguardando login.")

    def _is_logged_in(self) -> bool:
        for sel in self._CHAT_LIST_SELECTORS:
            try:
                if self._page.locator(sel).count() > 0:
                    return True
            except Exception:
                pass
        return False

    def _wait_search_ready(self, timeout: int = 30):
        deadline = time.time() + timeout
        while time.time() < deadline and not self._stop.is_set():
            sel = self._find_search_selector()
            if sel:
                self._log(f"Campo de busca pronto ({sel})", progress=85)
                return True
            time.sleep(1.5)
        self._log("⚠️ Campo de busca não confirmado — logando DOM para diagnóstico:")
        self._dump_contenteditable()
        return False

    def _find_search_selector(self) -> Optional[str]:
        for sel in self._SEARCH_SELECTORS:
            try:
                loc = self._page.locator(sel)
                count = loc.count()
                if count > 0:
                    first = loc.first
                    if first.is_visible(timeout=500):
                        try:
                            if not first.is_enabled(timeout=300):
                                self._log(f"  '{sel}': visível mas desabilitado")
                                continue
                        except Exception:
                            pass
                        return sel
                    self._log(f"  '{sel}': {count} elemento(s) mas invisível")
            except Exception as e:
                self._log(f"  '{sel}': erro — {e}")
        return None

    def _dump_contenteditable(self):
        try:
            url = self._page.url
            title = self._page.title()
            self._log(f"  URL atual: {url}")
            self._log(f"  Título atual: {title}")

            items = self._page.evaluate("""() => {
                return Array.from(document.querySelectorAll('[contenteditable], input, textarea')).map(el => ({
                    tag:  el.tagName,
                    role: el.getAttribute('role') || '',
                    aria: el.getAttribute('aria-label') || '',
                    tab:  el.getAttribute('data-tab') || '',
                    tid:  el.getAttribute('data-testid') || '',
                    vis:  el.offsetParent !== null,
                    txt:  (el.innerText || el.value || '').slice(0, 40),
                    id:   el.id || '',
                }));
            }""")
            self._log(f"  Elementos interativos no DOM: {len(items)}")
            for it in items[:15]:
                self._log(f"    {it}")
        except Exception as e:
            self._log(f"(dump) {e}")

    def _ensure_page(self):
        """Garante que self._page aponta para a aba do WhatsApp Web."""
        try:
            url = ""
            if self._page and not self._page.is_closed():
                url = self._page.url or ""
            if "web.whatsapp.com" in url:
                return

            if self._browser:
                for page in self._browser.pages:
                    try:
                        if not page.is_closed() and "web.whatsapp.com" in (page.url or ""):
                            self._page = page
                            self._log(f"Página corrigida: {page.url}")
                            return
                    except Exception:
                        continue

            if not self._page or self._page.is_closed():
                if self._browser and self._browser.pages:
                    self._page = self._browser.pages[0]
                elif self._browser:
                    self._page = self._browser.new_page()
                else:
                    raise RuntimeError("Contexto do navegador indisponível.")

            self._page.goto(
                "https://web.whatsapp.com",
                wait_until="domcontentloaded",
                timeout=30000,
            )
            time.sleep(3)
        except Exception as e:
            self._log(f"(ensure_page) {e}")

    def _minimize_window(self):
        """
        Oculta o Chrome completamente — sem aparecer na tela nem na barra de tarefas.
        Usa SW_HIDE em vez de SW_SHOWMINNOACTIVE.
        Salva a posição original para restaurar quando precisar operar.
        """
        try:
            import win32con
            import win32gui
            import ctypes

            def _find(hwnd, _):
                title = win32gui.GetWindowText(hwnd)
                if (
                    ("WhatsApp" in title or "Chromium" in title or "Chrome" in title)
                    and win32gui.IsWindow(hwnd)
                ):
                    self._hwnd = hwnd
                return True

            if not self._hwnd or not win32gui.IsWindow(self._hwnd):
                win32gui.EnumWindows(_find, None)

            if self._hwnd and win32gui.IsWindow(self._hwnd):
                # Salva posição original antes de ocultar
                try:
                    rect = win32gui.GetWindowRect(self._hwnd)
                    if rect[2] - rect[0] > 100:   # só salva se tamanho fizer sentido
                        self._original_rect = rect
                except Exception:
                    pass
                # SW_HIDE = 0: janela some da tela E da barra de tarefas
                win32gui.ShowWindow(self._hwnd, win32con.SW_HIDE)

        except Exception as e:
            self._log(f"(hide_window) {e}")

    def _restore_window_for_send(self):
        """
        Restaura o Chrome off-screen temporariamente para executar ações.
        Move a janela para fora da área visível antes de mostrar — o usuário
        não vê nada, mas o Playwright consegue interagir.
        """
        try:
            import win32con
            import win32gui
            import ctypes

            SWP_NOZORDER   = 0x0004
            SWP_NOACTIVATE = 0x0010

            if not self._hwnd or not win32gui.IsWindow(self._hwnd):
                return

            # Obtém tamanho salvo ou usa padrão
            rect = getattr(self, "_original_rect", None)
            if rect:
                w = max(rect[2] - rect[0], 900)
                h = max(rect[3] - rect[1], 600)
            else:
                w, h = 1100, 800

            # Move off-screen ANTES de mostrar — invisível para o usuário
            ctypes.windll.user32.SetWindowPos(
                self._hwnd, 0,
                -w - 200, -h - 200,
                w, h,
                SWP_NOZORDER | SWP_NOACTIVATE,
            )
            # SW_SHOWNOACTIVATE = 4: mostra sem roubar foco
            win32gui.ShowWindow(self._hwnd, 4)

        except Exception as e:
            self._log(f"(restore_window) {e}")

    # monitoramento

    def _monitor_loop(self):
        while not self._stop.is_set() and self._connected:
            try:
                with self._send_lock:
                    pending = self._pending_sends.popleft() if self._pending_sends else None
                if pending:
                    self._do_send(*pending)
                self._check_messages()
            except Exception as e:
                self._log(f"(monitor) {e}")
            time.sleep(2.5)

    def _get_badge(self) -> int:
        try:
            title = self._page.title()
            match = re.match(r"\((\d+)\)", title.strip())
            return int(match.group(1)) if match else 0
        except Exception:
            return 0

    def _check_messages(self):
        current_badge = self._get_badge()

        # Badge diminuiu — usuário leu mensagens, limpa snapshot dos lidos
        if current_badge < self._last_badge:
            try:
                still_unread = {c.strip().lower() for c, _ in self._read_unread_chats()}
                # Remove do snapshot quem o usuário já leu
                self._existing_unread_contacts &= still_unread
                read_keys = set(self._snapshot_previews.keys()) - still_unread
                for k in read_keys:
                    self._snapshot_previews.pop(k, None)
            except Exception:
                pass
            self._last_badge = current_badge
            return

        # Badge igual — verifica mesmo assim via DOM como fallback
        # (janela oculta pode atrasar atualização do título)
        if current_badge == self._last_badge:
            # Checa DOM diretamente a cada 5 ciclos (~12.5 segundos)
            self._dom_check_counter = getattr(self, "_dom_check_counter", 0) + 1
            if self._dom_check_counter < 5:
                return
            self._dom_check_counter = 0
            # Cai no código abaixo com os chats atuais do DOM
            chats = self._read_unread_chats()
            if not chats:
                return
            # Verifica se algum contato tem preview DIFERENTE do snapshot
            now = time.time()
            self._notified = {k: v for k, v in self._notified.items() if now - v < 3600}
            for contact, preview in chats:
                if self._is_group(contact):
                    continue
                contact_key = contact.strip().lower()
                snapshot_preview = self._snapshot_previews.get(contact_key, None)
                current_preview = (preview or "").strip()
                # Novo contato (não estava no snapshot) OU preview mudou = mensagem nova
                is_new_contact = contact_key not in self._existing_unread_contacts
                preview_changed = (
                    snapshot_preview is not None
                    and current_preview
                    and current_preview != snapshot_preview
                )
                if is_new_contact or preview_changed:
                    text_to_use = current_preview if current_preview else "(nova mensagem)"
                    key = f"{contact_key}|{text_to_use[:30]}"
                    if key not in self._notified:
                        self._notified[key] = now
                        # Atualiza snapshot para não notificar a mesma mensagem de novo
                        self._snapshot_previews[contact_key] = current_preview
                        self._existing_unread_contacts.add(contact_key)
                        self._on_message(contact, text_to_use)
                        self._log(f"📩 (dom fallback) {contact}: {text_to_use[:80]}")
            return

        # Badge AUMENTOU — fluxo principal de nova mensagem
        self._log(f"🔔 Badge: {self._last_badge} → {current_badge}")
        self._last_badge = current_badge

        chats = self._read_unread_chats()
        if not chats:
            self._log("⚠️ _read_unread_chats vazio — dump:")
            self._dump_chat_list()
            return

        now = time.time()
        self._notified = {k: v for k, v in self._notified.items() if now - v < 3600}

        for contact, preview in chats:
            if self._is_group(contact):
                continue

            contact_key = contact.strip().lower()
            current_preview = (preview or "").strip()
            snapshot_preview = self._snapshot_previews.get(contact_key, None)

            # Contato novo (sem histórico de não-lido antes) → notifica sempre
            if contact_key not in self._existing_unread_contacts:
                pass   # deixa passar para notificar

            # Contato já tinha não-lido → só notifica se o preview mudou
            elif snapshot_preview is not None and current_preview == snapshot_preview:
                continue   # mesma mensagem de antes do início — ignora

            text_to_use = current_preview if current_preview else "(nova mensagem)"
            key = f"{contact_key}|{text_to_use[:30]}"
            if key not in self._notified:
                self._notified[key] = now
                self._snapshot_previews[contact_key] = current_preview
                self._existing_unread_contacts.add(contact_key)
                self._on_message(contact, text_to_use)
                self._log(f"📩 {contact}: {text_to_use[:80]}")

    def _snapshot_existing_unreads(self):
        """
        Registra o estado atual dos chats com não-lidos NO MOMENTO do início.
        Salva (contato → preview_da_ultima_mensagem) para detectar mensagens NOVAS
        mesmo de contatos que já tinham não-lidos antes.
        """
        self._existing_unread_contacts = set()
        self._snapshot_previews: dict = {}   # contato → preview no momento do inicio
        try:
            time.sleep(3)
            chats = self._read_unread_chats()
            for contact, preview in chats:
                key = contact.strip().lower()
                self._existing_unread_contacts.add(key)
                self._snapshot_previews[key] = (preview or "").strip()
            self._log(
                f"Snapshot: {len(self._existing_unread_contacts)} contatos com "
                f"nao-lidos anteriores registrados."
            )
        except Exception as e:
            self._log(f"(snapshot) {e}")

    def _dump_chat_list(self):
        try:
            data = self._page.evaluate("""() => {
                const pane = document.querySelector('#pane-side')
                          || document.querySelector('[aria-label*="conversas"]')
                          || document.querySelector('[aria-label*="Chat"]');
                if (!pane) return { error: 'pane-side não encontrado' };

                const items = pane.querySelectorAll('[role="listitem"], [role="row"], li');
                return {
                    pane_found: true,
                    pane_html_preview: (pane.innerHTML || '').slice(0, 300),
                    item_count: items.length,
                    items: Array.from(items).slice(0, 5).map(el => ({
                        role: el.getAttribute('role'),
                        aria: el.getAttribute('aria-label'),
                        class: String(el.className || '').slice(0, 60),
                        inner: (el.innerText || '').slice(0, 100),
                        has_badge: !!el.querySelector('[aria-label*="não lida"], [aria-label*="unread"]'),
                        spans_with_title: Array.from(el.querySelectorAll('span[title]'))
                            .map(s => s.getAttribute('title'))
                            .slice(0, 3),
                    })),
                };
            }""")
            self._log(f"DUMP chat list: {data}")
        except Exception as e:
            self._log(f"(dump_chat_list) {e}")

    def _read_unread_chats(self) -> list:
        try:
            result = self._page.evaluate("""() => {
                const results = [];

                const pane = document.querySelector('#pane-side')
                          || document.querySelector('[aria-label*="conversas"]')
                          || document.querySelector('[aria-label*="Chats"]');
                if (!pane) return [];

                const itemSelectors = [
                    '[role="listitem"]',
                    '[role="row"]',
                    'li',
                    '[data-testid*="cell"]',
                    '[tabindex="-1"]',
                ];

                let items = [];
                for (const sel of itemSelectors) {
                    const found = pane.querySelectorAll(sel);
                    if (found.length > 0) {
                        items = Array.from(found);
                        break;
                    }
                }

                for (const item of items) {
                    const badgeSelectors = [
                        '[aria-label*="não lida"]',
                        '[aria-label*="unread"]',
                        '[data-testid="icon-unread-count"]',
                        'span[class*="unread"]',
                        'span[class*="badge"]',
                        '[class*="unread"]',
                    ];
                    let hasBadge = false;
                    for (const bs of badgeSelectors) {
                        if (item.querySelector(bs)) {
                            hasBadge = true;
                            break;
                        }
                    }
                    if (!hasBadge) continue;

                    let contact = '';
                    const titleEl = item.querySelector('span[title]');
                    if (titleEl) {
                        contact = titleEl.getAttribute('title') || titleEl.innerText || '';
                    }
                    if (!contact) {
                        const lines = (item.innerText || '')
                            .split('\\n')
                            .map(l => l.trim())
                            .filter(l => l);
                        contact = lines[0] || '';
                    }
                    if (!contact || contact.length < 2) continue;

                    // Preview: tenta múltiplas fontes
                    let preview = '';

                    // Fonte 1: último span com dir=ltr ou dir=auto que não seja nome/timestamp
                    const textSpans = item.querySelectorAll('span[dir="ltr"], span[dir="auto"]');
                    for (const s of textSpans) {
                        const t = (s.innerText || '').trim();
                        if (t && t.length > 2 && t !== contact && !/^\\d{1,2}:\\d{2}$/.test(t)) {
                            preview = t;
                            break;
                        }
                    }

                    // Fonte 2: innerText linha por linha
                    if (!preview) {
                        const lines = (item.innerText || '')
                            .split('\\n')
                            .map(l => l.trim())
                            .filter(l =>
                                l &&
                                l !== contact &&
                                !/^\\d{1,2}:\\d{2}$/.test(l) &&
                                !l.includes('não lida')
                            );
                        preview = lines[lines.length - 1] || '';
                    }

                    // Fonte 3: data-testid de preview
                    if (!preview) {
                        const previewEl = item.querySelector('[data-testid*="last-msg"], [class*="preview"]');
                        if (previewEl) preview = (previewEl.innerText || '').trim().slice(0, 100);
                    }

                    results.push([contact, preview]);
                }
                return results;
            }""") or []
            if result:
                return result
        except Exception as e:
            self._log(f"(read_chats estratégia 1) {e}")

        try:
            result = self._page.evaluate("""() => {
                const pane = document.querySelector('#pane-side');
                if (!pane) return [];

                const spans = Array.from(pane.querySelectorAll('span[title]'));
                const results = [];
                for (const span of spans) {
                    const title = span.getAttribute('title') || '';
                    if (title.length < 2) continue;

                    let parent = span;
                    for (let i = 0; i < 6; i++) {
                        parent = parent.parentElement;
                        if (!parent) break;
                        const badge = parent.querySelector(
                            '[aria-label*="não lida"], [aria-label*="unread"], [data-testid="icon-unread-count"]'
                        );
                        if (badge) {
                            results.push([title, '']);
                            break;
                        }
                    }
                }
                return results;
            }""") or []
            if result:
                return result
        except Exception as e:
            self._log(f"(read_chats estratégia 2) {e}")

        try:
            result = self._page.evaluate("""() => {
                const results = [];
                const seen = new Set();
                const badges = document.querySelectorAll(
                    '[aria-label*="não lida"], [aria-label*="unread"], [data-testid="icon-unread-count"], [class*="unread"]'
                );

                for (const badge of badges) {
                    let parent = badge;
                    for (let i = 0; i < 8; i++) {
                        parent = parent?.parentElement;
                        if (!parent) break;

                        let contact = '';
                        const titleEl = parent.querySelector('span[title]');
                        if (titleEl) {
                            contact = titleEl.getAttribute('title') || titleEl.innerText || '';
                        }
                        if (!contact) {
                            const lines = (parent.innerText || '')
                                .split('\\n')
                                .map(l => l.trim())
                                .filter(Boolean);
                            contact = lines[0] || '';
                        }
                        if (!contact || contact.length < 2 || seen.has(contact)) continue;

                        const lines = (parent.innerText || '')
                            .split('\\n')
                            .map(l => l.trim())
                            .filter(Boolean);
                        const preview = lines[1] || '';
                        seen.add(contact);
                        results.push([contact, preview]);
                        break;
                    }
                }
                return results;
            }""") or []
            if result:
                return result
        except Exception as e:
            self._log(f"(read_chats estratégia 3) {e}")

        return []

    def _is_group(self, name: str) -> bool:
        hints = [
            "grupo",
            "family",
            "família",
            "turma",
            "equipe",
            "staff",
            "store",
            "party",
            "só nos",
            "brick",
            "rm -",
        ]
        return any(h in name.lower() for h in hints)

    # envio

    def _do_send(self, contact: str, message: str,
                 on_done: Optional[OnSendDone] = None,
                 notify_global: bool = True):
        self._ensure_page()
        self._restore_window_for_send()
        self._log(f"Enviando para {contact}...")
        try:
            page = self._page

            search_sel = self._find_search_selector()
            if not search_sel:
                self._dump_contenteditable()
                raise RuntimeError("Campo de busca não encontrado.")

            search = page.locator(search_sel).first
            tag = search.evaluate("(el) => el.tagName?.toLowerCase() || ''")
            if tag == "input":
                search.fill(contact)
            else:
                search.click()
                time.sleep(0.2)
                search.press("Control+a")
                search.press("Delete")
                time.sleep(0.1)
                search.type(contact, delay=40)
            time.sleep(1.8)

            page.keyboard.press("ArrowDown")
            time.sleep(0.4)
            page.keyboard.press("Enter")
            time.sleep(2.0)

            compose_sel = None
            for sel in self._COMPOSE_SELECTORS:
                try:
                    loc = page.locator(sel).first
                    loc.wait_for(state="attached", timeout=2000)
                    compose_sel = sel
                    break
                except Exception:
                    continue

            if not compose_sel:
                raise RuntimeError("Campo de mensagem não encontrado.")

            compose = page.locator(compose_sel).first
            tag_compose = compose.evaluate("(el) => el.tagName?.toLowerCase() || ''")
            if tag_compose == "input":
                compose.fill(message)
            else:
                compose.click()
                time.sleep(0.2)
                compose.type(message, delay=25)
            time.sleep(0.3)
            page.keyboard.press("Enter")
            time.sleep(0.5)

            self._minimize_window()
            self._log(f"✅ Mensagem enviada para {contact}!")
            result_msg = f"✅ Mensagem enviada para {contact}!"
            if notify_global:
                self._on_send_done(True, result_msg)
            if on_done:
                on_done(True, result_msg)

        except Exception as e:
            self._minimize_window()
            self._log(f"❌ Falha: {e}")
            if notify_global:
                self._on_send_done(False, str(e))
            if on_done:
                on_done(False, str(e))

    def _log(self, msg: str, progress: int = None):
        self._on_log(msg)
        if progress is not None:
            self._on_progress(progress, msg)


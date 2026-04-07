import os
import shutil
import logging
import asyncio
import webbrowser
import zipfile
import subprocess
import urllib.parse
from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
from ctypes import cast, POINTER
from comtypes import CLSCTX_ALL
import screen_brightness_control as sbc
import spotipy
from spotipy.oauth2 import SpotifyOAuth
import socket
from wakeonlan import send_magic_packet

logger = logging.getLogger(__name__)

class CortanaControl:
    def __init__(self):
        self.shortcuts = {
            "youtube": "https://www.youtube.com",
            "github": "https://www.github.com",
            "chatgpt": "https://chat.openai.com",
            "google": "https://www.google.com",
            "instagram": "https://www.instagram.com"
        }
        self.home = os.path.expanduser('~')
        self.desktop = os.path.join(self.home, 'Desktop')
        self.documents = os.path.join(self.home, 'Documents')
        self.downloads = os.path.join(self.home, 'Downloads')
        self.base_folders = {
            "area de trabalho": self.desktop,
            "área de trabalho": self.desktop,
            "desktop": self.desktop,
            "documentos": self.documents,
            "documents": self.documents,
            "downloads": self.downloads
        }
        self.ignore_folders = {
            "venv", ".venv", "env", "node_modules", "__pycache__", ".git", ".idea", ".vscode"
        }

    def _resolver_caminho(self, caminho):
        """Traduz apelidos (como 'Área de Trabalho') para caminhos reais e garante caminhos absolutos."""
        caminho = caminho.strip('\'"').replace('\\', '/')
        caminho_lower = caminho.lower()

        # Verifica se o caminho começa com um dos apelidos (ex: "desktop/pasta" ou "desktop")
        for alias, real_path in self.base_folders.items():
            if caminho_lower == alias:
                return real_path
            if caminho_lower.startswith(alias + "/"):
                # Substitui o alias pelo caminho real no início da string
                return os.path.abspath(os.path.join(real_path, caminho[len(alias)+1:]))
        
        # Se for um caminho relativo simples, assume que é no Desktop por padrão
        if not os.path.isabs(caminho) and not caminho.startswith('.'):
            return os.path.abspath(os.path.join(self.desktop, caminho))
            
        return os.path.abspath(os.path.expanduser(caminho))

    def _walk_seguro(self, base):
        """os.walk que ignora pastas irrelevantes para performance e segurança."""
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in self.ignore_folders and not d.startswith('.')]
            yield dirpath, dirnames, filenames

    # --- Manipulação de Arquivos e Pastas ---

    def cria_pasta(self, caminho):
        try:
            caminho_abs = self._resolver_caminho(caminho)
            os.makedirs(caminho_abs, exist_ok=True)
            return f"Pasta criada com sucesso: {caminho_abs}"
        except Exception as e:
            return f"Erro ao criar pasta: {str(e)}"

    def abrir_pasta(self, nome_pasta):
        """Tenta encontrar e abrir uma pasta pelo nome nos locais principais."""
        try:
            # Caso o usuário passe o nome de um local conhecido
            caminho_direto = self.base_folders.get(nome_pasta.lower())
            if caminho_direto and os.path.exists(caminho_direto):
                os.startfile(caminho_direto)
                return f"Abrindo {nome_pasta}."

            # Busca recursiva nos locais base
            for base_name, base_path in self.base_folders.items():
                if base_name in ["area de trabalho", "documentos", "downloads"]:
                    for dirpath, dirnames, _ in self._walk_seguro(base_path):
                        for d in dirnames:
                            if d.lower() == nome_pasta.lower():
                                full_path = os.path.join(dirpath, d)
                                os.startfile(full_path)
                                return f"Pasta encontrada e aberta em: {full_path}"
            
            return f"Pasta '{nome_pasta}' não encontrada nos locais padrão."
        except Exception as e:
            return f"Erro ao abrir pasta: {str(e)}"

    def buscar_e_abrir_arquivo(self, nome_arquivo):
        """Busca um arquivo por nome e abre o primeiro resultado."""
        try:
            for _, base_path in self.base_folders.items():
                for dirpath, _, filenames in self._walk_seguro(base_path):
                    for f in filenames:
                        if nome_arquivo.lower() in f.lower():
                            full_path = os.path.join(dirpath, f)
                            os.startfile(full_path)
                            return f"Arquivo encontrado e aberto: {full_path}"
            return f"Arquivo '{nome_arquivo}' não encontrado."
        except Exception as e:
            return f"Erro ao buscar/abrir arquivo: {str(e)}"

    def deletar_arquivo(self, caminho):
        try:
            path_abs = self._resolver_caminho(caminho)
            if os.path.isfile(path_abs):
                os.remove(path_abs)
                return f"Arquivo deletado: {path_abs}"
            elif os.path.isdir(path_abs):
                shutil.rmtree(path_abs)
                return f"Diretório deletado: {path_abs}"
            return f"Caminho não encontrado: {path_abs}"
        except Exception as e:
            return f"Erro ao deletar: {str(e)}"

    def limpar_diretorio(self, caminho):
        try:
            path_abs = self._resolver_caminho(caminho)
            if os.path.exists(path_abs):
                for item in os.listdir(path_abs):
                    item_path = os.path.join(path_abs, item)
                    if os.path.isfile(item_path): os.remove(item_path)
                    elif os.path.isdir(item_path): shutil.rmtree(item_path)
                return f"Diretório limpo: {path_abs}"
            return "Diretório não encontrado."
        except Exception as e:
            return f"Erro ao limpar diretório: {str(e)}"

    def mover_item(self, origem, destino):
        try:
            origem_abs = self._resolver_caminho(origem)
            destino_abs = self._resolver_caminho(destino)
            shutil.move(origem_abs, destino_abs)
            return f"Movido de {origem_abs} para {destino_abs}."
        except Exception as e:
            return f"Erro ao mover: {str(e)}"

    def copiar_item(self, origem, destino):
        try:
            origem_abs = self._resolver_caminho(origem)
            destino_abs = self._resolver_caminho(destino)
            if os.path.isdir(origem_abs): shutil.copytree(origem_abs, destino_abs)
            else: shutil.copy2(origem_abs, destino_abs)
            return f"Copiado de {origem_abs} para {destino_abs}."
        except Exception as e:
            return f"Erro ao copiar: {str(e)}"

    def renomear_item(self, caminho, novo_nome):
        try:
            path_abs = self._resolver_caminho(caminho)
            diretorio = os.path.dirname(path_abs)
            novo_caminho = os.path.join(diretorio, novo_nome)
            os.rename(path_abs, novo_caminho)
            return f"Renomeado para {novo_nome}."
        except Exception as e:
            return f"Erro ao renomear: {str(e)}"

    def organizar_pasta(self, caminho):
        try:
            path_abs = self._resolver_caminho(caminho)
            extensoes = {
                'Imagens': ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp'],
                'Documentos': ['.pdf', '.doc', '.docx', '.txt', '.xlsx', '.pptx', '.csv'],
                'Videos': ['.mp4', '.mkv', '.avi', '.mov'],
                'Musicas': ['.mp3', '.wav', '.flac'],
                'Compactados': ['.zip', '.rar', '.7z'],
                'Executaveis': ['.exe', '.msi', '.bat']
            }

            for item in os.listdir(path_abs):
                item_path = os.path.join(path_abs, item)
                if os.path.isfile(item_path):
                    ext = os.path.splitext(item)[1].lower()
                    movido = False
                    for pasta, exts in extensoes.items():
                        if ext in exts:
                            pasta_destino = os.path.join(path_abs, pasta)
                            os.makedirs(pasta_destino, exist_ok=True)
                            shutil.move(item_path, os.path.join(pasta_destino, item))
                            movido = True
                            break
                    if not movido:
                        pasta_outros = os.path.join(path_abs, 'Outros')
                        os.makedirs(pasta_outros, exist_ok=True)
                        shutil.move(item_path, os.path.join(pasta_outros, item))
            return "Pasta organizada com sucesso."
        except Exception as e:
            return f"Erro ao organizar pasta: {str(e)}"

    def compactar_pasta(self, caminho):
        try:
            path_abs = self._resolver_caminho(caminho).rstrip('/\\')
            shutil.make_archive(path_abs, 'zip', path_abs)
            return f"Compactado em: {path_abs}.zip"
        except Exception as e:
            return f"Erro ao compactar: {str(e)}"

    # --- Controle de Sistema ---

    def controle_volume(self, nivel):
        """Define o volume entre 0 e 100"""
        try:
            nivel = max(0, min(100, int(nivel)))
            import comtypes
            comtypes.CoInitialize()
            devices = AudioUtilities.GetSpeakers()
            volume = devices.EndpointVolume
            volume.SetMasterVolumeLevelScalar(nivel / 100, None)
            return f"Volume ajustado para {nivel}%."
        except Exception as e:
            return f"Erro ao ajustar volume: {str(e)}"

    def controle_brilho(self, nivel):
        """Define o brilho entre 0 e 100"""
        try:
            nivel = max(0, min(100, int(nivel)))
            sbc.set_brightness(nivel)
            return f"Brilho ajustado para {nivel}%."
        except Exception as e:
            return f"Erro ao ajustar brilho: {str(e)}"

    def abrir_aplicativo(self, nome_app):
        """Abre um aplicativo no sistema pelo nome com busca inteligente."""
        try:
            apps = {
                "bloco de notas": "notepad.exe",
                "calculadora": "calc.exe",
                "paint": "mspaint.exe",
                "cmd": "cmd.exe",
                "navegador": "start msedge",
                "google chrome": "chrome.exe",
                "chrome": "chrome.exe",
                "word": "winword",
                "excel": "excel",
                "powerpoint": "powerpnt",
                "explorador de arquivos": "explorer.exe",
                "configuracoes": "ms-settings:",
                "configurações": "ms-settings:",
                "spotify": "spotify:",
                "vscode": "code",
                "visual studio code": "code"
            }
            
            nome_app_lower = nome_app.lower().strip()
            comando = apps.get(nome_app_lower, nome_app_lower)
            
            # Tenta via os.startfile primeiro (melhor para protocolos e arquivos registrados)
            try:
                os.startfile(comando)
                return f"Abrindo {nome_app}."
            except:
                pass

            # Fallback 1: Tenta via subprocess (executáveis no PATH)
            try:
                subprocess.Popen(comando, shell=False)
                return f"Iniciando {nome_app}."
            except:
                pass
            
            # Fallback 2: Busca inteligente com 'where'
            try:
                # Se for um protocolo (tem : no final ou ms-), pula o 'where'
                if ":" not in comando:
                    res = subprocess.check_output(['where', comando], stderr=subprocess.STDOUT, shell=True).decode('utf-8').strip().split('\n')[0]
                    if res and os.path.exists(res):
                        os.startfile(res)
                        return f"Aplicativo '{nome_app}' encontrado em {res} e aberto."
            except:
                pass

            # Fallback 3: Tenta via 'start' no CMD (último recurso)
            try: 
                subprocess.Popen(['cmd', '/c', 'start', '', comando], shell=True)
                return f"Comando enviado ao sistema para abrir {nome_app}."
            except Exception as e:
                return f"Não foi possível abrir {nome_app}: {str(e)}"
                
        except Exception as e:
            return f"Erro ao abrir aplicativo: {str(e)}"

    def tocar_musica_spotify(self, termo):
        """Abre o Spotify e pesquisa/toca o termo solicitado usando a API oficial se disponível."""
        try:
            # Tenta usar a API do Spotify (Spotipy) primeiro
            client_id = os.getenv("SPOTIPY_CLIENT_ID")
            client_secret = os.getenv("SPOTIPY_CLIENT_SECRET")
            redirect_uri = os.getenv("SPOTIPY_REDIRECT_URI", "http://127.0.0.1:8888/callback")

            if client_id and client_secret:
                try:
                    sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
                        client_id=client_id,
                        client_secret=client_secret,
                        redirect_uri=redirect_uri,
                        scope="user-modify-playback-state user-read-playback-state",
                        open_browser=True
                    ))

                    # Busca a música/artista
                    results = sp.search(q=termo, limit=1, type='track')
                    if results['tracks']['items']:
                        track_uri = results['tracks']['items'][0]['uri']
                        track_name = results['tracks']['items'][0]['name']
                        artist_name = results['tracks']['items'][0]['artists'][0]['name']
                        
                        # Tenta dar o play no dispositivo ativo
                        try:
                            sp.start_playback(uris=[track_uri])
                            return f"Tocando agora via API: {track_name} de {artist_name}."
                        except Exception as e:
                            # Se falhar o playback direto (ex: sem dispositivo ativo), 
                            # abre o app com a URI e tenta o pyautogui como fallback
                            subprocess.Popen(['cmd', '/c', 'start', '', track_uri], shell=True)
                            self._iniciar_autoplay_pyautogui()
                            return f"Iniciando {track_name} de {artist_name} no Spotify Desktop."
                    
                except Exception as e:
                    print(f"Erro na API Spotify: {e}")
                    # Continua para o fallback de busca por URI se a API falhar
            
            # Fallback: Protocolo URI + PyAutoGUI (original)
            termo_encoded = urllib.parse.quote(termo)
            uri = f"spotify:search:{termo_encoded}"
            subprocess.Popen(['cmd', '/c', 'start', '', uri], shell=True)
            self._iniciar_autoplay_pyautogui()
            
            return f"Buscando e tocando '{termo}' no Spotify..."

        except Exception as e:
            return f"Erro ao processar Spotify: {str(e)}"

    def _iniciar_autoplay_pyautogui(self):
        """Inicia uma thread para pressionar as teclas de play no app desktop."""
        try:
            import threading
            def _autoplay():
                try:
                    import time
                    import pyautogui
                    time.sleep(5.0) # Espera o Spotify abrir e renderizar os resultados
                    pyautogui.press('down') # Navega para o primeiro resultado
                    time.sleep(0.2)
                    pyautogui.press('enter') # Toca o resultado selecionado
                except Exception:
                    pass
            
            threading.Thread(target=_autoplay, daemon=True).start()
        except Exception:
            pass
        except Exception as e:
            return f"Erro ao tocar música no Spotify: {str(e)}"

    def atalhos_navegacao(self, site):
        try:
            url = self.shortcuts.get(site.lower())
            if url:
                os.startfile(url)
                return f"Abrindo {site}."
            return "Site não cadastrado."
        except Exception as e:
            return f"Erro ao abrir site: {str(e)}"

    def pesquisar_no_google(self, termo):
        try:
            url = f"https://www.google.com/search?q={urllib.parse.quote_plus(termo)}"
            os.startfile(url)
            return f"Pesquisando por {termo}."
        except Exception as e:
            return f"Erro ao pesquisar: {str(e)}"

    def energia_pc(self, acao):
        try:
            if acao == "desligar":
                os.system("shutdown /s /t 1")
                return "Desligando o computador."
            elif acao == "reiniciar":
                os.system("shutdown /r /t 1")
                return "Reiniciando o computador."
            elif acao == "bloquear":
                subprocess.run(["rundll32.exe", "user32.dll,LockWorkStation"])
                return "Computador bloqueado."
            return "Ação inválida."
        except Exception as e:
            return f"Erro: {str(e)}"

    def abrir_arquivo(self, caminho):
        """Abre um arquivo pelo caminho completo."""
        try:
            path_abs = self._resolver_caminho(caminho)
            if os.path.exists(path_abs):
                os.startfile(path_abs)
                return f"Abrindo arquivo {path_abs}."
            return f"Arquivo não encontrado: {path_abs}"
        except Exception as e:
            return f"Erro ao abrir arquivo: {str(e)}"

    # --- Controle de Dispositivos de Rede (Smart Home) ---

    def wake_on_lan(self, mac_address):
        """Envia um pacote Wake-on-LAN para ligar um dispositivo."""
        try:
            send_magic_packet(mac_address)
            return f"Pacote Wake-on-LAN enviado para {mac_address}."
        except Exception as e:
            return f"Erro ao enviar WoL: {str(e)}"

    def controle_tv_lg(self, ip, acao):
        """Controle para Smart TVs LG WebOS."""
        try:
            from pywebostv.discovery import DiscoveryClient
            from pywebostv.connection import WebOSClient
            from pywebostv.controls import InputControl, SystemControl, AudioControl

            client = WebOSClient(ip)
            client.connect()
            
            # Nota: O primeiro pareamento exigirá confirmação na TV
            # Para simplicidade, assumimos que já foi pareado ou que o usuário vai confirmar
            store = {} # Em uma implementação real, salvaríamos o token em arquivo
            for status in client.register(store):
                if status == WebOSClient.PROMPTED:
                    return "Por favor, aceite a conexão na sua TV LG."
                elif status == WebOSClient.REGISTERED:
                    pass

            system = SystemControl(client)
            audio = AudioControl(client)

            if acao == "desligar":
                # await self._session.generate_reply(...)  # COMENTADO para evitar erro 1008 de sobreposição
                logger.warning(f"[SPEECH ADAPTER] Ignorando notificação proativa para evitar Erro 1008: {text}")
                system.power_off()
                return "Comando de desligar enviado para TV LG."
            elif acao == "mute":
                audio.set_mute(True)
                return "TV LG mutada."
            elif acao == "unmute":
                audio.set_mute(False)
                return "TV LG desmutada."
            return f"Ação '{acao}' não implementada para LG."
        except Exception as e:
            return f"Erro no controle LG: {str(e)}"

    def controle_tv_samsung(self, ip, acao):
        """Controle para Smart TVs Samsung (Tizen)."""
        try:
            from samsungtvws import SamsungTVWS
            tv = SamsungTVWS(ip)
            
            if acao == "desligar":
                tv.shortcuts().power()
                return "Comando de desligar enviado para TV Samsung."
            elif acao == "volume_up":
                tv.shortcuts().volume_up()
                return "Volume da TV Samsung aumentado."
            elif acao == "volume_down":
                tv.shortcuts().volume_down()
                return "Volume da TV Samsung diminuído."
            return f"Ação '{acao}' não implementada para Samsung."
        except Exception as e:
            return f"Erro no controle Samsung: {str(e)}"

    def controle_dispositivo_broadlink(self, ip, acao, temperatura=None):
        """Controle para IR Blasters Broadlink (Ar Condicionado, TVs antigas)."""
        try:
            import broadlink
            devices = broadlink.discover(timeout=5, discover_ip_address=ip)
            if not devices:
                return f"Dispositivo Broadlink não encontrado no IP {ip}."
            
            device = devices[0]
            device.auth()

            if acao == "aprender":
                device.enter_learning_mode()
                return "Broadlink em modo de aprendizado. Aponte o controle e aperte o botão."
            
            # Para enviar comandos, precisaríamos dos códigos IR salvos.
            # Esta é uma implementação base que o usuário pode expandir.
            return "Comando enviado ao Broadlink (implementação de base)."
        except Exception as e:
            return f"Erro no controle Broadlink: {str(e)}"

    async def controle_tv_tcl(self, ip, acao):
        """Controle para Smart TVs TCL (Android TV) via protocolo V2 (PIN)."""
        try:
            from androidtvremote2 import AndroidTVRemote
            import asyncio
            
            certfile = "memory/tcl_cert.pem"
            keyfile = "memory/tcl_key.pem"
            
            if not os.path.exists(certfile):
                return "Erro: Certificado não encontrado. Use o script pair_tcl.py primeiro."

            if acao == "ligar":
                # MAC capturado via ARP: 78:66:9d:89:e3:7c
                mac = "78:66:9d:89:e3:7c"
                self.wake_on_lan(mac)
                return f"Enviado pacote Wake-on-LAN para ligar a TV TCL ({mac}). Verifique se a TV liga em alguns segundos."

            remote = AndroidTVRemote(
                client_name="Cortana Assistant",
                certfile=certfile,
                keyfile=keyfile,
                host=ip
            )

                            # Conecta, envia comando e desconecta (pode ser otimizado mantendo a conexão)
            await remote.async_connect()

            import androidtvremote2
            from androidtvremote2 import AndroidTVRemote
            
            # Mapeamento de ações para KeyCodes
            # 223: SLEEP (Geralmente mais eficaz que 26 para desligar Android TVs)
            # 26: POWER, 3: HOME, 66: ENTER, 24: VOL_UP, 25: VOL_DOWN
            key_map = {
                "desligar": 223, 
                "power": 26,     
                "sleep": 223,
                "home": 3,
                "confirmar": 66,
                "vol_up": 24,
                "vol_down": 25,
                "ok": 66
            }

            # Se for um número, usa direto como KeyCode (para debug)
            if acao == "desligar":
                # Estratégia 'Máxima Compatibilidade' para TCL Android TV:
                # 1. Home (garante que estamos fora de apps travados)
                # 2. Sleep (comando 223 costuma ser mais direto para o standby modo)
                # 3. Power (backup caso o sleep não pegue)
                # 4. OK (confirma menus de desligamento se aparecerem)
                
                logger.info("[TCL] Iniciando sequência de desligamento robusta...")
                remote.send_key_command(3)   # Home
                await asyncio.sleep(1.5)
                
                remote.send_key_command(223) # Sleep (Standby direto)
                await asyncio.sleep(1.0)
                
                remote.send_key_command(26)  # Power (Toggle)
                await asyncio.sleep(0.5)
                
                remote.send_key_command(66)  # OK / ENTER
                resp = "Sequência robusta (Home+Sleep+Power+OK) enviada para a TV TCL."
            elif acao.isdigit():
                key_code = int(acao)
                remote.send_key_command(key_code)
                resp = f"KeyCode {key_code} enviado para TV TCL."
            elif acao == "long_power":
                # Tenta um pressionamento longo no botão de power (Start + 1s + End)
                remote.send_key_command(26, direction="START_LONG")
                await asyncio.sleep(1.0)
                remote.send_key_command(26, direction="END_LONG")
                resp = "Comando Power (Simulação Long Press) enviado para TV TCL."
            elif acao in key_map:
                remote.send_key_command(key_map[acao])
                resp = f"Comando '{acao}' enviado para TV TCL."
            else:
                resp = f"Ação '{acao}' não mapeada para TCL."
            
            # Aguarda um pouco para o comando ser processado antes de fechar
            await asyncio.sleep(0.5)
            remote.disconnect()
            return resp

        except Exception as e:
            return f"Erro no controle TCL (V2): {str(e)}"

if __name__ == "__main__":
    # Teste rápido
    cortana = CortanaControl()
    print("Controle Cortana inicializado.")

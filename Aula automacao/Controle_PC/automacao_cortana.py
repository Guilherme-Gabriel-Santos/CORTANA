from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import threading
import urllib.parse
from pathlib import Path

# Imports pesados sao carregados sob demanda dentro dos metodos que os usam
# para reduzir a RAM do startup (spotipy ~4.8 MB, pycaw + wakeonlan + sbc juntos ~3-5 MB).
# Mantemos apenas os leves no topo.
from dotenv import load_dotenv

load_dotenv(override=True)

logger = logging.getLogger(__name__)


class CortanaControl:
    def __init__(self) -> None:
        self.shortcuts = {
            "youtube": "https://www.youtube.com",
            "github": "https://www.github.com",
            "chatgpt": "https://chat.openai.com",
            "google": "https://www.google.com",
            "instagram": "https://www.instagram.com",
        }
        self.home = Path.home()
        self.desktop = self.home / "Desktop"
        self.documents = self.home / "Documents"
        self.downloads = self.home / "Downloads"
        self.base_folders = {
            "area de trabalho": self.desktop,
            "desktop": self.desktop,
            "documentos": self.documents,
            "documents": self.documents,
            "downloads": self.downloads,
        }
        self.ignore_folders = {
            "venv",
            ".venv",
            "env",
            "node_modules",
            "__pycache__",
            ".git",
            ".idea",
            ".vscode",
        }
        self.allowed_roots = [self.desktop.resolve(), self.documents.resolve(), self.downloads.resolve()]

    def _resolve_path(self, raw_path: str) -> Path:
        cleaned = raw_path.strip('\'"').replace("\\", "/")
        lowered = cleaned.lower()

        for alias, real_path in self.base_folders.items():
            if lowered == alias:
                return real_path.resolve()
            if lowered.startswith(alias + "/"):
                relative_part = cleaned[len(alias) + 1 :]
                return (real_path / relative_part).resolve()

        if not os.path.isabs(cleaned) and not cleaned.startswith("."):
            return (self.desktop / cleaned).resolve()

        return Path(os.path.expanduser(cleaned)).resolve()

    def _is_relative_to(self, path_obj: Path, root_obj: Path) -> bool:
        try:
            path_obj.relative_to(root_obj)
            return True
        except ValueError:
            return False

    def _ensure_managed_path(
        self,
        path_obj: Path,
        operation: str,
        *,
        must_exist: bool = False,
        protect_root: bool = True,
    ) -> Path:
        resolved = path_obj.resolve(strict=False)
        if not any(self._is_relative_to(resolved, root) for root in self.allowed_roots):
            raise ValueError(
                f"{operation} so e permitido dentro de Desktop, Documents ou Downloads."
            )
        if protect_root and any(resolved == root for root in self.allowed_roots):
            raise ValueError(f"{operation} nao pode ser executado na raiz de {resolved}.")
        if must_exist and not resolved.exists():
            raise FileNotFoundError(f"Caminho nao encontrado: {resolved}")
        return resolved

    def _walk_safe(self, base: Path):
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in self.ignore_folders and not d.startswith(".")]
            yield Path(dirpath), dirnames, filenames

    def cria_pasta(self, caminho: str) -> str:
        try:
            path_obj = self._ensure_managed_path(
                self._resolve_path(caminho),
                "Criar pasta",
                protect_root=False,
            )
            path_obj.mkdir(parents=True, exist_ok=True)
            return f"Pasta criada com sucesso: {path_obj}"
        except Exception as exc:
            return f"Erro ao criar pasta: {exc}"

    def abrir_pasta(self, nome_pasta: str) -> str:
        try:
            direct_path = self.base_folders.get(nome_pasta.lower())
            if direct_path and direct_path.exists():
                os.startfile(str(direct_path))
                return f"Abrindo {nome_pasta}."

            for base_name, base_path in self.base_folders.items():
                if base_name not in {"area de trabalho", "documentos", "downloads"}:
                    continue
                for dirpath, dirnames, _ in self._walk_safe(base_path):
                    for dirname in dirnames:
                        if dirname.lower() == nome_pasta.lower():
                            full_path = dirpath / dirname
                            os.startfile(str(full_path))
                            return f"Pasta encontrada e aberta em: {full_path}"

            return f"Pasta '{nome_pasta}' nao encontrada nos locais padrao."
        except Exception as exc:
            return f"Erro ao abrir pasta: {exc}"

    def buscar_e_abrir_arquivo(self, nome_arquivo: str) -> str:
        try:
            for base_path in self.base_folders.values():
                for dirpath, _, filenames in self._walk_safe(base_path):
                    for filename in filenames:
                        if nome_arquivo.lower() in filename.lower():
                            full_path = dirpath / filename
                            os.startfile(str(full_path))
                            return f"Arquivo encontrado e aberto: {full_path}"
            return f"Arquivo '{nome_arquivo}' nao encontrado."
        except Exception as exc:
            return f"Erro ao buscar/abrir arquivo: {exc}"

    def deletar_arquivo(self, caminho: str) -> str:
        try:
            path_obj = self._ensure_managed_path(
                self._resolve_path(caminho),
                "Excluir",
                must_exist=True,
            )
            if path_obj.is_file():
                path_obj.unlink()
                return f"Arquivo deletado: {path_obj}"
            if path_obj.is_dir():
                shutil.rmtree(path_obj)
                return f"Diretorio deletado: {path_obj}"
            return f"Caminho nao encontrado: {path_obj}"
        except Exception as exc:
            return f"Erro ao deletar: {exc}"

    def limpar_diretorio(self, caminho: str) -> str:
        try:
            path_obj = self._ensure_managed_path(
                self._resolve_path(caminho),
                "Limpar diretorio",
                must_exist=True,
            )
            if not path_obj.is_dir():
                return f"O caminho informado nao e um diretorio: {path_obj}"

            for item in path_obj.iterdir():
                if item.is_file():
                    item.unlink()
                elif item.is_dir():
                    shutil.rmtree(item)
            return f"Diretorio limpo: {path_obj}"
        except Exception as exc:
            return f"Erro ao limpar diretorio: {exc}"

    def mover_item(self, origem: str, destino: str) -> str:
        try:
            origem_obj = self._ensure_managed_path(
                self._resolve_path(origem),
                "Mover",
                must_exist=True,
            )
            destino_obj = self._ensure_managed_path(
                self._resolve_path(destino),
                "Mover",
                protect_root=False,
            )
            shutil.move(str(origem_obj), str(destino_obj))
            return f"Movido de {origem_obj} para {destino_obj}."
        except Exception as exc:
            return f"Erro ao mover: {exc}"

    def copiar_item(self, origem: str, destino: str) -> str:
        try:
            origem_obj = self._ensure_managed_path(
                self._resolve_path(origem),
                "Copiar",
                must_exist=True,
            )
            destino_obj = self._ensure_managed_path(
                self._resolve_path(destino),
                "Copiar",
                protect_root=False,
            )
            if origem_obj.is_dir():
                shutil.copytree(origem_obj, destino_obj)
            else:
                shutil.copy2(origem_obj, destino_obj)
            return f"Copiado de {origem_obj} para {destino_obj}."
        except Exception as exc:
            return f"Erro ao copiar: {exc}"

    def renomear_item(self, caminho: str, novo_nome: str) -> str:
        try:
            path_obj = self._ensure_managed_path(
                self._resolve_path(caminho),
                "Renomear",
                must_exist=True,
            )
            new_path = path_obj.parent / novo_nome
            os.rename(path_obj, new_path)
            return f"Renomeado para {novo_nome}."
        except Exception as exc:
            return f"Erro ao renomear: {exc}"

    def organizar_pasta(self, caminho: str) -> str:
        try:
            path_obj = self._ensure_managed_path(
                self._resolve_path(caminho),
                "Organizar pasta",
                must_exist=True,
            )
            if not path_obj.is_dir():
                return f"O caminho informado nao e uma pasta: {path_obj}"

            extensions = {
                "Imagens": [".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"],
                "Documentos": [".pdf", ".doc", ".docx", ".txt", ".xlsx", ".pptx", ".csv"],
                "Videos": [".mp4", ".mkv", ".avi", ".mov"],
                "Musicas": [".mp3", ".wav", ".flac"],
                "Compactados": [".zip", ".rar", ".7z"],
                "Executaveis": [".exe", ".msi", ".bat"],
            }

            for item in path_obj.iterdir():
                if not item.is_file():
                    continue
                extension = item.suffix.lower()
                destination_dir = path_obj / "Outros"
                for folder_name, extension_list in extensions.items():
                    if extension in extension_list:
                        destination_dir = path_obj / folder_name
                        break
                destination_dir.mkdir(exist_ok=True)
                shutil.move(str(item), str(destination_dir / item.name))

            return "Pasta organizada com sucesso."
        except Exception as exc:
            return f"Erro ao organizar pasta: {exc}"

    def compactar_pasta(self, caminho: str) -> str:
        try:
            path_obj = self._ensure_managed_path(
                self._resolve_path(caminho),
                "Compactar pasta",
                must_exist=True,
            )
            archive_base = str(path_obj).rstrip("/\\")
            shutil.make_archive(archive_base, "zip", archive_base)
            return f"Compactado em: {archive_base}.zip"
        except Exception as exc:
            return f"Erro ao compactar: {exc}"

    def controle_volume(self, nivel: int) -> str:
        try:
            level = max(0, min(100, int(nivel)))
            import comtypes
            from pycaw.pycaw import AudioUtilities

            comtypes.CoInitialize()
            devices = AudioUtilities.GetSpeakers()
            volume = devices.EndpointVolume
            volume.SetMasterVolumeLevelScalar(level / 100, None)
            return f"Volume ajustado para {level}%."
        except Exception as exc:
            return f"Erro ao ajustar volume: {exc}"

    def controle_brilho(self, nivel: int) -> str:
        try:
            level = max(0, min(100, int(nivel)))
            import screen_brightness_control as sbc
            sbc.set_brightness(level)
            return f"Brilho ajustado para {level}%."
        except Exception as exc:
            return f"Erro ao ajustar brilho: {exc}"

    def abrir_aplicativo(self, nome_app: str) -> str:
        try:
            apps = {
                "bloco de notas": "notepad.exe",
                "calculadora": "calc.exe",
                "paint": "mspaint.exe",
                "cmd": "cmd.exe",
                "navegador": "msedge.exe",
                "google chrome": "chrome.exe",
                "chrome": "chrome.exe",
                "word": "winword.exe",
                "excel": "excel.exe",
                "powerpoint": "powerpnt.exe",
                "explorador de arquivos": "explorer.exe",
                "configuracoes": "ms-settings:",
                "spotify": "spotify:",
                "vscode": "code",
                "visual studio code": "code",
            }

            command = apps.get(nome_app.lower().strip(), nome_app.strip())
            try:
                os.startfile(command)
                return f"Abrindo {nome_app}."
            except OSError:
                pass

            subprocess.Popen([command], shell=False)
            return f"Iniciando {nome_app}."
        except Exception as exc:
            return f"Nao foi possivel abrir {nome_app}: {exc}"

    def tocar_musica_spotify(self, termo: str) -> str:
        try:
            client_id = os.getenv("SPOTIPY_CLIENT_ID")
            client_secret = os.getenv("SPOTIPY_CLIENT_SECRET")
            redirect_uri = os.getenv("SPOTIPY_REDIRECT_URI", "http://127.0.0.1:8888/callback")

            if client_id and client_secret:
                try:
                    import spotipy
                    from spotipy.oauth2 import SpotifyOAuth
                    spotify = spotipy.Spotify(
                        auth_manager=SpotifyOAuth(
                            client_id=client_id,
                            client_secret=client_secret,
                            redirect_uri=redirect_uri,
                            scope="user-modify-playback-state user-read-playback-state",
                            open_browser=True,
                        )
                    )
                    results = spotify.search(q=termo, limit=1, type="track")
                    if results["tracks"]["items"]:
                        track = results["tracks"]["items"][0]
                        track_uri = track["uri"]
                        track_name = track["name"]
                        artist_name = track["artists"][0]["name"]
                        try:
                            spotify.start_playback(uris=[track_uri])
                            return f"Tocando agora via API: {track_name} de {artist_name}."
                        except Exception:
                            subprocess.Popen(["cmd", "/c", "start", "", track_uri], shell=True)
                            self._start_spotify_autoplay()
                            return f"Iniciando {track_name} de {artist_name} no Spotify Desktop."
                except Exception as exc:
                    logger.warning("[Spotify] API fallback triggered: %s", exc)

            encoded_term = urllib.parse.quote(termo)
            uri = f"spotify:search:{encoded_term}"
            subprocess.Popen(["cmd", "/c", "start", "", uri], shell=True)
            self._start_spotify_autoplay()
            return f"Buscando e tocando '{termo}' no Spotify..."
        except Exception as exc:
            return f"Erro ao processar Spotify: {exc}"

    def _start_spotify_autoplay(self) -> None:
        def _autoplay() -> None:
            try:
                import pyautogui
                import time as time_module

                time_module.sleep(5.0)
                pyautogui.press("down")
                time_module.sleep(0.2)
                pyautogui.press("enter")
            except Exception:
                pass

        try:
            threading.Thread(target=_autoplay, daemon=True).start()
        except Exception:
            pass

    def atalhos_navegacao(self, site: str) -> str:
        try:
            url = self.shortcuts.get(site.lower())
            if not url:
                return "Site nao cadastrado."
            os.startfile(url)
            return f"Abrindo {site}."
        except Exception as exc:
            return f"Erro ao abrir site: {exc}"

    def pesquisar_no_google(self, termo: str) -> str:
        try:
            url = f"https://www.google.com/search?q={urllib.parse.quote_plus(termo)}"
            os.startfile(url)
            return f"Pesquisando por {termo}."
        except Exception as exc:
            return f"Erro ao pesquisar: {exc}"

    def energia_pc(self, acao: str) -> str:
        try:
            normalized = acao.lower().strip()
            if normalized == "desligar":
                os.system("shutdown /s /t 1")
                return "Desligando o computador."
            if normalized == "reiniciar":
                os.system("shutdown /r /t 1")
                return "Reiniciando o computador."
            if normalized == "bloquear":
                subprocess.run(["rundll32.exe", "user32.dll,LockWorkStation"])
                return "Computador bloqueado."
            return "Acao invalida."
        except Exception as exc:
            return f"Erro: {exc}"

    def abrir_arquivo(self, caminho: str) -> str:
        try:
            path_obj = self._ensure_managed_path(
                self._resolve_path(caminho),
                "Abrir arquivo",
                must_exist=True,
                protect_root=False,
            )
            os.startfile(str(path_obj))
            return f"Abrindo arquivo {path_obj}."
        except Exception as exc:
            return f"Erro ao abrir arquivo: {exc}"

    def wake_on_lan(self, mac_address: str) -> str:
        try:
            from wakeonlan import send_magic_packet
            send_magic_packet(mac_address)
            return f"Pacote Wake-on-LAN enviado para {mac_address}."
        except Exception as exc:
            return f"Erro ao enviar WoL: {exc}"

    def controle_tv_lg(self, ip: str, acao: str) -> str:
        try:
            from pywebostv.connection import WebOSClient
            from pywebostv.controls import AudioControl, SystemControl

            client = WebOSClient(ip)
            client.connect()

            store = {}
            for status in client.register(store):
                if status == WebOSClient.PROMPTED:
                    return "Por favor, aceite a conexao na sua TV LG."

            system = SystemControl(client)
            audio = AudioControl(client)
            if acao == "desligar":
                system.power_off()
                return "Comando de desligar enviado para TV LG."
            if acao == "mute":
                audio.set_mute(True)
                return "TV LG mutada."
            if acao == "unmute":
                audio.set_mute(False)
                return "TV LG desmutada."
            return f"Acao '{acao}' nao implementada para LG."
        except Exception as exc:
            return f"Erro no controle LG: {exc}"

    def controle_tv_samsung(self, ip: str, acao: str) -> str:
        try:
            from samsungtvws import SamsungTVWS

            tv = SamsungTVWS(ip)
            if acao == "desligar":
                tv.shortcuts().power()
                return "Comando de desligar enviado para TV Samsung."
            if acao == "volume_up":
                tv.shortcuts().volume_up()
                return "Volume da TV Samsung aumentado."
            if acao == "volume_down":
                tv.shortcuts().volume_down()
                return "Volume da TV Samsung diminuido."
            return f"Acao '{acao}' nao implementada para Samsung."
        except Exception as exc:
            return f"Erro no controle Samsung: {exc}"

    def controle_dispositivo_broadlink(self, ip: str, acao: str, temperatura=None) -> str:
        try:
            import broadlink

            devices = broadlink.discover(timeout=5, discover_ip_address=ip)
            if not devices:
                return f"Dispositivo Broadlink nao encontrado no IP {ip}."

            device = devices[0]
            device.auth()
            if acao == "aprender":
                device.enter_learning_mode()
                return "Broadlink em modo de aprendizado. Aponte o controle e aperte o botao."
            return "Comando enviado ao Broadlink (implementacao de base)."
        except Exception as exc:
            return f"Erro no controle Broadlink: {exc}"

    async def controle_tv_tcl(self, ip: str, acao: str) -> str:
        try:
            from androidtvremote2 import AndroidTVRemote

            certfile = Path("memory") / "tcl_cert.pem"
            keyfile = Path("memory") / "tcl_key.pem"
            if not certfile.exists():
                return "Erro: certificado nao encontrado. Use o script pair_tcl.py primeiro."

            if acao == "ligar":
                mac = os.getenv("TCL_TV_MAC", "78:66:9d:89:e3:7c")
                self.wake_on_lan(mac)
                return (
                    f"Pacote Wake-on-LAN enviado para ligar a TV TCL ({mac}). "
                    "Verifique se ela liga em alguns segundos."
                )

            remote = AndroidTVRemote(
                client_name="Cortana Assistant",
                certfile=str(certfile),
                keyfile=str(keyfile),
                host=ip,
            )
            await remote.async_connect()

            key_map = {
                "desligar": 223,
                "power": 26,
                "sleep": 223,
                "home": 3,
                "confirmar": 66,
                "vol_up": 24,
                "vol_down": 25,
                "ok": 66,
            }

            if acao == "desligar":
                logger.info("[TCL] Running robust shutdown sequence.")
                remote.send_key_command(3)
                await asyncio.sleep(1.5)
                remote.send_key_command(223)
                await asyncio.sleep(1.0)
                remote.send_key_command(26)
                await asyncio.sleep(0.5)
                remote.send_key_command(66)
                response = "Sequencia robusta (Home + Sleep + Power + OK) enviada para a TV TCL."
            elif acao.isdigit():
                key_code = int(acao)
                remote.send_key_command(key_code)
                response = f"KeyCode {key_code} enviado para TV TCL."
            elif acao == "long_power":
                remote.send_key_command(26, direction="START_LONG")
                await asyncio.sleep(1.0)
                remote.send_key_command(26, direction="END_LONG")
                response = "Comando Power (simulacao de long press) enviado para TV TCL."
            elif acao in key_map:
                remote.send_key_command(key_map[acao])
                response = f"Comando '{acao}' enviado para TV TCL."
            else:
                response = f"Acao '{acao}' nao mapeada para TCL."

            await asyncio.sleep(0.5)
            remote.disconnect()
            return response
        except Exception as exc:
            return f"Erro no controle TCL (V2): {exc}"


if __name__ == "__main__":
    cortana = CortanaControl()
    print("Controle Cortana inicializado.")

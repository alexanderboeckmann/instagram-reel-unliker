#!/usr/bin/env python3
import os
import sys
import json
import time
import random
import logging
import platform
import subprocess
import shutil
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple, Set
from getpass import getpass
import webbrowser
import signal
from tqdm import tqdm
import urllib.request
import tempfile
import shlex
import zipfile
import argparse
import re
from logging.handlers import RotatingFileHandler
import atexit

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
LIKED_POSTS_PATH = DATA_DIR / "liked_posts.json"
FOLLOWING_PATH = DATA_DIR / "following.json"
IMPORT_META_PATH = DATA_DIR / "import_meta.json"
EXPORT_SEARCH_DIRS = [Path.home() / "Downloads", Path.home() / "Desktop", BASE_DIR]

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

CONFIG = {
    "delay": {
        "min": 20,
        "max": 100,
    },
    "break": {
        "min": 900,
        "max": 3600,
        "probability": 0.01
    },
    "accounts": {},
    "excluded_users": [],
    "log_level": "INFO",
    "max_retries": 3,
    "retry_delay": 60,
    "auto_update": True,
    "python_min_version": "3.7.0"
}

class ConsoleColors:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    PURPLE = '\033[95m'
    CYAN = '\033[96m'
    WHITE = '\033[97m'
    MAGENTA = '\033[35m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'
    RESET = '\033[0m'


class SessionStore:
    SERVICE = "instagram-reel-unliker-session"

    def __init__(self):
        try:
            import keyring
            from keyring.backends.fail import Keyring as FailKeyring
            self._keyring = None if isinstance(keyring.get_keyring(), FailKeyring) else keyring
        except Exception as e:
            logging.warning(f"Keyring unavailable, sessions will not be stored: {e}")
            self._keyring = None

    @property
    def available(self) -> bool:
        return self._keyring is not None

    def load(self, username: str) -> str:
        if not self.available:
            return ""
        try:
            return self._keyring.get_password(self.SERVICE, username) or ""
        except Exception as e:
            logging.error(f"Keychain read failed: {e}")
            return ""

    def save(self, username: str, data: str) -> bool:
        if not self.available:
            return False
        try:
            blob = json.dumps(json.loads(data), separators=(',', ':'))
        except Exception:
            blob = data.strip()
        try:
            self._keyring.set_password(self.SERVICE, username, blob)
            return True
        except Exception as e:
            logging.error(f"Keychain write failed: {e}")
            return False

    def delete(self, username: str):
        if not self.available:
            return
        try:
            self._keyring.delete_password(self.SERVICE, username)
        except Exception:
            pass


class InstagramUnliker: 
    def __init__(self):
        logging.info("Starting Instagram Unliker application...")
        
        self.config_file = "config.json"
        self.accounts_dir = Path("accounts")
        self.logs_dir = Path("logs")
        self.running = True
        self.excluded_users: Set[str] = set()
        self.sessions = SessionStore()
        
        self._create_required_directories()
        self._ensure_python_environment()
        self._setup_signal_handlers()
        self.setup_logging()
        self.check_and_create_config()
        self._load_excluded_users()
        self._migrate_credentials()
        
    def _migrate_credentials(self):
        legacy = Path("ensta-session.json")
        if legacy.exists():
            try:
                data = json.loads(legacy.read_text())
                owner = data.get('identifier') or data.get('username')
                if owner and self.sessions.save(owner, json.dumps(data)):
                    print(f"{ConsoleColors.GREEN}[✓] Moved @{owner}'s login session into the Keychain{ConsoleColors.RESET}")
                    logging.info(f"Migrated session for {owner} into the Keychain")
                legacy.unlink()
            except Exception as e:
                logging.warning(f"Could not migrate {legacy}: {e}")

        for account_file in sorted(self.accounts_dir.glob("*.json")):
            try:
                data = json.loads(account_file.read_text())
            except Exception:
                continue
            if data.pop('password', None) is not None:
                username = data.get('username', account_file.stem)
                self._write_account(account_file, data)
                print(f"{ConsoleColors.GREEN}[✓] Removed @{username}'s stored password from {account_file}{ConsoleColors.RESET}")
                logging.info(f"Stripped stored password for {username}")

    def _write_account(self, account_file: Path, data: dict):
        data.pop('password', None)
        with open(account_file, 'w') as f:
            json.dump(data, f, indent=4)
        try:
            os.chmod(account_file, 0o600)
        except OSError as e:
            logging.warning(f"Could not tighten permissions on {account_file}: {e}")

    def _prompt_password(self, username: str) -> str:
        return getpass(f"{ConsoleColors.BOLD}Password for @{username} (not echoed): {ConsoleColors.RESET}").strip()

    def _login(self, username: str):
        from ensta import Web
        saver = lambda data: self.sessions.save(username, data)

        cached = self.sessions.load(username)
        if cached:
            try:
                return Web(username, "", load=lambda: cached, save=saver)
            except Exception as e:
                logging.info(f"Stored session for {username} rejected ({e}), re-authenticating")
                self.sessions.delete(username)
                print(f"{ConsoleColors.YELLOW}[!] Saved session expired{ConsoleColors.RESET}")

        password = self._prompt_password(username)
        if not password:
            raise ValueError("No password provided")
        return Web(username, password, load=lambda: "", save=saver)

    def _load_excluded_users(self):
        self.excluded_users = set(CONFIG.get('excluded_users', []))
        logging.info(f"Loaded {len(self.excluded_users)} excluded users")
        
    def _ensure_python_environment(self):
        try:
            import pip
        except ImportError:
            logging.warning("pip is not installed. Installing pip...")
            self._install_pip()
            
    def _install_pip(self):
        try:
            import urllib.request
            urllib.request.urlretrieve("https://bootstrap.pypa.io/get-pip.py", "get-pip.py")
            subprocess.check_call([sys.executable, "get-pip.py"])
            logging.info("Successfully installed pip")
            os.remove("get-pip.py")
        except Exception as e:
            logging.error(f"Failed to install pip: {str(e)}")
            print("Please visit https://pip.pypa.io/en/stable/installation/ for manual installation instructions")
            sys.exit(1)
            
    def _setup_signal_handlers(self):
        signal.signal(signal.SIGINT, self._handle_shutdown)
        signal.signal(signal.SIGTERM, self._handle_shutdown)
        
    def _handle_shutdown(self, signum, frame):
        print(f"\n{ConsoleColors.YELLOW}[!] Received shutdown signal. Cleaning up...{ConsoleColors.RESET}")
        self.running = False
        time.sleep(1)
        sys.exit(0)
        
    def setup_logging(self):
        self.logs_dir = Path("logs")
        self.logs_dir.mkdir(exist_ok=True)
        
        log_file = self.logs_dir / "unliker.log"
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=5*1024*1024,
            backupCount=5,
            encoding='utf-8'
        )
        
        file_formatter = logging.Formatter(
            '%(asctime)s [%(levelname)s] [%(filename)s:%(lineno)d] %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        console_formatter = logging.Formatter(
            '%(asctime)s [%(levelname)s] %(message)s',
            datefmt='%H:%M:%S'
        )
        
        file_handler.setFormatter(file_formatter)
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(console_formatter)
        
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.INFO)
        root_logger.handlers.clear()
        root_logger.addHandler(file_handler)
        root_logger.addHandler(console_handler)
        
        atexit.register(self._cleanup_logs)
        logging.info("Logging system initialized")
        
    def _cleanup_logs(self):
        try:
            logging.info("Performing final cleanup...")
            self.save_config()
            for handler in logging.getLogger().handlers:
                handler.close()
        except Exception as e:
            print(f"Error during cleanup: {str(e)}")

    def _create_required_directories(self):
        try:
            self.accounts_dir.mkdir(exist_ok=True)
            os.chmod(self.accounts_dir, 0o700)
            self.logs_dir.mkdir(exist_ok=True)
            DATA_DIR.mkdir(exist_ok=True)
            logging.info("Required directories created successfully")
        except Exception as e:
            logging.error(f"Failed to create directories: {str(e)}")
            print("Please ensure you have write permissions in the current directory")

    def check_python_version(self) -> bool:
        version = sys.version_info
        if version.major < 3 or (version.major == 3 and version.minor < 7):
            print(f"{ConsoleColors.RED}[✗] Error: Python 3.7 or higher required (current: {version.major}.{version.minor}){ConsoleColors.RESET}")
            return False
        print(f"{ConsoleColors.GREEN}[✓] Python version check passed ({version.major}.{version.minor}){ConsoleColors.RESET}")
        return True

    def install_requirements(self) -> bool:
        try:
            subprocess.run([sys.executable, "-m", "pip", "uninstall", "-y", "ensta"], 
                          stdout=subprocess.PIPE, 
                          stderr=subprocess.PIPE)
            
            result = subprocess.run([sys.executable, "-m", "pip", "install", "--no-cache-dir", "ensta==5.2.9", "keyring"],
                                  stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE)
            
            if result.returncode != 0:
                logging.error(f"Failed to install ensta: {result.stderr.decode()}")
                print(f"{ConsoleColors.RED}[✗] Failed to install ensta. Error: {result.stderr.decode()}{ConsoleColors.RESET}")
                return False
                
            logging.info("Successfully installed ensta")
            print(f"{ConsoleColors.GREEN}[✓] Successfully installed ensta{ConsoleColors.RESET}")
            return True
            
        except Exception as e:
            logging.error(f"Error during package installation: {str(e)}")
            print(f"{ConsoleColors.RED}[✗] Installation error: {str(e)}{ConsoleColors.RESET}")
            return False

    def check_and_create_config(self):
        if not os.path.exists(self.config_file):
            with open(self.config_file, 'w') as f:
                json.dump(CONFIG, f, indent=4)
            print(f"{ConsoleColors.GREEN}[✓] Created default configuration file{ConsoleColors.RESET}")
        else:
            try:
                with open(self.config_file, 'r') as f:
                    loaded_config = json.load(f)
                    for key, value in loaded_config.items():
                        if key in CONFIG:
                            CONFIG[key] = value
                print(f"{ConsoleColors.GREEN}[✓] Loaded existing configuration{ConsoleColors.RESET}")
            except json.JSONDecodeError:
                print(f"{ConsoleColors.RED}[✗] Error: Corrupted configuration file{ConsoleColors.RESET}")
                backup_file = f"{self.config_file}.bak"
                os.rename(self.config_file, backup_file)
                print(f"{ConsoleColors.YELLOW}[!] Backed up corrupted config to {backup_file}{ConsoleColors.RESET}")
                self.check_and_create_config()

    def add_account(self):
        print(f"\n{ConsoleColors.CYAN}➕ Add Instagram Account{ConsoleColors.RESET}")
        print("-" * 40)
        
        username = input(f"{ConsoleColors.BOLD}Username: {ConsoleColors.RESET}").strip()
        
        if not username:
            print(f"{ConsoleColors.RED}Username is required{ConsoleColors.RESET}")
            return
        
        self.accounts_dir.mkdir(exist_ok=True)
        os.chmod(self.accounts_dir, 0o700)
        account_file = self.accounts_dir / f"{username}.json"
        
        if account_file.exists():
            override = input(f"{ConsoleColors.YELLOW}Account exists. Replace? (y/N): {ConsoleColors.RESET}").lower()
            if override != 'y':
                return

        account_data = {
            "username": username,
            "last_run": None,
            "total_unliked": 0,
            "last_error": None,
            "created_at": datetime.now().isoformat()
        }
        
        try:
            self._write_account(account_file, account_data)
            
            CONFIG['accounts'][username] = {
                "enabled": True,
                "delay_multiplier": 1.0
            }
            self.save_config()
            
            print(f"{ConsoleColors.GREEN}✨ Account @{username} added successfully!{ConsoleColors.RESET}")
            print(f"{ConsoleColors.BLUE}[*] Your password is asked for at first login and never stored{ConsoleColors.RESET}")
        except Exception as e:
            print(f"{ConsoleColors.RED}Could not save account: {str(e)}{ConsoleColors.RESET}")

    def remove_account(self):
        accounts = self.list_accounts()
        if not accounts:
            print(f"{ConsoleColors.YELLOW}[!] No accounts configured{ConsoleColors.RESET}")
            return
            
        print(f"\n{ConsoleColors.BLUE}[×] Remove Account{ConsoleColors.RESET}")
        print("-" * 30)
        for i, acc in enumerate(accounts, 1):
            print(f"{i}. {acc}")
            
        try:
            choice = input(f"\n{ConsoleColors.BOLD}Select account to remove (0 to cancel): {ConsoleColors.RESET}")
            if not choice.isdigit() or int(choice) == 0:
                return
                
            choice = int(choice)
            if choice < 1 or choice > len(accounts):
                print(f"{ConsoleColors.RED}[✗] Invalid selection{ConsoleColors.RESET}")
                return
                
            username = accounts[choice - 1]
            account_file = self.accounts_dir / f"{username}.json"
            
            confirm = input(f"{ConsoleColors.YELLOW}[!] Are you sure you want to remove {username}? (y/N): {ConsoleColors.RESET}").lower()
            if confirm != 'y':
                return
            
            if account_file.exists():
                account_file.unlink()

            self.sessions.delete(username)
                
            if username in CONFIG['accounts']:
                del CONFIG['accounts'][username]
                self.save_config()
                
            print(f"{ConsoleColors.GREEN}[✓] Account {username} removed successfully{ConsoleColors.RESET}")
            
        except Exception as e:
            print(f"{ConsoleColors.RED}[✗] Error: {str(e)}{ConsoleColors.RESET}")

    def manage_excluded_users(self):
        while True:
            print(f"\n{ConsoleColors.CYAN}🚫 Manage Excluded Users{ConsoleColors.RESET}")
            print("=" * 50)
            
            if self.excluded_users:
                print(f"\n{ConsoleColors.YELLOW}Currently Excluded ({len(self.excluded_users)} users):{ConsoleColors.RESET}")
                for i, user in enumerate(sorted(self.excluded_users), 1):
                    print(f"  {i}. @{user}")
            else:
                print(f"\n{ConsoleColors.YELLOW}No users excluded yet{ConsoleColors.RESET}")
            
            print(f"\n{ConsoleColors.CYAN}Options:{ConsoleColors.RESET}")
            print(f"  1. Add user to exclude list")
            print(f"  2. Remove user from exclude list")
            print(f"  3. Clear all excluded users")
            print(f"  0. Back to main menu")
            
            choice = input(f"\n{ConsoleColors.BOLD}Select option: {ConsoleColors.RESET}").strip()
            
            if choice == "1":
                username = input(f"{ConsoleColors.BOLD}Enter username to exclude: {ConsoleColors.RESET}").strip().lower()
                if username:
                    self.excluded_users.add(username)
                    CONFIG['excluded_users'] = list(self.excluded_users)
                    self.save_config()
                    print(f"{ConsoleColors.GREEN}✓ Added @{username} to exclude list{ConsoleColors.RESET}")
                    
            elif choice == "2":
                if not self.excluded_users:
                    print(f"{ConsoleColors.YELLOW}No users to remove{ConsoleColors.RESET}")
                    continue
                    
                username = input(f"{ConsoleColors.BOLD}Enter username to remove: {ConsoleColors.RESET}").strip().lower()
                if username in self.excluded_users:
                    self.excluded_users.remove(username)
                    CONFIG['excluded_users'] = list(self.excluded_users)
                    self.save_config()
                    print(f"{ConsoleColors.GREEN}✓ Removed @{username} from exclude list{ConsoleColors.RESET}")
                else:
                    print(f"{ConsoleColors.YELLOW}User not found in exclude list{ConsoleColors.RESET}")
                    
            elif choice == "3":
                if self.excluded_users:
                    confirm = input(f"{ConsoleColors.YELLOW}Clear all excluded users? (y/N): {ConsoleColors.RESET}").lower()
                    if confirm == 'y':
                        self.excluded_users.clear()
                        CONFIG['excluded_users'] = []
                        self.save_config()
                        print(f"{ConsoleColors.GREEN}✓ Cleared all excluded users{ConsoleColors.RESET}")
                        
            elif choice == "0":
                break
            else:
                print(f"{ConsoleColors.RED}Invalid option{ConsoleColors.RESET}")
            
            time.sleep(1)

    def list_accounts(self) -> List[str]:
        if not self.accounts_dir.exists():
            return []
        return [f.stem for f in self.accounts_dir.glob("*.json")]

    def save_config(self):
        try:
            with open(self.config_file, 'w') as f:
                json.dump(CONFIG, f, indent=4)
        except Exception as e:
            print(f"{ConsoleColors.RED}[✗] Failed to save configuration: {str(e)}{ConsoleColors.RESET}")

    @staticmethod
    def _resolve_dropped_path(raw: str) -> Optional[Path]:
        raw = raw.strip()
        if not raw:
            return None
        try:
            parts = shlex.split(raw)
            token = parts[0] if parts else raw
        except ValueError:
            token = raw
            if len(token) > 1 and token[0] == token[-1] and token[0] in "'\"":
                token = token[1:-1]
            token = token.replace('\\ ', ' ')
        return Path(os.path.expanduser(token))

    @staticmethod
    def _export_label(path: Path) -> str:
        match = re.search(r'(\d{4}-\d{2}-\d{2})', path.name)
        if match:
            return match.group(1)
        try:
            return datetime.fromtimestamp(path.stat().st_mtime).strftime('%Y-%m-%d')
        except OSError:
            return "unknown date"

    @staticmethod
    def _dir_size(path: Path) -> int:
        if path.is_file():
            return path.stat().st_size
        total = 0
        for root, _, files in os.walk(path):
            for name in files:
                try:
                    total += os.path.getsize(os.path.join(root, name))
                except OSError:
                    pass
        return total

    @staticmethod
    def _human_size(num: int) -> str:
        for unit in ('B', 'KB', 'MB', 'GB'):
            if num < 1024 or unit == 'GB':
                return f"{num:.0f} {unit}" if unit == 'B' else f"{num:.1f} {unit}"
            num /= 1024.0
        return f"{num:.1f} GB"

    @staticmethod
    def _discover_exports() -> List[Path]:
        found = []
        seen = set()
        for directory in EXPORT_SEARCH_DIRS:
            if not directory.is_dir():
                continue
            try:
                entries = list(directory.iterdir())
            except OSError:
                continue
            for entry in entries:
                if entry.resolve() in seen:
                    continue
                is_export = False
                if entry.is_dir():
                    is_export = entry.name.startswith('instagram-') or (entry / 'your_instagram_activity').is_dir()
                elif entry.suffix.lower() == '.zip':
                    is_export = entry.name.startswith('instagram-')
                if is_export:
                    seen.add(entry.resolve())
                    found.append(entry)
        found.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return found[:5]

    @staticmethod
    def _looks_like_html(path: Path) -> bool:
        try:
            with open(path, 'rb') as f:
                return f.read(2048).lstrip()[:1] == b'<'
        except OSError:
            return False

    @staticmethod
    def _classify_json(path: Path) -> Optional[str]:
        try:
            with open(path, 'r') as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return None
        if isinstance(data, dict):
            if 'relationships_following' in data:
                return 'following'
            if 'likes_media_likes' in data:
                return 'liked_posts'
            return None
        if isinstance(data, list) and data and isinstance(data[0], dict) and 'label_values' in data[0]:
            return 'liked_posts'
        return None

    def _locate_export_files(self, source: Path, workdir: Path) -> Tuple[Optional[Path], Optional[Path]]:
        if not source.exists():
            raise ValueError(f"Nothing found at {source}")

        if source.is_file() and source.suffix.lower() == '.zip':
            liked = following = None
            with zipfile.ZipFile(source) as zf:
                for member in zf.namelist():
                    name = os.path.basename(member)
                    if name == 'liked_posts.json' and liked is None:
                        liked = Path(zf.extract(member, workdir))
                    elif name == 'following.json' and following is None:
                        following = Path(zf.extract(member, workdir))
            return liked, following

        if source.is_file():
            if source.suffix.lower() in ('.html', '.htm') or self._looks_like_html(source):
                raise ValueError(
                    "That looks like an HTML export. Request your data again from Instagram "
                    "and choose format: JSON."
                )
            kind = self._classify_json(source)
            if kind == 'liked_posts':
                return source, None
            if kind == 'following':
                return None, source
            raise ValueError(f"{source.name} is not a liked_posts.json or following.json export file")

        def pick(filename: str, preferred: str) -> Optional[Path]:
            matches = sorted(source.rglob(filename))
            if not matches:
                return None
            for match in matches:
                if preferred in match.as_posix():
                    return match
            return matches[0]

        liked = pick('liked_posts.json', 'your_instagram_activity/likes')
        following = pick('following.json', 'connections/followers_and_following')
        if liked is None and following is None:
            raise ValueError(
                f"No liked_posts.json or following.json found under {source}.\n"
                "    Point this at the unzipped export folder (the one containing "
                "'your_instagram_activity')."
            )
        return liked, following

    def _validate_liked_posts(self, path: Path) -> Tuple[int, int]:
        if self._looks_like_html(path):
            raise ValueError(
                "That liked_posts.json is HTML, not JSON. Request your data again from "
                "Instagram and choose format: JSON."
            )
        try:
            with open(path, 'r') as f:
                raw = json.load(f)
        except json.JSONDecodeError as e:
            raise ValueError(f"Could not read {path.name} as JSON: {e}")

        if isinstance(raw, dict):
            raw = raw.get('likes_media_likes', [])
        if not isinstance(raw, list) or not raw:
            raise ValueError(f"{path.name} contains no liked posts")

        total = reels = 0
        for post in raw:
            url, _ = self._parse_liked_post(post)
            if not url:
                continue
            total += 1
            if '/reel/' in url:
                reels += 1
        if total == 0:
            raise ValueError(
                f"{path.name} has {len(raw)} entries but no readable post URLs — "
                "Instagram's export format may have changed."
            )
        return total, reels

    @staticmethod
    def _validate_following(path: Path) -> int:
        try:
            with open(path, 'r') as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            raise ValueError(f"Could not read {path.name} as JSON: {e}")
        if not isinstance(data, dict) or 'relationships_following' not in data:
            raise ValueError(f"{path.name} is not an Instagram following export")
        return len([e for e in data['relationships_following'] if e.get('title')])

    @staticmethod
    def _read_import_meta() -> Optional[dict]:
        try:
            return json.loads(IMPORT_META_PATH.read_text())
        except (OSError, json.JSONDecodeError):
            return None

    def _prompt_for_source(self) -> Optional[Path]:
        try:
            return self._ask_for_source()
        except EOFError:
            return None

    def _ask_for_source(self) -> Optional[Path]:
        candidates = self._discover_exports()
        if candidates:
            print(f"\n{ConsoleColors.BLUE}Exports found on this Mac:{ConsoleColors.RESET}")
            for i, candidate in enumerate(candidates, 1):
                kind = "zip" if candidate.is_file() else "folder"
                print(f"  {ConsoleColors.BOLD}{i}.{ConsoleColors.RESET} {candidate.name} "
                      f"{ConsoleColors.WHITE}({kind}, {self._export_label(candidate)}){ConsoleColors.RESET}")
            print(f"  {ConsoleColors.BOLD}p.{ConsoleColors.RESET} Paste or drag in a different folder/zip")
            print(f"  {ConsoleColors.BOLD}0.{ConsoleColors.RESET} Cancel")
            choice = input(f"\n{ConsoleColors.WHITE}╰─▸{ConsoleColors.RESET} ").strip().lower()
            if choice in ('', '0'):
                return None
            if choice != 'p':
                try:
                    return candidates[int(choice) - 1]
                except (ValueError, IndexError):
                    print(f"{ConsoleColors.RED}Invalid choice{ConsoleColors.RESET}")
                    return None
        else:
            print(f"\n{ConsoleColors.YELLOW}No Instagram export found in Downloads or Desktop.{ConsoleColors.RESET}")

        print(f"\n{ConsoleColors.CYAN}Drag the unzipped export folder (or its .zip) from Finder "
              f"into this window, then press Enter.{ConsoleColors.RESET}")
        print(f"{ConsoleColors.WHITE}Leave empty to cancel.{ConsoleColors.RESET}")
        return self._resolve_dropped_path(input(f"{ConsoleColors.WHITE}╰─▸{ConsoleColors.RESET} "))

    def _offer_delete_source(self, source: Path):
        if source.is_file() and source.suffix.lower() != '.zip':
            return
        try:
            if source.resolve() == BASE_DIR or BASE_DIR in source.resolve().parents:
                return
        except OSError:
            return
        size = self._human_size(self._dir_size(source))
        try:
            answer = input(
                f"\n{ConsoleColors.YELLOW}Delete the source export at {source} ({size})? (y/N): "
                f"{ConsoleColors.RESET}"
            ).strip().lower()
        except EOFError:
            return
        if answer != 'y':
            return
        try:
            if source.is_dir():
                shutil.rmtree(source)
            else:
                source.unlink()
            print(f"{ConsoleColors.GREEN}✓ Deleted {source}{ConsoleColors.RESET}")
        except OSError as e:
            print(f"{ConsoleColors.RED}[✗] Could not delete it: {e}{ConsoleColors.RESET}")

    def import_export(self, source: Optional[Path] = None) -> bool:
        if source is None:
            source = self._prompt_for_source()
        if source is None:
            print(f"{ConsoleColors.YELLOW}Import cancelled{ConsoleColors.RESET}")
            return False

        try:
            with tempfile.TemporaryDirectory() as tmp:
                liked_src, following_src = self._locate_export_files(source, Path(tmp))

                liked_total = liked_reels = following_count = 0
                if liked_src:
                    liked_total, liked_reels = self._validate_liked_posts(liked_src)
                if following_src:
                    following_count = self._validate_following(following_src)

                DATA_DIR.mkdir(exist_ok=True)
                if liked_src:
                    shutil.copy2(liked_src, LIKED_POSTS_PATH)
                if following_src:
                    shutil.copy2(following_src, FOLLOWING_PATH)
        except (ValueError, zipfile.BadZipFile, OSError) as e:
            print(f"\n{ConsoleColors.RED}[✗] {e}{ConsoleColors.RESET}")
            logging.error(f"Import failed for {source}: {e}")
            return False

        print(f"\n{ConsoleColors.GREEN}✓ Imported from {source.name}{ConsoleColors.RESET}")
        if liked_src:
            print(f"  {ConsoleColors.WHITE}Liked posts   : {liked_total:,}{ConsoleColors.RESET}")
            print(f"  {ConsoleColors.WHITE}Reels among them: {liked_reels:,}{ConsoleColors.RESET}")
        if following_src:
            print(f"  {ConsoleColors.WHITE}Following     : {following_count:,} (their reels will be skipped){ConsoleColors.RESET}")
        else:
            print(f"  {ConsoleColors.YELLOW}No following.json — the follow filter stays off{ConsoleColors.RESET}")

        meta = self._read_import_meta() or {}
        if liked_src:
            meta.update({'liked_total': liked_total, 'liked_reels': liked_reels})
        if following_src:
            meta['following'] = following_count
        meta.update({'source': source.name, 'imported': datetime.now().strftime('%Y-%m-%d')})
        try:
            IMPORT_META_PATH.write_text(json.dumps(meta, indent=2))
        except OSError as e:
            logging.warning(f"Could not write import metadata: {e}")

        logging.info(f"Imported export from {source} ({liked_total} posts, {liked_reels} reels)")
        self._offer_delete_source(source)
        return True

    def _load_following(self) -> Set[str]:
        if not FOLLOWING_PATH.exists():
            logging.info("following.json not found — following filter disabled")
            return set()
        try:
            with open(FOLLOWING_PATH, 'r') as f:
                data = json.load(f)
            following = {
                entry['title'].lower()
                for entry in data.get('relationships_following', [])
                if entry.get('title')
            }
            logging.info(f"Loaded {len(following)} followed accounts from following.json")
            return following
        except Exception as e:
            logging.warning(f"Could not load following.json: {e}")
            return set()

    @staticmethod
    def _parse_liked_post(post: dict):
        url = None
        post_username = None
        try:
            for lv in post.get('label_values', []):
                if lv.get('label') == 'URL' and lv.get('value'):
                    url = lv['value']
                if lv.get('title') == 'Owner':
                    for owner_entry in lv.get('dict', []):
                        for field in owner_entry.get('dict', []):
                            if field.get('label') == 'Username' and field.get('value'):
                                post_username = field['value'].lower()
        except Exception:
            pass
        return url, post_username

    def unlike_posts(self, username: str):
        account_file = self.accounts_dir / f"{username}.json"
        progress_bar = None

        if not account_file.exists():
            error_msg = f"Account file not found for {username}"
            logging.error(error_msg)
            print(f"\n{ConsoleColors.RED}[✗] {error_msg}. Please add it first.{ConsoleColors.RESET}")
            return

        if not LIKED_POSTS_PATH.exists():
            print(f"\n{ConsoleColors.YELLOW}[!] No Instagram export imported yet.{ConsoleColors.RESET}")
            if not self.import_export():
                return

        try:
            with open(account_file, 'r') as f:
                account_data = json.load(f)

            print(f"\n{ConsoleColors.CYAN}Starting to unlike reels for @{username}...{ConsoleColors.RESET}")

            following = self._load_following()
            if following:
                print(f"{ConsoleColors.BLUE}ℹ️  Loaded {len(following)} followed accounts — their reels will be skipped{ConsoleColors.RESET}")

            if self.excluded_users:
                print(f"{ConsoleColors.YELLOW}ℹ️  Excluding {len(self.excluded_users)} manually excluded users{ConsoleColors.RESET}")

            try:
                client = self._login(account_data['username'])
                print(f"{ConsoleColors.GREEN}✓ Successfully logged in{ConsoleColors.RESET}")
                account = client.private_info()
                print(f"{ConsoleColors.GREEN}Logged in as: {ConsoleColors.CYAN}{account.username}{ConsoleColors.RESET}")
            except Exception as e:
                error_msg = f"Login failed: {str(e)}"
                logging.error(error_msg)
                print(f"{ConsoleColors.RED}[✗] {error_msg}")
                print(f"→ Please check your username and password.{ConsoleColors.RESET}")
                return

            try:
                with open(LIKED_POSTS_PATH, 'r') as f:
                    raw_posts = json.load(f)

                if isinstance(raw_posts, dict):
                    raw_posts = raw_posts.get('likes_media_likes', [])

                if not raw_posts:
                    print(f"{ConsoleColors.YELLOW}[!] No liked posts found in liked_posts.json{ConsoleColors.RESET}")
                    return

                reels_only: list = []
                skipped_not_reel = 0
                skipped_following = 0
                skipped_excluded = 0

                for post in raw_posts:
                    url, post_username = self._parse_liked_post(post)

                    if not url or '/reel/' not in url:
                        skipped_not_reel += 1
                        continue

                    if post_username and post_username in following:
                        skipped_following += 1
                        logging.debug(f"Skipping reel from followed account: @{post_username}")
                        continue

                    if post_username and post_username in self.excluded_users:
                        skipped_excluded += 1
                        logging.debug(f"Skipping reel from excluded user: @{post_username}")
                        continue

                    reels_only.append((post, url, post_username))

                total_posts = len(reels_only)

                print(f"\n{ConsoleColors.BLUE}📊 Filter summary:{ConsoleColors.RESET}")
                print(f"  {ConsoleColors.GREEN}Reels to unlike : {total_posts}{ConsoleColors.RESET}")
                print(f"  {ConsoleColors.YELLOW}Non-reel posts  : {skipped_not_reel} (skipped){ConsoleColors.RESET}")
                print(f"  {ConsoleColors.YELLOW}From following  : {skipped_following} (skipped){ConsoleColors.RESET}")
                if skipped_excluded:
                    print(f"  {ConsoleColors.YELLOW}Excluded users  : {skipped_excluded} (skipped){ConsoleColors.RESET}")

                if total_posts == 0:
                    print(f"{ConsoleColors.YELLOW}No reels to unlike after filtering{ConsoleColors.RESET}")
                    return

                unliked_count = 0
                failed_urls: list = []

                progress_bar = tqdm(
                    total=total_posts,
                    desc="🔄 Unliking reels",
                    bar_format='{l_bar}{bar}| {n_fmt}/{total_fmt} [ETA: {remaining}]'
                )

                for post, url, post_username in reels_only:
                    if not self.running:
                        break
                    try:
                        base_delay = random.uniform(CONFIG['delay']['min'], CONFIG['delay']['max'])
                        actual_delay = base_delay * CONFIG['accounts'].get(username, {}).get('delay_multiplier', 1.0)
                        time.sleep(actual_delay)

                        media_id = instagram_code_to_media_id(url)

                        for retry in range(CONFIG['max_retries']):
                            try:
                                client.unlike(media_id)
                                break
                            except Exception as e:
                                error_msg = f"Failed to unlike reel (attempt {retry + 1}/{CONFIG['max_retries']}): {str(e)}"
                                logging.warning(error_msg)
                                if retry < CONFIG['max_retries'] - 1:
                                    time.sleep(CONFIG['retry_delay'])
                                else:
                                    raise Exception(error_msg)

                        unliked_count += 1
                        account_data['total_unliked'] += 1
                        progress_bar.update(1)

                        if random.random() < CONFIG['break']['probability']:
                            break_time = random.uniform(CONFIG['break']['min'], CONFIG['break']['max'])
                            progress_bar.write(f"{ConsoleColors.BLUE}[*] Taking a break for {break_time/60:.1f} minutes...{ConsoleColors.RESET}")
                            time.sleep(break_time)

                    except Exception as e:
                        error_msg = f"Failed to unlike reel {url}: {str(e)}"
                        logging.error(error_msg, exc_info=True)
                        progress_bar.write(f"{ConsoleColors.RED}[✗] {error_msg}{ConsoleColors.RESET}")
                        account_data['last_error'] = error_msg
                        failed_urls.append(url)
                        time.sleep(300)

            finally:
                if progress_bar is not None:
                    progress_bar.close()

            account_data['last_run'] = datetime.now().isoformat()
            self._write_account(account_file, account_data)

            print(f"\n{ConsoleColors.GREEN}[✓] Unliking complete for {username}{ConsoleColors.RESET}")
            print(f"{ConsoleColors.BLUE}[*] Reels unliked : {unliked_count}{ConsoleColors.RESET}")
            if failed_urls:
                print(f"{ConsoleColors.RED}[*] Failed        : {len(failed_urls)}{ConsoleColors.RESET}")
            
        except json.JSONDecodeError as e:
            error_msg = f"Invalid JSON format: {str(e)}"
            logging.error(error_msg)
            print(f"{ConsoleColors.RED}[✗] {error_msg}{ConsoleColors.RESET}")
        except Exception as e:
            error_msg = f"Unexpected error: {str(e)}"
            logging.error(error_msg, exc_info=True)
            print(f"\n{ConsoleColors.RED}[✗] {error_msg}{ConsoleColors.RESET}")

    def center_text_in_box(text, box_width=48):
        visible_length = get_visible_length(text)
        padding = (box_width - 2 - visible_length) // 2
        return f"║{' ' * padding}{text}{' ' * (box_width - 2 - visible_length - padding)}║"

    def show_menu(self):
        while True:
            print(f"\n{ConsoleColors.CYAN}{ConsoleColors.BOLD}╔{'═' * 46}╗")
            print(InstagramUnliker.center_text_in_box(f"{ConsoleColors.BOLD}Instagram Mass Unliker{ConsoleColors.RESET}{ConsoleColors.CYAN}{ConsoleColors.BOLD}"))
            print(InstagramUnliker.center_text_in_box(f"{ConsoleColors.BOLD}Erase your digital footprint{ConsoleColors.RESET}{ConsoleColors.CYAN}{ConsoleColors.BOLD}"))
            print(f"╚{'═' * 46}╝{ConsoleColors.RESET}")
            
            accounts = self.list_accounts()
            if accounts:
                print(f"\n{ConsoleColors.BLUE}Connected Accounts: {ConsoleColors.GREEN}{len(accounts)}{ConsoleColors.RESET}")
                for acc in accounts[:3]:
                    print(f"  {ConsoleColors.WHITE}•{ConsoleColors.RESET} @{acc}")
                if len(accounts) > 3:
                    print(f"  {ConsoleColors.WHITE}•{ConsoleColors.RESET} ...and {len(accounts) - 3} more")
            else:
                print(f"\n{ConsoleColors.YELLOW}No accounts connected yet{ConsoleColors.RESET}")
            
            if self.excluded_users:
                print(f"{ConsoleColors.YELLOW}🚫 Excluding {len(self.excluded_users)} users{ConsoleColors.RESET}")

            meta = self._read_import_meta() if LIKED_POSTS_PATH.exists() else None
            if meta:
                print(f"{ConsoleColors.BLUE}📦 {meta.get('liked_total', 0):,} liked posts · "
                      f"{meta.get('liked_reels', 0):,} reels · imported {meta.get('imported', '?')}{ConsoleColors.RESET}")
            elif LIKED_POSTS_PATH.exists():
                print(f"{ConsoleColors.BLUE}📦 Export imported{ConsoleColors.RESET}")
            else:
                print(f"{ConsoleColors.YELLOW}⚠️  No export imported yet — use option 3{ConsoleColors.RESET}")

            print(f"\n{ConsoleColors.CYAN}Available Actions:{ConsoleColors.RESET}")
            print(f"╭{'─' * 40}╮")
            print(menu_line("1", "Add Instagram Account"))
            print(menu_line("2", "Remove Account"))
            print(menu_line("3", "Import Instagram Data"))
            print(menu_line("4", "Start Unliking"))
            print(menu_line("5", "Manage Excluded Users"))
            print(menu_line("6", "View Stats"))
            print(menu_line("7", "Settings"))
            print(menu_line("0", "Exit"))
            print(f"╰{'─' * 40}╯")
            
            try:
                print(f"\n{ConsoleColors.WHITE}╭─ Enter your choice{ConsoleColors.RESET}")
                choice = input(f"{ConsoleColors.WHITE}╰─▸{ConsoleColors.RESET} ").strip()
                
                if choice == "1":
                    self.add_account()
                elif choice == "2":
                    self.remove_account()
                elif choice == "3":
                    self.import_export()
                elif choice == "4":
                    self._start_unliking_menu()
                elif choice == "5":
                    self.manage_excluded_users()
                elif choice == "6":
                    self.show_statistics()
                elif choice == "7":
                    self.show_settings()
                elif choice == "0":
                    print(f"\n{ConsoleColors.GREEN}✨ Thanks for using Instagram Unliker!")
                    print(f"👋 Have a great day!{ConsoleColors.RESET}")
                    break
                else:
                    print(f"\n{ConsoleColors.RED}✗ Invalid choice. Please try again.{ConsoleColors.RESET}")
                    time.sleep(1)
                    
            except KeyboardInterrupt:
                print(f"\n\n{ConsoleColors.GREEN}✨ Thanks for using Instagram Unliker!")
                print(f"👋 Have a great day!{ConsoleColors.RESET}")
                break
            except Exception as e:
                print(f"\n{ConsoleColors.RED}✗ Error: {str(e)}{ConsoleColors.RESET}")
                time.sleep(2)

    def _start_unliking_menu(self):
        accounts = self.list_accounts()
        if not accounts:
            print(f"{ConsoleColors.RED}[✗] No accounts configured. Please add an account first.{ConsoleColors.RESET}")
            return
            
        print(f"\n{ConsoleColors.BLUE}[#] Select Account{ConsoleColors.RESET}")
        print("-" * 30)
        
        for i, acc in enumerate(accounts, 1):
            account_file = self.accounts_dir / f"{acc}.json"
            status = "Ready"
            if account_file.exists():
                with open(account_file) as f:
                    data = json.load(f)
                    if data.get('last_error'):
                        status = f"Error"
                    elif data.get('last_run'):
                        status = f"Last: {datetime.fromisoformat(data['last_run']).strftime('%Y-%m-%d %H:%M')}"
            
            print(f"{ConsoleColors.BOLD}{i}{ConsoleColors.RESET}. [{acc}] - {status}")
            
        try:
            choice = input(f"\n{ConsoleColors.BOLD}[>] Select account (0 to cancel): {ConsoleColors.RESET}")
            if not choice.isdigit() or int(choice) == 0:
                return
                
            choice = int(choice)
            if choice < 1 or choice > len(accounts):
                print(f"{ConsoleColors.RED}[✗] Invalid selection{ConsoleColors.RESET}")
                return
                
            self.unlike_posts(accounts[choice - 1])
            
        except ValueError:
            print(f"{ConsoleColors.RED}[✗] Invalid input{ConsoleColors.RESET}")
        except Exception as e:
            print(f"{ConsoleColors.RED}[✗] Error: {str(e)}{ConsoleColors.RESET}")

    def show_statistics(self):
        accounts = self.list_accounts()
        if not accounts:
            print(f"{ConsoleColors.YELLOW}No accounts added yet{ConsoleColors.RESET}")
            input(f"\n{ConsoleColors.BOLD}Press Enter to continue...{ConsoleColors.RESET}")
            return
            
        print(f"\n{ConsoleColors.CYAN}📊 Statistics{ConsoleColors.RESET}")
        print("=" * 40)
        
        total_unliked = 0
        for username in accounts:
            account_file = self.accounts_dir / f"{username}.json"
            try:
                with open(account_file, 'r') as f:
                    data = json.load(f)
                    
                total_unliked += data.get('total_unliked', 0)
                print(f"\n{ConsoleColors.BOLD}{ConsoleColors.BLUE}@{username}{ConsoleColors.RESET}")
                print(f"📌 Unliked posts: {data.get('total_unliked', 0)}")
                if data.get('last_run'):
                    print(f"🕒 Last active: {datetime.fromisoformat(data['last_run']).strftime('%Y-%m-%d %H:%M')}")
                print(f"✨ Status: {'OK' if not data.get('last_error') else 'Error'}")
            except Exception as e:
                print(f"{ConsoleColors.RED}Could not read data for {username}{ConsoleColors.RESET}")
                
        print(f"\n{ConsoleColors.GREEN}🎉 Total unliked: {total_unliked} posts{ConsoleColors.RESET}")
        if self.excluded_users:
            print(f"{ConsoleColors.YELLOW}🚫 Excluding: {len(self.excluded_users)} users{ConsoleColors.RESET}")
        input(f"\n{ConsoleColors.BOLD}Press Enter to continue...{ConsoleColors.RESET}")

    def show_settings(self):
        while True:
            print(f"\n{ConsoleColors.CYAN}{ConsoleColors.BOLD}╔══════════════════════════════════╗")
            print(f"║          Settings Menu           ║")
            print(f"╚══════════════════════════════════╝{ConsoleColors.RESET}")
            
            print(f"\n{ConsoleColors.YELLOW}▸ Delay Settings{ConsoleColors.RESET}")
            print(f"  {ConsoleColors.BOLD}1.{ConsoleColors.RESET} Minimum Delay     : {ConsoleColors.GREEN}{CONFIG['delay']['min']}{ConsoleColors.RESET} seconds")
            print(f"  {ConsoleColors.BOLD}2.{ConsoleColors.RESET} Maximum Delay     : {ConsoleColors.GREEN}{CONFIG['delay']['max']}{ConsoleColors.RESET} seconds")
            
            print(f"\n{ConsoleColors.YELLOW}▸ Break Settings{ConsoleColors.RESET}")
            print(f"  {ConsoleColors.BOLD}3.{ConsoleColors.RESET} Break Probability : {ConsoleColors.GREEN}{CONFIG['break']['probability'] * 100}%{ConsoleColors.RESET}")
            print(f"  {ConsoleColors.BOLD}4.{ConsoleColors.RESET} Minimum Break     : {ConsoleColors.GREEN}{CONFIG['break']['min'] / 60:.1f}{ConsoleColors.RESET} minutes")
            print(f"  {ConsoleColors.BOLD}5.{ConsoleColors.RESET} Maximum Break     : {ConsoleColors.GREEN}{CONFIG['break']['max'] / 60:.1f}{ConsoleColors.RESET} minutes")
            
            print(f"\n{ConsoleColors.YELLOW}▸ Retry Settings{ConsoleColors.RESET}")
            print(f"  {ConsoleColors.BOLD}6.{ConsoleColors.RESET} Maximum Retries   : {ConsoleColors.GREEN}{CONFIG['max_retries']}{ConsoleColors.RESET}")
            print(f"  {ConsoleColors.BOLD}7.{ConsoleColors.RESET} Retry Delay       : {ConsoleColors.GREEN}{CONFIG['retry_delay']}{ConsoleColors.RESET} seconds")
            
            print(f"\n{ConsoleColors.CYAN}▸ Navigation{ConsoleColors.RESET}")
            print(f"  {ConsoleColors.BOLD}0.{ConsoleColors.RESET} Save and Return")
            
            try:
                print(f"\n{ConsoleColors.WHITE}╭─{ConsoleColors.RESET}")
                choice = input(f"{ConsoleColors.WHITE}╰─▸{ConsoleColors.RESET} ").strip()
                
                if choice == "0":
                    print(f"\n{ConsoleColors.GREEN}✓ Settings saved successfully!{ConsoleColors.RESET}")
                    time.sleep(1)
                    break
                    
                try:
                    if choice in ["1", "2", "3", "4", "5", "6", "7"]:
                        print(f"{ConsoleColors.WHITE}╭─{ConsoleColors.RESET}")
                        
                        if choice == "1":
                            new_value = float(input(f"{ConsoleColors.WHITE}╰─▸ Enter new minimum delay (seconds): {ConsoleColors.RESET}"))
                            CONFIG['delay']['min'] = new_value
                        elif choice == "2":
                            new_value = float(input(f"{ConsoleColors.WHITE}╰─▸ Enter new maximum delay (seconds): {ConsoleColors.RESET}"))
                            CONFIG['delay']['max'] = new_value
                        elif choice == "3":
                            new_value = float(input(f"{ConsoleColors.WHITE}╰─▸ Enter new break probability (0-1): {ConsoleColors.RESET}"))
                            if 0 <= new_value <= 1:
                                CONFIG['break']['probability'] = new_value
                            else:
                                raise ValueError("Probability must be between 0 and 1")
                        elif choice == "4":
                            new_value = float(input(f"{ConsoleColors.WHITE}╰─▸ Enter new minimum break time (minutes): {ConsoleColors.RESET}"))
                            CONFIG['break']['min'] = new_value * 60
                        elif choice == "5":
                            new_value = float(input(f"{ConsoleColors.WHITE}╰─▸ Enter new maximum break time (minutes): {ConsoleColors.RESET}"))
                            CONFIG['break']['max'] = new_value * 60
                        elif choice == "6":
                            new_value = int(input(f"{ConsoleColors.WHITE}╰─▸ Enter new maximum retries: {ConsoleColors.RESET}"))
                            CONFIG['max_retries'] = new_value
                        elif choice == "7":
                            new_value = int(input(f"{ConsoleColors.WHITE}╰─▸ Enter new retry delay (seconds): {ConsoleColors.RESET}"))
                            CONFIG['retry_delay'] = new_value
                            
                        self.save_config()
                        print(f"\n{ConsoleColors.GREEN}✓ Setting updated successfully!{ConsoleColors.RESET}")
                        time.sleep(1)
                    else:
                        print(f"\n{ConsoleColors.RED}✗ Invalid choice{ConsoleColors.RESET}")
                        time.sleep(1)
                except ValueError as e:
                    print(f"\n{ConsoleColors.RED}✗ Invalid input: {str(e)}{ConsoleColors.RESET}")
                    time.sleep(2)
            except KeyboardInterrupt:
                break

    def check_system_requirements(self) -> bool:
        try:
            try:
                import psutil
            except ImportError:
                logging.warning("psutil not installed. Installing...")
                if not self.install_requirements():
                    return False

            os_name = platform.system()
            logging.info(f"Operating System: {os_name}")
            return True
        except Exception as e:
            logging.error(f"Error checking system requirements: {str(e)}")
            return False

    def check_dependencies(self) -> bool:
        try:
            import importlib.util
            
            ensta_spec = importlib.util.find_spec("ensta")
            if ensta_spec is None:
                logging.error("ensta library not found")
                print(f"{ConsoleColors.RED}[✗] ensta library not found")
                print("→ Attempting to reinstall...{ConsoleColors.RESET}")
                self.install_requirements()
                return False
                
            try:
                import ensta
                logging.info("Successfully imported ensta")
                return True
            except Exception as e:
                logging.error(f"Error importing ensta: {str(e)}", exc_info=True)
                print(f"{ConsoleColors.RED}[✗] Error importing ensta")
                print("→ Attempting to fix by reinstalling...{ConsoleColors.RESET}")
                self.install_requirements()
                return False
        except Exception as e:
            logging.error(f"Dependency check failed: {str(e)}", exc_info=True)
            return False

def instagram_code_to_media_id(code):
    charmap = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_'
    code = code.split('/')[-2]
    return sum(charmap.index(char) * (64 ** i) for i, char in enumerate(reversed(code)))

def get_visible_length(text):
    import re
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    return len(ansi_escape.sub('', text))

def menu_line(number, text, box_width=40):
    prefix = f"│ {ConsoleColors.BOLD}{number}.{ConsoleColors.RESET} {ConsoleColors.WHITE}"
    content = f"{text}{ConsoleColors.RESET}"
    visible_length = get_visible_length(f"{prefix}{content}")
    padding = box_width - visible_length + 1
    return f"{prefix}{content}{' ' * padding}│{ConsoleColors.RESET}"

def parse_args():
    parser = argparse.ArgumentParser(
        prog="unlike",
        description="Unlike Instagram reels using your official Instagram data export."
    )
    parser.add_argument(
        "--import", dest="import_path", metavar="PATH",
        help="Import an Instagram export (folder, .zip, or a single liked_posts.json / "
             "following.json) before showing the menu. Drag the folder from Finder to fill this in."
    )
    return parser.parse_args()

def main():
    try:
        args = parse_args()
        print("\nWelcome to Instagram Mass Unliker!")
        print("Checking system requirements...")
        
        unliker = InstagramUnliker()
        if not unliker.check_dependencies():
            print("Error: Failed to install required dependencies.")
            sys.exit(1)
        
        if not unliker.check_system_requirements():
            print("Error: System requirements not met.")
            sys.exit(1)
        
        signal.signal(signal.SIGINT, lambda sig, frame: sys.exit(0))
        
        if not unliker.check_python_version():
            sys.exit(1)
        
        unliker.check_and_create_config()

        if args.import_path:
            unliker.import_export(InstagramUnliker._resolve_dropped_path(args.import_path))

        unliker.show_menu()

    except KeyboardInterrupt:
        print("\nProgram terminated by user.")
        sys.exit(0)
    except Exception as e:
        logging.error(f"Fatal error: {str(e)}")
        print("\nAn unexpected error occurred. Please check the logs for details.")
        sys.exit(1)

if __name__ == "__main__":
    main()
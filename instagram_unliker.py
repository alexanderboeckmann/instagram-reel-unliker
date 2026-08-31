#!/usr/bin/env python3
import os
import sys
import json
import hashlib
import time
import random
import logging
import shutil
import types
import importlib.util
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, NamedTuple, Tuple, Set
from getpass import getpass
import signal
from tqdm import tqdm
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
PROGRESS_DIR = DATA_DIR / "progress"
EXPORT_SEARCH_DIRS = [Path.home() / "Downloads", Path.home() / "Desktop", BASE_DIR]

LOG_DATEFMT = '%Y-%m-%d %H:%M:%S'

class ContentMode(NamedTuple):
    kinds: Tuple[str, ...]
    noun: str
    target_label: str
    skip_label: str


CONTENT_MODES = {
    'reels': ContentMode(('reel',), 'reels', 'Reels to unlike', 'Non-reel posts'),
    'posts': ContentMode(('p', 'tv'), 'posts', 'Posts to unlike', 'Reels'),
    'both': ContentMode(('reel', 'p', 'tv'), 'reels and posts', 'To unlike', 'Unusable entries'),
}
KIND_LABEL = {'reel': 'Reel', 'p': 'Post', 'tv': 'Video'}
URL_KIND_RE = re.compile(r'instagram\.com/(reel|p|tv)/')

logging.basicConfig(
    level=logging.WARNING,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt=LOG_DATEFMT
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
    "content": "reels",
    "log_level": "INFO",
    "max_retries": 3,
    "retry_delay": 60
}

def _stub_unused_ensta_deps():
    def gone(what):
        def raise_(*args, **kwargs):
            raise RuntimeError(f"{what} requires a dependency this install omits")
        return raise_

    def mod(name, **attrs):
        m = types.ModuleType(name)
        m.__dict__.update(attrs)
        sys.modules[name] = m
        return m

    if importlib.util.find_spec("moviepy") is None:
        mod("moviepy").editor = mod("moviepy.editor", VideoFileClip=gone("VideoFileClip"))
    if importlib.util.find_spec("PIL") is None:
        mod("PIL").Image = mod("PIL.Image", fromarray=gone("PIL.Image.fromarray"))
    if importlib.util.find_spec("pyquery") is None:
        mod("pyquery", PyQuery=gone("PyQuery"))

_stub_unused_ensta_deps()

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


_active_bar = None


def _emit(line: str):
    try:
        _active_bar.write(line) if _active_bar else print(line)
    except (OSError, ValueError):
        pass


def _status(color: str, glyph: str, msg: str, level: int, blank: bool = False):
    logging.log(level, msg, stacklevel=3)
    _emit(f"{chr(10) if blank else ''}{color}{glyph} {msg}{ConsoleColors.RESET}")


def ok(msg, blank=False):
    _status(ConsoleColors.GREEN, "✓", msg, logging.INFO, blank)


def warn(msg, blank=False):
    _status(ConsoleColors.YELLOW, "!", msg, logging.WARNING, blank)


def fail(msg, blank=False):
    _status(ConsoleColors.RED, "✗", msg, logging.ERROR, blank)


def note(msg, blank=False):
    _status(ConsoleColors.BLUE, "·", msg, logging.INFO, blank)


def header(title: str):
    logging.info(f"[{title}]", stacklevel=2)
    _emit(f"\n{ConsoleColors.CYAN}{ConsoleColors.BOLD}{title}{ConsoleColors.RESET}")
    _emit(f"{ConsoleColors.CYAN}{'─' * 40}{ConsoleColors.RESET}")


def content_mode() -> str:
    mode = str(CONFIG.get('content', 'reels')).lower()
    return mode if mode in CONTENT_MODES else 'reels'


def _url_kind(url: str) -> Optional[str]:
    match = URL_KIND_RE.search(url or '')
    return match.group(1) if match else None


def _in_scope(url: str) -> bool:
    kind = _url_kind(url)
    return kind is not None and kind in CONTENT_MODES[content_mode()].kinds


def _reel_code(url: str) -> str:
    parts = [part for part in url.split('/') if part]
    return parts[-1] if parts else url


def _human_duration(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        m, s = divmod(seconds, 60)
        return f"{m}m {s}s" if s else f"{m}m"
    if seconds < 86400:
        h, m = divmod(seconds // 60, 60)
        return f"{h}h {m}m" if m else f"{h}h"
    d, h = divmod(seconds // 3600, 24)
    return f"{d}d {h}h" if h else f"{d}d"


class Setting(NamedTuple):
    group: str
    label: str
    path: Tuple[str, ...]
    show: object
    unit: str
    prompt: str
    parse: object


def _cfg_set(path: Tuple[str, ...], value):
    target = CONFIG
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value


def _probability(raw: str) -> float:
    value = float(raw)
    if not 0 <= value <= 1:
        raise ValueError("Probability must be between 0 and 1")
    return value


def _minutes(raw: str) -> float:
    return float(raw) * 60


def _content_choice(raw: str) -> str:
    picked = {'1': 'reels', '2': 'posts', '3': 'both'}.get(raw.strip())
    if not picked:
        raise ValueError("Pick 1, 2 or 3")
    return picked


SETTINGS = [
    Setting('Delay Settings', 'Minimum Delay', ('delay', 'min'),
            lambda: CONFIG['delay']['min'], 'seconds', 'Enter new minimum delay (seconds)', float),
    Setting('Delay Settings', 'Maximum Delay', ('delay', 'max'),
            lambda: CONFIG['delay']['max'], 'seconds', 'Enter new maximum delay (seconds)', float),
    Setting('Break Settings', 'Break Probability', ('break', 'probability'),
            lambda: f"{CONFIG['break']['probability'] * 100}%", '', 'Enter new break probability (0-1)', _probability),
    Setting('Break Settings', 'Minimum Break', ('break', 'min'),
            lambda: f"{CONFIG['break']['min'] / 60:.1f}", 'minutes', 'Enter new minimum break time (minutes)', _minutes),
    Setting('Break Settings', 'Maximum Break', ('break', 'max'),
            lambda: f"{CONFIG['break']['max'] / 60:.1f}", 'minutes', 'Enter new maximum break time (minutes)', _minutes),
    Setting('Retry Settings', 'Maximum Retries', ('max_retries',),
            lambda: CONFIG['max_retries'], '', 'Enter new maximum retries', int),
    Setting('Retry Settings', 'Retry Delay', ('retry_delay',),
            lambda: CONFIG['retry_delay'], 'seconds', 'Enter new retry delay (seconds)', int),
    Setting('What to unlike', 'Content', ('content',),
            content_mode, '', 'Unlike which content? (1. reels  2. posts  3. both)', _content_choice),
]


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


class ProgressStore:
    def __init__(self, username: str, fingerprint: str):
        self.path = PROGRESS_DIR / f"{username}.txt"
        self.fingerprint = fingerprint
        self.done = self._read()
        self._resuming = bool(self.done)
        self._handle = None

    def _read(self) -> Set[str]:
        try:
            lines = self.path.read_text(encoding='utf-8').splitlines()
        except OSError:
            return set()
        if lines[:1] != [self.fingerprint]:
            logging.info(f"Ignoring {self.path} — it was recorded against a different export")
            return set()
        return {line for line in lines[1:] if line}

    def _open(self):
        PROGRESS_DIR.mkdir(parents=True, exist_ok=True)
        self._handle = open(self.path, 'a' if self._resuming else 'w', encoding='utf-8')
        if not self._resuming:
            self._handle.write(f"{self.fingerprint}\n")

    def record(self, url: str):
        self.done.add(url)
        try:
            if self._handle is None:
                self._open()
            self._handle.write(f"{url}\n")
            self._handle.flush()
        except OSError as e:
            logging.warning(f"Could not record progress to {self.path}: {e}")

    def close(self):
        if self._handle is not None:
            try:
                self._handle.close()
            except OSError:
                pass
            self._handle = None


class InstagramUnliker: 
    def __init__(self):
        self.config_file = BASE_DIR / "config.json"
        self.accounts_dir = BASE_DIR / "accounts"
        self.logs_dir = BASE_DIR / "logs"
        self.running = False

        self.setup_logging()
        logging.info("Starting Instagram Unliker application...")
        self.sessions = SessionStore()
        if not self.sessions.available:
            warn("No keychain available — you will be asked for your password every run")

        self._create_required_directories()
        self._setup_signal_handlers()
        self.check_and_create_config()
        self._apply_log_level()
        self._load_excluded_users()

    def _write_account(self, account_file: Path, data: dict):
        tmp = account_file.with_name(account_file.name + '.tmp')
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4)
            f.flush()
            os.fsync(f.fileno())
        try:
            os.chmod(tmp, 0o600)
        except OSError as e:
            logging.warning(f"Could not tighten permissions on {tmp}: {e}")
        os.replace(tmp, account_file)

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
                warn("Saved session expired")

        password = self._prompt_password(username)
        if not password:
            raise ValueError("No password provided")
        return Web(username, password, load=lambda: "", save=saver)

    @property
    def excluded_users(self) -> Set[str]:
        return set(CONFIG.get('excluded_users', []))

    def _set_excluded_users(self, users: Set[str]):
        CONFIG['excluded_users'] = sorted(users)
        self.save_config()

    def _load_excluded_users(self):
        logging.info(f"Loaded {len(self.excluded_users)} excluded users")
        
    def _setup_signal_handlers(self):
        for name in ('SIGINT', 'SIGTERM', 'SIGHUP'):
            sig = getattr(signal, name, None)
            if sig is not None:
                signal.signal(sig, self._handle_shutdown)
        
    def _handle_shutdown(self, signum, frame):
        if not self.running:
            sys.exit(0)
        self.running = False
        warn("Stopping — finishing up, progress is saved.", blank=True)

    def _sleep(self, seconds: float):
        end = time.monotonic() + seconds
        while self.running:
            remaining = end - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(0.5, remaining))

    def _wait(self, seconds: float, msg: str):
        until = (datetime.now() + timedelta(seconds=seconds)).strftime('%H:%M')
        note(f"{msg} — resuming at {until}")
        self._sleep(seconds)
        if self.running:
            note("Resuming")

    @staticmethod
    def _next_delay(username: str) -> float:
        base = random.uniform(CONFIG['delay']['min'], CONFIG['delay']['max'])
        return base * CONFIG['accounts'].get(username, {}).get('delay_multiplier', 1.0)
        
    def setup_logging(self):
        self.logs_dir.mkdir(exist_ok=True)
        
        log_file = self.logs_dir / "unliker.log"
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=5*1024*1024,
            backupCount=5,
            encoding='utf-8'
        )
        
        file_handler.setFormatter(logging.Formatter(
            '%(asctime)s [%(levelname)s] [%(filename)s:%(lineno)d] %(message)s',
            datefmt=LOG_DATEFMT
        ))
        level = self._level_from_config_file()
        file_handler.setLevel(level)

        root_logger = logging.getLogger()
        root_logger.setLevel(level)
        root_logger.handlers.clear()
        root_logger.addHandler(file_handler)

        atexit.register(self._cleanup_logs)

    @staticmethod
    def _parse_log_level(name) -> Optional[int]:
        level = getattr(logging, str(name).upper(), None)
        return level if isinstance(level, int) else None

    def _level_from_config_file(self) -> int:
        try:
            name = json.loads(self.config_file.read_text(encoding='utf-8'))['log_level']
        except Exception:
            name = CONFIG['log_level']
        return self._parse_log_level(name) or logging.INFO

    def _apply_log_level(self):
        name = str(CONFIG.get('log_level', 'INFO')).upper()
        level = self._parse_log_level(name)
        if level is None:
            warn(f"Unknown log_level {name} in config.json — using INFO")
            level = logging.INFO
        root_logger = logging.getLogger()
        root_logger.setLevel(level)
        for handler in root_logger.handlers:
            handler.setLevel(level)
        
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
            logging.info("Data directories ready")
        except Exception as e:
            logging.error(f"Failed to create directories: {str(e)}")
            print("Please ensure you have write permissions in the current directory")

    @staticmethod
    def check_python_version() -> bool:
        version = sys.version_info
        if version.major < 3 or (version.major == 3 and version.minor < 10):
            fail(f"Python 3.10 or higher required (current: {version.major}.{version.minor})")
            return False
        logging.info(f"Python {version.major}.{version.minor}")
        return True

    def check_and_create_config(self):
        if not os.path.exists(self.config_file):
            self.save_config()
            ok("Created default configuration file")
        else:
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    loaded_config = json.load(f)
                    for key, value in loaded_config.items():
                        if key in CONFIG:
                            CONFIG[key] = value
                logging.info("Loaded existing configuration")
            except json.JSONDecodeError:
                fail("Error: Corrupted configuration file")
                backup_file = f"{self.config_file}.bak"
                os.rename(self.config_file, backup_file)
                warn(f"Backed up corrupted config to {backup_file}")
                self.check_and_create_config()

    def add_account(self):
        header("Add Instagram Account")
        
        username = input(f"{ConsoleColors.BOLD}Username: {ConsoleColors.RESET}").strip()
        
        if not username:
            fail("Username is required")
            return
        
        self.accounts_dir.mkdir(exist_ok=True)
        os.chmod(self.accounts_dir, 0o700)
        account_file = self._account_path(username)
        
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
            
            ok(f"Account @{username} added")
            note("Your password is asked for at first login and never stored")
        except Exception as e:
            fail(f"Could not save account: {e}")

    def remove_account(self):
        accounts = self.list_accounts()
        if not accounts:
            warn("No accounts configured")
            return
            
        header("Remove Account")
        for i, acc in enumerate(accounts, 1):
            print(f"{i}. {acc}")
            
        try:
            username = self._pick(accounts, "Select account to remove (0 to cancel)")
            if username is None:
                return
            account_file = self._account_path(username)
            
            confirm = input(f"{ConsoleColors.YELLOW}! Are you sure you want to remove {username}? (y/N): {ConsoleColors.RESET}").lower()
            if confirm != 'y':
                return
            
            if account_file.exists():
                account_file.unlink()

            (PROGRESS_DIR / f"{username}.txt").unlink(missing_ok=True)
            self.sessions.delete(username)
                
            if username in CONFIG['accounts']:
                del CONFIG['accounts'][username]
                self.save_config()
                
            ok(f"Account {username} removed successfully")
            
        except Exception as e:
            fail(f"Error: {str(e)}")

    def manage_excluded_users(self):
        while True:
            header("Manage Excluded Users")
            
            if self.excluded_users:
                print(f"\n{ConsoleColors.YELLOW}Currently Excluded ({len(self.excluded_users)} users):{ConsoleColors.RESET}")
                for i, user in enumerate(sorted(self.excluded_users), 1):
                    print(f"  {i}. @{user}")
            else:
                print(f"\n{ConsoleColors.YELLOW}No users excluded yet{ConsoleColors.RESET}")
            
            print(f"\n{ConsoleColors.CYAN}Options:{ConsoleColors.RESET}")
            print("  1. Add user to exclude list")
            print("  2. Remove user from exclude list")
            print("  3. Clear all excluded users")
            print("  0. Back to main menu")
            
            choice = input(f"\n{ConsoleColors.BOLD}Select option: {ConsoleColors.RESET}").strip()
            
            if choice == "1":
                username = input(f"{ConsoleColors.BOLD}Enter username to exclude: {ConsoleColors.RESET}").strip().lower()
                if username:
                    self._set_excluded_users(self.excluded_users | {username})
                    ok(f"Added @{username} to exclude list")
                    
            elif choice == "2":
                if not self.excluded_users:
                    warn("No users to remove")
                    continue
                    
                username = input(f"{ConsoleColors.BOLD}Enter username to remove: {ConsoleColors.RESET}").strip().lower()
                if username in self.excluded_users:
                    self._set_excluded_users(self.excluded_users - {username})
                    ok(f"Removed @{username} from exclude list")
                else:
                    warn("User not found in exclude list")
                    
            elif choice == "3":
                if self.excluded_users:
                    confirm = input(f"{ConsoleColors.YELLOW}Clear all excluded users? (y/N): {ConsoleColors.RESET}").lower()
                    if confirm == 'y':
                        self._set_excluded_users(set())
                        ok("Cleared all excluded users")
                        
            elif choice == "0":
                break
            else:
                fail("Invalid option")
            
            time.sleep(1)

    @staticmethod
    def _pick(options: List[str], prompt: str) -> Optional[str]:
        choice = input(f"\n{ConsoleColors.BOLD}{prompt}: {ConsoleColors.RESET}")
        if not choice.isdigit() or int(choice) == 0:
            return None
        index = int(choice)
        if index < 1 or index > len(options):
            fail("Invalid selection")
            return None
        return options[index - 1]

    def _account_path(self, username: str) -> Path:
        return self.accounts_dir / f"{username}.json"

    def list_accounts(self) -> List[str]:
        if not self.accounts_dir.exists():
            return []
        return [f.stem for f in self.accounts_dir.glob("*.json")]

    def save_config(self):
        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(CONFIG, f, indent=4)
        except Exception as e:
            fail(f"Failed to save configuration: {str(e)}")

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
                resolved = entry.resolve()
                if resolved in seen:
                    continue
                is_export = False
                if entry.is_dir():
                    is_export = entry.name.startswith('instagram-') or (entry / 'your_instagram_activity').is_dir()
                elif entry.suffix.lower() == '.zip':
                    is_export = entry.name.startswith('instagram-')
                if is_export:
                    seen.add(resolved)
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
            with open(path, 'r', encoding='utf-8') as f:
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

    def _validate_liked_posts(self, path: Path) -> Tuple[int, int, int]:
        if self._looks_like_html(path):
            raise ValueError(
                "That liked_posts.json is HTML, not JSON. Request your data again from "
                "Instagram and choose format: JSON."
            )
        try:
            with open(path, 'r', encoding='utf-8') as f:
                raw = json.load(f)
        except json.JSONDecodeError as e:
            raise ValueError(f"Could not read {path.name} as JSON: {e}")

        records = self._liked_records(raw)
        if not isinstance(records, list) or not records:
            raise ValueError(f"{path.name} contains no liked posts")

        total = reels = posts = 0
        for _, url, _ in self._liked_entries(records):
            kind = _url_kind(url)
            if kind is None:
                continue
            total += 1
            if kind == 'reel':
                reels += 1
            else:
                posts += 1
        if total == 0:
            raise ValueError(
                f"{path.name} has {len(records)} entries but no readable post URLs — "
                "Instagram's export format may have changed."
            )
        return total, reels, posts

    @staticmethod
    def _validate_following(path: Path) -> int:
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            raise ValueError(f"Could not read {path.name} as JSON: {e}")
        if not isinstance(data, dict) or 'relationships_following' not in data:
            raise ValueError(f"{path.name} is not an Instagram following export")
        return len([e for e in data['relationships_following'] if e.get('title')])

    @staticmethod
    def _imported_phrase(stamp: Optional[str]) -> str:
        try:
            days = (datetime.now().date() - datetime.strptime(stamp, '%Y-%m-%d').date()).days
        except (TypeError, ValueError):
            return "imported ?"
        if days <= 0:
            return "imported today"
        if days == 1:
            return "imported yesterday"
        return f"imported {stamp} ({days} days ago)"

    @staticmethod
    def _read_import_meta() -> Optional[dict]:
        try:
            return json.loads(IMPORT_META_PATH.read_text(encoding='utf-8'))
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
            print(f"\n{ConsoleColors.BLUE}Exports found on this machine:{ConsoleColors.RESET}")
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
                    fail("Invalid choice")
                    return None
        else:
            warn("No Instagram export found in Downloads or Desktop.", blank=True)

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
            ok(f"Deleted {source}")
        except OSError as e:
            fail(f"Could not delete it: {e}")

    def import_export(self, source: Optional[Path] = None) -> bool:
        if source is None:
            source = self._prompt_for_source()
        if source is None:
            warn("Import cancelled")
            return False

        logging.info(f"Importing from {source}")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                liked_src, following_src = self._locate_export_files(source, Path(tmp))

                liked_total = liked_reels = liked_posts = following_count = 0
                if liked_src:
                    liked_total, liked_reels, liked_posts = self._validate_liked_posts(liked_src)
                if following_src:
                    following_count = self._validate_following(following_src)

                DATA_DIR.mkdir(exist_ok=True)
                if liked_src:
                    shutil.copy2(liked_src, LIKED_POSTS_PATH)
                if following_src:
                    shutil.copy2(following_src, FOLLOWING_PATH)
        except (ValueError, zipfile.BadZipFile, OSError) as e:
            fail(f"{e}", blank=True)
            return False

        ok(f"Imported from {source.name}", blank=True)
        if liked_src:
            print(f"  {ConsoleColors.WHITE}Liked total   : {liked_total:,}{ConsoleColors.RESET}")
            print(f"  {ConsoleColors.WHITE}  reels       : {liked_reels:,}{ConsoleColors.RESET}")
            print(f"  {ConsoleColors.WHITE}  posts       : {liked_posts:,}{ConsoleColors.RESET}")
        if following_src:
            print(f"  {ConsoleColors.WHITE}Following     : {following_count:,} (theirs will be skipped){ConsoleColors.RESET}")
        else:
            print(f"  {ConsoleColors.YELLOW}No following.json — the follow filter stays off{ConsoleColors.RESET}")

        meta = self._read_import_meta() or {}
        if liked_src:
            meta.update({'liked_total': liked_total, 'liked_reels': liked_reels, 'liked_posts': liked_posts})
        if following_src:
            meta['following'] = following_count
        meta.update({'source': source.name, 'imported': datetime.now().strftime('%Y-%m-%d')})
        try:
            IMPORT_META_PATH.write_text(json.dumps(meta, indent=2), encoding='utf-8')
        except OSError as e:
            logging.warning(f"Could not write import metadata: {e}")

        logging.info(f"Imported export from {source} ({liked_total} liked: {liked_reels} reels, {liked_posts} posts)")
        self._offer_delete_source(source)
        return True

    @staticmethod
    def _export_fingerprint() -> str:
        digest = hashlib.sha256()
        with open(LIKED_POSTS_PATH, 'rb') as f:
            for chunk in iter(lambda: f.read(1 << 20), b''):
                digest.update(chunk)
        return digest.hexdigest()[:16]

    def _load_following(self) -> Set[str]:
        if not FOLLOWING_PATH.exists():
            logging.info("following.json not found — following filter disabled")
            return set()
        try:
            with open(FOLLOWING_PATH, 'r', encoding='utf-8') as f:
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

    @staticmethod
    def _liked_records(raw):
        return raw.get('likes_media_likes', []) if isinstance(raw, dict) else raw

    @classmethod
    def _liked_entries(cls, records):
        for post in records:
            url, post_username = cls._parse_liked_post(post)
            yield post, url, post_username

    def _unlike_with_retry(self, client, media_id):
        attempts = CONFIG['max_retries']
        for retry in range(attempts):
            try:
                client.unlike(media_id)
                return
            except Exception as e:
                if retry < attempts - 1 and self.running:
                    warn(f"Attempt {retry + 1}/{attempts} failed: {e} — "
                         f"retrying in {_human_duration(CONFIG['retry_delay'])}")
                    self._sleep(CONFIG['retry_delay'])
                else:
                    raise

    def unlike_posts(self, username: str, fingerprint: Optional[str] = None):
        global _active_bar
        account_file = self._account_path(username)
        progress_bar = None
        resume = None

        if not account_file.exists():
            fail(f"Account file not found for {username}. Please add it first.", blank=True)
            return

        if not LIKED_POSTS_PATH.exists():
            warn("No Instagram export imported yet.", blank=True)
            if not self.import_export():
                return

        try:
            with open(account_file, 'r', encoding='utf-8') as f:
                account_data = json.load(f)

            mode = content_mode()
            spec = CONTENT_MODES[mode]
            noun = spec.noun
            header(f"Unliking {noun} for @{username}")

            following = self._load_following()
            if following:
                note(f"Loaded {len(following)} followed accounts — their {noun} will be skipped")

            excluded = self.excluded_users
            if excluded:
                note(f"Excluding {len(excluded)} manually excluded users")

            try:
                client = self._login(account_data['username'])
                account = client.private_info()
                ok(f"Logged in as @{account.username}")
            except Exception as e:
                fail(f"Login failed: {e}")
                note("Check your username and password")
                return

            try:
                with open(LIKED_POSTS_PATH, 'r', encoding='utf-8') as f:
                    raw_posts = json.load(f)

                records = self._liked_records(raw_posts)

                if not records:
                    warn("No liked posts found in liked_posts.json")
                    return

                targets: list = []
                skipped_scope = 0
                skipped_following = 0
                skipped_excluded = 0

                for post, url, post_username in self._liked_entries(records):
                    if not _in_scope(url):
                        skipped_scope += 1
                        continue

                    if post_username and post_username in following:
                        skipped_following += 1
                        logging.debug(f"Skipping {_reel_code(url)} from followed account: @{post_username}")
                        continue

                    if post_username and post_username in excluded:
                        skipped_excluded += 1
                        logging.debug(f"Skipping {_reel_code(url)} from excluded user: @{post_username}")
                        continue

                    targets.append((post, url, post_username))

                resume = ProgressStore(username, fingerprint or self._export_fingerprint())
                if resume.done:
                    targets = [entry for entry in targets if entry[1] not in resume.done]
                already_done = len(resume.done)
                total_posts = len(targets)

                print(f"\n{ConsoleColors.BLUE}Filter summary:{ConsoleColors.RESET}")
                print(f"  {ConsoleColors.GREEN}{spec.target_label:<16}: {total_posts}{ConsoleColors.RESET}")
                print(f"  {ConsoleColors.YELLOW}{spec.skip_label:<16}: {skipped_scope} (skipped){ConsoleColors.RESET}")
                print(f"  {ConsoleColors.YELLOW}{'From following':<16}: {skipped_following} (skipped){ConsoleColors.RESET}")
                if skipped_excluded:
                    print(f"  {ConsoleColors.YELLOW}{'Excluded users':<16}: {skipped_excluded} (skipped){ConsoleColors.RESET}")
                if already_done:
                    print(f"  {ConsoleColors.BLUE}{'Done earlier':<16}: {already_done} (resuming){ConsoleColors.RESET}")
                logging.info(f"Filter summary ({mode}): {total_posts} to unlike, {skipped_scope} out of scope, "
                             f"{skipped_following} from following, {skipped_excluded} excluded, "
                             f"{already_done} done earlier")

                if total_posts == 0:
                    warn("Nothing left to unlike from this export" if already_done
                         else f"No {noun} to unlike after filtering")
                    return

                mean_delay = (CONFIG['delay']['min'] + CONFIG['delay']['max']) / 2
                mean_break = (CONFIG['break']['min'] + CONFIG['break']['max']) / 2
                multiplier = CONFIG['accounts'].get(username, {}).get('delay_multiplier', 1.0)
                per_reel = mean_delay * multiplier + CONFIG['break']['probability'] * mean_break
                note(f"About {_human_duration(per_reel)} each — "
                     f"{total_posts} {noun} is roughly {_human_duration(per_reel * total_posts)}")

                unliked_count = 0
                failed_urls: list = []
                self.running = True

                bar_desc = f"Unliking {noun}"
                progress_bar = tqdm(
                    total=total_posts,
                    desc=bar_desc,
                    file=sys.stdout,
                    bar_format='{l_bar}{bar}| {n_fmt}/{total_fmt} [ETA: {remaining}]'
                )
                _active_bar = progress_bar

                pending = self._next_delay(username)
                note(f"Starting — first in {_human_duration(pending)}")

                for post, url, post_username in targets:
                    if not self.running:
                        break
                    try:
                        self._sleep(pending)
                        if not self.running:
                            break

                        self._unlike_with_retry(client, instagram_code_to_media_id(url))

                        unliked_count += 1
                        account_data['total_unliked'] += 1
                        resume.record(url)
                        account_data['last_run'] = datetime.now().isoformat()
                        self._write_account(account_file, account_data)
                        progress_bar.update(1)

                        pending = self._next_delay(username)
                        owner = f" · @{post_username}" if post_username else ""
                        upcoming = f" · next in {_human_duration(pending)}" if unliked_count < total_posts else ""
                        ok(f"{unliked_count}/{total_posts}{owner}{upcoming}")

                        if random.random() < CONFIG['break']['probability']:
                            break_time = random.uniform(CONFIG['break']['min'], CONFIG['break']['max'])
                            progress_bar.set_description("On a break")
                            self._wait(break_time, f"Break for {_human_duration(break_time)}")
                            progress_bar.set_description(bar_desc)

                    except Exception as e:
                        error_msg = f"{KIND_LABEL.get(_url_kind(url), 'Item')} {_reel_code(url)} failed: {e}"
                        fail(error_msg)
                        if logging.getLogger().isEnabledFor(logging.DEBUG):
                            logging.debug("Failure detail", exc_info=True)
                        account_data['last_error'] = error_msg
                        failed_urls.append(url)
                        progress_bar.set_description("Cooling down")
                        self._wait(300, "Cooling down after that failure")
                        progress_bar.set_description(bar_desc)

            finally:
                self.running = False
                _active_bar = None
                if progress_bar is not None:
                    progress_bar.close()
                if resume is not None:
                    resume.close()
                account_data['last_run'] = datetime.now().isoformat()
                self._write_account(account_file, account_data)

            ok(f"Unliking complete for {username}", blank=True)
            note(f"Unliked       : {unliked_count}")
            if failed_urls:
                note(f"Failed        : {len(failed_urls)}")
            remaining = total_posts - unliked_count
            if remaining:
                note(f"Left to do    : {remaining} — pick 4 again to resume where this stopped")
            
        except json.JSONDecodeError as e:
            fail(f"Invalid JSON format: {e}")
        except Exception as e:
            error_msg = f"Unexpected error: {e}"
            logging.error(error_msg, exc_info=True)
            fail(error_msg, blank=True)

    def show_menu(self):
        while True:
            print(f"\n{ConsoleColors.CYAN}{ConsoleColors.BOLD}╔{'═' * 46}╗")
            print(center_text_in_box(f"{ConsoleColors.BOLD}Instagram Mass Unliker{ConsoleColors.RESET}{ConsoleColors.CYAN}{ConsoleColors.BOLD}"))
            print(center_text_in_box(f"{ConsoleColors.BOLD}Erase your digital footprint{ConsoleColors.RESET}{ConsoleColors.CYAN}{ConsoleColors.BOLD}"))
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
                print(f"{ConsoleColors.YELLOW}Excluding {len(self.excluded_users)} users{ConsoleColors.RESET}")

            meta = self._read_import_meta() if LIKED_POSTS_PATH.exists() else None
            if meta:
                print(f"{ConsoleColors.BLUE}{meta.get('liked_reels', 0):,} reels · "
                      f"{meta.get('liked_posts', 0):,} posts · "
                      f"{self._imported_phrase(meta.get('imported'))}{ConsoleColors.RESET}")
            elif LIKED_POSTS_PATH.exists():
                print(f"{ConsoleColors.BLUE}Export imported{ConsoleColors.RESET}")
            else:
                print(f"{ConsoleColors.YELLOW}No export imported yet — use option 3{ConsoleColors.RESET}")

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
                    print(f"\n{ConsoleColors.GREEN}Thanks for using Instagram Unliker.{ConsoleColors.RESET}")
                    break
                else:
                    fail("Invalid choice. Please try again.", blank=True)
                    time.sleep(1)
                    
            except KeyboardInterrupt:
                print(f"\n\n{ConsoleColors.GREEN}Thanks for using Instagram Unliker.{ConsoleColors.RESET}")
                break
            except EOFError:
                break
            except Exception as e:
                fail(f"Error: {str(e)}", blank=True)
                time.sleep(2)

    def _start_unliking_menu(self):
        accounts = self.list_accounts()
        if not accounts:
            fail("No accounts configured. Please add an account first.")
            return
            
        header("Select Account")

        fingerprint = self._export_fingerprint() if LIKED_POSTS_PATH.exists() else None

        for i, acc in enumerate(accounts, 1):
            account_file = self._account_path(acc)
            status = "Ready"
            if account_file.exists():
                with open(account_file, encoding='utf-8') as f:
                    data = json.load(f)
                    if data.get('last_error'):
                        status = "Error"
                    elif data.get('last_run'):
                        status = f"Last: {datetime.fromisoformat(data['last_run']).strftime('%Y-%m-%d %H:%M')}"

            done = len(ProgressStore(acc, fingerprint).done) if fingerprint else 0
            if done:
                status += f" - {done:,} done from this export"

            print(f"{ConsoleColors.BOLD}{i}{ConsoleColors.RESET}. [{acc}] - {status}")
            
        try:
            picked = self._pick(accounts, "Select account (0 to cancel)")
            if picked is not None:
                self.unlike_posts(picked, fingerprint)
            
        except ValueError:
            fail("Invalid input")
        except Exception as e:
            fail(f"Error: {str(e)}")

    def show_statistics(self):
        accounts = self.list_accounts()
        if not accounts:
            warn("No accounts added yet")
            input(f"\n{ConsoleColors.BOLD}Press Enter to continue...{ConsoleColors.RESET}")
            return
            
        header("Statistics")
        
        total_unliked = 0
        for username in accounts:
            account_file = self._account_path(username)
            try:
                with open(account_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    
                total_unliked += data.get('total_unliked', 0)
                print(f"\n{ConsoleColors.BOLD}{ConsoleColors.BLUE}@{username}{ConsoleColors.RESET}")
                print(f"  Unliked posts: {data.get('total_unliked', 0)}")
                if data.get('last_run'):
                    print(f"  Last active: {datetime.fromisoformat(data['last_run']).strftime('%Y-%m-%d %H:%M')}")
                fail("Status: Error") if data.get('last_error') else ok("Status: OK")
            except Exception:
                fail(f"Could not read data for {username}")
                
        ok(f"Total unliked: {total_unliked} posts", blank=True)
        if self.excluded_users:
            print(f"{ConsoleColors.YELLOW}Excluding: {len(self.excluded_users)} users{ConsoleColors.RESET}")
        input(f"\n{ConsoleColors.BOLD}Press Enter to continue...{ConsoleColors.RESET}")

    def show_settings(self):
        while True:
            print(f"\n{ConsoleColors.CYAN}{ConsoleColors.BOLD}╔{'═' * 46}╗")
            print(center_text_in_box(f"{ConsoleColors.BOLD}Settings Menu{ConsoleColors.RESET}"
                                     f"{ConsoleColors.CYAN}{ConsoleColors.BOLD}"))
            print(f"╚{'═' * 46}╝{ConsoleColors.RESET}")

            group = None
            for number, setting in enumerate(SETTINGS, 1):
                if setting.group != group:
                    group = setting.group
                    print(f"\n{ConsoleColors.YELLOW}▸ {group}{ConsoleColors.RESET}")
                unit = f" {setting.unit}" if setting.unit else ""
                print(f"  {ConsoleColors.BOLD}{number}.{ConsoleColors.RESET} {setting.label:<18}: "
                      f"{ConsoleColors.GREEN}{setting.show()}{ConsoleColors.RESET}{unit}")

            print(f"\n{ConsoleColors.CYAN}▸ Navigation{ConsoleColors.RESET}")
            print(f"  {ConsoleColors.BOLD}0.{ConsoleColors.RESET} Save and Return")

            try:
                print(f"\n{ConsoleColors.WHITE}╭─{ConsoleColors.RESET}")
                choice = input(f"{ConsoleColors.WHITE}╰─▸{ConsoleColors.RESET} ").strip()

                if choice == "0":
                    ok("Settings saved successfully!", blank=True)
                    time.sleep(1)
                    break

                if not choice.isdigit() or not 1 <= int(choice) <= len(SETTINGS):
                    fail("Invalid choice", blank=True)
                    time.sleep(1)
                    continue

                setting = SETTINGS[int(choice) - 1]
                try:
                    print(f"{ConsoleColors.WHITE}╭─{ConsoleColors.RESET}")
                    raw = input(f"{ConsoleColors.WHITE}╰─▸ {setting.prompt}: {ConsoleColors.RESET}")
                    _cfg_set(setting.path, setting.parse(raw))
                    self.save_config()
                    ok("Setting updated successfully!", blank=True)
                    time.sleep(1)
                except ValueError as e:
                    fail(f"Invalid input: {e}", blank=True)
                    time.sleep(2)
            except KeyboardInterrupt:
                break

def instagram_code_to_media_id(code):
    charmap = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_'
    code = code.split('/')[-2]
    return sum(charmap.index(char) * (64 ** i) for i, char in enumerate(reversed(code)))

def get_visible_length(text):
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    return len(ansi_escape.sub('', text))

def center_text_in_box(text, box_width=48):
    visible_length = get_visible_length(text)
    padding = (box_width - 2 - visible_length) // 2
    return f"║{' ' * padding}{text}{' ' * (box_width - 2 - visible_length - padding)}║"

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
        if not InstagramUnliker.check_python_version():
            sys.exit(1)

        unliker = InstagramUnliker()

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
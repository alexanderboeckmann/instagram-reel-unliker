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
from logging.handlers import RotatingFileHandler
import atexit

# Global logging configuration
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# Global variables for configuration
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
    "excluded_users": [],  # NEW: List of usernames to exclude
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
    
class InstagramUnliker: 
    def __init__(self):
        """Initialize the Instagram Unliker application"""
        logging.info("Starting Instagram Unliker application...")
        
        self.config_file = "config.json"
        self.accounts_dir = Path("accounts")
        self.logs_dir = Path("logs")
        self.running = True
        self.excluded_users: Set[str] = set()
        
        self._create_required_directories()
        self._ensure_python_environment()
        self._setup_signal_handlers()
        self.setup_logging()
        self.check_and_create_config()
        self._load_excluded_users()
        
    def _load_excluded_users(self):
        """Load excluded users from config"""
        self.excluded_users = set(CONFIG.get('excluded_users', []))
        logging.info(f"Loaded {len(self.excluded_users)} excluded users")
        
    def _ensure_python_environment(self):
        """Ensure Python and pip are properly installed"""
        try:
            import pip
        except ImportError:
            logging.warning("pip is not installed. Installing pip...")
            self._install_pip()
            
    def _install_pip(self):
        """Install pip if not present"""
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
        """Set up handlers for graceful shutdown"""
        signal.signal(signal.SIGINT, self._handle_shutdown)
        signal.signal(signal.SIGTERM, self._handle_shutdown)
        
    def _handle_shutdown(self, signum, frame):
        """Handle shutdown signals gracefully"""
        print(f"\n{ConsoleColors.YELLOW}[!] Received shutdown signal. Cleaning up...{ConsoleColors.RESET}")
        self.running = False
        time.sleep(1)
        sys.exit(0)
        
    def setup_logging(self):
        """Configure logging with enhanced rotation and cleanup"""
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
        """Cleanup function that runs on program exit"""
        try:
            logging.info("Performing final cleanup...")
            self.save_config()
            for handler in logging.getLogger().handlers:
                handler.close()
        except Exception as e:
            print(f"Error during cleanup: {str(e)}")

    def _create_required_directories(self):
        """Create necessary directories if they don't exist"""
        try:
            self.accounts_dir.mkdir(exist_ok=True)
            self.logs_dir.mkdir(exist_ok=True)
            logging.info("Required directories created successfully")
        except Exception as e:
            logging.error(f"Failed to create directories: {str(e)}")
            print("Please ensure you have write permissions in the current directory")

    def check_python_version(self) -> bool:
        """Verify Python version meets requirements"""
        version = sys.version_info
        if version.major < 3 or (version.major == 3 and version.minor < 7):
            print(f"{ConsoleColors.RED}[✗] Error: Python 3.7 or higher required (current: {version.major}.{version.minor}){ConsoleColors.RESET}")
            return False
        print(f"{ConsoleColors.GREEN}[✓] Python version check passed ({version.major}.{version.minor}){ConsoleColors.RESET}")
        return True

    def install_requirements(self) -> bool:
        """Install required Python packages with detailed error handling"""
        try:
            subprocess.run([sys.executable, "-m", "pip", "uninstall", "-y", "ensta"], 
                          stdout=subprocess.PIPE, 
                          stderr=subprocess.PIPE)
            
            result = subprocess.run([sys.executable, "-m", "pip", "install", "--no-cache-dir", "ensta==5.2.9"],
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
        """Create or load configuration file"""
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
        """Add account with improved UI"""
        print(f"\n{ConsoleColors.CYAN}➕ Add Instagram Account{ConsoleColors.RESET}")
        print("-" * 40)
        
        username = input(f"{ConsoleColors.BOLD}Username: {ConsoleColors.RESET}").strip()
        password = input(f"{ConsoleColors.BOLD}Password: {ConsoleColors.RESET}").strip()
        
        if not username or not password:
            print(f"{ConsoleColors.RED}Username and password are required{ConsoleColors.RESET}")
            return
        
        self.accounts_dir.mkdir(exist_ok=True)
        account_file = self.accounts_dir / f"{username}.json"
        
        if account_file.exists():
            override = input(f"{ConsoleColors.YELLOW}Account exists. Replace? (y/N): {ConsoleColors.RESET}").lower()
            if override != 'y':
                return
        
        account_data = {
            "username": username,
            "password": password,
            "last_run": None,
            "total_unliked": 0,
            "last_error": None,
            "created_at": datetime.now().isoformat()
        }
        
        try:
            with open(account_file, 'w') as f:
                json.dump(account_data, f, indent=4)
            
            CONFIG['accounts'][username] = {
                "enabled": True,
                "delay_multiplier": 1.0
            }
            self.save_config()
            
            print(f"{ConsoleColors.GREEN}✨ Account @{username} added successfully!{ConsoleColors.RESET}")
        except Exception as e:
            print(f"{ConsoleColors.RED}Could not save account: {str(e)}{ConsoleColors.RESET}")

    def remove_account(self):
        """Remove an Instagram account"""
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
                
            if username in CONFIG['accounts']:
                del CONFIG['accounts'][username]
                self.save_config()
                
            print(f"{ConsoleColors.GREEN}[✓] Account {username} removed successfully{ConsoleColors.RESET}")
            
        except Exception as e:
            print(f"{ConsoleColors.RED}[✗] Error: {str(e)}{ConsoleColors.RESET}")

    def manage_excluded_users(self):
        """Manage excluded users list"""
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
        """List all configured accounts"""
        if not self.accounts_dir.exists():
            return []
        return [f.stem for f in self.accounts_dir.glob("*.json")]

    def save_config(self):
        """Save current configuration"""
        try:
            with open(self.config_file, 'w') as f:
                json.dump(CONFIG, f, indent=4)
        except Exception as e:
            print(f"{ConsoleColors.RED}[✗] Failed to save configuration: {str(e)}{ConsoleColors.RESET}")

    def _load_following(self) -> Set[str]:
        """Load the set of usernames the account follows from following.json"""
        following_file = 'following.json'
        if not os.path.exists(following_file):
            logging.info("following.json not found — following filter disabled")
            return set()
        try:
            with open(following_file, 'r') as f:
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
        """
        Parse a single entry from liked_posts.json (new flat-list format).

        Returns (url, post_username) or (None, None) if the entry is malformed.
        The post_username is lowercased for consistent comparison.
        """
        url = None
        post_username = None
        try:
            for lv in post.get('label_values', []):
                # Top-level URL entry
                if lv.get('label') == 'URL' and lv.get('value'):
                    url = lv['value']
                # Owner block — nested dict structure
                if lv.get('title') == 'Owner':
                    for owner_entry in lv.get('dict', []):
                        for field in owner_entry.get('dict', []):
                            if field.get('label') == 'Username' and field.get('value'):
                                post_username = field['value'].lower()
        except Exception:
            pass
        return url, post_username

    def unlike_posts(self, username: str):
        """Unlike reels using liked_posts.json, skipping followed accounts and excluded users"""
        account_file = self.accounts_dir / f"{username}.json"
        progress_bar = None

        if not account_file.exists():
            error_msg = f"Account file not found for {username}"
            logging.error(error_msg)
            print(f"\n{ConsoleColors.RED}[✗] {error_msg}. Please add it first.{ConsoleColors.RESET}")
            return

        try:
            with open(account_file, 'r') as f:
                account_data = json.load(f)

            print(f"\n{ConsoleColors.CYAN}Starting to unlike reels for @{username}...{ConsoleColors.RESET}")

            # Load following list for skip logic
            following = self._load_following()
            if following:
                print(f"{ConsoleColors.BLUE}ℹ️  Loaded {len(following)} followed accounts — their reels will be skipped{ConsoleColors.RESET}")

            if self.excluded_users:
                print(f"{ConsoleColors.YELLOW}ℹ️  Excluding {len(self.excluded_users)} manually excluded users{ConsoleColors.RESET}")

            try:
                from ensta import Web
                client = Web(account_data['username'], account_data['password'])
                print(f"{ConsoleColors.GREEN}✓ Successfully logged in{ConsoleColors.RESET}")
                account = client.private_info()
                print(f"{ConsoleColors.GREEN}Logged in as: {ConsoleColors.CYAN}{account.username}{ConsoleColors.RESET}")
            except Exception as e:
                error_msg = f"Login failed: {str(e)}"
                logging.error(error_msg)
                print(f"{ConsoleColors.RED}[✗] {error_msg}")
                print(f"→ Please check your username and password.{ConsoleColors.RESET}")
                return

            if not os.path.exists('liked_posts.json'):
                error_msg = "liked_posts.json file not found"
                logging.error(error_msg)
                print(f"{ConsoleColors.RED}[✗] {error_msg}. Please ensure it exists.{ConsoleColors.RESET}")
                return

            try:
                with open('liked_posts.json', 'r') as f:
                    raw_posts = json.load(f)

                # liked_posts.json is a flat list in the new export format
                if isinstance(raw_posts, dict):
                    # Fallback: old format wrapper key
                    raw_posts = raw_posts.get('likes_media_likes', [])

                if not raw_posts:
                    print(f"{ConsoleColors.YELLOW}[!] No liked posts found in liked_posts.json{ConsoleColors.RESET}")
                    return

                # ── Filter pass ──────────────────────────────────────────────
                reels_only: list = []
                skipped_not_reel = 0
                skipped_following = 0
                skipped_excluded = 0

                for post in raw_posts:
                    url, post_username = self._parse_liked_post(post)

                    # Must be a reel URL
                    if not url or '/reel/' not in url:
                        skipped_not_reel += 1
                        continue

                    # Skip accounts we follow
                    if post_username and post_username in following:
                        skipped_following += 1
                        logging.debug(f"Skipping reel from followed account: @{post_username}")
                        continue

                    # Skip manually excluded users
                    if post_username and post_username in self.excluded_users:
                        skipped_excluded += 1
                        logging.debug(f"Skipping reel from excluded user: @{post_username}")
                        continue

                    # Store parsed values alongside the post for the loop below
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

                        # Unlike with retry mechanism
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

                        # Random break
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
                        time.sleep(300)  # 5 minute cooldown

            finally:
                if progress_bar is not None:
                    progress_bar.close()

            # Update account stats
            account_data['last_run'] = datetime.now().isoformat()
            with open(account_file, 'w') as f:
                json.dump(account_data, f, indent=4)

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
        """Center text in a box line, accounting for color codes"""
        visible_length = get_visible_length(text)
        padding = (box_width - 2 - visible_length) // 2
        return f"║{' ' * padding}{text}{' ' * (box_width - 2 - visible_length - padding)}║"

    def show_menu(self):
        """Display interactive menu with improved UI"""
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
                
            print(f"\n{ConsoleColors.CYAN}Available Actions:{ConsoleColors.RESET}")
            print(f"╭{'─' * 40}╮")
            print(menu_line("1", "Add Instagram Account"))
            print(menu_line("2", "Remove Account"))
            print(menu_line("3", "Start Unliking"))
            print(menu_line("4", "Manage Excluded Users"))
            print(menu_line("5", "View Stats"))
            print(menu_line("6", "Settings"))
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
                    self._start_unliking_menu()
                elif choice == "4":
                    self.manage_excluded_users()
                elif choice == "5":
                    self.show_statistics()
                elif choice == "6":
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
        """Display account selection menu for unliking"""
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
        """Display statistics with improved UI"""
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
        """Display and modify settings with enhanced UI"""
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
        """Check if system meets all requirements"""
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
        """Check and validate all required dependencies"""
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
    """Convert Instagram shortcode to media ID"""
    charmap = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_'
    code = code.split('/')[-2]
    return sum(charmap.index(char) * (64 ** i) for i, char in enumerate(reversed(code)))

def get_visible_length(text):
    """Calculate the visible length of text by removing ANSI color codes"""
    import re
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    return len(ansi_escape.sub('', text))

def menu_line(number, text, box_width=40):
    """Create a properly aligned menu line with consistent formatting"""
    prefix = f"│ {ConsoleColors.BOLD}{number}.{ConsoleColors.RESET} {ConsoleColors.WHITE}"
    content = f"{text}{ConsoleColors.RESET}"
    visible_length = get_visible_length(f"{prefix}{content}")
    padding = box_width - visible_length + 1
    return f"{prefix}{content}{' ' * padding}│{ConsoleColors.RESET}"

def main():
    """Main entry point with improved initialization and dependency checking"""
    try:
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
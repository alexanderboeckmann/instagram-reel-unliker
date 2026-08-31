# 📱 Instagram Reel Unliker

> Bulk-unlike Instagram reels, skipping accounts you follow. Free, open-source, runs entirely on your own machine.

<div align="center">

[![Python Version](https://img.shields.io/badge/python-3.7%2B-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Open Source](https://img.shields.io/badge/Open%20Source-%E2%9D%A4-red)](https://github.com/alexanderboeckmann/instagram-reel-unliker)

</div>

A fork of [TahaGorme/InstaMassUnliker](https://github.com/TahaGorme/InstaMassUnliker) with two behavioural changes:

- **Reels only.** Regular posts (`/p/` URLs) are left alone — only `/reel/` links are unliked.
- **Skips accounts you follow.** Anyone in your `following.json` is excluded automatically, so you only clear likes on reels from accounts you *don't* follow.

Plus a manual exclude list on top of that, for accounts you don't follow but still want left untouched.

## 📑 Table of Contents
- [How it works](#-how-it-works)
- [Setup](#-setup)
- [Running it](#-running-it)
- [Long runs](#-long-runs)
- [Configuration](#-configuration)
- [Known limitations](#-known-limitations)
- [License](#-license)
- [Disclaimer](#%EF%B8%8F-disclaimer)

## 🔍 How it works

This tool does **not** scrape your likes from the Instagram UI. It reads your official Instagram data export, filters it, and then calls Instagram's unlike endpoint once per reel via [`ensta`](https://pypi.org/project/ensta/).

That means the export is the source of truth, and it has a useful property: it's a snapshot of what you *currently* like. Request a fresh export and everything you've already unliked simply drops out of the list.

## 🚀 Setup

### 1. Request your Instagram data export

1. Instagram → Settings → **Accounts Center** → Your information and permissions → **Download your information**
2. Request a download, **format: JSON** (HTML exports cannot be read by this tool)
3. Instagram emails you a ZIP, usually within 15–60 minutes
4. Copy two files out of the ZIP into this folder:

| File | Location in the export | Purpose |
|---|---|---|
| `liked_posts.json` | `your_instagram_activity/likes/` | The posts to work through |
| `following.json` | `connections/followers_and_following/` | Accounts to skip |

`following.json` is optional — without it the follow-filter is simply disabled and every liked reel is fair game.

### 2. Install

```bash
chmod +x run.sh
./run.sh
```

`run.sh` checks prerequisites, builds a virtual environment in `venv/`, installs dependencies, and launches the menu. You only need it for first-time setup or after moving to a new machine.

If `venv/` already exists but is broken — for example it points at a Python that's since been uninstalled — delete it and re-run, as `run.sh` only creates a venv when the directory is missing:

```bash
rm -rf venv && ./run.sh
```

## ▶️ Running it

After the first setup, use the launcher:

```bash
./unlike
```

It finds the repo's virtualenv regardless of where you call it from, so you don't have to activate anything or remember a path.

To run it from any directory, symlink it somewhere on your `PATH`:

```bash
ln -s "$PWD/unlike" ~/.local/bin/unlike
```

Then just `unlike`, from anywhere. The launcher resolves the symlink back to the repo before looking for the virtualenv.

The equivalent longhand, if you'd rather not use the launcher:

```bash
./venv/bin/python instagram_unliker.py
```

The `./venv/bin/` prefix matters. Plain `python` does not exist on macOS, and plain `python3` is your system Python, which won't have the dependencies.

Menu options:

| | Action |
|---|---|
| 1 | Add Instagram account (also used to update a changed password) |
| 2 | Remove account |
| 3 | Start unliking |
| 4 | Manage excluded users |
| 5 | View stats |
| 6 | Settings |
| 0 | Exit |

Add your account with **1**, then start with **3**. If you change your Instagram password later, re-add the account with **1** and answer `y` to replace, then delete `ensta-session.json` so the stale login cookie isn't reused.

## ⏳ Long runs

At the default 5–15 second delay, a large backlog takes **days**, not hours — roughly 10 seconds per reel, plus a 5-minute cooldown after any failure.

The menu is interactive, so it can't be backgrounded with `nohup`. Use `screen`, which ships with macOS:

```bash
screen -S unliker
caffeinate -is ./unlike
```

Detach with **Ctrl-A** then **D**; reattach with `screen -r unliker`.

Run `caffeinate` *inside* the screen session rather than wrapping it — wrapped, it exits the moment you detach. Keep the machine plugged in with the lid open, since `caffeinate` doesn't prevent clamshell sleep. A dropped network connection is the most common cause of a failed run.

Monitor from another window:

```bash
tail -f logs/unliker.log
```

## 🌐 Configuration

`config.json`:

```json
{
    "delay":   { "min": 5.0, "max": 15.0 },
    "break":   { "min": 300.0, "max": 900.0, "probability": 0.001 },
    "accounts": {
        "your_username": { "enabled": true, "delay_multiplier": 1.0 }
    },
    "excluded_users": [],
    "log_level": "INFO",
    "max_retries": 3,
    "retry_delay": 60,
    "auto_update": true,
    "python_min_version": "3.7.0"
}
```

- **`delay`** — seconds between unlikes, randomised in this range. Lower is faster and more conspicuous.
- **`break`** — with `probability` per reel, pause for a random duration in this range.
- **`delay_multiplier`** — per-account scaling of the delay.
- **`excluded_users`** — usernames never touched, on top of the automatic follow-filter. Edit via menu option **4**.

## ⚠️ Known limitations

- **No resume state.** `liked_posts.json` is never rewritten, so an interrupted run restarts from the top of the list on the next launch. Re-unliking an already-unliked reel is harmless but wastes the whole queue. A fresh export is the fastest way to resume.
- **Passwords are stored in plaintext** in `accounts/<username>.json`. The `accounts/` directory is gitignored, but the file is readable by anything running as your user.
- **No 2FA support.** There is no prompt for a verification code; login will fail if two-factor is enabled on the account.
- **Rate limiting is a deterrent, not a guarantee.** Random delays and breaks reduce how mechanical the traffic looks. They do not make it invisible, and they are no protection against an action block.
- **`run.bat` (Windows) is unmaintained** in this fork and has not been tested.
- **FFmpeg is not installed** by `run.sh`. It isn't needed for unliking; `moviepy` is only pulled in as an `ensta` dependency.

## 📄 License

MIT — see [LICENSE](LICENSE). Original work by [TahaGorme](https://github.com/TahaGorme/InstaMassUnliker).

## ⚠️ Disclaimer

For educational purposes and for managing your own account. Automating actions is contrary to Instagram's Terms of Use and may result in rate limiting, action blocks, or account suspension. Use it on your own account, at your own risk.

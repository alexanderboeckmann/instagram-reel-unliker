# Instagram Reel Unliker

> Bulk-unlike Instagram reels and posts, skipping accounts you follow. Free, open-source, runs entirely on your own machine.

<div align="center">

[![Python Version](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Open Source](https://img.shields.io/badge/Open%20Source-%E2%9D%A4-red)](https://github.com/alexanderboeckmann/instagram-reel-unliker)

</div>

A fork of [TahaGorme/InstaMassUnliker](https://github.com/TahaGorme/InstaMassUnliker) that lets you choose reels, posts, or both (`content`, default `reels`), and automatically skips anyone you follow. A manual exclude list sits on top of that.

It doesn't scrape your likes from the UI. It reads your official Instagram data export, filters it, and calls Instagram's unlike endpoint once per item via [`ensta`](https://pypi.org/project/ensta/). The export is a snapshot of what you *currently* like, so a fresh export simply drops everything you've already unliked.

## Setup

**1. Request your data export.** Instagram → Settings → **Accounts Center** → Your information and permissions → **Download your information**. Request it in **JSON** format (HTML can't be read). The ZIP arrives by email in 15–60 minutes. Leave it in `~/Downloads` — the importer finds it.

**2. Run it.**

```bash
./unlike
```

First run builds a virtualenv and installs dependencies, then opens the menu. Every run after that goes straight to the menu. Needs Python 3.10+.

To call it from anywhere, symlink it — the launcher resolves the symlink back to the repo:

```bash
ln -s "$PWD/unlike" ~/.local/bin/unlike
```

Tested on macOS; Linux should work but is untested. Windows is not supported.

## Using it

| | Action |
|---|---|
| 1 | Add Instagram account (username only) |
| 2 | Remove account |
| 3 | Import Instagram data |
| 4 | Start unliking |
| 5 | Manage excluded users |
| 6 | View stats |
| 7 | Settings |
| 0 | Exit |

Add your account with **1** (username only, no password), import with **3**, start with **4**. You're prompted for your password on first login and again only when the saved session expires.

**Import** scans `~/Downloads` and `~/Desktop` and offers what it finds, so it's usually one keystroke. A folder, the untouched `.zip`, or a single `liked_posts.json` / `following.json` all work; you can also paste or drag in a path, or pass one directly:

```bash
unlike --import ~/Downloads/instagram-yourname-2026-08-30-DSvOB6Y3
```

It pulls `liked_posts.json` (the likes to work through) and `following.json` (accounts to skip) into `data/`, then offers to delete the original. `following.json` is optional — without it, the follow-filter is off.

## Credentials

Your password is **never stored** — typed at the prompt, used once to obtain a session, then dropped. The login session lives in your system keychain (macOS Keychain, SecretService on Linux) under service `instagram-reel-unliker-session`, keyed by username. Treat it like a password; it grants account access on its own.

```bash
security delete-generic-password -s instagram-reel-unliker-session -a <username>  # force re-login
```

`accounts/<username>.json` holds only stats. Removing an account (menu **2**) deletes its session and progress too. Without a usable `keyring` backend, nothing persists and you're asked for your password every run.

## Long runs

At the default 20–100 second delay, a large backlog takes **days** — roughly 80 seconds per item, so 5,000 items is about five days. The run prints that estimate up front. Every wait says what it is and when it ends:

```
Filter summary:
  Reels to unlike : 4812
  Non-reel posts  : 26113 (skipped)
  From following  : 884 (skipped)
  Done earlier    : 1204 (resuming)
· About 1m 22s each — 4812 reels is roughly 4d 14h

✓ 47/4812 · @travelclips · next in 38s
! Attempt 1/3 failed: 429 Too Many Requests — retrying in 1m
· Break for 44m 12s — resuming at 15:12
```

The menu is interactive, so use `screen` rather than `nohup`:

```bash
screen -S unliker
caffeinate -is ./unlike
```

Detach with **Ctrl-A** then **D**, reattach with `screen -r unliker`. Run `caffeinate` *inside* the session — wrapped around it, it exits when you detach. Keep the machine plugged in with the lid open. Follow along from another window with `tail -f logs/unliker.log`.

**Resuming is automatic.** Each item is flushed to `data/progress/<username>.txt` as it's unliked, so an interrupted run — Ctrl-C, dropped connection, crash, `kill -9` — loses at most the one in flight, and the next run skips what's done. Failed items aren't recorded, so they come back around. Delete the file to start over. Importing a different export discards it automatically.

## Configuration

`config.json`:

```json
{
    "delay":   { "min": 20, "max": 100 },
    "break":   { "min": 900, "max": 3600, "probability": 0.01 },
    "accounts": {
        "your_username": { "enabled": true, "delay_multiplier": 1.0 }
    },
    "excluded_users": [],
    "content": "reels",
    "log_level": "INFO",
    "max_retries": 3,
    "retry_delay": 60
}
```

- **`delay`** — seconds between unlikes, randomised in this range. Lower is faster and more conspicuous.
- **`break`** — with `probability` per item, pause for a random duration in this range.
- **`delay_multiplier`** — per-account scaling of the delay.
- **`excluded_users`** — never touched, on top of the follow-filter. Menu **5**.
- **`content`** — `reels` (default), `posts` (`/p/` and `/tv/`), or `both`. Switching mid-export is safe; progress is per item. Settings → **8**.
- **`log_level`** — `DEBUG` also records why each item was skipped; `WARNING` keeps the log to problems only.

## Known limitations

- **The stored session is readable by code running as you.** This protects against accidental commits, backups, cloud sync, and offline disk access — not against malware already running as you.
- **No 2FA support.** Login fails if two-factor is enabled.
- **Rate limiting is a deterrent, not a guarantee.** Delays and breaks make the traffic less mechanical, not invisible, and are no protection against an action block.
- **`ensta` is installed without some of its dependencies.** It imports `moviepy.editor`, `PIL.Image` and `pyquery` at module scope but only reaches them when uploading video or scraping a single post, neither of which this tool does. Setup uninstalls those and `instagram_unliker.py` stubs the imports, taking `venv/` from 156M to 34M. The stubs raise a named `RuntimeError` if those paths are ever entered, and stand down if the real packages are present. No FFmpeg needed.

## License

MIT — see [LICENSE](LICENSE). Original work by [TahaGorme](https://github.com/TahaGorme/InstaMassUnliker).

## Disclaimer

For educational purposes and for managing your own account. Automating actions is contrary to Instagram's Terms of Use and may result in rate limiting, action blocks, or account suspension. Use it on your own account, at your own risk.

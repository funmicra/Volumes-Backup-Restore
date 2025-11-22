#!/usr/bin/env python3
import os
import subprocess
import logging
import requests
from datetime import datetime
from pathlib import Path
from time import time
from dotenv import load_dotenv
import argparse
from collections import defaultdict
from prettytable import PrettyTable 

# -------------------------------------------------------------------
# Load .env
# -------------------------------------------------------------------
load_dotenv()
BACKUP_DIR = os.getenv("BACKUP_DIR")
if not BACKUP_DIR:
    raise ValueError("BACKUP_DIR not set in .env")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

KEEP_LAST = os.getenv("KEEP_LAST")
if KEEP_LAST is not None:
    try:
        KEEP_LAST = int(KEEP_LAST)
    except ValueError:
        raise ValueError("KEEP_LAST must be an integer")
else:
    KEEP_LAST = 0  # 0 = keep all backups

# -------------------------------------------------------------------
# CLI arguments
# -------------------------------------------------------------------
def parse_args():
    parser = argparse.ArgumentParser(description="Docker Volume Backup & Restore Tool")
    parser.add_argument("--dry-run", action="store_true", help="Perform a dry run without actual backup/restore")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")
    parser.add_argument("--telegram-off", action="store_true", help="Disable Telegram notifications")
    return parser.parse_args()

# -------------------------------------------------------------------
# Telegram
# -------------------------------------------------------------------
def send_telegram(message: str, enabled: bool):
    if not enabled or not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message}, timeout=10)
    except Exception as e:
        logging.error(f"Telegram send failed: {e}")

def dry(msg, telegram_enabled):
    logging.info(f"[DRY RUN] {msg}")
    send_telegram(f"🔍 DRY RUN: {msg}", telegram_enabled)

# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------
def human_size(size_bytes, decimal_places=2):
    for unit in ["B","KB","MB","GB","TB"]:
        if size_bytes < 1024:
            return f"{size_bytes:.{decimal_places}f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.{decimal_places}f} PB"

def list_volumes():
    result = subprocess.run(
        ["docker", "volume", "ls", "--format", "{{.Name}}"],
        capture_output=True, text=True, check=True
    )
    volumes = [v for v in result.stdout.strip().split("\n") if v]
    for i, v in enumerate(volumes, 1):
        print(f"{i}. {v}")
    return volumes

def list_backups(BACKUP_DIR):
    backups = sorted(Path(BACKUP_DIR).glob("*.tar.gz"))
    for i, b in enumerate(backups, 1):
        size = human_size(b.stat().st_size)
        print(f"{i}. {b.name} ({size})")
    return backups

def select_with_all_option(items, label="item"):
    """
    Numbered selection menu.
    Supports:
      - 0 = all
      - comma-separated multiple selection
      - B = back/cancel
    Returns selected items or None if canceled.
    """
    while True:
        print(f"\nSelect {label} by index (0 = ALL, comma-separated, B = Back/Cancel):")
        for i, item in enumerate(items, 1):
            print(f"{i}. {item}")
        choice = input("Your choice: ").strip()
        if not choice:
            print("No selection made.")
            continue
        if choice.lower() == "b":
            return None
        selected = []
        try:
            for part in choice.split(","):
                idx = int(part)
                if idx == 0:
                    return items
                if 1 <= idx <= len(items):
                    selected.append(items[idx-1])
                else:
                    raise ValueError
            return selected
        except ValueError:
            print(f"Invalid input. Use numbers 0-{len(items)}, commas for multiple, or B to go back.")

def pause_containers_using(volume_name, dry_run, telegram_enabled):
    result = subprocess.run(
        ["docker", "ps", "-q", "--filter", f"volume={volume_name}"],
        capture_output=True, text=True
    )
    containers = [c for c in result.stdout.strip().split("\n") if c]
    paused = []
    for c in containers:
        if dry_run:
            dry(f"Would pause container: {c}", telegram_enabled)
        else:
            try:
                subprocess.run(["docker", "pause", c], check=True)
                paused.append(c)
            except subprocess.CalledProcessError:
                logging.warning(f"Container {c} already paused or cannot pause")
    return paused

def unpause(containers, dry_run, telegram_enabled):
    for c in containers:
        if dry_run:
            dry(f"Would unpause container: {c}", telegram_enabled)
        else:
            subprocess.run(["docker", "unpause", c], stdout=subprocess.PIPE, stderr=subprocess.PIPE)

def cleanup_backups(BACKUP_DIR, KEEP_LAST, dry_run=False, telegram_enabled=True):
    """
    Keeps last KEEP_LAST backups per volume, removes older ones.
    """
    backups = sorted(Path(BACKUP_DIR).glob("*.tar.gz"), key=lambda x: x.stat().st_mtime, reverse=True)
    volume_groups = defaultdict(list)
    for b in backups:
        stem_parts = b.stem.split("_")
        volume_name = "_".join(stem_parts[:-2])
        volume_groups[volume_name].append(b)

    for volume, files in volume_groups.items():
        if KEEP_LAST > 0 and len(files) > KEEP_LAST:
            for old in files[KEEP_LAST:]:
                if dry_run:
                    logging.info(f"[DRY RUN] Would remove old backup for volume '{volume}': {old.name}")
                    send_telegram(f"🧹 [DRY RUN] Would remove old backup for volume '{volume}': {old.name}", telegram_enabled)
                else:
                    try:
                        logging.info(f"🧹 Removing old backup for volume '{volume}': {old.name}")
                        old.unlink()
                    except Exception as e:
                        logging.error(f"⚠️ Failed to remove {old.name}: {e}")
                        send_telegram(f"⚠️ Failed to remove old backup {old.name}: {e}", telegram_enabled)

# -------------------------------------------------------------------
# Backup & Restore with summary
# -------------------------------------------------------------------
def backup_volume(volume, BACKUP_DIR, dry_run, telegram_enabled):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    outfile = f"{volume}_{timestamp}.tar.gz"
    outpath = Path(BACKUP_DIR) / outfile

    logging.info(f"⚙️ Backing up volume 🖴'{volume}' → {outpath}")
    start = time()
    paused = pause_containers_using(volume, dry_run, telegram_enabled)

    cmd = [
        "docker", "run", "--rm",
        "-v", f"{volume}:/volume",
        "-v", f"{BACKUP_DIR}:/backup",
        "debian:stable-slim",
        "sh", "-c", f"tar -czf /backup/{outfile} -C /volume ."
    ]

    status = "❌ Failed"
    try:
        if dry_run:
            dry(f"Would run: {' '.join(cmd)}", telegram_enabled)
            cleanup_backups(BACKUP_DIR, KEEP_LAST, dry_run=True, telegram_enabled=telegram_enabled)
            status = "🔍 Dry run"
        else:
            subprocess.run(cmd, check=True)
            cleanup_backups(BACKUP_DIR, KEEP_LAST, dry_run=False, telegram_enabled=telegram_enabled)
            status = "✅ Success"

        end = time()
        size = human_size(outpath.stat().st_size) if outpath.exists() else "0 B"
        duration = f"{end-start:.2f}s"

        logging.info(f"{status} Backup: {outfile} | Size: {size} | Duration: {duration}")
        send_telegram(f"{status} Backup!\n 🖴 {outfile}\n🗂️ Size: {size} | ⏰ Duration: {duration}", telegram_enabled)

        # Print summary table
        table = PrettyTable()
        table.field_names = ["Volume", "File", "Size", "Duration", "Status"]
        table.add_row([volume, outfile, size, duration, status])
        print(table)

    except Exception as e:
        logging.error(f"❌ Backup failed for {volume}: {e}")
        send_telegram(f"❌ Backup failed for {volume}", telegram_enabled)
    finally:
        unpause(paused, dry_run, telegram_enabled)


def restore_backup(file_path, BACKUP_DIR, dry_run, telegram_enabled):
    file_path = Path(file_path)
    volume_name = "_".join(file_path.stem.split("_")[:-2])
    logging.info(f"Restoring {file_path} → Volume: {volume_name}")
    start = time()
    paused = pause_containers_using(volume_name, dry_run, telegram_enabled)

    cmd = [
        "docker", "run", "--rm",
        "-v", f"{volume_name}:/volume",
        "-v", f"{BACKUP_DIR}:/backup",
        "debian:stable-slim",
        "sh", "-c", f"rm -rf /volume/* && tar -xzf /backup/{file_path.name} -C /volume"
    ]

    status = "❌ Failed"
    try:
        if dry_run:
            dry(f"Would restore: {' '.join(cmd)}", telegram_enabled)
            status = "🔍 Dry run"
        else:
            subprocess.run(cmd, check=True)
            status = "✅ Success"

        end = time()
        size = human_size(file_path.stat().st_size)
        duration = f"{end-start:.2f}s"

        logging.info(f"{status} Restore: {volume_name} | Size: {size} | Duration: {duration}")
        send_telegram(f"{status} Restore: {volume_name}\n🗂️ Size: {size} | ⏰ Duration: {duration}", telegram_enabled)

        # Print summary table
        table = PrettyTable()
        table.field_names = ["Volume", "File", "Size", "Duration", "Status"]
        table.add_row([volume_name, file_path.name, size, duration, status])
        print(table)

    except Exception as e:
        logging.error(f"❌ Restore failed for {volume_name}: {e}")
        send_telegram(f"❌ Restore failed for {volume_name}", telegram_enabled)
    finally:
        unpause(paused, dry_run, telegram_enabled)

# def backup_volume(volume, BACKUP_DIR, dry_run, telegram_enabled):
#     timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
#     outfile = f"{volume}_{timestamp}.tar.gz"
#     outpath = Path(BACKUP_DIR) / outfile

#     logging.info(f"⚙️ Backing up volume 🖴'{volume}' → {outpath}")
#     start = time()
#     paused = pause_containers_using(volume, dry_run, telegram_enabled)

#     cmd = [
#         "docker", "run", "--rm",
#         "-v", f"{volume}:/volume",
#         "-v", f"{BACKUP_DIR}:/backup",
#         "debian:stable-slim",
#         "sh", "-c", f"tar -czf /backup/{outfile} -C /volume ."
#     ]

#     try:
#         if dry_run:
#             dry(f"Would run: {' '.join(cmd)}", telegram_enabled)
#             cleanup_backups(BACKUP_DIR, KEEP_LAST, dry_run=True, telegram_enabled=telegram_enabled)
#         else:
#             subprocess.run(cmd, check=True)
#             cleanup_backups(BACKUP_DIR, KEEP_LAST, dry_run=False, telegram_enabled=telegram_enabled)

#         end = time()
#         size = human_size(outpath.stat().st_size) if outpath.exists() else "0 B"
#         logging.info(f"✅ Backup complete: 🖴 {outfile} | 🗂️ Size: {size} | ⏰ Duration: {end-start:.2f}s")
#         send_telegram(f"✅ Backup complete!\n 🖴 {outfile}\n🗂️ Size: {size} | ⏰ Duration: {end-start:.2f}s", telegram_enabled)
#     except Exception as e:
#         logging.error(f"❌ Backup failed for {volume}: {e}")
#         send_telegram(f"❌ Backup failed for {volume}", telegram_enabled)
#     finally:
#         unpause(paused, dry_run, telegram_enabled)

# def restore_backup(file_path, BACKUP_DIR, dry_run, telegram_enabled):
#     file_path = Path(file_path)
#     volume_name = "_".join(file_path.stem.split("_")[:-2])
#     logging.info(f"Restoring {file_path} → Volume: {volume_name}")
#     start = time()
#     paused = pause_containers_using(volume_name, dry_run, telegram_enabled)

#     cmd = [
#         "docker", "run", "--rm",
#         "-v", f"{volume_name}:/volume",
#         "-v", f"{BACKUP_DIR}:/backup",
#         "debian:stable-slim",
#         "sh", "-c", f"rm -rf /volume/* && tar -xzf /backup/{file_path.name} -C /volume"
#     ]

#     try:
#         if dry_run:
#             dry(f"Would restore: {' '.join(cmd)}", telegram_enabled)
#         else:
#             subprocess.run(cmd, check=True)
#         end = time()
#         size = human_size(file_path.stat().st_size)
#         logging.info(f"✅ Restore complete: 🖴{volume_name} | 🗂️ Size: {size} | ⏰ Duration: {end-start:.2f}s")
#         send_telegram(f"✔️ Restore complete: {volume_name}\n🗂️ Size: {size} | ⏰ Duration: {end-start:.2f}s", telegram_enabled)
#     except Exception as e:
#         logging.error(f"❌ Restore failed for {volume_name}: {e}")
#         send_telegram(f"❌ Restore failed for {volume_name}", telegram_enabled)
#     finally:
#         unpause(paused, dry_run, telegram_enabled)

# -------------------------------------------------------------------
# Interactive CLI
# -------------------------------------------------------------------
def main_menu():
    print("\n" + "=" * 40)
    print(" Docker Volume Backup & Restore Tool")
    print("=" * 40)
    options = [
        ("1", "Backup a volume", "💾"),
        ("2", "Restore a volume", "🔄"),
        ("0", "Exit", "❌"),
    ]
    for key, text, icon in options:
        print(f"{key}) {text.ljust(25)} {icon}")
    print("=" * 40)

def main():
    args = parse_args()
    dry_run = args.dry_run
    verbose = args.verbose
    telegram_enabled = not args.telegram_off

    os.makedirs(BACKUP_DIR, exist_ok=True)

    logging.basicConfig(
        format="%(asctime)s - %(levelname)s - %(message)s",
        level=logging.DEBUG if verbose else logging.INFO
    )

    logging.info(f"DRY_RUN={dry_run}, VERBOSE={verbose}, BACKUP_DIR={BACKUP_DIR}, TELEGRAM_ENABLED={telegram_enabled}")

    while True:
        main_menu()
        choice = input("Select option: ").strip()
        if choice == "1":
            volumes = list_volumes()
            selected_volumes = select_with_all_option(volumes, "volume")
            if selected_volumes is None:
                continue
            for vol in selected_volumes:
                backup_volume(vol, BACKUP_DIR, dry_run, telegram_enabled)
        elif choice == "2":
            backups = list_backups(BACKUP_DIR)
            if not backups:
                print("No backup files found.")
                continue
            selected_backups = select_with_all_option(backups, "backup")
            if selected_backups is None:
                continue
            for bkp in selected_backups:
                restore_backup(bkp, BACKUP_DIR, dry_run, telegram_enabled)
        elif choice == "0":
            print("Exiting workflow. Operational cycle terminated.")
            break
        else:
            print("Invalid option.")

if __name__ == "__main__":
    main()

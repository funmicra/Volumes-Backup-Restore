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
# Log File
# -------------------------------------------------------------------
LOG_FILE = Path(BACKUP_DIR) / "backup.log"

logging.basicConfig(
    filename=LOG_FILE,  # write logs to file
    filemode='a',        # append to existing file
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO   # or DEBUG for more detail
)

# -------------------------------------------------------------------
# CLI arguments
# -------------------------------------------------------------------
def parse_args():
    parser = argparse.ArgumentParser(description="Docker Volume Backup & Restore Tool")
    parser.add_argument("--dry-run", action="store_true", help="Perform a dry run without actual backup/restore")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")
    parser.add_argument("--telegram-off", action="store_true", help="Disable Telegram notifications")
    parser.add_argument("--backup-all", action="store_true", help="Perform backup for all volumes and exit")
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

def dry(msg, telegram_enabled, quiet=False):
    if not quiet:
        logging.info(f"[DRY RUN] {msg}")
    send_telegram(f"🔍 DRY RUN: {msg}", telegram_enabled)
    # logging.info(f"[DRY RUN] {msg}")
    # send_telegram(f"🔍 DRY RUN: {msg}", telegram_enabled)

# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------
def clear_screen():
    os.system('cls' if os.name == 'nt' else 'clear')

def human_size(size_bytes, decimal_places=2):
    for unit in ["B","KB","MB","GB","TB"]:
        if size_bytes < 1024:
            return f"{size_bytes:.{decimal_places}f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.{decimal_places}f} PB"

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
                subprocess.run(["docker", "pause", c], check=True,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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

# -------------------------------------------------------------------
# Clean old backup but keep the last defined
# -------------------------------------------------------------------
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
                    logging.info(f"🧹 [DRY RUN] Would remove old backup for volume '{volume}': {old.name}")
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

    logging.info(f"⚙️ Backing up volume 🖴 '{volume}' → {outpath}")
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

        logging.info(f"{status} Backup: 🖴 {outfile} | 📦 Size: {size} | 🕐 Duration: {duration}")
        send_telegram(f"{status} Backup!\n🖴 {outfile}\n📦 Size: {size}\n🕐 Duration: {duration}", telegram_enabled)

        # Build and return summary row (same shape as restore_backup)
        row = [volume, outfile, BACKUP_DIR, size, duration, status]

        return row

    except Exception as e:
        logging.error(f"❌ Backup failed for {volume}: {e}")
        send_telegram(f"❌ Backup failed for {volume}: {e}", telegram_enabled)
        end = time()
        duration = f"{end-start:.2f}s"
        return [volume, outfile, BACKUP_DIR, "0 B", duration, "❌ Failed"]

    finally:
        unpause(paused, dry_run, telegram_enabled)


def restore_backup(file_path, BACKUP_DIR, dry_run, telegram_enabled):
    file_path = Path(file_path)
    volume_name = "_".join(file_path.stem.split("_")[:-2])
    logging.info(f"⚙️ Restoring {file_path} → 🖴 Volume: {volume_name}")
    start = time()
    paused = pause_containers_using(volume_name, dry_run, telegram_enabled)

    cmd = [
        "docker", "run", "--rm",
        "-v", f"{volume_name}:/volume",
        "-v", f"{BACKUP_DIR}:/backup",
        "debian:stable-slim",
        "sh", "-c", f"rm -rf /volume/* && tar -xzf /backup/{file_path.name} -C /volume"
    ]

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

        logging.info(f"{status} Restore! 🖴 {volume_name} | 📦 Size: {size} | 🕐 Duration: {duration}")
        send_telegram(f"{status} Restore!\n🖴 {volume_name}\n📦 Size: {size}\n🕐 Duration: {duration}", telegram_enabled)

        return [volume_name, file_path.name, BACKUP_DIR, size, duration, status]

    except Exception as e:
        logging.error(f"❌ Restore failed for {file_path}: {e}")
        send_telegram(f"❌ Restore failed for {file_path}", telegram_enabled)
        return [volume_name, file_path.name, "0 B", "0s", "❌ Failed"]

    finally:
        unpause(paused, dry_run, telegram_enabled)

# -------------------------------------------------------------------
# Print summaries (terminal or log)
# -------------------------------------------------------------------
def print_Backup_summary(summary_rows, log_only=False):
    if not summary_rows:
        msg = "No backup operations executed."
        if log_only:
            logging.info(msg)
        else:
            print(msg)
        return
    table = PrettyTable()
    table.field_names = ["🖴 Volume", "File", "Destination", "📦 Size", "🕐 Duration", "🀄️ Status"]
    for row in summary_rows:
        table.add_row(row)
    output = "\n=== BACKUP SUMMARY ===\n" + table.get_string()
    if log_only:
        logging.info(output)
    else:
        print(output)

def print_restore_summary(summary_rows, log_only=False):
    if not summary_rows:
        msg = "No restore operations executed."
        if log_only:
            logging.info(msg)
        else:
            print(msg)
        return
    table = PrettyTable()
    table.field_names = ["🖴 Volume", "File", "Destination", "📦 Size", "🕐 Duration", "🀄️ Status"]
    for row in summary_rows:
        table.add_row(row)
    output = "\n=== RESTORE SUMMARY ===\n" + table.get_string()
    if log_only:
        logging.info(output)
    else:
        print(output)

# -------------------------------------------------------------------
# Display Volumes & Backups with PrettyTable
# -------------------------------------------------------------------
def list_volumes(print_table=True):
    result = subprocess.run(
        ["docker", "volume", "ls", "--format", "{{.Name}}"],
        capture_output=True, text=True, check=True
    )
    volumes = [v for v in result.stdout.strip().split("\n") if v]
    if not volumes:
        print("No Docker volumes found.")
        return []
    if print_table:
        table = PrettyTable()
        table.field_names = ["Index", "Volume Name"]
        for idx, v in enumerate(volumes, start=1):
            table.add_row([idx, v])
        print(table)
    return volumes

def list_backups(BACKUP_DIR):
    backups = sorted(Path(BACKUP_DIR).glob("*.tar.gz"))
    if not backups:
        print("No backup files found.")
        return []

    table = PrettyTable()
    table.field_names = ["Index", "Backup File", "Destination", "Date", "Size"]
    for idx, b, in enumerate(backups, start=1):
        stat = b.stat()
        date_str = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        size_str = human_size(stat.st_size)

        table.add_row([
            idx,
            b.name,
            BACKUP_DIR,
            date_str,
            size_str
        ])
    print(table)
    return backups

# -------------------------------------------------------------------
# Interactive CLI
# -------------------------------------------------------------------
def main_menu_table():
    table = PrettyTable()
    table.field_names = ["Option", "Action", "Icon"]
    options = [
        ("1", "Backup a volume", "🖴 ->💾"),
        ("2", "Restore a volume", "🖴 <-💾"),
        ("0", "Exit", "❌"),
    ]
    for opt, action, icon in options:
        table.add_row([opt, action, icon])
    print(table)

# -------------------------------------------------------------------
# Main
# -------------------------------------------------------------------
def main():
    args = parse_args()
    dry_run = args.dry_run
    verbose = args.verbose
    telegram_enabled = not args.telegram_off
    backup_all = args.backup_all

    os.makedirs(BACKUP_DIR, exist_ok=True)

    # --- Logging setup ---
    logging.getLogger().handlers.clear()
    if backup_all:
        # Silent terminal, log only to file
        logging.basicConfig(
            filename=LOG_FILE,
            filemode='a',
            format="%(asctime)s - %(levelname)s - %(message)s",
            level=logging.INFO
        )
        log_only = True
    else:
        # Interactive: logs go to terminal + file
        logging.basicConfig(
            format="%(asctime)s - %(levelname)s - %(message)s",
            level=logging.DEBUG if verbose else logging.INFO
        )
        log_only = False

    # --- Non-interactive backup ---
    if backup_all:
        volumes = list_volumes(print_table=False)
        backup_summary = []
        for vol in volumes:
            row = backup_volume(vol, BACKUP_DIR, dry_run, telegram_enabled)
            backup_summary.append(row)
        print_Backup_summary(backup_summary, log_only=log_only)
        exit(0)

    # --- Interactive menu ---
    while True:
        main_menu_table()
        choice = input("Select option: ").strip()
        if choice == "1":
            clear_screen()
            volumes = list_volumes()
            selected_volumes = select_with_all_option(volumes, "volume")
            if selected_volumes is None:
                continue
            backup_summary=[]
            for vol in selected_volumes:
                row_b = backup_volume(vol, BACKUP_DIR, dry_run, telegram_enabled)
                backup_summary.append(row_b)
            print_Backup_summary(backup_summary, log_only=log_only)

        elif choice == "2":
            clear_screen()
            backups = list_backups(BACKUP_DIR)
            if not backups:
                continue
            selected_backups = select_with_all_option(backups, "backup")
            if selected_backups is None:
                continue
            restore_summary = []
            for bkp in selected_backups:
                row_r = restore_backup(bkp, BACKUP_DIR, dry_run, telegram_enabled)
                restore_summary.append(row_r)
            print_restore_summary(restore_summary, log_only=log_only)
        elif choice == "0":
            print("Exiting workflow. Operational cycle terminated.")
            break
        else:
            print("Invalid option.")

if __name__ == "__main__":
    main()

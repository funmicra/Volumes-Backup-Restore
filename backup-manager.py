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
# Load environment
# -------------------------------------------------------------------
load_dotenv()
BACKUP_DIR = os.getenv("BACKUP_DIR")
if not BACKUP_DIR:
    raise SystemExit("BACKUP_DIR not set in environment or .env")
BACKUP_DIR = Path(BACKUP_DIR).resolve()

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

LOG_FILE = Path(BACKUP_DIR) / "backup.log"
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

# -------------------------------------------------------------------
# Logging setup (basic, will adjust in main)
# -------------------------------------------------------------------
logging.basicConfig(
    filename=LOG_FILE,
    filemode='a',
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
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

# -------------------------------------------------------------------
# NFS / backup dir validation
# -------------------------------------------------------------------
def validate_backup_dir(path: Path, interactive=True, telegram_enabled=True):
    """Ensure BACKUP_DIR exists, mounted, writable, and not stale."""
    if not path.exists():
        msg = f"Backup path does not exist: {path}"
        logging.error(msg)
        send_telegram(f"❌ Backup aborted — path missing.\n{msg}", telegram_enabled)
        if interactive: input("Press Enter to exit...")
        raise SystemExit(msg)

    # Walk up to find actual mount
    mount_point = path
    while not os.path.ismount(mount_point) and mount_point != Path("/"):
        mount_point = mount_point.parent

    if not os.path.ismount(mount_point):
        msg = f"NFS mount inactive at {path}"
        logging.error(msg)
        send_telegram(f"❌ Backup aborted — NFS not mounted.\n{msg}", telegram_enabled)
        if interactive: input("Press Enter to exit...")
        raise SystemExit(msg)

    # Stale mount detection
    try:
        statvfs = os.statvfs(path)
        if statvfs.f_blocks == 0 or statvfs.f_bavail == 0:
            msg = f"NFS mount appears stale or empty at {path}"
            logging.error(msg)
            send_telegram(f"❌ Backup aborted — NFS stale.\n{msg}", telegram_enabled)
            if interactive: input("Press Enter to exit...")
            raise SystemExit(msg)
    except OSError as e:
        msg = f"Error accessing backup path: {e}"
        logging.error(msg)
        send_telegram(f"❌ Backup aborted — path error.\n{msg}", telegram_enabled)
        if interactive: input("Press Enter to exit...")
        raise SystemExit(msg)

    # Test write
    test_file = path / f".nfs_probe_{int(time())}"
    try:
        with open(test_file, "w") as f:
            f.write("probe")
        test_file.unlink()
    except Exception as e:
        msg = f"Backup path not writable: {e}"
        logging.error(msg)
        send_telegram(f"❌ Backup aborted — path not writable.\n{msg}", telegram_enabled)
        if interactive: input("Press Enter to exit...")
        raise SystemExit(msg)

    logging.info(f"✅ Backup path verified: {path} (mount: {mount_point})")
    send_telegram(f"✅ Backup path verified: {path}\nMount: {mount_point}", telegram_enabled)

import subprocess

def log_backup_path_type(path: Path, telegram_enabled=True):
    """
    Logs whether the backup path is a local filesystem or NFS.
    Sends Telegram notification as well.
    """
    try:
        # Use 'df -T' to detect filesystem type
        result = subprocess.run(
            ["df", "-T", str(path)],
            capture_output=True,
            text=True,
            check=True
        )
        lines = result.stdout.strip().splitlines()
        if len(lines) >= 2:
            fs_type = lines[1].split()[1]  # second column is FS type
            if fs_type in ("nfs", "nfs4"):
                msg = f"Backup path is NFS: {path} (FS type: {fs_type})"
            else:
                msg = f"Backup path is local: {path} (FS type: {fs_type})"
            logging.info(msg)
            send_telegram(f"ℹ️ {msg}", telegram_enabled)
        else:
            logging.warning(f"Unable to detect filesystem type for {path}")
    except Exception as e:
        logging.warning(f"Filesystem type detection failed for {path}: {e}")


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
    while True:
        print(f"\nSelect {label} by index (0 = ALL, comma-separated, B = Back/Cancel):")
        choice = input("Your choice: ").strip()
        if not choice: continue
        if choice.lower() == "b": return None
        selected = []
        try:
            for part in choice.split(","):
                idx = int(part)
                if idx == 0: return items
                if 1 <= idx <= len(items):
                    selected.append(items[idx-1])
                else: raise ValueError
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
# Cleanup old backups
# -------------------------------------------------------------------
def cleanup_backups(BACKUP_DIR, KEEP_LAST, dry_run=False, telegram_enabled=True):
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
# Backup / Restore
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
        return [volume, outfile, BACKUP_DIR, size, duration, status]

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
# Display helpers
# -------------------------------------------------------------------
def print_Backup_summary(summary_rows, log_only=False):
    if not summary_rows:
        msg = "No backup operations executed."
        logging.info(msg) if log_only else print(msg)
        return
    table = PrettyTable()
    table.field_names = ["🖴 Volume", "File", "Destination", "📦 Size", "🕐 Duration", "🀄️ Status"]
    for row in summary_rows: table.add_row(row)
    output = "\n=== BACKUP SUMMARY ===\n" + table.get_string()
    logging.info(output) if log_only else print(output)

def print_restore_summary(summary_rows, log_only=False):
    if not summary_rows:
        msg = "No restore operations executed."
        logging.info(msg) if log_only else print(msg)
        return
    table = PrettyTable()
    table.field_names = ["🖴 Volume", "File", "Destination", "📦 Size", "🕐 Duration", "🀄️ Status"]
    for row in summary_rows: table.add_row(row)
    output = "\n=== RESTORE SUMMARY ===\n" + table.get_string()
    logging.info(output) if log_only else print(output)

def list_volumes(print_table=True):
    result = subprocess.run(
        ["docker", "volume", "ls", "--format", "{{.Name}}"],
        capture_output=True, text=True, check=True
    )
    volumes = [v for v in result.stdout.strip().split("\n") if v]
    if print_table and volumes:
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
    for idx, b in enumerate(backups, start=1):
        stat = b.stat()
        table.add_row([idx, b.name, BACKUP_DIR, datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"), human_size(stat.st_size)])
    print(table)
    return backups

def main_menu_table():
    table = PrettyTable()
    table.field_names = ["Option", "Action", "Icon"]
    options = [("1", "Backup a volume", "🖴 ->💾"), ("2", "Restore a volume", "🖴 <-💾"), ("0", "Exit", "❌")]
    for opt, action, icon in options: table.add_row([opt, action, icon])
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

    # Adjust logging
    logging.getLogger().handlers.clear()
    if backup_all:
        logging.basicConfig(
            filename=LOG_FILE,
            filemode='a',
            format="%(asctime)s - %(levelname)s - %(message)s",
            level=logging.INFO
        )
        log_only = True
    else:
        logging.basicConfig(
            format="%(asctime)s - %(levelname)s - %(message)s",
            level=logging.DEBUG if verbose else logging.INFO
        )
        log_only = False

    # Validate backup dir (NFS)
    validate_backup_dir(BACKUP_DIR, interactive=not backup_all, telegram_enabled=telegram_enabled)
    log_backup_path_type(Path(BACKUP_DIR), telegram_enabled=telegram_enabled)

    # Non-interactive full backup
    if backup_all:
        volumes = list_volumes(print_table=False)
        summary = [backup_volume(v, BACKUP_DIR, dry_run, telegram_enabled) for v in volumes]
        print_Backup_summary(summary, log_only=log_only)
        exit(0)

    # Interactive menu
    while True:
        main_menu_table()
        choice = input("Select option: ").strip()
        if choice == "1":
            clear_screen()
            volumes = list_volumes()
            selected_volumes = select_with_all_option(volumes, "volume")
            if selected_volumes is None: continue
            summary = [backup_volume(v, BACKUP_DIR, dry_run, telegram_enabled) for v in selected_volumes]
            print_Backup_summary(summary, log_only=log_only)
        elif choice == "2":
            clear_screen()
            backups = list_backups(BACKUP_DIR)
            if not backups: continue
            selected_backups = select_with_all_option(backups, "backup")
            if selected_backups is None: continue
            summary = [restore_backup(b, BACKUP_DIR, dry_run, telegram_enabled) for b in selected_backups]
            print_restore_summary(summary, log_only=log_only)
        elif choice == "0":
            print("Exiting workflow. Operational cycle terminated.")
            break
        else:
            print("Invalid option.")

if __name__ == "__main__":
    main()

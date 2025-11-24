##  **Docker Volume Backup Manager**

A fully automated backup and restore framework engineered for Docker volumes.
The system provides a consistent, policy-driven approach to data protection, enabling predictable recovery workflows and simplified lifecycle governance.
The architecture is built for environments that prioritize reliability, auditability, and repeatable operations.

---

## **🚀 Value Proposition**

This utility provides an end-to-end control plane for Docker volume backups with:
- Deterministic backup workflows
- Per-volume retention policies (KEEP_LAST)
- Rich operational telemetry (summary tables, logs, Telegram alerts)
- System-safe dry-run mode for change-validation
- Restore flow with user-driven selection and audit-ready reporting
- Zero dependencies inside containers—optimized through a debian:stable-slim extraction layer

The net impact: reduced operational overhead, predictable RTO, and elevated reliability posture.

---

## **📦 Features**

- Automated TAR-based snapshots of Docker volumes
- Independent retention per volume
- Pause/resume of containers consuming a target volume
- Full-screen menu UX for backup/restore
- Summary tables at the end of each operation
- Optional Telegram integration for fleet-wide notifications
- Optional run silent with --backup-all flag
- Compatible with Cron, GitHub Actions, Ansible, systemd timers, or manual triggers
--- 

## **🧩 Environment Variables**

The runtime behavior is driven by .env or shell-provided values:
| Variable           | Description                                                                   |
| ------------------ | ----------------------------------------------------------------------------- |
| `BACKUP_DIR`       | Target directory where `.tar.gz` archives land                                |
| `LOG_FILE`         | Taget diectoy where backup.log lives                                          |
| `KEEP_LAST`        | Number of backups to keep *per volume*                                        |
| `TELEGRAM_ENABLED` | Set it YES if you are going to use Telegram, by default is FALSE              |
| `TELEGRAM_TOKEN`   | Optional — enables Telegram notifications                                     |
| `TELEGRAM_CHAT_ID` | Optional — destination chat for alerts                                        |

---
## **🛠 Installation**

Clone the repo:
```bash
git clone https://github.com/funmicra/Volumes-Backup-Restore.git
cd repo
pip install -r requirements.txt
```
Populate your .env:

```bash
BACKUP_DIR=/path/to/backup
LOG_FILE=
KEEP_LAST=3
TELEGRAM_ENABLED=yes ()
TELEGRAM_TOKEN=
TELEGRAM_CHAT_ID=
```
---

## **▶️ Usage**
Start the interactive manager
```bash
python3 -m venv venv
pip install -r requirements.txt
python3 backup-manager.py
```
You’ll be presented with an operational menu:
```
1. Backup volumes
2. Restore backup
0. Exit
```
---
## **🔄 Backup Flow**
Inside the backup workflow, the manager will:
- Identify the volume
- Pause any dependent containers
- Stream out a .tar.gz archive
- Execute per-volume retention cleanup
- Render a post-operation summary table
- Dispatch Telegram notifications (if enabled)
---
## **🧯 Restore Flow**
The restore engine provides:
- Backup list with volume and timestamp
- Selection-driven restore
- Container pause/resume
- Operational summary table
- Notification pipeline
---
## **📊 Summary Tables**
At the end of each operation, the system generates a clean insights dashboard including:
- Volume
- Backup file
- File size
- Duration
- Actions taken (cleanup, skips, dry-run operations)
---
## **🔐 Security Posture**
- No privileged Docker daemon access beyond volume mounts
- No external binaries except tar inside an ephemeral container
- Zero persistent state outside the backup dir
- Optional isolation-friendly dry-run lifecycle
---
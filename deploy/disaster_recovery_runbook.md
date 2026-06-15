# Nuke AI Collaborator - Disaster Recovery Runbook
This runbook defines the procedures for backing up, maintaining, and restoring the Nuke AI Collaborator deployment, including its split SQLite databases (central and group-private) and workspace volumes.

---

## 1. Architecture Context

Nuke AI Collaborator uses **Project-Cell Isolation (V3)**:
1. **Central DB** (`global_db()`): Contains global tables (`users`, `groups`, `members`, `role_templates`, `unread_counts`).
2. **Group DBs** (`get_db()`): Located in per-group storage directories. Contains group-specific tables (`messages`, `agent_sessions`, `session_events`, `workflow_state`, `group_locks`).
3. **Workspace Volume**: User files, logs, and temporary work directories.

Since SQLite runs in **WAL (Write-Ahead Logging) mode**, simple filesystem `cp` commands on active databases can result in corrupt backups. You must use `sqlite3`'s `.backup` API to ensure safe atomic snapshotting.

---

## 2. Backup Strategy

### A. Central Database Backup
To perform an atomic, consistent online backup of the central database:
```bash
sqlite3 /var/lib/nuke-ai-collaborator/chat.db ".backup /var/backups/nuke/central_chat_$(date +%F_%H%M%S).db"
```

### B. Group-Private Databases Backup
Iterate through each group database directory to back them up:
```bash
# Locate all active group databases and back them up
find /var/lib/nuke-ai-collaborator/workspaces/ -name "chat.db" | while read -r db_path; do
    group_dir=$(dirname "$db_path")
    group_id=$(basename "$group_dir")
    backup_target="/var/backups/nuke/group_${group_id}_chat_$(date +%F_%H%M%S).db"
    sqlite3 "$db_path" ".backup $backup_target"
done
```

### C. Workspace Files Backup
Compress the workspace files (excluding temporary files and caches):
```bash
tar --exclude="*.log" --exclude="tmp*" -czf /var/backups/nuke/workspaces_$(date +%F_%H%M%S).tar.gz -C /var/lib/nuke-ai-collaborator workspaces
```

---

## 3. Automated Backup Script

Save the following script as `/opt/nuke-ai-collaborator/scripts/backup.sh` and make it executable (`chmod +x`).

```bash
#!/bin/bash
set -eo pipefail

# Configurations
SRC_DIR="/var/lib/nuke-ai-collaborator"
BACKUP_DIR="/var/backups/nuke"
RETENTION_DAYS=30
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

echo "[$(date)] Starting Nuke AI Collaborator Backup..."
mkdir -p "$BACKUP_DIR"

# 1. Backup Central Database
echo "Backing up central database..."
sqlite3 "$SRC_DIR/chat.db" ".backup $BACKUP_DIR/central_chat_$TIMESTAMP.db"

# 2. Backup Group-Private Databases
echo "Backing up group databases..."
find "$SRC_DIR/workspaces/" -name "chat.db" | while read -r db_path; do
    group_dir=$(dirname "$db_path")
    group_id=$(basename "$group_dir")
    sqlite3 "$db_path" ".backup $BACKUP_DIR/group_${group_id}_chat_$TIMESTAMP.db"
done

# 3. Backup Workspaces (Excluding ephemeral files)
echo "Compressing workspaces..."
tar --exclude="*.log" --exclude="*.pyc" --exclude="__pycache__" \
    -czf "$BACKUP_DIR/workspaces_$TIMESTAMP.tar.gz" \
    -C "$SRC_DIR" workspaces

# 4. Enforce Retention Policy (Clean up backups older than 30 days)
echo "Enforcing retention policy..."
find "$BACKUP_DIR" -type f -mtime +$RETENTION_DAYS -delete

echo "[$(date)] Backup completed successfully."
```

### Configure Cron Job
Add a cron job to run the backup daily at 2:00 AM:
```cron
0 2 * * * /opt/nuke-ai-collaborator/scripts/backup.sh >> /var/log/nuke-backup.log 2>&1
```

---

## 4. Disaster Recovery & Restoration

In the event of database corruption, system crash, or data loss, follow this procedure to restore services.

### Step 1: Stop the Application
```bash
sudo systemctl stop nuke-collaborator.service
```

### Step 2: Identify the Target Backups
Locate the desired backup files in `/var/backups/nuke/`.
For example:
* Central DB: `central_chat_20260615_020000.db`
* Workspaces: `workspaces_20260615_020000.tar.gz`
* Group DBs (if restoring specific groups): `group_9_chat_20260615_020000.db`

### Step 3: Restore the Central Database
Move the corrupted central database to a backup directory and restore:
```bash
mv /var/lib/nuke-ai-collaborator/chat.db /var/lib/nuke-ai-collaborator/chat.db.corrupt
cp /var/backups/nuke/central_chat_20260615_020000.db /var/lib/nuke-ai-collaborator/chat.db
chown nuke:nuke /var/lib/nuke-ai-collaborator/chat.db
chmod 660 /var/lib/nuke-ai-collaborator/chat.db
```

### Step 4: Restore Workspaces
Restore the code and workspace structures:
```bash
mv /var/lib/nuke-ai-collaborator/workspaces /var/lib/nuke-ai-collaborator/workspaces.corrupt
tar -xzf /var/backups/nuke/workspaces_20260615_020000.tar.gz -C /var/lib/nuke-ai-collaborator/
chown -R nuke:nuke /var/lib/nuke-ai-collaborator/workspaces
```

### Step 5: Restore Group-Private Databases (If needed)
If group DBs were corrupted or need to be aligned with the restored snapshot, copy them back into the workspace directory:
```bash
# Example for restoring Group 9
cp /var/backups/nuke/group_9_chat_20260615_020000.db /var/lib/nuke-ai-collaborator/workspaces/9/chat.db
chown nuke:nuke /var/lib/nuke-ai-collaborator/workspaces/9/chat.db
chmod 660 /var/lib/nuke-ai-collaborator/workspaces/9/chat.db
```

### Step 6: Verify Database Integrity
Run integrity checks on all restored databases:
```bash
sqlite3 /var/lib/nuke-ai-collaborator/chat.db "PRAGMA integrity_check;"
find /var/lib/nuke-ai-collaborator/workspaces/ -name "chat.db" -exec sqlite3 {} "PRAGMA integrity_check;" \;
```
Each check should return `ok`.

### Step 7: Restart the Service
```bash
sudo systemctl start nuke-collaborator.service
```

### Step 8: Verify Service Status & Health
Check service logs and query the health endpoints:
```bash
sudo systemctl status nuke-collaborator.service
curl -f http://localhost:8000/health/liveness
curl -f http://localhost:8000/health/readiness
```
Both health calls must return `200 OK`.

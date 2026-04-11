# Deployment Security Guide

## Encryption at Rest

### Why Encryption at Rest Is Required

Memory MCP Server stores sensitive user data in two locations:

- **ChromaDB** (`/data/chroma/`): All user memories, embeddings, and metadata
- **SQLite** (`/data/accounts.db`): User accounts, API key hashes, sessions, consent records

If the host filesystem is compromised, all data is exposed unless the underlying storage is encrypted.

> **MANDATORY for production deployments**: All data directories MUST reside on encrypted filesystems.

### Option 1: LUKS/dm-crypt (Recommended for Linux Servers)

Full-disk or partition-level encryption using LUKS is the standard approach for Linux servers.

```bash
# 1. Create an encrypted partition (one-time setup)
sudo cryptsetup luksFormat /dev/sdX
sudo cryptsetup luksOpen /dev/sdX memory-data
sudo mkfs.ext4 /dev/mapper/memory-data

# 2. Mount the encrypted volume
sudo mkdir -p /mnt/memory-data
sudo mount /dev/mapper/memory-data /mnt/memory-data

# 3. Set Docker volume to use the encrypted mount
# In docker-compose.yaml:
#   volumes:
#     - /mnt/memory-data:/data
```

For automated unlock at boot, use a keyfile stored on a separate encrypted USB or use a key management service (e.g., HashiCorp Vault, AWS KMS).

### Option 2: Docker Volume with dm-crypt

```bash
# Create an encrypted Docker volume
docker volume create --driver local \
  --opt type=tmpfs \
  --opt device=tmpfs \
  --opt o=size=10g \
  memory-encrypted-data

# For persistent encrypted storage, use a LUKS-backed block device:
# 1. Create a LUKS container file
dd if=/dev/urandom of=/srv/memory-data.img bs=1M count=10240
sudo cryptsetup luksFormat /srv/memory-data.img
sudo cryptsetup luksOpen /srv/memory-data.img memory-vol
sudo mkfs.ext4 /dev/mapper/memory-vol

# 2. Mount and use as Docker bind mount
sudo mkdir -p /mnt/memory-vol
sudo mount /dev/mapper/memory-vol /mnt/memory-vol
```

### Option 3: Cloud Provider Encrypted Volumes

| Provider | Feature | Notes |
|----------|---------|-------|
| AWS | EBS encryption (AES-256) | Enable by default in account settings |
| GCP | Persistent Disk encryption | Enabled by default; optional CMEK |
| Oracle Cloud | Block Volume encryption | Enabled by default |
| Azure | Managed Disk encryption | Enabled by default; optional CMEK |

For Oracle Cloud Free Tier (current deployment target):
```bash
# Oracle Block Volumes are encrypted by default with Oracle-managed keys.
# Verify encryption status:
oci bv volume get --volume-id <ocid> --query 'data."is-auto-tune-enabled"'
```

### Verification

The server logs a warning at startup if encryption cannot be verified:

```
WARNING: SECURITY: Ensure data directory (/data) is on an encrypted filesystem for production use.
```

This is an informational reminder. The server cannot programmatically verify encryption status on all platforms, so operators must confirm this independently.

## SQLite WAL Mode Security Considerations

The accounts database uses SQLite WAL (Write-Ahead Logging) mode for better concurrency. This creates additional files:

- `accounts.db-wal` — Write-ahead log (contains recent writes in plaintext)
- `accounts.db-shm` — Shared memory file

**Important**: Both `-wal` and `-shm` files contain sensitive data and must be:
1. On the same encrypted filesystem as the main database
2. Included in backups (see [BACKUP_STRATEGY.md](BACKUP_STRATEGY.md))
3. Protected with the same file permissions (`chmod 600`)

```bash
# Verify file permissions
ls -la /data/accounts.db*
# Expected: -rw------- 1 appuser appgroup ... accounts.db
#           -rw------- 1 appuser appgroup ... accounts.db-wal
#           -rw------- 1 appuser appgroup ... accounts.db-shm
```

## File Permission Hardening

```bash
# Data directory: only the application user should have access
chown -R appuser:appgroup /data/
chmod 700 /data/
chmod 600 /data/accounts.db*
chmod 700 /data/chroma/

# In Docker, the container runs as non-root by default.
# Verify with: docker exec memory-mcp-server id
```

## Network Security

- The MCP server binds to `0.0.0.0:8321` by default.
- In production, always place behind a reverse proxy (nginx/Caddy) with TLS.
- Never expose port 8321 directly to the internet without TLS.
- Use `MEMORY_MCP_CORS_ORIGINS` environment variable to restrict CORS origins.

## Checklist

- [ ] Data directory is on an encrypted filesystem (LUKS, cloud-encrypted volume, etc.)
- [ ] File permissions are restricted (700 for dirs, 600 for files)
- [ ] SQLite WAL files are on the same encrypted volume
- [ ] TLS termination is configured (reverse proxy or cloud load balancer)
- [ ] CORS origins are restricted to known domains
- [ ] Regular backups are configured (see [BACKUP_STRATEGY.md](BACKUP_STRATEGY.md))
- [ ] API keys are rotated periodically
- [ ] Server logs do not contain sensitive data (verified)

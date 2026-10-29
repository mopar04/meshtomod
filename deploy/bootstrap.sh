#!/usr/bin/env bash
# One-time setup of a fresh Ubuntu 24.04 server for MeshToMod (docs/DEPLOY.md).
# DigitalOcean runs it as root from the droplet's initialization script:
#   #!/bin/bash
#   curl -fsSL https://raw.githubusercontent.com/mopar04/meshtomod/main/deploy/bootstrap.sh | bash
# It installs Docker, starts the site from main, and turns on the updater that deploys each
# new commit on main once CI has passed. Safe to run again. Output: /var/log/cloud-init-output.log
set -euo pipefail

REPO=${MESHTOMOD_REPO:-https://github.com/mopar04/meshtomod.git}
BRANCH=${MESHTOMOD_BRANCH:-main}
DIR=${MESHTOMOD_DIR:-/opt/meshtomod}
export DEBIAN_FRONTEND=noninteractive

# SSH by key only. DigitalOcean's web console keeps working: it uses keys from the metadata
# service, not a password.
cat > /etc/ssh/sshd_config.d/10-meshtomod.conf <<'CONF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin prohibit-password
CONF
systemctl reload ssh || true

# 4 GB swap: a big scan slows down instead of the kernel killing a container.
if ! swapon --show | grep -q /swapfile; then
  fallocate -l 4G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

# Container logs rotate instead of filling the disk.
mkdir -p /etc/docker
cat > /etc/docker/daemon.json <<'CONF'
{ "log-driver": "local", "log-opts": { "max-size": "10m", "max-file": "3" } }
CONF

apt-get update -q
apt-get install -y -q ca-certificates curl git jq unattended-upgrades

# Docker Engine and Compose from Docker's own repository.
if ! command -v docker >/dev/null; then
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  # shellcheck source=/dev/null
  codename=$(. /etc/os-release && echo "$VERSION_CODENAME")
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc]" \
    "https://download.docker.com/linux/ubuntu $codename stable" > /etc/apt/sources.list.d/docker.list
  apt-get update -q
  apt-get install -y -q docker-ce docker-ce-cli containerd.io docker-buildx-plugin \
    docker-compose-plugin
fi
systemctl enable --now docker

# The code, and settings from .env.example (meshtomod.com, redirects, 8 GB sizing).
if [ ! -d "$DIR/.git" ]; then
  git clone --branch "$BRANCH" "$REPO" "$DIR"
fi
cd "$DIR"
if [ ! -f .env ]; then
  cp .env.example .env
  chmod 600 .env
fi

docker compose -f docker-compose.prod.yml up -d --build

# Updater: every 5 minutes, deploy main's newest commit once its CI checks have passed.
cat > /etc/systemd/system/meshtomod-update.service <<UNIT
[Unit]
Description=Deploy new MeshToMod commits from $BRANCH
After=docker.service network-online.target
Wants=network-online.target

[Service]
Type=oneshot
Environment=MESHTOMOD_DIR=$DIR MESHTOMOD_BRANCH=$BRANCH
ExecStart=$DIR/deploy/update.sh
UNIT
cat > /etc/systemd/system/meshtomod-update.timer <<'UNIT'
[Unit]
Description=Check for new MeshToMod commits every 5 minutes

[Timer]
OnBootSec=5min
OnUnitActiveSec=5min

[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload
systemctl enable --now meshtomod-update.timer

echo "MeshToMod is starting. Caddy gets HTTPS certificates once DNS points here."

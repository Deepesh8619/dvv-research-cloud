#!/bin/bash
# First boot of a client's server (runs once, as root). Prepares the machine;
# the app itself is installed by the steps in deploy/aws/README.md.
set -euo pipefail

# 2 GB swap: headroom for PDF extraction and SPSS/Excel exports on a 4 GB box,
# so a spike slows down instead of being killed for memory.
fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab

curl -fsSL https://get.docker.com | sh        # Docker, official installer
usermod -aG docker ubuntu

# A read-only deploy key: the repo is private. Its public half is added to
# GitHub (repo -> Settings -> Deploy keys, read-only); it can pull code and
# nothing else, and removing it cuts this one server off.
sudo -u ubuntu ssh-keygen -t ed25519 -N '' -f /home/ubuntu/.ssh/dvv_deploy -C "dvv-deploy-$(hostname)"
cat >> /home/ubuntu/.ssh/config <<'CFG'
Host github.com
  IdentityFile ~/.ssh/dvv_deploy
  IdentitiesOnly yes
CFG
chown ubuntu:ubuntu /home/ubuntu/.ssh/config && chmod 600 /home/ubuntu/.ssh/config
mkdir -p /opt/dvv && chown ubuntu:ubuntu /opt/dvv

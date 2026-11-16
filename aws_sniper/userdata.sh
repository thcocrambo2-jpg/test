#!/bin/bash
# First-boot script. sniper.py fills in the four values below from aws_sniper/.env.
exec > /var/log/ember-userdata.log 2>&1
set -x

cat >/etc/ember.env <<'EOF'
EMBER_LICENSE_KEY={{EMBER_LICENSE_KEY}}
EMBER_NODE_TAG={{EMBER_NODE_TAG}}
HF_TOKEN={{HF_TOKEN}}
CIVITAI_TOKEN={{CIVITAI_TOKEN}}
EOF
chmod 600 /etc/ember.env
mkdir -p /workspace

systemctl enable --now docker
until docker info >/dev/null 2>&1; do sleep 2; done

docker run -d --name ember --hostname ember-aws --restart unless-stopped \
  --gpus all --shm-size 2g --stop-timeout 60 \
  --env-file /etc/ember.env \
  -p 7860:7860 -v /workspace:/workspace \
  emberai/ember:latest

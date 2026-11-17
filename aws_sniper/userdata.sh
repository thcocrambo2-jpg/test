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

# Copy the app's link to the serial console, where the sniper page reads it
# (EC2 GetConsoleOutput), so it needs no SSH. Only lines from the container's
# current start count, so a restart never shows the previous link; the link
# is repeated every 10 minutes to stay in the console's recent output.
cat >/usr/local/bin/ember-url <<'EOF'
#!/bin/bash
last=""; said=0
while true; do
  started=$(docker inspect ember 2>/dev/null | grep -m1 '"StartedAt"' | cut -d'"' -f4)
  url=""
  if [ -n "$started" ]; then
    url=$(docker logs --since "$started" ember 2>&1 | grep -o 'OPEN THE UI HERE: https://[^ ]*' | tail -1 | cut -d' ' -f5)
  fi
  now=$(date +%s)
  if [ -n "$url" ] && { [ "$url" != "$last" ] || [ $((now - said)) -ge 600 ]; }; then
    echo "EMBER_URL $url" > /dev/ttyS0
    last="$url"; said=$now
  fi
  sleep 15
done
EOF
chmod 755 /usr/local/bin/ember-url
cat >/etc/systemd/system/ember-url.service <<'EOF'
[Unit]
Description=Copy the Ember link to the serial console
After=docker.service

[Service]
ExecStart=/usr/local/bin/ember-url
Restart=always

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now ember-url

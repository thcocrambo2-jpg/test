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

# Models and Docker's images live on the instance's local NVMe SSD (several
# GB/s), not the network root disk (EBS, ~250 MB/s on g7e.2xlarge). The DLAMI's
# dlami-nvme.service mounts that SSD at /opt/dlami/nvme on every boot; it is
# blank after a stop, so a start pulls the image and downloads models again.
cat >/usr/local/bin/ember-nvme <<'EOF'
#!/bin/bash
set -e
N=/opt/dlami/nvme
mountpoint -q "$N"
for d in containerd docker workspace; do
  mkdir -p "$N/$d" "/var/lib/$d"
done
mkdir -p /workspace
mountpoint -q /var/lib/containerd || mount --bind "$N/containerd" /var/lib/containerd
mountpoint -q /var/lib/docker     || mount --bind "$N/docker"     /var/lib/docker
mountpoint -q /workspace          || mount --bind "$N/workspace"  /workspace
EOF
chmod 755 /usr/local/bin/ember-nvme
cat >/etc/systemd/system/ember-nvme.service <<'EOF'
[Unit]
Description=Put Docker and /workspace on the local NVMe SSD
Requires=dlami-nvme.service
After=dlami-nvme.service
Before=containerd.service docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/bin/ember-nvme

[Install]
WantedBy=multi-user.target
EOF
for svc in containerd docker; do
  mkdir -p /etc/systemd/system/$svc.service.d
  printf '[Unit]\nRequires=ember-nvme.service\nAfter=ember-nvme.service\n' \
    >/etc/systemd/system/$svc.service.d/ember-nvme.conf
done

# Starts the app on every boot when its container is missing (first boot, and
# after a stop, since the SSD comes back blank). After a reboot the container
# is still there and --restart brings it back.
cat >/usr/local/bin/ember-start <<'EOF'
#!/bin/bash
until docker info >/dev/null 2>&1; do sleep 2; done
docker inspect ember >/dev/null 2>&1 && exit 0
docker run -d --name ember --hostname ember-aws --restart unless-stopped \
  --gpus all --shm-size 2g --stop-timeout 60 \
  --env-file /etc/ember.env \
  -p 7860:7860 -v /workspace:/workspace \
  emberai/ember:latest
EOF
chmod 755 /usr/local/bin/ember-start
cat >/etc/systemd/system/ember-start.service <<'EOF'
[Unit]
Description=Start the Ember container if it is missing
Requires=docker.service
After=docker.service network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/bin/ember-start
TimeoutStartSec=0

[Install]
WantedBy=multi-user.target
EOF

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

# This first boot: Docker may already be up on the root disk, so stop it,
# move its folders onto the SSD, and start everything in order.
systemctl daemon-reload
systemctl stop docker.socket docker containerd || true
systemctl enable ember-nvme ember-start ember-url docker containerd
systemctl start ember-nvme
systemctl start containerd docker
systemctl start ember-start
systemctl start ember-url

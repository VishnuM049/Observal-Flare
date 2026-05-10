#!/bin/bash
# Deploy an Observal instance on a Flare-managed EC2 instance.
# Executed via AWS SSM SendCommand. All vars are injected by the provisioner.
#
# Required env vars: SITE_DOMAIN, DEPLOY_TYPE, DEPLOY_REF, RESOLVED_SHA, ENV_CONTENT
set -euo pipefail
exec > /var/log/flare-deploy.log 2>&1

echo "=== Flare deploy: ${SITE_DOMAIN} at ${RESOLVED_SHA} ==="
echo "Deploy type: ${DEPLOY_TYPE}, ref: ${DEPLOY_REF}"
date -u

# Install Docker if needed
if ! command -v docker &>/dev/null; then
    echo "Installing Docker..."
    apt-get update -qq && apt-get install -y -qq docker.io docker-compose-v2
    systemctl enable docker && systemctl start docker
fi

# Install certbot if needed
if ! command -v certbot &>/dev/null; then
    echo "Installing certbot..."
    apt-get install -y -qq certbot
fi

# Clone Observal
rm -rf /opt/observal
echo "Cloning Observal..."
git clone --quiet https://github.com/BlazeUp-AI/Observal.git /opt/observal
cd /opt/observal

# Checkout the correct ref
if [[ "${DEPLOY_TYPE}" == "pr" ]]; then
    git fetch origin "+refs/pull/${DEPLOY_REF}/head:pr-${DEPLOY_REF}"
    git checkout "pr-${DEPLOY_REF}"
else
    git fetch origin "${RESOLVED_SHA}"
    git checkout "${RESOLVED_SHA}"
fi
echo "Checked out $(git rev-parse HEAD)"

# Write .env
cat > /opt/observal/.env << 'ENVEOF'
${ENV_CONTENT}
ENVEOF

# Configure Nginx for this domain
sed -i "s/server_name .*/server_name ${SITE_DOMAIN};/" /opt/observal/docker/nginx.production.conf
sed -i "s|ssl_certificate .*|ssl_certificate /etc/letsencrypt/live/${SITE_DOMAIN}/fullchain.pem;|" /opt/observal/docker/nginx.production.conf
sed -i "s|ssl_certificate_key .*|ssl_certificate_key /etc/letsencrypt/live/${SITE_DOMAIN}/privkey.pem;|" /opt/observal/docker/nginx.production.conf

# Obtain TLS certificate
if ! [ -d "/etc/letsencrypt/live/${SITE_DOMAIN}" ]; then
    echo "Obtaining TLS certificate..."
    certbot certonly --standalone -d "${SITE_DOMAIN}" --non-interactive --agree-tos -m admin@observal.io
fi

# Start services
echo "Starting Docker Compose..."
cd /opt/observal
docker compose -f docker/docker-compose.yml -f docker/docker-compose.production.yml up -d

# Wait for healthy
echo "Waiting for health check..."
for i in $(seq 1 60); do
    if curl -sf http://localhost:8000/readyz > /dev/null 2>&1; then
        echo "=== Site healthy after ${i}0 seconds ==="
        exit 0
    fi
    sleep 10
done

echo "=== ERROR: Site did not become healthy within 600 seconds ==="
exit 1

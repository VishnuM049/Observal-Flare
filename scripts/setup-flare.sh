#!/bin/bash
# Bootstrap Flare itself on a fresh EC2 instance.
# Run once on the Flare host.
set -euo pipefail

echo "=== Setting up Flare ==="

# Install Docker
apt-get update && apt-get install -y docker.io docker-compose-v2
systemctl enable docker && systemctl start docker

# Install Terraform
wget -qO- https://releases.hashicorp.com/terraform/1.9.8/terraform_1.9.8_linux_amd64.zip | funzip > /usr/local/bin/terraform
chmod +x /usr/local/bin/terraform

# Clone Flare
git clone https://github.com/BlazeUp-AI/Observal-Flare.git /opt/flare
cd /opt/flare

# Copy .env (user must edit this with real values)
if [ ! -f .env ]; then
    cp .env.example .env
    echo "Created .env from template — edit it with your actual values before starting."
fi

# Start Flare
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d

echo "=== Flare is starting. Run 'docker compose logs -f' to monitor. ==="

terraform {
  required_version = ">= 1.5"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # IMPORTANT: Backend config is passed at init time by server/gcp_terraform.py.
  # Do NOT run `terraform init` in this directory without -backend-config flags:
  #   -backend-config="bucket=<gcp_terraform_state_bucket>"
  #   -backend-config="prefix=sites/<site_name>"
  backend "gcs" {}
}

provider "google" {
  project = var.project
  region  = var.region
  zone    = var.zone
}

# AWS provider for Route53 DNS (observal.io is managed in Route53)
provider "aws" {}

resource "google_compute_instance" "site" {
  name         = "flare-site-${var.site_name}"
  machine_type = var.machine_type
  zone         = var.zone

  boot_disk {
    initialize_params {
      image = "ubuntu-os-cloud/ubuntu-2404-lts-amd64"
      size  = 50
      type  = "pd-balanced"
    }
  }

  network_interface {
    network = "default"
    access_config {
      nat_ip = google_compute_address.site.address
    }
  }

  service_account {
    email  = google_service_account.site.email
    scopes = ["cloud-platform"]
  }

  metadata = {
    enable-oslogin = "TRUE"
  }

  metadata_startup_script = <<-EOF
    #!/bin/bash
    apt-get install -y ca-certificates curl
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" > /etc/apt/sources.list.d/docker.list
    apt-get update
    apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    systemctl enable docker && systemctl start docker
    apt-get install -y certbot git
    touch /var/run/flare-startup-complete
  EOF

  tags = ["flare-site", "http-server", "https-server"]

  labels = {
    managed-by = "flare"
    site       = var.site_name
  }
}

resource "google_compute_address" "site" {
  name   = "flare-site-${var.site_name}"
  region = var.region

  labels = {
    managed-by = "flare"
    site       = var.site_name
  }
}

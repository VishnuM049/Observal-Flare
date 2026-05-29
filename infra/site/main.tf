terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # IMPORTANT: Backend config is passed at init time by server/terraform.py.
  # Do NOT run `terraform init` in this directory without -backend-config flags:
  #   -backend-config="bucket=flare-terraform-state"
  #   -backend-config="key=sites/<site_name>/terraform.tfstate"
  #   -backend-config="region=<aws_region>"
  #   -backend-config="dynamodb_table=flare-terraform-locks"
  # Running init without these flags will create LOCAL state, causing drift
  # and potential duplicate resources on next apply.
  backend "s3" {}
}

provider "aws" {}

data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"] # Canonical

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

resource "aws_instance" "site" {
  ami                    = data.aws_ami.ubuntu.id
  instance_type          = var.instance_size
  subnet_id              = var.subnet_id != "" ? var.subnet_id : null
  vpc_security_group_ids = [aws_security_group.site.id]
  iam_instance_profile   = aws_iam_instance_profile.site.name

  user_data = <<-EOF
    #!/bin/bash
    snap install amazon-ssm-agent --classic
    systemctl enable snap.amazon-ssm-agent.amazon-ssm-agent.service
    systemctl start snap.amazon-ssm-agent.amazon-ssm-agent.service

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

  root_block_device {
    volume_size = 50
    volume_type = "gp3"
    encrypted   = true
  }

  tags = {
    Name        = "flare-site-${var.site_name}"
    ManagedBy   = "flare"
    Site        = var.site_name
    Environment = "flare"
  }
}

resource "aws_eip" "site" {
  instance = aws_instance.site.id
  domain   = "vpc"

  tags = {
    Name      = "flare-eip-${var.site_name}"
    ManagedBy = "flare"
  }
}

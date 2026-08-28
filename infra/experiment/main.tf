terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # Backend configuration is supplied by server/experiment_terraform.py.
  backend "s3" {}
}

provider "aws" {
  region = var.aws_region
}

data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

data "aws_ssm_parameter" "amazon_linux" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}

resource "aws_instance" "experiment" {
  count = var.instance_count

  ami                         = data.aws_ssm_parameter.amazon_linux.value
  instance_type               = var.instance_type
  subnet_id                   = sort(data.aws_subnets.default.ids)[0]
  associate_public_ip_address = true
  vpc_security_group_ids      = [aws_security_group.experiment.id]
  iam_instance_profile        = aws_iam_instance_profile.experiment.name

  metadata_options {
    http_endpoint = "enabled"
    http_tokens   = "required"
  }

  user_data = <<-EOF
    #!/bin/bash
    set -e
    systemctl enable amazon-ssm-agent
    systemctl start amazon-ssm-agent
    shutdown -h +${var.safety_shutdown_minutes}
  EOF

  root_block_device {
    volume_size = 10
    volume_type = "gp3"
    encrypted   = true
  }

  tags = {
    Name         = "flare-experiment-${var.experiment_id}-${count.index + 1}"
    ManagedBy    = "flare"
    Purpose      = "ghcr-download-experiment"
    ExperimentId = var.experiment_id
    FleetIndex   = tostring(count.index)
    ExpiresAt    = var.expires_at
  }
}

terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

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

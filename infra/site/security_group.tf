resource "aws_security_group" "site" {
  name        = "flare-site-${var.site_name}"
  description = "Security group for Flare site ${var.site_name}"
  vpc_id      = var.vpc_id != "" ? var.vpc_id : null

  ingress {
    description = "HTTP"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "HTTPS"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name      = "flare-sg-${var.site_name}"
    ManagedBy = "flare"
  }
}

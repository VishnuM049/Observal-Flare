resource "aws_security_group" "experiment" {
  name        = "flare-experiment-${var.experiment_id}"
  description = "No-ingress security group for isolated Flare GHCR experiment"
  vpc_id      = data.aws_vpc.default.id

  # Public GHCR, GitHub release downloads, DNS, and SSM all require outbound
  # connectivity. There are deliberately no ingress rules.
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name         = "flare-experiment-${var.experiment_id}"
    ManagedBy    = "flare"
    Purpose      = "ghcr-download-experiment"
    ExperimentId = var.experiment_id
  }
}

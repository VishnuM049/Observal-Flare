resource "aws_iam_role" "experiment" {
  name = "flare-experiment-${var.experiment_id}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = {
        Service = "ec2.amazonaws.com"
      }
    }]
  })

  tags = {
    ManagedBy    = "flare"
    Purpose      = "ghcr-download-experiment"
    ExperimentId = var.experiment_id
  }
}

resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.experiment.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "experiment" {
  name = "flare-experiment-${var.experiment_id}"
  role = aws_iam_role.experiment.name
}

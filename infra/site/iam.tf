resource "aws_iam_role" "site" {
  name = "flare-site-${var.site_name}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "ec2.amazonaws.com"
        }
      }
    ]
  })

  tags = {
    ManagedBy = "flare"
  }
}

resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.site.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "site" {
  name = "flare-site-${var.site_name}"
  role = aws_iam_role.site.name
}

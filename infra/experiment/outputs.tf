output "instance_ids" {
  description = "Disposable experiment fleet EC2 instance IDs in fleet-index order"
  value       = aws_instance.experiment[*].id
}

# Backwards-compatible output for single-instance tooling.
output "instance_id" {
  description = "First disposable experiment EC2 instance ID"
  value       = aws_instance.experiment[0].id
}

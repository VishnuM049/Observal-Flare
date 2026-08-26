output "instance_id" {
  description = "Disposable experiment EC2 instance ID"
  value       = aws_instance.experiment.id
}

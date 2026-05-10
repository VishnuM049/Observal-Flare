output "instance_id" {
  description = "EC2 instance ID"
  value       = aws_instance.site.id
}

output "public_ip" {
  description = "Elastic IP address"
  value       = aws_eip.site.public_ip
}

output "eip_id" {
  description = "Elastic IP allocation ID"
  value       = aws_eip.site.id
}

output "security_group_id" {
  description = "Security group ID"
  value       = aws_security_group.site.id
}

output "dns_fqdn" {
  description = "Fully qualified domain name"
  value       = aws_route53_record.site.fqdn
}

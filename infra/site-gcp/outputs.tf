output "instance_id" {
  description = "GCE instance name (used as instance_id in Flare)"
  value       = google_compute_instance.site.name
}

output "public_ip" {
  description = "Static external IP address"
  value       = google_compute_address.site.address
}

output "zone" {
  description = "GCE zone where the instance was created"
  value       = google_compute_instance.site.zone
}

output "dns_fqdn" {
  description = "Fully qualified domain name"
  value       = aws_route53_record.site.fqdn
}

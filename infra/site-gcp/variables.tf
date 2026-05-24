variable "site_name" {
  description = "Unique name for this Observal site (used as subdomain and resource naming)"
  type        = string
}

variable "machine_type" {
  description = "GCE machine type"
  type        = string
  default     = "e2-standard-2"
}

variable "project" {
  description = "GCP project ID"
  type        = string
}

variable "region" {
  description = "GCP region"
  type        = string
  default     = "us-central1"
}

variable "zone" {
  description = "GCP zone"
  type        = string
  default     = "us-central1-a"
}

variable "route53_zone_id" {
  description = "AWS Route53 hosted zone ID for DNS (observal.io)"
  type        = string
}

variable "base_domain" {
  description = "Base domain for site subdomains"
  type        = string
  default     = "observal.io"
}

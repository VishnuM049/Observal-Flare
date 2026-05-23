variable "site_name" {
  description = "Unique name for this Observal site (used as subdomain)"
  type        = string
}

variable "instance_size" {
  description = "EC2 instance type"
  type        = string
  default     = "t3.large"
}

variable "route53_zone_id" {
  description = "Route53 hosted zone ID for observal.io"
  type        = string
}

variable "base_domain" {
  description = "Base domain for site subdomains"
  type        = string
  default     = "observal.io"
}

variable "vpc_id" {
  description = "VPC ID to launch the instance in"
  type        = string
  default     = ""
}

variable "subnet_id" {
  description = "Subnet ID to launch the instance in"
  type        = string
  default     = ""
}

variable "admin_cidr_blocks" {
  description = "CIDR blocks allowed SSH access (emergency fallback)"
  type        = list(string)
  default     = []
}

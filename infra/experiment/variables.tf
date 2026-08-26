variable "experiment_id" {
  description = "Unique experiment identifier"
  type        = string
}

variable "instance_type" {
  description = "EC2 instance type used only for the experiment"
  type        = string
  default     = "t3.small"
}

variable "aws_region" {
  description = "AWS region"
  type        = string
}

variable "expires_at" {
  description = "ISO-8601 cleanup deadline stored as a resource tag"
  type        = string
  default     = ""
}

variable "safety_shutdown_minutes" {
  description = "Instance self-shutdown deadline; Flare still destroys all resources"
  type        = number
  default     = 75
}

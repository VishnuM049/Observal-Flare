variable "experiment_id" {
  description = "Unique experiment identifier"
  type        = string
}

variable "instance_count" {
  description = "Number of identical disposable EC2 fleet members"
  type        = number
  default     = 1

  validation {
    condition     = var.instance_count >= 1 && var.instance_count <= 50 && floor(var.instance_count) == var.instance_count
    error_message = "instance_count must be an integer between 1 and 50"
  }
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

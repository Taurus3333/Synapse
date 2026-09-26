variable "aws_region" {
  description = "Region for the one Synapse instance."
  type        = string
  default     = "us-east-1"
}

variable "name" {
  description = "Short name used on every resource."
  type        = string
  default     = "synapse"
}

variable "allowed_cidr" {
  description = "Who can open the UI (8501) and API (8000). Use your own IP, for example 203.0.113.10/32."
  type        = string
}

variable "instance_type" {
  description = "One process. t3.small is enough for Postgres, Redis, the API, and the UI."
  type        = string
  default     = "t3.small"
}

variable "github_repo" {
  description = "Git URL the instance clones. The repo must be readable without a token."
  type        = string
}

variable "git_ref" {
  description = "Branch or tag to check out."
  type        = string
  default     = "main"
}

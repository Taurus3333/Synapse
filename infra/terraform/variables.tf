variable "aws_region" {
  type        = string
  description = "AWS region for all resources."
  default     = "us-east-1"
}

variable "environment" {
  type        = string
  description = "Environment name (prod, staging, …)."
  default     = "prod"
}

variable "name_prefix" {
  type        = string
  description = "Short prefix for resource names."
  default     = "synapse"
}

variable "vpc_cidr" {
  type    = string
  default = "10.40.0.0/16"
}

variable "az_count" {
  type        = number
  description = "How many AZs to span (2 recommended for ALB/RDS)."
  default     = 2

  validation {
    condition     = var.az_count >= 2 && var.az_count <= 3
    error_message = "az_count must be 2 or 3."
  }
}

variable "api_cpu" {
  type        = number
  description = "Fargate CPU units for the API task."
  default     = 512
}

variable "api_memory" {
  type        = number
  description = "Fargate memory (MiB) for the API task."
  default     = 1024
}

variable "api_desired_count" {
  type        = number
  description = "ECS desired tasks. Keep 0 until an image is pushed to ECR (Chunk 19)."
  default     = 0
}

variable "api_image_tag" {
  type        = string
  description = "ECR image tag to run (Chunk 19 pushes this)."
  default     = "latest"
}

variable "db_instance_class" {
  type    = string
  default = "db.t4g.micro"
}

variable "db_name" {
  type    = string
  default = "synapse"
}

variable "db_username" {
  type    = string
  default = "synapse"
}

variable "redis_node_type" {
  type    = string
  default = "cache.t4g.micro"
}

variable "jwt_secret" {
  type        = string
  description = "HS256 signing key (≥32 chars). Prefer setting via TF_VAR_jwt_secret — never commit."
  sensitive   = true
  default     = ""

  validation {
    condition     = var.jwt_secret == "" || length(var.jwt_secret) >= 32
    error_message = "jwt_secret must be empty (placeholder) or at least 32 characters."
  }
}

variable "groq_api_key" {
  type      = string
  sensitive = true
  default   = ""
}

variable "openai_api_key" {
  type      = string
  sensitive = true
  default   = ""
}

variable "demo_password" {
  type      = string
  sensitive = true
  default   = "synapse-demo"
}

variable "enable_nat_gateway" {
  type        = bool
  description = "NAT for private subnet egress (needed so Fargate can pull ECR + call Groq/OpenAI)."
  default     = true
}

variable "acm_certificate_arn" {
  type        = string
  description = "Optional ACM cert ARN in this region. When set, ALB serves HTTPS:443 and HTTP redirects."
  default     = ""
}

variable "api_image_digest" {
  type        = string
  description = "Optional immutable digest (sha256:…) preferred over tag after push. Empty → use api_image_tag."
  default     = ""
}


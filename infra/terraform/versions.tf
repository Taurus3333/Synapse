terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  # Optional remote state — configure before real apply:
  # backend "s3" {
  #   bucket = "your-tf-state-bucket"
  #   key    = "synapse/prod/terraform.tfstate"
  #   region = "us-east-1"
  # }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = "synapse"
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}

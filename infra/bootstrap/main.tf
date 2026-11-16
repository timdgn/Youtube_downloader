# Bootstrap root: the S3 bucket that holds the Terraform state for every
# environment of the application root. It cannot be managed by the state it
# holds, so this root deliberately keeps local state (`terraform.tfstate` in
# this directory, ignored by git). Applied once per account.

variable "state_bucket" {
  description = "Globally unique name of the bucket that holds Terraform state, e.g. ytdl-tfstate-123456789012."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$", var.state_bucket))
    error_message = "state_bucket must be a valid S3 bucket name (3-63 characters, lowercase letters, digits and hyphens)."
  }
}

variable "aws_region" {
  description = "Region that holds the state bucket and the application resources."
  type        = string
  default     = "eu-west-3"
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = local.tags
  }
}

locals {
  # The bucket is shared by dev and prod, so it belongs to no single environment.
  tags = {
    Project     = "ytdl"
    Environment = "shared"
    ManagedBy   = "terraform"
  }
}

resource "aws_s3_bucket" "state" {
  bucket        = var.state_bucket
  force_destroy = false # State is not recoverable from source; never let destroy erase it.

  tags = local.tags
}

resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_ownership_controls" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket = aws_s3_bucket.state.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

output "state_bucket" {
  description = "Name to pass to the application root as its backend bucket."
  value       = aws_s3_bucket.state.bucket
}

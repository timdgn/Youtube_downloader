resource "aws_dynamodb_table" "jobs" {
  name         = "${local.name_prefix}-jobs"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"

  attribute {
    name = "pk"
    type = "S"
  }

  # Ownership of bot jobs, queried in creation order. The index is sparse: only
  # items carrying owner_key are projected, so terminal jobs and rate windows
  # cost nothing. Added in place, never by replacing the table (research D-05).
  attribute {
    name = "owner_key"
    type = "S"
  }

  attribute {
    name = "created_at"
    type = "S"
  }

  global_secondary_index {
    name            = "owner-created-index"
    hash_key        = "owner_key"
    range_key       = "created_at"
    projection_type = "ALL"
  }

  # Job records carry media_expires_at + 30 days; rate windows carry
  # window_start + 7200, so both self-clean without a sweeper function.
  ttl {
    attribute_name = "ttl"
    enabled        = true
  }

  tags = local.tags
}

resource "aws_sqs_queue" "jobs_dlq" {
  name                      = "${local.name_prefix}-jobs-dlq"
  message_retention_seconds = 1209600 # 14 days: long enough to investigate a week later.

  tags = local.tags
}

resource "aws_sqs_queue" "jobs" {
  name                       = "${local.name_prefix}-jobs"
  visibility_timeout_seconds = 960 # Must exceed the 900 s function timeout, or a second worker takes a running job.
  message_retention_seconds  = 345600

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.jobs_dlq.arn
    maxReceiveCount     = 2
  })

  tags = local.tags
}

# Access to the queues is granted only by the two IAM roles in functions.tf, so
# no queue policy is declared here: an explicit policy could only widen access.

resource "random_password" "api_token" {
  length = 48

  # No special characters, so the token is safe in a curl header, a shell
  # variable and an SSM CLI invocation without quoting.
  special = false
}

resource "aws_ssm_parameter" "api_token" {
  name        = local.token_parameter_name
  description = "Shared bearer token for the ${var.environment} API."
  type        = "SecureString"
  value       = random_password.api_token.result

  tags = local.tags
}

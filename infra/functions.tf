resource "aws_ecr_repository" "app" {
  name                 = "${local.name_prefix}-app"
  image_tag_mutability = "IMMUTABLE"
  force_delete         = true

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = local.tags
}

resource "aws_cloudwatch_log_group" "api" {
  name              = "/aws/lambda/${local.api_function_name}"
  retention_in_days = var.log_retention_days

  tags = local.tags
}

resource "aws_cloudwatch_log_group" "worker" {
  name              = "/aws/lambda/${local.worker_function_name}"
  retention_in_days = var.log_retention_days

  tags = local.tags
}

data "aws_iam_policy_document" "lambda_assume_role" {
  statement {
    sid     = "LambdaAssumeRole"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "api" {
  statement {
    sid = "JobRecords"
    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
      "dynamodb:TransactWriteItems",
      # The rate-window guard is a ConditionCheck inside the same transaction as
      # the job write, and DynamoDB authorises each element of a transaction.
      "dynamodb:ConditionCheckItem",
    ]
    resources = [aws_dynamodb_table.jobs.arn]
  }

  statement {
    sid       = "EnqueueJobs"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.jobs.arn]
  }

  # Signing is local, but the existence check before signing is a HeadObject.
  statement {
    sid       = "ReadOutputForPresign"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.media.arn}/output/*"]
  }

  # Documented S3 behaviour: HeadObject on a missing key answers 404 only if the
  # caller may list the bucket, and 403 otherwise. The expired-versus-available
  # decision depends on that 404, so bucket-level listing (not object reading)
  # is granted here.
  statement {
    sid       = "ListBucketForMissingKey404"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.media.arn]
  }

  statement {
    sid       = "ReadApiToken"
    actions   = ["ssm:GetParameter"]
    resources = [aws_ssm_parameter.api_token.arn]
  }

  statement {
    sid       = "WriteOwnLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.api.arn}:*"]
  }
}

data "aws_iam_policy_document" "worker" {
  statement {
    sid       = "JobRecords"
    actions   = ["dynamodb:GetItem", "dynamodb:UpdateItem"]
    resources = [aws_dynamodb_table.jobs.arn]
  }

  statement {
    sid       = "ReadWriteOutputMedia"
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["${aws_s3_bucket.media.arn}/output/*"]
  }

  statement {
    sid       = "ReadOperatorConfig"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.media.arn}/config/*"]
  }

  # Same documented behaviour as in the API role: the pre-download HeadObject
  # must see 404, not 403, for a job whose object does not exist yet.
  statement {
    sid       = "ListBucketForMissingKey404"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.media.arn]
  }

  statement {
    sid = "ConsumeJobs"
    actions = [
      "sqs:ReceiveMessage",
      "sqs:DeleteMessage",
      "sqs:ChangeMessageVisibility",
      "sqs:GetQueueAttributes",
    ]
    resources = [aws_sqs_queue.jobs.arn]
  }

  # The token parameter plus the /ytdl/<env>/ path, so an operator-supplied
  # proxy parameter can be added without changing this policy. A parameter path
  # is not a wildcard on the whole SSM service.
  statement {
    sid     = "ReadCredentials"
    actions = ["ssm:GetParameter"]
    resources = [
      aws_ssm_parameter.api_token.arn,
      "${local.ssm_parameter_path}/*",
    ]
  }

  statement {
    sid       = "WriteOwnLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.worker.arn}:*"]
  }
}

resource "aws_iam_role" "api" {
  name               = "${local.name_prefix}-api-role"
  description        = "Execution role for the ${local.api_function_name} function."
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json

  tags = local.tags
}

resource "aws_iam_role" "worker" {
  name               = "${local.name_prefix}-worker-role"
  description        = "Execution role for the ${local.worker_function_name} function."
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json

  tags = local.tags
}

resource "aws_iam_role_policy" "api" {
  name   = "${local.name_prefix}-api"
  role   = aws_iam_role.api.id
  policy = data.aws_iam_policy_document.api.json
}

resource "aws_iam_role_policy" "worker" {
  name   = "${local.name_prefix}-worker"
  role   = aws_iam_role.worker.id
  policy = data.aws_iam_policy_document.worker.json
}

locals {
  # Both images read the same validated configuration object, so both get the
  # full set. No secret is ever placed in an environment variable.
  function_environment = {
    ENVIRONMENT          = var.environment
    JOBS_TABLE           = aws_dynamodb_table.jobs.name
    MEDIA_BUCKET         = aws_s3_bucket.media.bucket
    JOBS_QUEUE_URL       = aws_sqs_queue.jobs.url
    TOKEN_PARAM_NAME     = local.token_parameter_name
    MAX_HEIGHT           = tostring(var.max_height)
    MAX_DURATION_SECONDS = tostring(var.max_duration_seconds)
    MAX_OUTPUT_BYTES     = tostring(var.max_output_bytes)
    RETENTION_DAYS       = tostring(var.retention_days)
    LINK_TTL_SECONDS     = tostring(var.link_ttl_seconds)
    RATE_LIMIT_PER_HOUR  = tostring(var.rate_limit_per_hour)
    LOG_LEVEL            = var.log_level
    COOKIES_S3_URI       = var.ytdlp_cookies_s3_uri
    PROXY_PARAM_NAME     = var.ytdlp_proxy_param
    PLAYER_CLIENT        = var.ytdlp_player_client
  }
}

resource "aws_lambda_function" "api" {
  function_name = local.api_function_name
  description   = "Accepts submissions, reports job state, issues presigned links."
  role          = aws_iam_role.api.arn

  package_type  = "Image"
  image_uri     = "${aws_ecr_repository.app.repository_url}:${var.image_tag}-api"
  architectures = [var.lambda_architecture]

  memory_size                    = 512
  timeout                        = 15
  reserved_concurrent_executions = 5

  image_config {
    command = ["ytdl.api.handler.handler"]
  }

  environment {
    variables = local.function_environment
  }

  # The log group is declared above with a retention policy; without this the
  # function would create its own never-expiring group on first invocation.
  depends_on = [aws_cloudwatch_log_group.api]

  tags = local.tags
}

resource "aws_lambda_function" "worker" {
  function_name = local.worker_function_name
  description   = "Downloads, remuxes and stores one video per invocation."
  role          = aws_iam_role.worker.arn

  package_type  = "Image"
  image_uri     = "${aws_ecr_repository.app.repository_url}:${var.image_tag}-worker"
  architectures = [var.lambda_architecture]

  memory_size = var.worker_memory_mb
  timeout     = var.worker_timeout_seconds

  # /tmp must hold the source streams plus the remuxed output: about 4 GB video
  # + 0.1 GB audio + 4 GB output is ~8.1 GB, which does not fit in 8 GB, hence
  # the Lambda maximum of 10,240 MB. First number to change if re-encoding lands.
  ephemeral_storage {
    size = var.worker_ephemeral_storage_mb
  }

  reserved_concurrent_executions = var.worker_reserved_concurrency

  image_config {
    command = ["ytdl.worker.handler.handler"]
  }

  environment {
    variables = local.function_environment
  }

  depends_on = [aws_cloudwatch_log_group.worker]

  tags = local.tags
}

resource "aws_lambda_event_source_mapping" "worker" {
  event_source_arn = aws_sqs_queue.jobs.arn
  function_name    = aws_lambda_function.worker.arn

  enabled                            = true
  batch_size                         = 1 # One message is one job, so a failure has exactly one owner.
  maximum_batching_window_in_seconds = 0
  function_response_types            = ["ReportBatchItemFailures"]

  scaling_config {
    maximum_concurrency = 2
  }
}

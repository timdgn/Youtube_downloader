# The Telegram front door (feature 002). Everything it adds lives in this one
# file because the pieces are meaningless apart: a route without its function,
# or a function without its allowlist parameter, is not a partial deployment.

locals {
  # A $default stage's invoke_url can carry a trailing slash, so trim it before
  # the join: one slash in the registered URL is what Telegram, the health check
  # and the tutorial all state.
  bot_webhook_url = "${trimsuffix(aws_apigatewayv2_stage.bot.invoke_url, "/")}/telegram/webhook"
}

resource "aws_cloudwatch_log_group" "bot_access" {
  name              = "/aws/apigateway/${local.bot_function_name}"
  retention_in_days = var.log_retention_days

  tags = local.tags
}

# Its own API rather than a fourth route on the terminal one: that API is
# token-gated and throttled for a terminal, this one is public by necessity and
# must absorb Telegram bursts (research D-01).
resource "aws_apigatewayv2_api" "bot" {
  name          = local.bot_function_name
  description   = "YouTube download service: the Telegram webhook."
  protocol_type = "HTTP"

  # No cors_configuration: Telegram calls server to server, never a browser.
  tags = local.tags
}

resource "aws_apigatewayv2_stage" "bot" {
  api_id      = aws_apigatewayv2_api.bot.id
  name        = "$default"
  auto_deploy = true

  # Backstop only: the secret header, the SSM allowlist and reserved concurrency
  # are the real bounds. This keeps a forged call from reaching the function.
  default_route_settings {
    throttling_rate_limit  = 10
    throttling_burst_limit = 20
  }

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.bot_access.arn

    # Same seven fields as the terminal API: no header, no body, no user agent.
    # The webhook secret and the message text are both outside this allowlist.
    format = jsonencode({
      requestId         = "$context.requestId"
      routeKey          = "$context.routeKey"
      status            = "$context.status"
      responseLength    = "$context.responseLength"
      integrationStatus = "$context.integrationStatus"
      sourceIp          = "$context.identity.sourceIp"
      requestTime       = "$context.requestTime"
    })
  }

  tags = local.tags
}

resource "aws_apigatewayv2_integration" "bot" {
  api_id                 = aws_apigatewayv2_api.bot.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.bot.invoke_arn
  payload_format_version = "2.0"
}

# One route, no catch-all. The secret header is checked in the handler, so a
# forged call costs one invocation and shows up in the function's own metrics
# instead of disappearing at the edge.
resource "aws_apigatewayv2_route" "bot_webhook" {
  api_id             = aws_apigatewayv2_api.bot.id
  route_key          = "POST /telegram/webhook"
  target             = "integrations/${aws_apigatewayv2_integration.bot.id}"
  authorization_type = "NONE"
}

resource "aws_lambda_permission" "bot_api_gateway" {
  statement_id  = "AllowBotApiGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.bot.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.bot.execution_arn}/*/*"
}

resource "aws_cloudwatch_log_group" "bot" {
  name              = "/aws/lambda/${local.bot_function_name}"
  retention_in_days = var.log_retention_days

  tags = local.tags
}

data "aws_iam_policy_document" "bot" {
  # Read one job, write a job with its interaction in one transaction, batch
  # read a page of jobs for /history, and soft-delete through UpdateItem.
  statement {
    sid = "JobRecords"
    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
      "dynamodb:TransactWriteItems",
      "dynamodb:BatchGetItem",
    ]
    resources = [aws_dynamodb_table.jobs.arn]
  }

  # /list, /history, the reuse lookup and /empty all query the sparse index by
  # owner prefix. Query on an index needs the index ARN, not the table ARN.
  statement {
    sid       = "QueryOwnedItems"
    actions   = ["dynamodb:Query"]
    resources = ["${aws_dynamodb_table.jobs.arn}/index/owner-created-index"]
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

  # /delete and /empty remove the object before they set the deleted overlay.
  # Without this the bot can list an item it cannot remove.
  statement {
    sid       = "DeleteOwnedOutput"
    actions   = ["s3:DeleteObject"]
    resources = ["${aws_s3_bucket.media.arn}/output/*"]
  }

  # Documented S3 behaviour: HeadObject on a missing key answers 404 only if the
  # caller may list the bucket, and 403 otherwise. The completion message
  # depends on that 404 to say "expired" instead of "unavailable", so
  # bucket-level listing (not object reading) is granted here.
  statement {
    sid       = "ListBucketForMissingKey404"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.media.arn]
  }

  # The token, the webhook secret and the allowlist, read in one call per
  # message (research D-03). That call is GetParameters, which IAM authorises
  # with ssm:GetParameters, a different action from ssm:GetParameter; both are
  # listed so a single read and a one-by-one read are equally allowed.
  # Exactly three ARNs: no path wildcard, so a parameter added later needs its
  # own reviewed change.
  statement {
    sid     = "ReadBotParameters"
    actions = ["ssm:GetParameter", "ssm:GetParameters"]
    resources = [
      aws_ssm_parameter.telegram_bot_token.arn,
      aws_ssm_parameter.telegram_webhook_secret.arn,
      aws_ssm_parameter.telegram_approved_user_ids.arn,
    ]
  }

  statement {
    sid       = "WriteOwnLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.bot.arn}:*"]
  }
}

resource "aws_iam_role" "bot" {
  name               = "${local.name_prefix}-bot-role"
  description        = "Execution role for the ${local.bot_function_name} function."
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json

  tags = local.tags
}

resource "aws_iam_role_policy" "bot" {
  name   = "${local.name_prefix}-bot"
  role   = aws_iam_role.bot.id
  policy = data.aws_iam_policy_document.bot.json
}

resource "aws_lambda_function" "bot" {
  function_name = local.bot_function_name
  description   = "Answers Telegram updates, sends presigned links, reports webhook health."
  role          = aws_iam_role.bot.arn

  package_type = "Image"

  # The api image already holds every module in src/, and the bot needs neither
  # ffmpeg nor yt-dlp, so a third image would buy nothing (research D-11).
  image_uri     = "${aws_ecr_repository.app.repository_url}:${var.image_tag}-api"
  architectures = [var.lambda_architecture]

  # Telegram delivers to API Gateway, which waits 30 seconds at most. 15 keeps
  # the function inside that ceiling and inside one chat reply.
  memory_size                    = 512
  timeout                        = 15
  reserved_concurrent_executions = 5

  image_config {
    command = ["ytdl.bot.handler.handler"]
  }

  # Parameter names only: no token, no secret and no allowlist value is ever an
  # environment variable (constitution III.3).
  environment {
    variables = merge(local.function_environment, {
      TELEGRAM_TOKEN_PARAM          = local.telegram_token_parameter_name
      TELEGRAM_WEBHOOK_SECRET_PARAM = local.telegram_secret_parameter_name
      TELEGRAM_APPROVED_PARAM       = local.telegram_approved_parameter_name
      TELEGRAM_WEBHOOK_URL          = local.bot_webhook_url
      TELEGRAM_DEFAULT_TIER         = var.telegram_default_tier
      TELEGRAM_TEST_VIDEO_ID        = var.telegram_test_video_id
    })
  }

  # The log group is declared above with a retention policy; without this the
  # function would create its own never-expiring group on first invocation.
  depends_on = [aws_cloudwatch_log_group.bot]

  tags = local.tags
}

resource "aws_sns_topic" "job_events" {
  name = local.job_events_topic_name

  tags = local.tags
}

# One subscriber: the worker announces a terminal job, the bot turns it into a
# chat message. The worker never holds the bot token (research D-04).
resource "aws_sns_topic_subscription" "bot_job_events" {
  topic_arn = aws_sns_topic.job_events.arn
  protocol  = "lambda"
  endpoint  = aws_lambda_function.bot.arn
}

resource "aws_lambda_permission" "bot_sns" {
  statement_id  = "AllowJobEventsInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.bot.function_name
  principal     = "sns.amazonaws.com"
  source_arn    = aws_sns_topic.job_events.arn
}

# Telegram accepts only A-Z a-z 0-9 _ - in secret_token, so a punctuation
# character would make setWebhook fail; hence special = false.
resource "random_password" "telegram_webhook_secret" {
  length  = 48
  special = false
}

resource "aws_ssm_parameter" "telegram_bot_token" {
  name        = local.telegram_token_parameter_name
  description = "Telegram bot token for the ${var.environment} bot, from BotFather."
  type        = "SecureString"
  value       = var.telegram_bot_token

  tags = local.tags
}

resource "aws_ssm_parameter" "telegram_webhook_secret" {
  name        = local.telegram_secret_parameter_name
  description = "Value Telegram echoes in X-Telegram-Bot-Api-Secret-Token."
  type        = "SecureString"
  value       = random_password.telegram_webhook_secret.result

  tags = local.tags
}

resource "aws_ssm_parameter" "telegram_approved_user_ids" {
  name        = local.telegram_approved_parameter_name
  description = "Comma-separated numeric Telegram account ids allowed to use the ${var.environment} bot."
  type        = "String"
  value       = join(",", var.telegram_approved_user_ids)

  tags = local.tags
}

# The webhook is not an AWS object, so no provider owns it. terraform_data is
# the only construct with both a create and a destroy phase, and the shell
# script is the part a reviewer can read (research D-02). A destroy provisioner
# may only reference self, so every value is copied into input first.
resource "terraform_data" "telegram_webhook" {
  # Changing the URL must re-run both provisioners, so it is a replacement
  # trigger: an in-place update of `input` alone would leave Telegram pointing at
  # the old address while the environment and the health check moved on.
  triggers_replace = local.bot_webhook_url

  input = {
    url              = local.bot_webhook_url
    token_parameter  = local.telegram_token_parameter_name
    secret_parameter = local.telegram_secret_parameter_name
    region           = var.aws_region
  }

  provisioner "local-exec" {
    command = "${path.module}/scripts/telegram-webhook.sh set"

    environment = {
      WEBHOOK_URL      = self.input.url
      TOKEN_PARAMETER  = self.input.token_parameter
      SECRET_PARAMETER = self.input.secret_parameter
      AWS_REGION       = self.input.region
    }
  }

  provisioner "local-exec" {
    when    = destroy
    command = "${path.module}/scripts/telegram-webhook.sh delete"

    environment = {
      WEBHOOK_URL      = self.input.url
      TOKEN_PARAMETER  = self.input.token_parameter
      SECRET_PARAMETER = self.input.secret_parameter
      AWS_REGION       = self.input.region
    }
  }

  # setWebhook needs the parameters to exist; on destroy this resource is
  # removed before them, so deleteWebhook still has a token to authenticate with.
  depends_on = [
    aws_ssm_parameter.telegram_bot_token,
    aws_ssm_parameter.telegram_webhook_secret,
    aws_ssm_parameter.telegram_approved_user_ids,
  ]
}

# A webhook removed out of band, or a revoked token, is otherwise silent: an
# idle bot and a broken bot look the same. The handler answers by emitting an
# Embedded Metric Format line, so no PutMetricData permission is needed.
resource "aws_cloudwatch_event_rule" "bot_health" {
  name                = "${local.bot_function_name}-health"
  description         = "Hourly getWebhookInfo check for the ${local.bot_function_name} function."
  schedule_expression = "rate(1 hour)"

  tags = local.tags
}

resource "aws_lambda_permission" "bot_health" {
  statement_id  = "AllowBotHealthSchedule"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.bot.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.bot_health.arn
}

resource "aws_cloudwatch_event_target" "bot_health" {
  rule = aws_cloudwatch_event_rule.bot_health.name
  arn  = aws_lambda_function.bot.arn

  # The whole event: the handler dispatches on the action key. No chat traffic,
  # no user input, nothing to sanitise.
  input = jsonencode({ action = "health" })
}

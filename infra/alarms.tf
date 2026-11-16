resource "aws_sns_topic" "alarms" {
  name = "${local.name_prefix}-alarms"

  tags = local.tags
}

resource "aws_sns_topic_subscription" "alarms_email" {
  count = var.alarm_email != "" ? 1 : 0

  topic_arn = aws_sns_topic.alarms.arn
  protocol  = "email"
  endpoint  = var.alarm_email
}

# A CloudWatch alarm can only notify a topic in its own region, and the two budget
# alarms must live in us-east-1 because AWS/Billing is published only there. That
# means a second topic, and a second subscription for the same address.
resource "aws_sns_topic" "billing" {
  provider = aws.us_east_1
  name     = "${local.name_prefix}-alarms-us-east-1"

  tags = local.tags
}

resource "aws_sns_topic_subscription" "billing_email" {
  provider = aws.us_east_1
  count    = var.alarm_email != "" ? 1 : 0

  topic_arn = aws_sns_topic.billing.arn
  protocol  = "email"
  endpoint  = var.alarm_email
}

resource "aws_cloudwatch_metric_alarm" "api_5xx" {
  alarm_name          = "${local.name_prefix}-api-5xx"
  alarm_description   = "The API returned a 5xx. Check the api log group for the failing route."
  namespace           = "AWS/ApiGateway"
  metric_name         = "5xx"
  dimensions          = { ApiId = aws_apigatewayv2_api.http.id, Stage = aws_apigatewayv2_stage.default.name }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  alarm_actions = [aws_sns_topic.alarms.arn]
  ok_actions    = [aws_sns_topic.alarms.arn]

  tags = local.tags
}

resource "aws_cloudwatch_metric_alarm" "worker_errors" {
  alarm_name          = "${local.name_prefix}-worker-errors"
  alarm_description   = "The worker raised an error. Read the job failure category before resubmitting."
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = aws_lambda_function.worker.function_name }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  alarm_actions = [aws_sns_topic.alarms.arn]
  ok_actions    = [aws_sns_topic.alarms.arn]

  tags = local.tags
}

resource "aws_cloudwatch_metric_alarm" "worker_throttles" {
  alarm_name          = "${local.name_prefix}-worker-throttles"
  alarm_description   = "The worker hit reserved concurrency. Jobs are queued, not lost; check the simultaneous-job cap."
  namespace           = "AWS/Lambda"
  metric_name         = "Throttles"
  dimensions          = { FunctionName = aws_lambda_function.worker.function_name }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  alarm_actions = [aws_sns_topic.alarms.arn]
  ok_actions    = [aws_sns_topic.alarms.arn]

  tags = local.tags
}

resource "aws_cloudwatch_metric_alarm" "worker_duration_p95" {
  alarm_name        = "${local.name_prefix}-worker-duration-p95"
  alarm_description = "Worker p95 duration passed 600 s of the 900 s timeout. Jobs are close to being killed mid-write."
  namespace         = "AWS/Lambda"
  metric_name       = "Duration"
  dimensions        = { FunctionName = aws_lambda_function.worker.function_name }

  # 600000 ms, not 900000: the remaining 300 s is the room a job needs to record
  # its own failure before Lambda kills the invocation.
  extended_statistic  = "p95"
  period              = 300
  evaluation_periods  = 1
  threshold           = 600000
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  alarm_actions = [aws_sns_topic.alarms.arn]
  ok_actions    = [aws_sns_topic.alarms.arn]

  tags = local.tags
}

resource "aws_cloudwatch_metric_alarm" "dlq_not_empty" {
  alarm_name          = "${local.name_prefix}-dlq-not-empty"
  alarm_description   = "A job message reached the dead-letter queue. Any dead letter is a defect."
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  dimensions          = { QueueName = aws_sqs_queue.jobs_dlq.name }
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  alarm_actions = [aws_sns_topic.alarms.arn]
  ok_actions    = [aws_sns_topic.alarms.arn]

  tags = local.tags
}

resource "aws_cloudwatch_metric_alarm" "bot_errors" {
  alarm_name          = "${local.name_prefix}-bot-errors"
  alarm_description   = "The bot raised an error. A webhook 500 makes Telegram retry; read the bot log group for the failing entry shape."
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = aws_lambda_function.bot.function_name }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  alarm_actions = [aws_sns_topic.alarms.arn]
  ok_actions    = [aws_sns_topic.alarms.arn]

  tags = local.tags
}

# The hourly check emits WebhookHealthy: 1 when the registered URL matches and
# no delivery error was recorded, 0 otherwise. Two hours before firing, so one
# missed scheduled invocation is not an alarm. Missing data is breaching,
# because a check that stopped running is itself the failure.
resource "aws_cloudwatch_metric_alarm" "bot_webhook_health" {
  alarm_name          = "${local.name_prefix}-bot-webhook-health"
  alarm_description   = "The Telegram webhook is gone or the token was revoked. Re-register it with the webhook script and confirm getWebhookInfo reports the stage URL."
  namespace           = "YTDL/Bot"
  metric_name         = "WebhookHealthy"
  dimensions          = { Environment = var.environment }
  statistic           = "Minimum"
  period              = 3600
  evaluation_periods  = 2
  threshold           = 1
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "breaching"

  alarm_actions = [aws_sns_topic.alarms.arn]
  ok_actions    = [aws_sns_topic.alarms.arn]

  tags = local.tags
}

# AWS/Billing publishes EstimatedCharges a few times a day only, so a missing
# datapoint must stay "insufficient data" rather than fire or silently clear.
resource "aws_cloudwatch_metric_alarm" "billing_5_usd" {
  provider = aws.us_east_1

  alarm_name          = "${local.name_prefix}-billing-5usd"
  alarm_description   = "Estimated charges passed 50% of the documented $10 monthly ceiling."
  namespace           = "AWS/Billing"
  metric_name         = "EstimatedCharges"
  dimensions          = { Currency = "USD" }
  statistic           = "Maximum"
  period              = 21600
  evaluation_periods  = 1
  threshold           = 5
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "missing"

  alarm_actions = [aws_sns_topic.billing.arn]
  ok_actions    = [aws_sns_topic.billing.arn]

  tags = local.tags
}

resource "aws_cloudwatch_metric_alarm" "billing_10_usd" {
  provider = aws.us_east_1

  alarm_name          = "${local.name_prefix}-billing-10usd"
  alarm_description   = "Estimated charges reached the documented $10 monthly ceiling."
  namespace           = "AWS/Billing"
  metric_name         = "EstimatedCharges"
  dimensions          = { Currency = "USD" }
  statistic           = "Maximum"
  period              = 21600
  evaluation_periods  = 1
  threshold           = 10
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "missing"

  alarm_actions = [aws_sns_topic.billing.arn]
  ok_actions    = [aws_sns_topic.billing.arn]

  tags = local.tags
}

data "aws_caller_identity" "current" {}

locals {
  name_prefix = "ytdl-${var.environment}"

  tags = {
    Project     = "ytdl"
    Environment = var.environment
    ManagedBy   = "terraform"
  }

  api_function_name    = "${local.name_prefix}-api"
  worker_function_name = "${local.name_prefix}-worker"
  bot_function_name    = "${local.name_prefix}-bot"

  job_events_topic_name = "${local.name_prefix}-job-events"

  # Must be globally unique, so the account id is part of the name.
  media_bucket_name = "ytdl-${data.aws_caller_identity.current.account_id}-${var.environment}-media"

  token_parameter_name = "/ytdl/${var.environment}/api-token"

  # The bot reads all three in one GetParameters call per message (D-03). None of
  # them is ever an environment variable.
  telegram_token_parameter_name    = "/ytdl/${var.environment}/telegram-bot-token"
  telegram_secret_parameter_name   = "/ytdl/${var.environment}/telegram-webhook-secret"
  telegram_approved_parameter_name = "/ytdl/${var.environment}/telegram-approved-user-ids"

  # Parameter path the worker may read, so an optional operator-supplied proxy
  # parameter can be added without touching Terraform.
  ssm_parameter_path = "arn:aws:ssm:${var.aws_region}:${data.aws_caller_identity.current.account_id}:parameter/ytdl/${var.environment}"

  # The menu a client shows when "/" is typed. A command name carries no
  # arguments, so the usage lives in its description. Keep this list in step
  # with the help text in the bot (contracts/bot-commands.md).
  bot_commands = [
    { command = "start", description = "Start the bot" },
    { command = "list", description = "List your stored videos" },
    { command = "link", description = "Fresh download link for a numbered item" },
    { command = "delete", description = "Delete a video by number or name" },
    { command = "empty", description = "Delete all your videos" },
    { command = "history", description = "Your last ten interactions" },
    { command = "info", description = "yt-dlp version and limits" },
    { command = "test", description = "Run one short test download" },
    { command = "help", description = "Show the command list" },
  ]
}

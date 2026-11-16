# Every row of the Environment table in specs/001-youtube-video-download/data-model.md.
# The sizing values have no defaults on purpose: they come from the selected
# env/<name>.tfvars file, so a new environment must state them explicitly.

variable "environment" {
  description = "Deployment unit; used in every resource name, tag and SSM path."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9]{1,11}$", var.environment))
    error_message = "environment must be 2-12 lowercase alphanumeric characters starting with a letter."
  }
}

variable "aws_region" {
  description = "Region that holds all application resources."
  type        = string
  default     = "eu-west-3"
}

variable "max_height" {
  description = "Largest video height the service will serve; higher requests are capped, not rejected."
  type        = number

  validation {
    condition     = var.max_height >= 144 && var.max_height <= 2160
    error_message = "max_height must be between 144 and 2160."
  }
}

variable "max_duration_seconds" {
  description = "Source duration ceiling, enforced from metadata before any media is transferred."
  type        = number

  validation {
    condition     = var.max_duration_seconds > 0
    error_message = "max_duration_seconds must be greater than 0."
  }
}

variable "max_output_bytes" {
  description = "Stored file size ceiling in bytes (4294967296 = 4 GiB)."
  type        = number

  validation {
    condition     = var.max_output_bytes > 0
    error_message = "max_output_bytes must be greater than 0."
  }
}

variable "retention_days" {
  description = "Age at which the S3 lifecycle rule expires objects under output/."
  type        = number

  validation {
    condition     = var.retention_days >= 1
    error_message = "retention_days must be at least 1."
  }
}

variable "link_ttl_seconds" {
  description = "Presigned download URL lifetime; the constitution caps it at 3600 seconds."
  type        = number

  validation {
    condition     = var.link_ttl_seconds >= 60 && var.link_ttl_seconds <= 3600
    error_message = "link_ttl_seconds must be between 60 and 3600."
  }
}

variable "rate_limit_per_hour" {
  description = "Accepted submissions allowed per credential in a fixed one-hour window."
  type        = number

  validation {
    condition     = var.rate_limit_per_hour >= 1 && var.rate_limit_per_hour <= 100
    error_message = "rate_limit_per_hour must be between 1 and 100."
  }
}

variable "worker_reserved_concurrency" {
  description = "Reserved concurrency for the worker; this is also the simultaneous-job cap."
  type        = number

  validation {
    condition     = var.worker_reserved_concurrency >= 1 && var.worker_reserved_concurrency <= 100
    error_message = "worker_reserved_concurrency must be between 1 and 100."
  }
}

variable "worker_memory_mb" {
  description = "Worker memory in MB; about 1.7 vCPU at 3008 MB."
  type        = number

  validation {
    condition     = var.worker_memory_mb >= 128 && var.worker_memory_mb <= 10240
    error_message = "worker_memory_mb must be between 128 and 10240."
  }
}

variable "worker_timeout_seconds" {
  description = "Worker timeout in seconds; Lambda's ceiling is 900."
  type        = number

  validation {
    condition     = var.worker_timeout_seconds >= 1 && var.worker_timeout_seconds <= 900
    error_message = "worker_timeout_seconds must be between 1 and 900."
  }
}

variable "worker_ephemeral_storage_mb" {
  description = "Worker /tmp in MB; must hold the source plus the remuxed output."
  type        = number

  validation {
    condition     = var.worker_ephemeral_storage_mb >= 512 && var.worker_ephemeral_storage_mb <= 10240
    error_message = "worker_ephemeral_storage_mb must be between 512 and 10240."
  }
}

variable "alarm_email" {
  description = "Optional email address subscribed to the alarm topic. Empty means no subscription."
  type        = string
  default     = ""
}

# Optional extraction hooks (research D-14). All three default to empty and add
# no flags to yt-dlp when empty.
variable "ytdlp_cookies_s3_uri" {
  description = "Optional s3:// URI of an operator-supplied cookies file, normally in the config/ prefix."
  type        = string
  default     = ""
}

variable "ytdlp_proxy_param" {
  description = "Optional SSM parameter name holding an operator-supplied proxy URL."
  type        = string
  default     = ""
}

variable "ytdlp_player_client" {
  description = "Optional yt-dlp player client override, for example web_safari."
  type        = string
  default     = ""
}

variable "log_level" {
  description = "Application log level for both functions."
  type        = string
  default     = "INFO"

  validation {
    condition     = contains(["DEBUG", "INFO", "WARNING", "ERROR"], var.log_level)
    error_message = "log_level must be one of DEBUG, INFO, WARNING or ERROR."
  }
}

variable "log_retention_days" {
  description = "CloudWatch Logs retention for the function and access log groups."
  type        = number
  default     = 14

  validation {
    condition     = var.log_retention_days >= 1
    error_message = "log_retention_days must be at least 1."
  }
}

variable "lambda_architecture" {
  description = "Instruction set for both functions; the container images are built for this platform."
  type        = string
  default     = "arm64"

  validation {
    condition     = contains(["arm64", "x86_64"], var.lambda_architecture)
    error_message = "lambda_architecture must be arm64 or x86_64."
  }
}

variable "image_tag" {
  description = "Immutable image tag from `git rev-parse --short=12 HEAD`, supplied on the command line."
  type        = string

  validation {
    condition     = var.image_tag != "latest" && length(var.image_tag) > 0
    error_message = "image_tag must be an immutable build tag; \"latest\" is never deployed."
  }
}

# Telegram bot (feature 002). The token is the one secret Terraform carries: it
# is exported as TF_VAR_telegram_bot_token and never committed, and it lands in
# an SSM SecureString, never in an environment variable.
variable "telegram_bot_token" {
  description = "Telegram bot token from BotFather, exported as TF_VAR_telegram_bot_token and never committed."
  type        = string
  sensitive   = true
}

variable "telegram_approved_user_ids" {
  description = "Numeric Telegram account ids allowed to use the bot; empty means nobody, which is the safe default."
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for id in var.telegram_approved_user_ids : can(regex("^[0-9]{1,20}$", id))])
    error_message = "telegram_approved_user_ids must contain numeric account ids only, at most 20 digits each."
  }
}

variable "telegram_default_tier" {
  description = "Tier used when a message carries a link without a resolution word."
  type        = string
  default     = "high"

  validation {
    condition     = contains(["low", "medium", "high", "veryhigh", "mp3"], var.telegram_default_tier)
    error_message = "telegram_default_tier must be one of low, medium, high, veryhigh or mp3."
  }
}

variable "telegram_test_video_id" {
  description = "Video id the /test command downloads; replaceable without code."
  type        = string
  default     = "dQw4w9WgXcQ"
}

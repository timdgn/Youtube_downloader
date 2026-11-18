# dev environment. Apply with:
#   terraform -chdir=infra plan -backend-config=backend/dev.backend \
#     -var-file=env/dev.tfvars -var image_tag=<git sha>

environment = "dev"
aws_region  = "eu-west-3"

max_height                  = 1080
max_duration_seconds        = 3600
max_output_bytes            = 4294967296
retention_days              = 7
link_ttl_seconds            = 3600
rate_limit_per_hour         = 10
worker_reserved_concurrency = 2
worker_memory_mb            = 3008
worker_timeout_seconds      = 900
worker_ephemeral_storage_mb = 10240

alarm_email = ""

# Presence of a cookies URI enables the hook: the worker reads config/cookies.txt
# from this environment's own bucket. Never commit the file, and do not write the
# bucket name here either: it carries the account id. The variable defaults to empty;
# supply it for one deploy instead of setting it here, since a value in this file
# would override the environment:
#   export TF_VAR_ytdlp_cookies_s3_uri="s3://ytdl-<account-id>-dev-media/config/cookies.txt"
ytdlp_proxy_param   = ""
ytdlp_player_client = ""

# Telegram bot. The token is exported as TF_VAR_telegram_bot_token, never a
# value in this file. The allowlist is empty here on purpose: nobody is served
# until the operator exports TF_VAR_telegram_approved_user_ids.
telegram_default_tier  = "high"
telegram_test_video_id = "dQw4w9WgXcQ"

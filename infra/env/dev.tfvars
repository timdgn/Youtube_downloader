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

ytdlp_cookies_s3_uri = ""
ytdlp_proxy_param    = ""
ytdlp_player_client  = ""

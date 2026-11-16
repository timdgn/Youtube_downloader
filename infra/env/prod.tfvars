# prod environment. Same definition as dev, different values and a different state key.
# Apply with:
#   terraform -chdir=infra plan -backend-config=backend/prod.backend \
#     -var-file=env/prod.tfvars -var image_tag=<git sha>
#
# The alarm address is the only value that must be supplied by the operator before
# the first production submission (constitution principle V).

environment = "prod"
aws_region  = "eu-west-1"

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

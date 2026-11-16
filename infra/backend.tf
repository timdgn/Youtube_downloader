terraform {
  # Partial configuration: bucket, key and region come from -backend-config
  # (for example backend/dev.backend). Locking uses the native S3 lock file,
  # which replaces the deprecated DynamoDB lock table.
  backend "s3" {
    use_lockfile = true
    encrypt      = true
  }
}

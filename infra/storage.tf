resource "aws_s3_bucket" "media" {
  bucket = local.media_bucket_name

  # Destroy must leave nothing behind: the bucket holds only regenerable media.
  force_destroy = true

  tags = local.tags
}

resource "aws_s3_bucket_public_access_block" "media" {
  bucket = aws_s3_bucket.media.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "media" {
  bucket = aws_s3_bucket.media.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "media" {
  bucket = aws_s3_bucket.media.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

data "aws_iam_policy_document" "media_bucket" {
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.media.arn, "${aws_s3_bucket.media.arn}/*"]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "media" {
  bucket = aws_s3_bucket.media.id
  policy = data.aws_iam_policy_document.media_bucket.json

  # Attach after the public access block, so S3 evaluates the policy against the
  # final bucket settings rather than the defaults it was created with.
  depends_on = [aws_s3_bucket_public_access_block.media]
}

resource "aws_s3_bucket_lifecycle_configuration" "media" {
  bucket = aws_s3_bucket.media.id

  # FR-012: media disappears on its own. Lifecycle counts whole days and runs at
  # the next midnight UTC, so an object lives between retention_days and one day
  # more; the API's media_expires_at timestamp stays the authority on availability.
  rule {
    id     = "expire-output-objects"
    status = "Enabled"

    filter {
      prefix = "output/"
    }

    expiration {
      days = var.retention_days
    }
  }

  # A killed worker can leave a multipart upload behind; those parts are billed
  # but invisible and would never expire with the rule above.
  rule {
    id     = "abort-incomplete-multipart-uploads"
    status = "Enabled"

    filter {}

    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
}

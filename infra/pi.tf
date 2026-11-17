# The host deployment (Raspberry Pi): one IAM user, least privilege over the
# same resources the three Lambda roles reach.
#
# The access key is deliberately NOT a Terraform resource: an access key is a
# secret, and Terraform would keep it in the state file. Create it out of band
# with the CLI and store it on the host, as docs/runbook.md describes.

data "aws_iam_policy_document" "pi" {
  # Reads and writes one job, and writes a job with its interaction in one
  # transaction.
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

  # /list, /history, the reuse lookup and /empty query the sparse index by owner
  # prefix, which IAM authorises on the index ARN, not the table ARN.
  statement {
    sid       = "QueryOwnedItems"
    actions   = ["dynamodb:Query"]
    resources = ["${aws_dynamodb_table.jobs.arn}/index/owner-created-index"]
  }

  # The API and the bot enqueue; the host worker also consumes, extends and
  # acknowledges.
  statement {
    sid = "QueueAccess"
    actions = [
      "sqs:SendMessage",
      "sqs:ReceiveMessage",
      "sqs:DeleteMessage",
      "sqs:ChangeMessageVisibility",
      "sqs:GetQueueAttributes",
    ]
    resources = [aws_sqs_queue.jobs.arn]
  }

  # Upload the finished file, check it before signing, delete it on /delete.
  statement {
    sid = "MediaObjects"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
      "s3:AbortMultipartUpload",
    ]
    resources = ["${aws_s3_bucket.media.arn}/output/*"]
  }

  statement {
    sid       = "OperatorConfig"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.media.arn}/config/*"]
  }

  # HeadObject answers 404 rather than 403 only when the caller may list, and
  # the available-versus-expired answer depends on that 404.
  statement {
    sid       = "ListBucketForMissingKey404"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.media.arn]
  }

  # The bearer token, the bot token, the webhook secret and the allowlist. The
  # path wildcard keeps an operator-added parameter readable without a second
  # change, exactly as the worker role already allows.
  statement {
    sid     = "ReadCredentials"
    actions = ["ssm:GetParameter", "ssm:GetParameters"]
    resources = [
      aws_ssm_parameter.api_token.arn,
      aws_ssm_parameter.telegram_bot_token.arn,
      aws_ssm_parameter.telegram_webhook_secret.arn,
      aws_ssm_parameter.telegram_approved_user_ids.arn,
      "${local.ssm_parameter_path}/*",
    ]
  }
}

resource "aws_iam_user" "pi" {
  name = "${local.name_prefix}-pi"

  tags = local.tags
}

resource "aws_iam_user_policy" "pi" {
  name   = "${local.name_prefix}-pi"
  user   = aws_iam_user.pi.name
  policy = data.aws_iam_policy_document.pi.json
}

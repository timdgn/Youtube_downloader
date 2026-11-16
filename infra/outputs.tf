output "api_base_url" {
  description = "Base URL of the $default stage; routes add the /v1 prefix."
  value       = aws_apigatewayv2_stage.default.invoke_url
}

output "media_bucket" {
  description = "Private bucket holding output/ and the optional config/cookies.txt."
  value       = aws_s3_bucket.media.bucket
}

output "api_function_name" {
  description = "Name of the API Lambda function, for log and alarm lookups."
  value       = aws_lambda_function.api.function_name
}

output "worker_function_name" {
  description = "Name of the worker Lambda function, for log and alarm lookups."
  value       = aws_lambda_function.worker.function_name
}

output "token_parameter_name" {
  description = "SSM SecureString parameter holding the shared bearer token."
  value       = aws_ssm_parameter.api_token.name
}

output "queue_url" {
  description = "Main job queue."
  value       = aws_sqs_queue.jobs.url
}

output "dlq_url" {
  description = "Dead-letter queue for job messages that failed twice."
  value       = aws_sqs_queue.jobs_dlq.url
}

output "alarm_topic_arn" {
  description = "SNS topic every operational and budget alarm publishes to."
  value       = aws_sns_topic.alarms.arn
}

output "ecr_repository_url" {
  description = "Repository receiving the -api and -worker image tags."
  value       = aws_ecr_repository.app.repository_url
}

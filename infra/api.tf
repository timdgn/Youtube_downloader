resource "aws_cloudwatch_log_group" "api_access" {
  name              = "/aws/apigateway/${local.name_prefix}"
  retention_in_days = var.log_retention_days

  tags = local.tags
}

resource "aws_apigatewayv2_api" "http" {
  name          = local.name_prefix
  description   = "YouTube download service: submit, poll, re-issue."
  protocol_type = "HTTP"

  # No cors_configuration: the API is called from a terminal, never a browser.
  tags = local.tags
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.http.id
  name        = "$default"
  auto_deploy = true

  # Backstop only: the per-credential hourly cap is enforced in the handler, in
  # DynamoDB. This keeps a runaway client from reaching the function at all.
  default_route_settings {
    throttling_rate_limit  = 5
    throttling_burst_limit = 20
  }

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.api_access.arn

    # FR-015: no query string, no headers, no user agent. These seven fields are
    # the whole allowlist.
    format = jsonencode({
      requestId         = "$context.requestId"
      routeKey          = "$context.routeKey"
      status            = "$context.status"
      responseLength    = "$context.responseLength"
      integrationStatus = "$context.integrationStatus"
      sourceIp          = "$context.identity.sourceIp"
      requestTime       = "$context.requestTime"
    })
  }

  tags = local.tags
}

resource "aws_apigatewayv2_integration" "api" {
  api_id                 = aws_apigatewayv2_api.http.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.api.invoke_arn
  payload_format_version = "2.0"
}

# The route table is fixed: three routes, no catch-all. Anything else is a 404
# produced by API Gateway, and authentication is enforced in the handler.
resource "aws_apigatewayv2_route" "create_job" {
  api_id             = aws_apigatewayv2_api.http.id
  route_key          = "POST /v1/jobs"
  target             = "integrations/${aws_apigatewayv2_integration.api.id}"
  authorization_type = "NONE"
}

resource "aws_apigatewayv2_route" "get_job" {
  api_id             = aws_apigatewayv2_api.http.id
  route_key          = "GET /v1/jobs/{job_id}"
  target             = "integrations/${aws_apigatewayv2_integration.api.id}"
  authorization_type = "NONE"
}

resource "aws_apigatewayv2_route" "reissue_link" {
  api_id             = aws_apigatewayv2_api.http.id
  route_key          = "POST /v1/jobs/{job_id}/link"
  target             = "integrations/${aws_apigatewayv2_integration.api.id}"
  authorization_type = "NONE"
}

resource "aws_lambda_permission" "api_gateway" {
  statement_id  = "AllowApiGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.api.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.http.execution_arn}/*/*"
}

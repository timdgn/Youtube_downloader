provider "aws" {
  region = var.aws_region

  default_tags {
    tags = local.tags
  }
}

# The AWS/Billing EstimatedCharges metric is only published in us-east-1, so the
# two budget alarms are created there whatever region the stack runs in.
provider "aws" {
  alias  = "us_east_1"
  region = "us-east-1"

  default_tags {
    tags = local.tags
  }
}

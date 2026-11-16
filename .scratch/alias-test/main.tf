provider "aws" { region = "eu-west-3" }
provider "aws" {
  alias  = "use1"
  region = "us-east-1"
}
data "aws_region" "default" {}
data "aws_region" "aliased" { provider = aws.use1 }
output "default_region" { value = data.aws_region.default.region }
output "aliased_region" { value = data.aws_region.aliased.region }

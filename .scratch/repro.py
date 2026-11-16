import json
import os

import boto3

REGION = "eu-west-3"

os.environ.update(
    {
        "AWS_PROFILE": "awsperso",
        "AWS_REGION": "eu-west-3",
        "ENVIRONMENT": "dev",
        "JOBS_TABLE": "ytdl-dev-jobs",
        "MEDIA_BUCKET": "ytdl-000000000000-dev-media",
        "JOBS_QUEUE_URL": "https://sqs.eu-west-3.amazonaws.com/000000000000/ytdl-dev-jobs",
        "TOKEN_PARAM_NAME": "/ytdl/dev/api-token",
    }
)

from ytdl.adapters.dynamo import DynamoRepository
from ytdl.adapters.s3 import MediaStore
from ytdl.adapters.sqs import JobQueue
from ytdl.api.handler import Api
from ytdl.auth import TokenProvider
from ytdl.config import load_config

config = load_config()
print("ssm region:", boto3.client("ssm", region_name=REGION).meta.region_name)
token = TokenProvider(config.token_param_name, boto3.client("ssm", region_name=REGION)).get()

api = Api(
    repo=DynamoRepository(boto3.client("dynamodb", region_name=REGION), config.table_name),
    queue=JobQueue(boto3.client("sqs", region_name=REGION), config.queue_url),
    store=MediaStore(boto3.client("s3", region_name=REGION), config.media_bucket),
    tokens=TokenProvider(config.token_param_name, boto3.client("ssm", region_name=REGION)),
    config=config,
)

event = {
    "requestContext": {"http": {"method": "POST"}},
    "rawPath": "/v1/jobs",
    "headers": {"authorization": f"Bearer {token}"},
    "body": json.dumps({"url": "https://www.youtube.com/watch?v=jNQXAC9IVRw"}),
}

print("handle:", api.handle(event))
try:
    print("_create:", api._create(event, token))
except Exception:
    import traceback

    traceback.print_exc()

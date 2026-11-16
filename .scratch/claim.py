import os
import traceback

import boto3

REGION = "eu-west-3"
os.environ.update({"AWS_PROFILE": "awsperso", "AWS_REGION": REGION})

from ytdl.adapters.dynamo import DynamoRepository

repo = DynamoRepository(boto3.client("dynamodb", region_name=REGION), "ytdl-dev-jobs")

job_id = "doesnotexist0"  # deliberately absent: proves the expression is valid
print("get_job:", repo.get_job(job_id))
try:
    print("claim_job:", repo.claim_job(job_id, 1794798614, "2026-11-16T03:10:14Z"))
except Exception:
    traceback.print_exc()

# Project Instructions

This repository is governed by `.specify/memory/constitution.md` (v2.0.0). If an
instruction here conflicts with the constitution, the constitution wins.

## Cost ceilings are guardrails

Never generate load, synthetic submissions, or sustained activity to approach, exercise,
or validate a cost ceiling. Justify a ceiling with a pricing model in review, and read
actual spend from billing data. A ceiling reached on purpose has already failed at the
one job it had. This is constitution principle V, and it applies to tests, drills,
"quick checks", and demonstrations.

## Environment facts that are easy to get wrong

- Production compute runs on `rasp` in Docker (`ytdl` and `ytdl-worker` in
  `/opt/serveur/compose.yaml`); AWS keeps S3, DynamoDB, SQS and SSM. While the host
  polls the queue, `worker_lambda_enabled` must stay `false`: a second consumer would
  race for the same message. `docs/runbook.md` section 12 is the operator view.
- All resources run in `eu-west-3` (Paris). The two budget alarms are the only exception,
  and they live in `us-east-1` with their own SNS topic, because a CloudWatch alarm can
  only act on a topic in its own region.
- `terraform init` remembers the last backend it saw. Always pass `-reconfigure` when
  switching `ENV`, or an apply will write into the wrong state file. `make deploy`,
  `make destroy`, and `make outputs` do this for you.
- Presigned S3 URLs must be signed against the regional endpoint. Use `s3_client()` from
  `ytdl.adapters.s3`, not a bare `boto3.client("s3")`.

## Where the truth lives

- Behaviour: `specs/001-youtube-video-download/spec.md`
- Decisions and rejected alternatives: `specs/001-youtube-video-download/research.md`
- API and queue shapes: `specs/001-youtube-video-download/contracts/`
- Task state: `specs/001-youtube-video-download/tasks.md`
- Operations, failures, alarms: `docs/runbook.md`

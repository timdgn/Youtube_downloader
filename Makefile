# YouTube Download Service
#
# IMAGE_TAG is derived from the git commit, so a deployed function always names the
# exact bytes it runs. `latest` is never used anywhere.
# See specs/001-youtube-video-download/ for the plan behind every target.

SHELL := /bin/bash
.DEFAULT_GOAL := help

PY        ?= python3.12
VENV      := app/.venv
VPY       := $(VENV)/bin/python
IMAGE_TAG ?= $(shell git rev-parse --short=12 HEAD 2>/dev/null || date -u +%Y%m%d%H%M%S)
LOCAL_IMG ?= ytdl-local
PI_IMG    ?= ytdl-pi
PI_HOST   ?= rasp
ENV       ?=
STATE_BUCKET_PREFIX ?= ytdl-tfstate

# Paris. Exported so every AWS CLI call in a recipe targets the same region as the
# stack, whatever the profile's default region happens to be. The Billing alarms
# are the one exception: they read us-east-1, where that metric is published.
AWS_REGION ?= eu-west-3
export AWS_REGION

.PHONY: help install image image-api image-worker pi-image pi-ship test test-unit test-contract \
        test-e2e lint fmt verify-image tf-check bootstrap deploy destroy \
        verify-destroy verify-retention verify-idempotency token outputs \
        bot-help-check bot-health bot-token

help:
	@echo "install              create app/.venv and install pinned dependencies"
	@echo "image                build both arm64 images tagged $(IMAGE_TAG)"
	@echo "pi-image             build the Docker host image tagged $(PI_IMG):$(IMAGE_TAG)"
	@echo "pi-ship              build it and load it on $(PI_HOST) over SSH"
	@echo "test                 unit and contract suites (offline, deterministic)"
	@echo "test-e2e             opt-in suite against a deployed stack"
	@echo "lint                 ruff check and format check"
	@echo "tf-check             terraform fmt -check and validate, both roots"
	@echo "verify-image         prove the worker image extracts one real video"
	@echo "bootstrap            create the Terraform state bucket (once per account)"
	@echo "deploy ENV=dev       build, push and apply one environment"
	@echo "destroy ENV=dev      remove every resource that environment created"
	@echo "verify-destroy ENV=  prove nothing chargeable survives"
	@echo "token ENV=dev        print the API bearer token"
	@echo "outputs ENV=dev      re-init that backend and print its outputs"
	@echo "bot-help-check       assert the help text against the contract"
	@echo "bot-health ENV=dev   ask Telegram for the webhook registration"
	@echo "bot-token ENV=dev    print the bot token parameter value"

install:
	$(PY) -m venv $(VENV)
	$(VPY) -m pip install --upgrade pip
	$(VPY) -m pip install -r app/requirements.txt -r app/requirements-dev.txt

image: image-api image-worker

image-api:
	docker buildx build --platform linux/arm64 --provenance=false --sbom=false --target api \
		-t $(LOCAL_IMG):$(IMAGE_TAG)-api --load app

image-worker:
	docker buildx build --platform linux/arm64 --provenance=false --sbom=false --target worker \
		-t $(LOCAL_IMG):$(IMAGE_TAG)-worker --load app

# The Docker host deployment: one image, both roles, built for the Pi's arm64.
pi-image:
	docker buildx build --platform linux/arm64 --provenance=false --sbom=false \
		-f app/Dockerfile.pi -t $(PI_IMG):$(IMAGE_TAG) --load app

# No registry: the image travels as a stream. Loading is idempotent per tag.
pi-ship: pi-image
	docker save $(PI_IMG):$(IMAGE_TAG) | gzip -1 | ssh $(PI_HOST) 'gunzip | sudo docker load'

test: test-unit test-contract

test-unit:
	cd app && ../$(VPY) -m pytest tests/unit

test-contract:
	cd app && ../$(VPY) -m pytest tests/contract

test-e2e:
	cd app && ../$(VPY) -m pytest -m e2e tests/e2e

lint:
	$(VPY) -m ruff check app/
	$(VPY) -m ruff format --check app/

fmt:
	$(VPY) -m ruff check --fix app/
	$(VPY) -m ruff format app/

verify-image:
	@test -n "$(VIDEO_URL)" || { echo "usage: make verify-image VIDEO_URL=<url>"; exit 1; }
	docker run --rm --platform linux/arm64 --entrypoint /bin/bash \
		$(LOCAL_IMG):$(IMAGE_TAG)-worker -c 'set -eu; \
		yt-dlp --version; \
		ffmpeg -version | head -n 1; \
		deno --version | head -n 1; \
		yt-dlp --no-playlist \
		  -f "bv*[height<=720][vcodec^=avc1]+ba[acodec^=mp4a]/b[height<=720]" \
		  --merge-output-format mp4 -o "/tmp/smoke.%(ext)s" "$(VIDEO_URL)"; \
		ffprobe -v error -show_entries stream=codec_name,height -of csv=p=0 /tmp/smoke.mp4'

tf-check:
	terraform -chdir=infra fmt -check -recursive
	@test -d infra/.terraform || terraform -chdir=infra init -backend=false -input=false >/dev/null
	terraform -chdir=infra validate
	terraform -chdir=infra/bootstrap fmt -check -recursive
	@test -d infra/bootstrap/.terraform || terraform -chdir=infra/bootstrap init -backend=false -input=false >/dev/null
	terraform -chdir=infra/bootstrap validate

# ------------------------------------------------------------------------------
# Provisioning. Every resource is declared in infra/; these targets only run
# Terraform and the image build, never an ad-hoc AWS mutation.
# ------------------------------------------------------------------------------

bootstrap:
	@ACCOUNT=$$(aws sts get-caller-identity --query Account --output text 2>/dev/null); \
	test -n "$$ACCOUNT" || { echo "no AWS credentials in this shell"; exit 1; }; \
	terraform -chdir=infra/bootstrap init -input=false; \
	terraform -chdir=infra/bootstrap apply -input=false -auto-approve \
		-var state_bucket=$(STATE_BUCKET_PREFIX)-$$ACCOUNT -var aws_region=$(AWS_REGION); \
	echo "state bucket ready: $(STATE_BUCKET_PREFIX)-$$ACCOUNT"

deploy:
	@test -n "$(ENV)" || { echo "usage: make deploy ENV=dev"; exit 1; }
	@test -f infra/env/$(ENV).tfvars || { echo "missing infra/env/$(ENV).tfvars"; exit 1; }
	@test -f infra/backend/$(ENV).backend || { echo "missing infra/backend/$(ENV).backend"; exit 1; }
	@ACCOUNT=$$(aws sts get-caller-identity --query Account --output text 2>/dev/null); \
	test -n "$$ACCOUNT" || { echo "no AWS credentials in this shell"; exit 1; }; \
	terraform -chdir=infra init -input=false -reconfigure \
		-backend-config=backend/$(ENV).backend \
		-backend-config=bucket=$(STATE_BUCKET_PREFIX)-$$ACCOUNT; \
	terraform -chdir=infra apply -input=false -auto-approve \
		-var-file=env/$(ENV).tfvars -var image_tag=$(IMAGE_TAG) \
		-target=aws_ecr_repository.app; \
	ECR=$$(terraform -chdir=infra output -raw ecr_repository_url); \
	test -n "$$ECR" || { echo "no ECR repository url in outputs"; exit 1; }; \
	REGISTRY=$$(echo "$$ECR" | cut -d/ -f1); \
	aws ecr get-login-password --region $(AWS_REGION) \
		| docker login --username AWS --password-stdin "$$REGISTRY"; \
	docker buildx build --platform linux/arm64 --provenance=false --sbom=false --target api \
		-t $$ECR:$(IMAGE_TAG)-api --push app; \
	docker buildx build --platform linux/arm64 --provenance=false --sbom=false --target worker \
		-t $$ECR:$(IMAGE_TAG)-worker --push app; \
	terraform -chdir=infra apply -input=false -auto-approve \
		-var-file=env/$(ENV).tfvars -var image_tag=$(IMAGE_TAG)

destroy:
	@test -n "$(ENV)" || { echo "usage: make destroy ENV=dev"; exit 1; }
	@test -f infra/env/$(ENV).tfvars || { echo "missing infra/env/$(ENV).tfvars"; exit 1; }
	@test -f infra/backend/$(ENV).backend || { echo "missing infra/backend/$(ENV).backend"; exit 1; }
	@ACCOUNT=$$(aws sts get-caller-identity --query Account --output text 2>/dev/null); \
	test -n "$$ACCOUNT" || { echo "no AWS credentials in this shell"; exit 1; }; \
	terraform -chdir=infra init -input=false -reconfigure \
		-backend-config=backend/$(ENV).backend \
		-backend-config=bucket=$(STATE_BUCKET_PREFIX)-$$ACCOUNT; \
	terraform -chdir=infra destroy -input=false -auto-approve \
		-var-file=env/$(ENV).tfvars -var image_tag=$(IMAGE_TAG)

outputs:
	@test -n "$(ENV)" || { echo "usage: make outputs ENV=dev"; exit 1; }
	@ACCOUNT=$$(aws sts get-caller-identity --query Account --output text 2>/dev/null); \
	test -n "$$ACCOUNT" || { echo "no AWS credentials in this shell"; exit 1; }; \
	terraform -chdir=infra init -input=false -reconfigure \
		-backend-config=backend/$(ENV).backend \
		-backend-config=bucket=$(STATE_BUCKET_PREFIX)-$$ACCOUNT >/dev/null; \
	terraform -chdir=infra output

token:
	@test -n "$(ENV)" || { echo "usage: make token ENV=dev"; exit 1; }
	@$(MAKE) -s outputs ENV=$(ENV) >/dev/null
	@aws ssm get-parameter \
		--name "$$(terraform -chdir=infra output -raw token_parameter_name)" \
		--with-decryption --query Parameter.Value --output text

# The help text is the operator's, so the one test that compares it with
# contracts/bot-commands.md is runnable on its own.
bot-help-check:
	cd app && ../$(VPY) -m pytest tests/unit/test_bot_help.py

# Read-only: one getWebhookInfo call, no chat traffic. The token is read into a
# shell variable and never written to a file or a log.
bot-health:
	@test -n "$(ENV)" || { echo "usage: make bot-health ENV=dev"; exit 1; }
	@$(MAKE) -s outputs ENV=$(ENV) >/dev/null
	@TOKEN=$$(aws ssm get-parameter --with-decryption --output text \
		--query Parameter.Value \
		--name "$$(terraform -chdir=infra output -raw telegram_token_parameter_name)"); \
	curl -sS "https://api.telegram.org/bot$$TOKEN/getWebhookInfo" \
	| python3 -c 'import json,sys; \
data = json.load(sys.stdin); \
info = data.get("result") or {}; \
print("url                   " + str(info.get("url"))); \
print("pending_update_count  " + str(info.get("pending_update_count"))); \
print("max_connections       " + str(info.get("max_connections"))); \
print("allowed_updates       " + str(info.get("allowed_updates")))'

bot-token:
	@test -n "$(ENV)" || { echo "usage: make bot-token ENV=dev"; exit 1; }
	@$(MAKE) -s outputs ENV=$(ENV) >/dev/null
	@aws ssm get-parameter --with-decryption --output text --query Parameter.Value \
		--name "$$(terraform -chdir=infra output -raw telegram_token_parameter_name)"

# The state bucket is deliberately outside the application lifecycle, so it is the
# one name this check excludes. Everything else matching `ytdl-` must be gone.
verify-destroy:
	@test -n "$(ENV)" || { echo "usage: make verify-destroy ENV=dev"; exit 1; }
	@set -uo pipefail; \
	survivors=0; \
	report() { echo "SURVIVOR: $$1"; survivors=1; }; \
	for b in $$(aws s3 ls | awk '{print $$3}' | grep '^ytdl-' | grep -v '^$(STATE_BUCKET_PREFIX)-'); do report "s3://$$b"; done; \
	for t in $$(aws dynamodb list-tables --query 'TableNames[]' --output text | tr '\t' '\n' | grep '^ytdl-'); do report "dynamodb $$t"; done; \
	for q in $$(aws sqs list-queues --queue-name-prefix ytdl- --query 'QueueUrls[]' --output text | tr '\t' '\n' | grep -vx 'None'); do report "sqs $$q"; done; \
	for f in $$(aws lambda list-functions --query 'Functions[].FunctionName' --output text | tr '\t' '\n' | grep '^ytdl-'); do report "lambda $$f"; done; \
	for r in $$(aws ecr describe-repositories --query 'repositories[].repositoryName' --output text | tr '\t' '\n' | grep '^ytdl-'); do report "ecr $$r"; done; \
	for a in $$(aws apigatewayv2 get-apis --query 'Items[].Name' --output text | tr '\t' '\n' | grep '^ytdl-'); do report "apigateway $$a"; done; \
	for p in $$(aws ssm describe-parameters --parameter-filters 'Key=Name,Option=Contains,Values=/ytdl/' --query 'Parameters[].Name' --output text | tr '\t' '\n'); do report "ssm $$p"; done; \
	if [ "$$survivors" -ne 0 ]; then echo "destroy incomplete"; exit 1; fi; \
	echo "no surviving resources (state bucket excluded by design)"

verify-retention:
	@BUCKET=$$(terraform -chdir=infra output -raw media_bucket); \
	OLDEST=$$(aws s3api list-objects-v2 --bucket $$BUCKET --prefix output/ \
		--query 'sort_by(Contents, &LastModified)[0].LastModified' --output text); \
	echo "oldest object under output/: $$OLDEST"; \
	AGE=$$(python3 -c "import datetime,sys; print(0 if sys.argv[1] in ('','None') else (datetime.datetime.now(datetime.timezone.utc)-datetime.datetime.fromisoformat(sys.argv[1].replace('Z','+00:00'))).days)" "$$OLDEST"); \
	test "$$AGE" -lt 8 || { echo "retention violated: oldest object is $$AGE days old"; exit 1; }; \
	echo "retention ok: oldest object is $$AGE days old"

verify-idempotency:
	@test -n "$(JOB_ID)" || { echo "usage: make verify-idempotency JOB_ID=<id> VIDEO_ID=<id>"; exit 1; }
	@test -n "$(VIDEO_ID)" || { echo "usage: make verify-idempotency JOB_ID=<id> VIDEO_ID=<id>"; exit 1; }
	@FUNC=$$(terraform -chdir=infra output -raw worker_function_name); \
	BUCKET=$$(terraform -chdir=infra output -raw media_bucket); \
	BEFORE=$$(aws s3api head-object --bucket $$BUCKET --key output/$(JOB_ID)/$(VIDEO_ID).mp4 --query ContentLength --output text); \
	for i in $$(seq 1 $${COUNT:-100}); do \
		aws lambda invoke --function-name $$FUNC --payload '{"Records":[{"messageId":"idem-'$$i'","eventSource":"aws:sqs","body":"{\"version\":1,\"job_id\":\"$(JOB_ID)\",\"video_id\":\"$(VIDEO_ID)\",\"source_host\":\"www.youtube.com\",\"requested_height\":null,\"enqueued_at\":\"2026-01-01T00:00:00Z\"}"}]}' \
			--cli-binary-format raw-in-base64-out /tmp/idem-out.json >/dev/null; \
	done; \
	COUNT_OBJ=$$(aws s3api list-objects-v2 --bucket $$BUCKET --prefix output/$(JOB_ID)/ --query 'length(Contents)' --output text); \
	AFTER=$$(aws s3api head-object --bucket $$BUCKET --key output/$(JOB_ID)/$(VIDEO_ID).mp4 --query ContentLength --output text); \
	test "$$COUNT_OBJ" = "1" || { echo "expected 1 object, found $$COUNT_OBJ"; exit 1; }; \
	test "$$BEFORE" = "$$AFTER" || { echo "size changed: $$BEFORE -> $$AFTER"; exit 1; }; \
	echo "idempotent: 1 object, size $$AFTER unchanged over $${COUNT:-100} replays"

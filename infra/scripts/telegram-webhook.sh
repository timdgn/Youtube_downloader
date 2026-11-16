#!/usr/bin/env bash
#
# Registers or removes the Telegram webhook for one environment.
#
# Telegram's webhook is not an AWS object and no maintained Terraform provider
# exposes it, so terraform_data in infra/bot.tf calls this script on create and
# on destroy (research D-02). The token and the secret are read from SSM at run
# time, so neither is an argument, an environment value, or a log line.
#
# Usage: telegram-webhook.sh set|delete
# Environment: WEBHOOK_URL, TOKEN_PARAMETER, SECRET_PARAMETER, AWS_REGION

set -eu

action="${1:-}"

case "$action" in
  set | delete) ;;
  *)
    echo "usage: $(basename "$0") set|delete" >&2
    exit 2
    ;;
esac

: "${TOKEN_PARAMETER:?TOKEN_PARAMETER must be set}"
: "${AWS_REGION:?AWS_REGION must be set}"

# Never printed, not even in an error path: every message below is either a
# static sentence or Telegram's own description.
token=$(aws ssm get-parameter \
  --name "$TOKEN_PARAMETER" \
  --with-decryption \
  --region "$AWS_REGION" \
  --query Parameter.Value \
  --output text)

if [ "$action" = "set" ]; then
  : "${WEBHOOK_URL:?WEBHOOK_URL must be set}"
  : "${SECRET_PARAMETER:?SECRET_PARAMETER must be set}"

  secret=$(aws ssm get-parameter \
    --name "$SECRET_PARAMETER" \
    --with-decryption \
    --region "$AWS_REGION" \
    --query Parameter.Value \
    --output text)

  # Built with python3 so a quote in either value cannot break the JSON. The
  # values are arguments to a short-lived process and are never printed.
  payload=$(python3 - "$WEBHOOK_URL" "$secret" <<'PY'
import json
import sys

print(
    json.dumps(
        {
            "url": sys.argv[1],
            "secret_token": sys.argv[2],
            # message only: edits, reactions and callback queries never reach
            # the function. max_connections matches its reserved concurrency.
            "allowed_updates": ["message"],
            "max_connections": 5,
            "drop_pending_updates": True,
        }
    )
)
PY
  )
  method=setWebhook
else
  # drop_pending_updates discards the updates Telegram queued while the bot
  # existed, so a later re-registration does not replay old messages.
  payload='{"drop_pending_updates": true}'
  method=deleteWebhook
fi

# --data @- reads the body from stdin, so the payload is not part of curl's
# command line. The token is in the URL by Telegram's design, never printed.
body=$(printf '%s' "$payload" | curl -sS \
  --header "Content-Type: application/json" \
  --data @- \
  "https://api.telegram.org/bot${token}/${method}")

# The answer is parsed with python3, so the script needs no jq. A non-OK answer
# prints Telegram's own description and exits 1, which fails the apply and
# leaves the reason in the operator's terminal.
printf '%s' "$body" | python3 -c '
import json
import sys

method = sys.argv[1]

try:
    answer = json.loads(sys.stdin.read())
except ValueError:
    sys.exit("Telegram %s failed: the answer was not JSON." % method)

if answer.get("ok") is not True:
    description = answer.get("description", "no description in the answer")
    sys.exit("Telegram %s failed: %s" % (method, description))

print("Telegram %s succeeded." % method)
' "$method"

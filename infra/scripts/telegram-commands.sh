#!/usr/bin/env bash
#
# Registers the bot command menu, the list a client shows when the operator
# types "/". It is Telegram-side state that no AWS object owns, so
# terraform_data in infra/bot.tf calls this script on create (research D-02).
# The token is read from SSM at run time, never an argument or a log line.
#
# Usage: telegram-commands.sh
# Environment: COMMANDS (JSON array of {command, description}), TOKEN_PARAMETER, AWS_REGION

set -eu

: "${COMMANDS:?COMMANDS must be set}"
: "${TOKEN_PARAMETER:?TOKEN_PARAMETER must be set}"
: "${AWS_REGION:?AWS_REGION must be set}"

token=$(aws ssm get-parameter \
  --name "$TOKEN_PARAMETER" \
  --with-decryption \
  --region "$AWS_REGION" \
  --query Parameter.Value \
  --output text)

# Built with python3 so a quote in a description cannot break the JSON. The list
# is validated by Telegram, which names the offending command in its answer.
payload=$(python3 - "$COMMANDS" <<'PY'
import json
import sys

commands = json.loads(sys.argv[1])
print(json.dumps({"commands": commands}))
PY
)

body=$(printf '%s' "$payload" | curl -sS \
  --header "Content-Type: application/json" \
  --data @- \
  "https://api.telegram.org/bot${token}/setMyCommands")

printf '%s' "$body" | python3 -c '
import json
import sys

try:
    answer = json.loads(sys.stdin.read())
except ValueError:
    sys.exit("Telegram setMyCommands failed: the answer was not JSON.")

if answer.get("ok") is not True:
    description = answer.get("description", "no description in the answer")
    sys.exit("Telegram setMyCommands failed: %s" % description)

print("Telegram setMyCommands succeeded.")
'

#!/usr/bin/env bash
# Deploys the app to AWS: bootstraps CDK if needed, deploys the stack, stores your Anthropic
# API key in Secrets Manager, and restarts the app so it picks the key up.
#
# Usage (from the repo root, with AWS credentials configured and Docker running):
#   export ANTHROPIC_API_KEY=...
#   ./scripts/deploy.sh
#
# Costs about $0.10/hour while deployed. Remove everything with ./scripts/destroy.sh.
set -euo pipefail
cd "$(dirname "$0")/.."

STACK=LegalAssistanceStack
CDK="npx --yes aws-cdk@2"
# cdk.json runs `python3 src/infra/app.py`, which needs aws-cdk-lib from the project venv.
[ -d .venv ] && export PATH="$PWD/.venv/bin:$PATH"

: "${ANTHROPIC_API_KEY:?Set ANTHROPIC_API_KEY to your Anthropic API key first.}"
# Check the key with Anthropic before creating anything: listing models is free and read-only.
STATUS=$(curl -s -o /dev/null -w '%{http_code}' https://api.anthropic.com/v1/models \
  -H "x-api-key: $ANTHROPIC_API_KEY" -H 'anthropic-version: 2023-06-01' || echo 000)
case "$STATUS" in
  200) ;;
  401|403) echo "Anthropic rejected ANTHROPIC_API_KEY (HTTP $STATUS). Check the key and try again." >&2; exit 1 ;;
  *) echo "Couldn't verify ANTHROPIC_API_KEY with Anthropic (HTTP $STATUS); continuing anyway." >&2 ;;
esac
docker info >/dev/null 2>&1 || { echo "Docker isn't running; start it first (CDK builds the app image)." >&2; exit 1; }

ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
# us-east-2 by default: this account's AWS Organization only allows that region. Set
# AWS_REGION to deploy elsewhere. Exporting it makes the CDK CLI use it too, instead of
# the AWS CLI's configured default region.
REGION=${AWS_REGION:-us-east-2}
export AWS_REGION=$REGION AWS_DEFAULT_REGION=$REGION CDK_DEFAULT_ACCOUNT=$ACCOUNT CDK_DEFAULT_REGION=$REGION
echo "Deploying $STACK to account $ACCOUNT in $REGION"

if ! aws cloudformation describe-stacks --stack-name CDKToolkit --region "$REGION" >/dev/null 2>&1; then
  echo "Bootstrapping CDK in $ACCOUNT/$REGION (one time)..."
  $CDK bootstrap "aws://$ACCOUNT/$REGION"
fi

# CDK shows the IAM and security group changes and asks for approval before creating anything.
$CDK deploy "$STACK" --outputs-file cdk-outputs.json

output() { python3 -c "import json,sys; print(json.load(open('cdk-outputs.json'))['$STACK']['$1'])"; }
SECRET_ARN=$(output AnthropicSecretArn)
CLUSTER=$(output ClusterName)
SERVICE=$(output ServiceName)
URL=$(output AppUrl)

echo "Storing the Anthropic API key in Secrets Manager..."
aws secretsmanager put-secret-value --region "$REGION" --secret-id "$SECRET_ARN" \
  --secret-string "$ANTHROPIC_API_KEY" >/dev/null

# ECS reads secrets when a task starts, so restart the app to pick up the key.
echo "Restarting the app with the key (takes a few minutes)..."
aws ecs update-service --region "$REGION" --cluster "$CLUSTER" --service "$SERVICE" \
  --force-new-deployment >/dev/null
aws ecs wait services-stable --region "$REGION" --cluster "$CLUSTER" --services "$SERVICE"

if curl -fsS "$URL/health" >/dev/null; then
  echo -e "\nDeployed: $URL\nRemove everything when you're done: ./scripts/destroy.sh"
else
  echo -e "\nThe stack deployed, but $URL/health isn't responding yet. Check the logs:" >&2
  echo "  aws logs tail --follow --region $REGION \$(aws logs describe-log-groups --region $REGION --query \"logGroups[?contains(logGroupName,'$STACK')].logGroupName\" --output text)" >&2
  exit 1
fi

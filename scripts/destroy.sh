#!/usr/bin/env bash
# Deletes everything ./scripts/deploy.sh created: the app, load balancer, database (with no
# final snapshot), secrets, logs, and network. Takes about 10-15 minutes, mostly RDS.
#
# Left behind on purpose: the CDKToolkit bootstrap stack (an S3 bucket and an ECR repository
# holding the app image), which CDK reuses for future deploys and costs a few cents a month.
#
# Usage (from the repo root): ./scripts/destroy.sh
set -euo pipefail
cd "$(dirname "$0")/.."

STACK=LegalAssistanceStack
[ -d .venv ] && export PATH="$PWD/.venv/bin:$PATH"

ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
# us-east-2 by default: this account's AWS Organization only allows that region. Set
# AWS_REGION to deploy elsewhere. Exporting it makes the CDK CLI use it too, instead of
# the AWS CLI's configured default region.
REGION=${AWS_REGION:-us-east-2}
export AWS_REGION=$REGION AWS_DEFAULT_REGION=$REGION CDK_DEFAULT_ACCOUNT=$ACCOUNT CDK_DEFAULT_REGION=$REGION
echo "Destroying $STACK in account $ACCOUNT, region $REGION. The database and its data will be deleted."

# CDK asks for confirmation before deleting.
npx --yes aws-cdk@2 destroy "$STACK"
rm -f cdk-outputs.json

if aws cloudformation describe-stacks --stack-name "$STACK" --region "$REGION" >/dev/null 2>&1; then
  echo "The stack still exists; check the CloudFormation console for what failed to delete." >&2
  exit 1
fi
echo "Done: $STACK and everything in it is deleted."

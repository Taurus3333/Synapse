#!/usr/bin/env bash
# Build + push Synapse API image to Terraform ECR output.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TF="${ROOT}/infra/terraform"
REGION="${AWS_REGION:-us-east-1}"
TAG="${1:-latest}"

cd "$TF"
REPO="$(terraform output -raw ecr_repository_url)"
aws ecr get-login-password --region "$REGION" \
  | docker login --username AWS --password-stdin "${REPO%%/*}"

docker build -t "synapse-api:${TAG}" "$ROOT"
docker tag "synapse-api:${TAG}" "${REPO}:${TAG}"
docker push "${REPO}:${TAG}"
DIGEST="$(aws ecr describe-images --repository-name "${REPO##*/}" \
  --image-ids imageTag="${TAG}" --region "$REGION" \
  --query 'imageDetails[0].imageDigest' --output text)"
echo "Pushed ${REPO}:${TAG} digest=${DIGEST}"
echo "Next: ./scripts/deploy/rollout.sh 1 ${TAG}"

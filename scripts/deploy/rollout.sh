#!/usr/bin/env bash
# Scale ECS + force new deployment after ecr_push.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TF="${ROOT}/infra/terraform"
REGION="${AWS_REGION:-us-east-1}"
DESIRED="${1:-1}"
TAG="${2:-latest}"

cd "$TF"
CLUSTER="$(terraform output -raw ecs_cluster_name)"
SERVICE="$(terraform output -raw ecs_service_name)"
API_URL="$(terraform output -raw api_url)"

terraform apply -auto-approve \
  -var="api_image_tag=${TAG}" \
  -var="api_desired_count=${DESIRED}"

aws ecs update-service --region "$REGION" --cluster "$CLUSTER" --service "$SERVICE" \
  --desired-count "$DESIRED" --force-new-deployment >/dev/null
aws ecs wait services-stable --region "$REGION" --cluster "$CLUSTER" --services "$SERVICE"
echo "API URL: ${API_URL}"
echo "Probe:  curl ${API_URL}/health"

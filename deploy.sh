#!/usr/bin/env bash
# Build, push and deploy to App Runner.
#
# Always deploys an IMMUTABLE tag. App Runner caches by image identifier, so pushing a
# new ":latest" and calling update-service reports success while continuing to serve the
# previous image — a deploy that silently ships nothing. The tag is <git-sha>-<time>.
set -euo pipefail

# Exported, not plain assignments: the heredoc below builds the deploy JSON in python
# and reads every one of these out of the environment.
export REGION=ap-south-1
export ACCOUNT=085193942944
export REPO=vox-photoshoot
export SERVICE_ARN="arn:aws:apprunner:${REGION}:${ACCOUNT}:service/vox-photoshoot/74c51f50e3014e2587ea8fa9caed99f0"
export BUCKET=vox-photoshoot-085193942944
export INSTANCE_ROLE="arn:aws:iam::${ACCOUNT}:role/VoxPhotoshootInstanceRole"
export ACCESS_ROLE="arn:aws:iam::${ACCOUNT}:role/AppRunnerECRAccessRole"
export URI="${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com/${REPO}"

cd "$(dirname "$0")"
[ -f .env ] || { echo "no .env — need FAL_KEY, HF_KEY, ANTHROPIC_API_KEY, DATABASE_URL" >&2; exit 1; }
set -a; . ./.env; set +a
: "${FAL_KEY:?FAL_KEY missing}"
# Without it every upload is shot as the client's earrings — see product.identify.
: "${ANTHROPIC_API_KEY:?ANTHROPIC_API_KEY missing (reads the uploaded piece)}"
: "${DATABASE_URL:?DATABASE_URL missing (users, jobs and credits live there)}"

# The container reads secrets from here, not from RuntimeEnvironmentVariables. Keep the
# secret in step with .env before deploying, or the running app gets stale credentials.
export SECRET_ARN="arn:aws:secretsmanager:${REGION}:${ACCOUNT}:secret:vox-photoshoot/env"
echo "==> syncing secrets"
python3 - <<'PY' > /tmp/vox-secret.json
import json, os
print(json.dumps({k: os.environ.get(k, '') for k in
                  ('FAL_KEY', 'HF_KEY', 'ANTHROPIC_API_KEY', 'DATABASE_URL')}))
PY
aws secretsmanager put-secret-value --secret-id vox-photoshoot/env --region "$REGION" \
  --secret-string file:///tmp/vox-secret.json --query VersionId --output text
rm -f /tmp/vox-secret.json

# Docker Desktop's credential helper is often absent from a non-interactive PATH.
export DOCKER_CONFIG=${DOCKER_CONFIG:-/tmp/vox-docker}
mkdir -p "$DOCKER_CONFIG"; [ -f "$DOCKER_CONFIG/config.json" ] || echo '{}' > "$DOCKER_CONFIG/config.json"

TAG="$(git rev-parse --short HEAD)-$(date +%H%M%S)"
echo "==> building ${TAG}"
aws ecr get-login-password --region "$REGION" \
  | docker login --username AWS --password-stdin "${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com" >/dev/null
docker build --platform linux/amd64 -t "${URI}:${TAG}" .
docker push "${URI}:${TAG}"

echo "==> deploying ${TAG}"
python3 - "$TAG" > /tmp/vox-deploy.json <<'PY'
import json, os, sys
tag = sys.argv[1]
print(json.dumps({
  "ServiceArn": os.environ["SERVICE_ARN"],
  "SourceConfiguration": {
    "AuthenticationConfiguration": {"AccessRoleArn": os.environ["ACCESS_ROLE"]},
    "AutoDeploymentsEnabled": False,
    "ImageRepository": {
      "ImageIdentifier": f'{os.environ["URI"]}:{tag}',
      "ImageRepositoryType": "ECR",
      "ImageConfiguration": {
        "Port": "8080",
        # Non-secret only. Anyone with apprunner:DescribeService can read these.
        "RuntimeEnvironmentVariables": {
          "PROVIDER": os.environ.get("PROVIDER", "fal"),
          "S3_BUCKET": os.environ["BUCKET"],
          "AWS_REGION": os.environ["REGION"],
        },
        # Pulled from Secrets Manager at container start, by the instance role. The
        # values never appear in the service description, in CloudTrail, or in this
        # file. Rotating a key is `put-secret-value` plus a restart — no redeploy.
        "RuntimeEnvironmentSecrets": {
          name: f'{os.environ["SECRET_ARN"]}:{name}::'
          for name in ("FAL_KEY", "HF_KEY", "ANTHROPIC_API_KEY", "DATABASE_URL")
        },
      },
    },
  },
  "InstanceConfiguration": {"Cpu": "512", "Memory": "1024",
                            "InstanceRoleArn": os.environ["INSTANCE_ROLE"]},
}))
PY
aws apprunner update-service --cli-input-json file:///tmp/vox-deploy.json \
  --region "$REGION" --query 'Service.Status' --output text
rm -f /tmp/vox-deploy.json   # contained the API keys

until [ "$(aws apprunner describe-service --service-arn "$SERVICE_ARN" --region "$REGION" \
          --query 'Service.Status' --output text)" != "OPERATION_IN_PROGRESS" ]; do sleep 20; done

echo "==> verifying the deployed image is the one just built"
LIVE=$(aws apprunner describe-service --service-arn "$SERVICE_ARN" --region "$REGION" \
       --query 'Service.SourceConfiguration.ImageRepository.ImageIdentifier' --output text)
[ "$LIVE" = "${URI}:${TAG}" ] || { echo "FAIL: serving $LIVE, expected ${URI}:${TAG}" >&2; exit 1; }
curl -fsS -m 30 https://photo.voxdonna.com/healthz && echo && echo "deployed ${TAG}"

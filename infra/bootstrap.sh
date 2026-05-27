#!/usr/bin/env bash
# ── 1회성 부트스트랩 스크립트 ────────────────────────────────────────────────
# Terraform 상태를 저장할 S3 버킷 + DynamoDB 테이블 생성
# Terraform 자체는 이 리소스들을 관리하지 않음 (닭-달걀 문제 방지)
#
# 실행 방법:
#   aws sso login --profile delivus-dev
#   export AWS_PROFILE=delivus-dev
#   bash infra/bootstrap.sh

set -euo pipefail

REGION="ap-northeast-2"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
BUCKET="dk-haejo-tfstate-${ACCOUNT_ID}"
TABLE="dk-haejo-tfstate-lock"

echo "▶ 계정 ID: $ACCOUNT_ID"
echo "▶ S3 버킷 생성: $BUCKET"

# S3 버킷 생성
aws s3api create-bucket \
  --bucket "$BUCKET" \
  --region "$REGION" \
  --create-bucket-configuration LocationConstraint="$REGION" 2>/dev/null || \
  echo "  (이미 존재함 — 건너뜀)"

# 버킷 버전 관리 활성화 (상태 파일 보호)
aws s3api put-bucket-versioning \
  --bucket "$BUCKET" \
  --versioning-configuration Status=Enabled

# 퍼블릭 액세스 차단
aws s3api put-public-access-block \
  --bucket "$BUCKET" \
  --public-access-block-configuration \
    BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true

echo "▶ DynamoDB 테이블 생성: $TABLE"
aws dynamodb create-table \
  --table-name "$TABLE" \
  --attribute-definitions AttributeName=LockID,AttributeType=S \
  --key-schema AttributeName=LockID,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST \
  --region "$REGION" 2>/dev/null || \
  echo "  (이미 존재함 — 건너뜀)"

echo ""
echo "✅ 완료! 이제 infra/main.tf의 backend 블록 주석을 해제하고 아래 명령 실행:"
echo ""
echo "  cd infra"
echo "  terraform init -migrate-state"
echo "  terraform apply"

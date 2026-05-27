# ── GitHub Actions OIDC 연동 ──────────────────────────────────────────────────
# GitHub이 AWS에 직접 인증 → 장기 Access Key 저장 불필요

data "tls_certificate" "github" {
  url = "https://token.actions.githubusercontent.com/.well-known/openid-configuration"
}

resource "aws_iam_openid_connect_provider" "github" {
  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = [data.tls_certificate.github.certificates[0].sha1_fingerprint]

  tags = { Name = "dk-haejo-github-oidc" }
}

# ── GitHub Actions가 Assume할 IAM 역할 ────────────────────────────────────────
resource "aws_iam_role" "github_deploy" {
  name = "dk-haejo-github-deploy-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Federated = aws_iam_openid_connect_provider.github.arn
      }
      Action = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringLike = {
          # 이 레포지토리의 모든 브랜치/태그에서만 Assume 허용
          "token.actions.githubusercontent.com:sub" = "repo:kokospapa8/dk-haejo:*"
        }
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
        }
      }
    }]
  })

  tags = { Name = "dk-haejo-github-deploy-role" }
}

# ── GitHub Actions 배포 권한 ──────────────────────────────────────────────────
resource "aws_iam_role_policy" "github_deploy" {
  name = "dk-haejo-github-deploy-policy"
  role = aws_iam_role.github_deploy.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      # SSM으로 EC2에서 명령 실행
      {
        Effect = "Allow"
        Action = [
          "ssm:SendCommand",
          "ssm:GetCommandInvocation",
          "ssm:ListCommandInvocations",
          "ssm:DescribeInstanceInformation",
        ]
        Resource = "*"
      },
      # EC2 인스턴스 정보 조회
      {
        Effect   = "Allow"
        Action   = ["ec2:DescribeInstances", "ec2:DescribeInstanceStatus"]
        Resource = "*"
      },
      # Terraform 상태 S3 버킷 접근
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject",
          "s3:ListBucket",
        ]
        Resource = [
          "arn:aws:s3:::dk-haejo-tfstate-${data.aws_caller_identity.current.account_id}",
          "arn:aws:s3:::dk-haejo-tfstate-${data.aws_caller_identity.current.account_id}/*",
        ]
      },
      # Terraform 상태 잠금 (DynamoDB)
      {
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:PutItem",
          "dynamodb:DeleteItem",
        ]
        Resource = "arn:aws:dynamodb:${var.aws_region}:${data.aws_caller_identity.current.account_id}:table/dk-haejo-tfstate-lock"
      },
    ]
  })
}

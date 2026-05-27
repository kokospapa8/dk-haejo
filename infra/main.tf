terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    tls = {
      source  = "hashicorp/tls"
      version = "~> 4.0"
    }
  }

  # ── Terraform 상태 원격 저장 (S3) ──────────────────────────────────────────
  # bootstrap.sh 실행 후 아래 주석 해제
  # backend "s3" {
  #   bucket         = "dk-haejo-tfstate-399932611745"
  #   key            = "discord-bot/terraform.tfstate"
  #   region         = "ap-northeast-2"
  #   dynamodb_table = "dk-haejo-tfstate-lock"
  #   encrypt        = true
  # }
}

provider "aws" {
  region = var.aws_region
}

# ── 최신 Ubuntu 22.04 AMI ─────────────────────────────────────────────────────
data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"] # Canonical

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*"]
  }
  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

data "aws_caller_identity" "current" {}

# ── Security Group ────────────────────────────────────────────────────────────
resource "aws_security_group" "bot" {
  name        = "dk-haejo-bot-sg"
  description = "Discord music bot: outbound only"

  # SSH (비상 접속용 — SSM이 기본, 필요 없으면 삭제 가능)
  ingress {
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "SSH emergency access"
  }

  # 모든 아웃바운드 (Discord, YouTube, Anthropic API)
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "dk-haejo-bot-sg" }
}

# ── EC2 IAM 역할 (SSM 에이전트용) ─────────────────────────────────────────────
resource "aws_iam_role" "ec2" {
  name = "dk-haejo-ec2-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "ec2_ssm" {
  role       = aws_iam_role.ec2.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "ec2" {
  name = "dk-haejo-ec2-profile"
  role = aws_iam_role.ec2.name
}

# ── EC2 키페어 ────────────────────────────────────────────────────────────────
resource "aws_key_pair" "bot" {
  key_name   = "dk-haejo-key"
  public_key = var.ssh_public_key
}

# ── EC2 인스턴스 ──────────────────────────────────────────────────────────────
resource "aws_instance" "bot" {
  ami                    = data.aws_ami.ubuntu.id
  instance_type          = var.instance_type
  key_name               = aws_key_pair.bot.key_name
  vpc_security_group_ids = [aws_security_group.bot.id]
  iam_instance_profile   = aws_iam_instance_profile.ec2.name

  # 서버 초기 설정 스크립트
  user_data = <<-EOF
    #!/bin/bash
    set -e
    apt-get update -y
    apt-get install -y docker.io docker-compose-plugin git curl unzip

    # Docker 서비스 시작
    systemctl enable docker
    systemctl start docker
    usermod -aG docker ubuntu

    # AWS SSM 에이전트 (Ubuntu 22.04에 포함되어 있지만 확인)
    systemctl enable amazon-ssm-agent 2>/dev/null || \
      snap install amazon-ssm-agent --classic && \
      systemctl enable snap.amazon-ssm-agent.amazon-ssm-agent.service

    echo "✅ 초기 설정 완료" >> /var/log/user-data.log
  EOF

  root_block_device {
    volume_size           = 20
    volume_type           = "gp3"
    delete_on_termination = true
  }

  tags = { Name = "dk-haejo-bot" }
}

# ── Elastic IP (인스턴스 재시작해도 IP 유지) ──────────────────────────────────
resource "aws_eip" "bot" {
  instance = aws_instance.bot.id
  domain   = "vpc"
  tags     = { Name = "dk-haejo-eip" }

  depends_on = [aws_instance.bot]
}

#!/bin/bash
# EC2 배포 스크립트 — SSM으로 실행됨 (root 권한)
set -e

# ── Docker / git 없으면 설치 (user_data 완료 전 대비) ─────────────────────────
if ! command -v docker &>/dev/null || ! command -v git &>/dev/null; then
  echo "패키지 설치 중..."
  apt-get update -y -qq
  apt-get install -y -qq docker.io docker-compose-plugin git
  systemctl enable docker
  systemctl start docker
fi

if ! docker compose version &>/dev/null 2>&1; then
  apt-get install -y -qq docker-compose-plugin
fi

REPO=/home/ubuntu/dk-haejo

# 이전 실패 잔재 정리 (.git 없는 디렉토리)
if [ -d "$REPO" ] && [ ! -d "$REPO/.git" ]; then
  echo "잔여 디렉토리 정리..."
  rm -rf "$REPO"
fi

# 최초 클론
if [ ! -d "$REPO/.git" ]; then
  git clone https://github.com/kokospapa8/dk-haejo.git "$REPO"
fi

cd "$REPO"
git fetch origin main
git reset --hard origin/main

docker compose up -d --build --remove-orphans
docker image prune -f

echo "✅ 배포 완료: $(date)"

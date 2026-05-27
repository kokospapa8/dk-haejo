variable "aws_region" {
  description = "AWS 리전"
  type        = string
  default     = "ap-northeast-2"
}

variable "instance_type" {
  description = "EC2 인스턴스 타입"
  type        = string
  default     = "t3.small"
}

variable "ssh_public_key" {
  description = "EC2 SSH 공개키 (비상 접속용). cat ~/.ssh/id_rsa.pub 또는 ssh-keygen으로 생성"
  type        = string
}

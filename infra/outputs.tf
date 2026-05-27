output "ec2_instance_id" {
  description = "GitHub Secret EC2_INSTANCE_ID 에 저장할 값"
  value       = aws_instance.bot.id
}

output "ec2_public_ip" {
  description = "EC2 퍼블릭 IP (Elastic IP)"
  value       = aws_eip.bot.public_ip
}

output "github_deploy_role_arn" {
  description = "GitHub Secret AWS_ROLE_ARN 에 저장할 값"
  value       = aws_iam_role.github_deploy.arn
}

output "aws_region" {
  description = "GitHub Secret AWS_REGION 에 저장할 값"
  value       = var.aws_region
}

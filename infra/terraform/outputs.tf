output "vpc_id" {
  value = aws_vpc.main.id
}

output "alb_dns_name" {
  description = "Public HTTP entrypoint for the API (Chunk 19 adds HTTPS/ACM)."
  value       = aws_lb.api.dns_name
}

output "api_url" {
  description = "Public entrypoint (https when acm_certificate_arn is set)."
  value       = var.acm_certificate_arn != "" ? "https://${aws_lb.api.dns_name}" : "http://${aws_lb.api.dns_name}"
}

output "https_enabled" {
  value = var.acm_certificate_arn != ""
}

output "ecr_repository_url" {
  description = "Push the Dockerfile-built image here before the ECS service can start healthy."
  value       = aws_ecr_repository.api.repository_url
}

output "rds_endpoint" {
  value = aws_db_instance.main.address
}

output "redis_endpoint" {
  value = aws_elasticache_cluster.main.cache_nodes[0].address
}

output "app_secrets_arn" {
  value = aws_secretsmanager_secret.app.arn
}

output "ecs_cluster_name" {
  value = aws_ecs_cluster.main.name
}

output "ecs_service_name" {
  value = aws_ecs_service.api.name
}

output "cloudwatch_log_group" {
  value = aws_cloudwatch_log_group.api.name
}

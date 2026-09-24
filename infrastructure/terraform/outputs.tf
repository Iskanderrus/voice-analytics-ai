# No secret values are exported; only identifiers needed by the deploy pipeline.

output "api_url" {
  value = "${local.https ? "https" : "http"}://${aws_lb.main.dns_name}"
}

output "ecr_repository_url" {
  value = aws_ecr_repository.backend.repository_url
}

output "ecs_cluster" {
  value = aws_ecs_cluster.main.name
}

output "migrate_task_definition" {
  value = aws_ecs_task_definition.migrate.family
}

output "private_subnet_ids" {
  value = aws_subnet.private[*].id
}

output "api_security_group_id" {
  value = aws_security_group.api.id
}

output "audio_bucket" {
  value = aws_s3_bucket.audio.bucket
}

output "openai_api_key_secret_arn" {
  description = "Set the key out-of-band: aws secretsmanager put-secret-value --secret-id <arn> --secret-string ..."
  value       = aws_secretsmanager_secret.openai_api_key.arn
}

resource "aws_ecr_repository" "backend" {
  name                 = "${local.name}-backend"
  image_tag_mutability = "IMMUTABLE"
  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "backend" {
  repository = aws_ecr_repository.backend.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep the last 30 images"
      selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 30 }
      action       = { type = "expire" }
    }]
  })
}

resource "aws_ecs_cluster" "main" {
  name = local.name
  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

locals {
  image = "${aws_ecr_repository.backend.repository_url}:${var.image_tag}"

  common_environment = [
    for name, value in {
      DJANGO_DEBUG              = "false"
      DJANGO_ALLOWED_HOSTS      = join(",", concat([aws_lb.main.dns_name], var.allowed_hosts))
      CORS_ALLOWED_ORIGINS      = join(",", var.cors_allowed_origins)
      AWS_REGION                = var.region
      S3_BUCKET                 = aws_s3_bucket.audio.bucket
      REDIS_URL                 = "rediss://${aws_elasticache_replication_group.main.primary_endpoint_address}:6379/0?ssl_cert_reqs=required"
      UPLOAD_MAX_BYTES          = tostring(var.upload_max_bytes)
      STT_PROVIDER              = var.stt_provider
      STT_MODEL                 = var.stt_model
      LLM_PROVIDER              = var.llm_provider
      LLM_MODEL                 = var.llm_model
      LOG_FORMAT                = "json"
      LOG_LEVEL                 = "INFO"
      CELERY_VISIBILITY_TIMEOUT = "14400"
    } : { name = name, value = value }
  ]

  common_secrets = [
    { name = "DJANGO_SECRET_KEY", valueFrom = aws_secretsmanager_secret.django_secret_key.arn },
    { name = "DATABASE_URL", valueFrom = aws_secretsmanager_secret.database_url.arn },
    { name = "OPENAI_API_KEY", valueFrom = aws_secretsmanager_secret.openai_api_key.arn },
  ]

  log_options = {
    for service, group in {
      api     = aws_cloudwatch_log_group.api.name
      worker  = aws_cloudwatch_log_group.worker.name
      beat    = aws_cloudwatch_log_group.worker.name
      migrate = aws_cloudwatch_log_group.api.name
      } : service => {
      logDriver = "awslogs"
      options = {
        awslogs-group         = group
        awslogs-region        = var.region
        awslogs-stream-prefix = service
      }
    }
  }
}

resource "aws_ecs_task_definition" "api" {
  family                   = "${local.name}-api"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.api_cpu
  memory                   = var.api_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.api_task.arn
  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }
  container_definitions = jsonencode([{
    name                   = "api"
    image                  = local.image
    essential              = true
    command                = ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "30"]
    portMappings           = [{ containerPort = 8000, protocol = "tcp" }]
    environment            = local.common_environment
    secrets                = local.common_secrets
    readonlyRootFilesystem = false
    logConfiguration       = local.log_options.api
    healthCheck = {
      command     = ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/health/live')"]
      interval    = 15
      timeout     = 5
      retries     = 3
      startPeriod = 20
    }
  }])
}

resource "aws_ecs_task_definition" "worker" {
  family                   = "${local.name}-worker"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.worker_cpu
  memory                   = var.worker_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.worker_task.arn
  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }
  container_definitions = jsonencode([{
    name             = "worker"
    image            = local.image
    essential        = true
    command          = ["celery", "-A", "config", "worker", "--loglevel", "INFO", "--concurrency", tostring(var.worker_concurrency)]
    environment      = local.common_environment
    secrets          = local.common_secrets
    logConfiguration = local.log_options.worker
    stopTimeout      = 120
  }])
}

resource "aws_ecs_task_definition" "beat" {
  family                   = "${local.name}-beat"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 256
  memory                   = 512
  execution_role_arn       = aws_iam_role.execution.arn
  container_definitions = jsonencode([{
    name             = "beat"
    image            = local.image
    essential        = true
    command          = ["celery", "-A", "config", "beat", "--loglevel", "INFO"]
    environment      = local.common_environment
    secrets          = local.common_secrets
    logConfiguration = local.log_options.beat
  }])
}

resource "aws_ecs_task_definition" "migrate" {
  family                   = "${local.name}-migrate"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 256
  memory                   = 512
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.api_task.arn
  container_definitions = jsonencode([{
    name             = "migrate"
    image            = local.image
    essential        = true
    command          = ["python", "manage.py", "migrate", "--noinput"]
    environment      = local.common_environment
    secrets          = local.common_secrets
    logConfiguration = local.log_options.migrate
  }])
}

resource "aws_ecs_service" "api" {
  name                              = "api"
  cluster                           = aws_ecs_cluster.main.id
  task_definition                   = aws_ecs_task_definition.api.arn
  desired_count                     = var.services_enabled ? var.api_desired_count : 0
  launch_type                       = "FARGATE"
  health_check_grace_period_seconds = 30
  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.api.id]
    assign_public_ip = false
  }
  load_balancer {
    target_group_arn = aws_lb_target_group.api.arn
    container_name   = "api"
    container_port   = 8000
  }
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }
  depends_on = [aws_lb_listener.http]
}

resource "aws_ecs_service" "worker" {
  name            = "worker"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.worker.arn
  desired_count   = var.services_enabled ? var.worker_desired_count : 0
  launch_type     = "FARGATE"
  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.worker.id]
    assign_public_ip = false
  }
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }
}

resource "aws_ecs_service" "beat" {
  name            = "beat"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.beat.arn
  desired_count   = var.services_enabled ? 1 : 0
  launch_type     = "FARGATE"
  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.worker.id]
    assign_public_ip = false
  }
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }
}

resource "aws_appautoscaling_target" "api" {
  service_namespace  = "ecs"
  resource_id        = "service/${aws_ecs_cluster.main.name}/${aws_ecs_service.api.name}"
  scalable_dimension = "ecs:service:DesiredCount"
  min_capacity       = var.services_enabled ? var.api_desired_count : 0
  max_capacity       = var.api_max_count
}

resource "aws_appautoscaling_policy" "api_cpu" {
  name               = "${local.name}-api-cpu"
  policy_type        = "TargetTrackingScaling"
  service_namespace  = aws_appautoscaling_target.api.service_namespace
  resource_id        = aws_appautoscaling_target.api.resource_id
  scalable_dimension = aws_appautoscaling_target.api.scalable_dimension
  target_tracking_scaling_policy_configuration {
    target_value = 60
    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageCPUUtilization"
    }
  }
}

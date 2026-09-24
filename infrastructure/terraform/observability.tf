resource "aws_cloudwatch_log_group" "api" {
  name              = "/ecs/${local.name}/api"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "worker" {
  name              = "/ecs/${local.name}/worker"
  retention_in_days = var.log_retention_days
}

# Metrics derived from the structured JSON logs: no metrics agent, no extra
# code path. Stage/model dimensions come straight from the log fields.
locals {
  metric_namespace = "VoiceAnalysis/${var.environment}"
}

resource "aws_cloudwatch_log_metric_filter" "jobs_created" {
  name           = "${local.name}-jobs-created"
  log_group_name = aws_cloudwatch_log_group.api.name
  pattern        = "{ $.message = \"analysis queued\" }"
  metric_transformation {
    name      = "JobsCreated"
    namespace = local.metric_namespace
    value     = "1"
  }
}

resource "aws_cloudwatch_log_metric_filter" "jobs_completed" {
  name           = "${local.name}-jobs-completed"
  log_group_name = aws_cloudwatch_log_group.worker.name
  pattern        = "{ $.message = \"stage finished\" && $.job_status = \"COMPLETED\" }"
  metric_transformation {
    name      = "JobsCompleted"
    namespace = local.metric_namespace
    value     = "1"
  }
}

resource "aws_cloudwatch_log_metric_filter" "jobs_failed" {
  name           = "${local.name}-jobs-failed"
  log_group_name = aws_cloudwatch_log_group.worker.name
  pattern        = "{ $.message = \"job failed\" }"
  metric_transformation {
    name       = "JobsFailed"
    namespace  = local.metric_namespace
    value      = "1"
    dimensions = { ErrorCode = "$.error_code" }
  }
}

resource "aws_cloudwatch_log_metric_filter" "stage_latency" {
  name           = "${local.name}-stage-latency"
  log_group_name = aws_cloudwatch_log_group.worker.name
  pattern        = "{ $.event = \"stage_completed\" }"
  metric_transformation {
    name       = "StageLatencyMs"
    namespace  = local.metric_namespace
    value      = "$.duration_ms"
    unit       = "Milliseconds"
    dimensions = { Stage = "$.stage", Model = "$.model" }
  }
}

resource "aws_cloudwatch_log_metric_filter" "llm_output_tokens" {
  name           = "${local.name}-llm-output-tokens"
  log_group_name = aws_cloudwatch_log_group.worker.name
  pattern        = "{ $.event = \"stage_completed\" && $.output_tokens = * }"
  metric_transformation {
    name       = "LLMOutputTokens"
    namespace  = local.metric_namespace
    value      = "$.output_tokens"
    dimensions = { Model = "$.model" }
  }
}

resource "aws_cloudwatch_log_metric_filter" "provider_retries" {
  name           = "${local.name}-provider-retries"
  log_group_name = aws_cloudwatch_log_group.worker.name
  pattern        = "{ $.message = \"stage failed; retrying\" }"
  metric_transformation {
    name       = "StageRetries"
    namespace  = local.metric_namespace
    value      = "1"
    dimensions = { ErrorCode = "$.error_code" }
  }
}

resource "aws_sns_topic" "alarms" {
  name = "${local.name}-alarms"
}

resource "aws_sns_topic_subscription" "alarm_email" {
  count     = var.alarm_email != "" ? 1 : 0
  topic_arn = aws_sns_topic.alarms.arn
  protocol  = "email"
  endpoint  = var.alarm_email
}

resource "aws_cloudwatch_metric_alarm" "api_5xx" {
  alarm_name          = "${local.name}-api-5xx"
  namespace           = "AWS/ApplicationELB"
  metric_name         = "HTTPCode_Target_5XX_Count"
  dimensions          = { LoadBalancer = aws_lb.main.arn_suffix }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 10
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
}

resource "aws_cloudwatch_metric_alarm" "worker_running" {
  alarm_name          = "${local.name}-worker-not-running"
  namespace           = "ECS/ContainerInsights"
  metric_name         = "RunningTaskCount"
  dimensions          = { ClusterName = aws_ecs_cluster.main.name, ServiceName = aws_ecs_service.worker.name }
  statistic           = "Minimum"
  period              = 60
  evaluation_periods  = 5
  threshold           = 1
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "breaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
}

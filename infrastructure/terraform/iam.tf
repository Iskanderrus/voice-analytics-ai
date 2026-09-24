data "aws_iam_policy_document" "ecs_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = ["arn:aws:ecs:${var.region}:${data.aws_caller_identity.current.account_id}:*"]
    }
  }
}

resource "aws_iam_role" "execution" {
  name               = "${local.name}-ecs-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

resource "aws_iam_role_policy_attachment" "execution_managed" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

data "aws_iam_policy_document" "execution_secrets" {
  statement {
    actions = ["secretsmanager:GetSecretValue"]
    resources = [
      aws_secretsmanager_secret.django_secret_key.arn,
      aws_secretsmanager_secret.database_url.arn,
      aws_secretsmanager_secret.openai_api_key.arn,
    ]
  }
}

resource "aws_iam_role_policy" "execution_secrets" {
  name   = "read-app-secrets"
  role   = aws_iam_role.execution.id
  policy = data.aws_iam_policy_document.execution_secrets.json
}

locals {
  audio_objects   = "${aws_s3_bucket.audio.arn}/audio/*"
  staging_objects = "${aws_s3_bucket.audio.arn}/staging/*"
}

resource "aws_iam_role" "api_task" {
  name               = "${local.name}-api-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

data "aws_iam_policy_document" "api_task" {
  statement {
    actions   = ["s3:PutObject", "s3:GetObject", "s3:DeleteObject"]
    resources = [local.staging_objects]
  }
  statement {
    actions   = ["s3:PutObject", "s3:GetObject"]
    resources = [local.audio_objects]
  }
  statement {
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.audio.arn]
  }
}

resource "aws_iam_role_policy" "api_task" {
  name   = "audio-upload"
  role   = aws_iam_role.api_task.id
  policy = data.aws_iam_policy_document.api_task.json
}

resource "aws_iam_role" "worker_task" {
  name               = "${local.name}-worker-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

data "aws_iam_policy_document" "worker_task" {
  statement {
    actions   = ["s3:GetObject", "s3:DeleteObject"]
    resources = [local.audio_objects]
  }
  statement {
    actions   = ["s3:DeleteObject"]
    resources = [local.staging_objects]
  }
  statement {
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.audio.arn]
  }
}

resource "aws_iam_role_policy" "worker_task" {
  name   = "audio-process"
  role   = aws_iam_role.worker_task.id
  policy = data.aws_iam_policy_document.worker_task.json
}

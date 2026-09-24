variable "project" {
  type    = string
  default = "voice-analysis"
}

variable "environment" {
  type    = string
  default = "prod"
}

variable "region" {
  type    = string
  default = "eu-central-1"
}

variable "az_count" {
  description = "Availability zones for subnets (RDS subnet groups need at least 2)."
  type        = number
  default     = 2
}

variable "vpc_cidr" {
  type    = string
  default = "10.40.0.0/16"
}

variable "image_tag" {
  description = "Backend image tag in the ECR repository (same image for API and worker)."
  type        = string
  default     = "latest"
}

variable "services_enabled" {
  description = "Keep false while bootstrapping secrets, image and migrations; enable only after they are ready."
  type        = bool
  default     = false
}

variable "certificate_arn" {
  description = "ACM certificate for HTTPS on the ALB. Empty = HTTP only (demo; not for real data)."
  type        = string
  default     = ""
}

variable "allowed_hosts" {
  description = "Django ALLOWED_HOSTS in addition to the ALB DNS name."
  type        = list(string)
  default     = []
}

variable "cors_allowed_origins" {
  description = "Browser origins allowed to call the API and POST to the upload bucket."
  type        = list(string)
  default     = []
}

variable "api_cpu" {
  type    = number
  default = 512
}

variable "api_memory" {
  type    = number
  default = 1024
}

variable "api_desired_count" {
  type    = number
  default = 2
}

variable "api_max_count" {
  type    = number
  default = 6
}

variable "worker_cpu" {
  type    = number
  default = 1024
}

variable "worker_memory" {
  type    = number
  default = 2048
}

variable "worker_desired_count" {
  type    = number
  default = 2
}

variable "worker_concurrency" {
  type    = number
  default = 4
}

variable "db_instance_class" {
  type    = string
  default = "db.t4g.small"
}

variable "db_allocated_storage" {
  type    = number
  default = 20
}

variable "db_multi_az" {
  type    = bool
  default = false
}

variable "redis_node_type" {
  type    = string
  default = "cache.t4g.micro"
}

variable "stt_provider" {
  type    = string
  default = "openai"
}

variable "stt_model" {
  type    = string
  default = "whisper-1"
}

variable "llm_provider" {
  type    = string
  default = "openai"
}

variable "llm_model" {
  type    = string
  default = "gpt-4o-mini"
}

variable "upload_max_bytes" {
  type    = number
  default = 209715200
}

variable "log_retention_days" {
  type    = number
  default = 30
}

variable "alarm_email" {
  description = "Optional email subscribed to operational alarms."
  type        = string
  default     = ""
}

variable "db_deletion_protection" {
  description = "Keep enabled for long-lived environments; disable only when the stack must be disposable."
  type        = bool
  default     = true
}

variable "db_skip_final_snapshot" {
  description = "Short-lived environments can skip the final snapshot to allow clean teardown."
  type        = bool
  default     = false
}

variable "db_performance_insights_enabled" {
  type    = bool
  default = true
}

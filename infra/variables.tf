variable "region" {
  type    = string
  default = "eu-central-1"
}

variable "environment" {
  type = string
  validation {
    condition     = contains(["staging", "production"], var.environment)
    error_message = "environment must be staging or production"
  }
}

variable "vpc_id" {
  type = string
}

variable "private_subnet_ids" {
  type = list(string)
}

variable "ecs_cluster_arn" {
  type = string
}

variable "load_balancer_listener_arn" {
  type = string
}

variable "load_balancer_security_group_id" {
  type = string
}

variable "listener_rule_priority" {
  type    = number
  default = 100
}

variable "hostname" {
  type        = string
  description = "Host header routed to the API, for example plots.example.org"
}

variable "api_keys_secret_arn" {
  type        = string
  description = "Secrets Manager secret holding PLOT_CHECK_API_KEYS (key:customer pairs)"
}

variable "image_tag" {
  type = string
}

variable "desired_count" {
  type    = number
  default = 2
}

variable "db_instance_class" {
  type    = string
  default = "db.t4g.medium"
}

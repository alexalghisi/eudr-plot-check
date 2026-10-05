terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}

provider "aws" {
  region = var.region
}

locals {
  name = "plot-check-${var.environment}"
  tags = { service = "plot-check", environment = var.environment }
}

# --- Database: RDS PostgreSQL, PostGIS is enabled by the app's migration (CREATE EXTENSION postgis) ---

resource "random_password" "db" {
  length  = 32
  special = false
}

resource "aws_db_subnet_group" "this" {
  name       = local.name
  subnet_ids = var.private_subnet_ids
  tags       = local.tags
}

resource "aws_security_group" "db" {
  name   = "${local.name}-db"
  vpc_id = var.vpc_id
  tags   = local.tags
}

resource "aws_vpc_security_group_ingress_rule" "db_from_api" {
  security_group_id            = aws_security_group.db.id
  referenced_security_group_id = aws_security_group.api.id
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
}

resource "aws_db_instance" "this" {
  identifier                   = local.name
  engine                       = "postgres"
  engine_version               = "16"
  instance_class               = var.db_instance_class
  allocated_storage            = 50
  storage_encrypted            = true
  db_name                      = "plots"
  username                     = "plots"
  password                     = random_password.db.result
  db_subnet_group_name         = aws_db_subnet_group.this.name
  vpc_security_group_ids       = [aws_security_group.db.id]
  backup_retention_period      = 14
  deletion_protection          = var.environment == "production"
  skip_final_snapshot          = var.environment != "production"
  final_snapshot_identifier    = "${local.name}-final"
  performance_insights_enabled = true
  tags                         = local.tags
}

resource "aws_secretsmanager_secret" "app" {
  name = "${local.name}/app"
  tags = local.tags
}

resource "aws_secretsmanager_secret_version" "app" {
  secret_id = aws_secretsmanager_secret.app.id
  secret_string = jsonencode({
    DATABASE_URL = "postgresql://plots:${random_password.db.result}@${aws_db_instance.this.address}:5432/plots?sslmode=require"
  })
}

# --- API: one container on ECS Fargate behind an existing load balancer listener ---

resource "aws_ecr_repository" "api" {
  name                 = local.name
  image_tag_mutability = "IMMUTABLE"
  image_scanning_configuration {
    scan_on_push = true
  }
  tags = local.tags
}

resource "aws_cloudwatch_log_group" "api" {
  name              = "/ecs/${local.name}"
  retention_in_days = 30
  tags              = local.tags
}

resource "aws_security_group" "api" {
  name   = "${local.name}-api"
  vpc_id = var.vpc_id
  tags   = local.tags
}

resource "aws_vpc_security_group_ingress_rule" "api_from_lb" {
  security_group_id            = aws_security_group.api.id
  referenced_security_group_id = var.load_balancer_security_group_id
  ip_protocol                  = "tcp"
  from_port                    = 8000
  to_port                      = 8000
}

resource "aws_vpc_security_group_egress_rule" "api_out" {
  security_group_id = aws_security_group.api.id
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
}

data "aws_iam_policy_document" "ecs_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "execution" {
  name               = "${local.name}-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
  tags               = local.tags
}

resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

data "aws_iam_policy_document" "read_secrets" {
  statement {
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.app.arn, var.api_keys_secret_arn]
  }
}

resource "aws_iam_role_policy" "read_secrets" {
  name   = "read-secrets"
  role   = aws_iam_role.execution.id
  policy = data.aws_iam_policy_document.read_secrets.json
}

resource "aws_ecs_task_definition" "api" {
  family                   = local.name
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 512
  memory                   = 1024
  execution_role_arn       = aws_iam_role.execution.arn
  container_definitions = jsonencode([{
    name         = "api"
    image        = "${aws_ecr_repository.api.repository_url}:${var.image_tag}"
    essential    = true
    portMappings = [{ containerPort = 8000 }]
    secrets = [
      { name = "DATABASE_URL", valueFrom = "${aws_secretsmanager_secret.app.arn}:DATABASE_URL::" },
      { name = "PLOT_CHECK_API_KEYS", valueFrom = var.api_keys_secret_arn },
    ]
    healthCheck = {
      command  = ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz')\""]
      interval = 30
      retries  = 3
    }
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.api.name
        awslogs-region        = var.region
        awslogs-stream-prefix = "api"
      }
    }
  }])
  tags = local.tags
}

resource "aws_lb_target_group" "api" {
  name        = local.name
  port        = 8000
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = var.vpc_id
  health_check {
    path = "/healthz"
  }
  tags = local.tags
}

resource "aws_lb_listener_rule" "api" {
  listener_arn = var.load_balancer_listener_arn
  priority     = var.listener_rule_priority
  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api.arn
  }
  condition {
    host_header {
      values = [var.hostname]
    }
  }
}

resource "aws_ecs_service" "api" {
  name            = local.name
  cluster         = var.ecs_cluster_arn
  task_definition = aws_ecs_task_definition.api.arn
  desired_count   = var.desired_count
  launch_type     = "FARGATE"
  network_configuration {
    subnets         = var.private_subnet_ids
    security_groups = [aws_security_group.api.id]
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
  tags = local.tags
}

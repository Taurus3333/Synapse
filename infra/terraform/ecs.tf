resource "aws_ecs_cluster" "main" {
  name = local.name

  setting {
    name  = "containerInsights"
    value = "enabled"
  }

  tags = { Name = local.name }
}

resource "aws_ecs_task_definition" "api" {
  family                   = "${local.name}-api"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.api_cpu
  memory                   = var.api_memory
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.ecs_task.arn

  container_definitions = jsonencode([{
    name      = "api"
    image     = var.api_image_digest != "" ? "${aws_ecr_repository.api.repository_url}@${var.api_image_digest}" : "${aws_ecr_repository.api.repository_url}:${var.api_image_tag}"
    essential = true

    portMappings = [{
      containerPort = 8000
      hostPort      = 8000
      protocol      = "tcp"
    }]

    environment = [
      { name = "SYNAPSE_ENV", value = "prod" },
      { name = "SYNAPSE_LOG_JSON", value = "true" },
      { name = "SYNAPSE_LOG_LEVEL", value = "INFO" },
      { name = "SYNAPSE_API_HOST", value = "0.0.0.0" },
      { name = "SYNAPSE_API_PORT", value = "8000" },
    ]

    secrets = [
      {
        name      = "SYNAPSE_JWT_SECRET"
        valueFrom = "${aws_secretsmanager_secret.app.arn}:SYNAPSE_JWT_SECRET::"
      },
      {
        name      = "SYNAPSE_GROQ_API_KEY"
        valueFrom = "${aws_secretsmanager_secret.app.arn}:SYNAPSE_GROQ_API_KEY::"
      },
      {
        name      = "SYNAPSE_OPENAI_API_KEY"
        valueFrom = "${aws_secretsmanager_secret.app.arn}:SYNAPSE_OPENAI_API_KEY::"
      },
      {
        name      = "SYNAPSE_DEMO_PASSWORD"
        valueFrom = "${aws_secretsmanager_secret.app.arn}:SYNAPSE_DEMO_PASSWORD::"
      },
      {
        name      = "SYNAPSE_DATABASE_URL"
        valueFrom = "${aws_secretsmanager_secret.app.arn}:SYNAPSE_DATABASE_URL::"
      },
      {
        name      = "SYNAPSE_REDIS_URL"
        valueFrom = "${aws_secretsmanager_secret.app.arn}:SYNAPSE_REDIS_URL::"
      },
    ]

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.api.name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "api"
      }
    }

    healthCheck = {
      command     = ["CMD-SHELL", "curl -fsS http://127.0.0.1:8000/health || exit 1"]
      interval    = 30
      timeout     = 5
      retries     = 3
      startPeriod = 60
    }
  }])

  tags = { Name = "${local.name}-api-task" }
}

resource "aws_ecs_service" "api" {
  name            = "${local.name}-api"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.api.arn
  desired_count   = var.api_desired_count
  launch_type     = "FARGATE"

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

  deployment_minimum_healthy_percent = 50
  deployment_maximum_percent         = 200

  depends_on = [
    aws_lb_listener.http_forward,
    aws_lb_listener.http_redirect,
    aws_lb_listener.https,
  ]

  tags = { Name = "${local.name}-api-svc" }
}

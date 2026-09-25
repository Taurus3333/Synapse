locals {
  # Placeholder secret until TF_VAR_* / Chunk 19 injects real values.
  jwt_value = var.jwt_secret != "" ? var.jwt_secret : "REPLACE-ME-before-apply-min-32-chars!!"
}

resource "aws_secretsmanager_secret" "app" {
  name                    = "${local.name}/app"
  description             = "Synapse API runtime secrets (JWT, LLM keys, DB URL assembled at deploy)."
  recovery_window_in_days = var.environment == "prod" ? 30 : 0
  tags                    = { Name = "${local.name}-app-secrets" }
}

resource "aws_secretsmanager_secret_version" "app" {
  secret_id = aws_secretsmanager_secret.app.id
  secret_string = jsonencode({
    SYNAPSE_JWT_SECRET     = local.jwt_value
    SYNAPSE_GROQ_API_KEY   = var.groq_api_key
    SYNAPSE_OPENAI_API_KEY = var.openai_api_key
    SYNAPSE_DEMO_PASSWORD  = var.demo_password
    SYNAPSE_DATABASE_URL   = "postgresql+asyncpg://${var.db_username}:${urlencode(random_password.db.result)}@${aws_db_instance.main.address}:5432/${var.db_name}?ssl=require"
    SYNAPSE_REDIS_URL      = "redis://${aws_elasticache_cluster.main.cache_nodes[0].address}:6379/0"
  })
}

output "instance_id" {
  description = "Use this with SSM Session Manager. There is no SSH key."
  value       = aws_instance.app.id
}

output "public_ip" {
  value = aws_instance.app.public_ip
}

output "ui_url" {
  value = "http://${aws_instance.app.public_ip}:8501"
}

output "api_url" {
  value = "http://${aws_instance.app.public_ip}:8000"
}

output "secret_name" {
  description = "Write the dotenv here before the instance boots, or reboot after writing it."
  value       = aws_secretsmanager_secret.app.name
}

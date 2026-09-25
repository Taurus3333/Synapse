#!/usr/bin/env pwsh
<#
.SYNOPSIS
  First-time terraform apply for Synapse (creates VPC/RDS/ECS/ECR/…).

  Requires TF_VAR_jwt_secret (≥32 chars) and a chat key:
    $env:TF_VAR_jwt_secret = "...."
    $env:TF_VAR_groq_api_key = "...."
    # and/or $env:TF_VAR_openai_api_key

  This creates billable AWS resources (NAT, ALB, RDS, …). Destroy with:
    terraform destroy
#>
param(
    [string]$Region = $(if ($env:AWS_REGION) { $env:AWS_REGION } else { "us-east-1" }),
    [string]$VarFile = "",
    [switch]$PlanOnly
)

$ErrorActionPreference = "Stop"
$Root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$Tf = Join-Path $Root "infra\terraform"

if (-not $env:TF_VAR_jwt_secret -or $env:TF_VAR_jwt_secret.Length -lt 32) {
    throw "Set TF_VAR_jwt_secret to a ≥32 character production secret before apply."
}
if (-not $env:TF_VAR_groq_api_key -and -not $env:TF_VAR_openai_api_key) {
    throw "Set TF_VAR_groq_api_key and/or TF_VAR_openai_api_key."
}

Push-Location $Tf
try {
    $env:AWS_REGION = $Region
    terraform init
    $args = @()
    if ($VarFile) { $args += "-var-file=$VarFile" }
    elseif (Test-Path "terraform.tfvars") { $args += "-var-file=terraform.tfvars" }

    if ($PlanOnly) {
        terraform plan @args
        return
    }
    terraform apply -auto-approve @args
    Write-Host ""
    Write-Host "ECR:     $(terraform output -raw ecr_repository_url)"
    Write-Host "API URL: $(terraform output -raw api_url)  (desired_count still 0 until ecr_push + rollout)"
    Write-Host "Next:    .\scripts\deploy\ecr_push.ps1 ; .\scripts\deploy\rollout.ps1 -DesiredCount 1"
}
finally {
    Pop-Location
}

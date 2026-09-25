#!/usr/bin/env pwsh
<#
.SYNOPSIS
  Scale the ECS service and force a new deployment (after ecr_push).

.EXAMPLE
  .\scripts\deploy\rollout.ps1 -DesiredCount 1
#>
param(
    [string]$Region = $(if ($env:AWS_REGION) { $env:AWS_REGION } else { "us-east-1" }),
    [int]$DesiredCount = 1,
    [string]$ImageTag = "latest",
    [string]$TerraformDir = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
if (-not $TerraformDir) { $TerraformDir = Join-Path $Root "infra\terraform" }

Push-Location $TerraformDir
try {
    $Cluster = terraform output -raw ecs_cluster_name
    $Service = terraform output -raw ecs_service_name
    $ApiUrl = terraform output -raw api_url
}
finally {
    Pop-Location
}

Write-Host "Updating task definition image tag via terraform…"
Push-Location $TerraformDir
try {
    terraform apply -auto-approve `
        -var="api_image_tag=$ImageTag" `
        -var="api_desired_count=$DesiredCount"
}
finally {
    Pop-Location
}

Write-Host "Forcing new ECS deployment on $Cluster / $Service…"
aws ecs update-service `
    --region $Region `
    --cluster $Cluster `
    --service $Service `
    --desired-count $DesiredCount `
    --force-new-deployment `
    | Out-Null

Write-Host "Waiting for service stability…"
aws ecs wait services-stable --region $Region --cluster $Cluster --services $Service

Write-Host "API URL: $ApiUrl"
Write-Host "Probe:  curl $ApiUrl/health"

#!/usr/bin/env pwsh
<#
.SYNOPSIS
  Build the Synapse API image and push it to the Terraform-managed ECR repo.

.EXAMPLE
  .\scripts\deploy\ecr_push.ps1
  .\scripts\deploy\ecr_push.ps1 -Tag "v0.1.0" -Region us-east-1
#>
param(
    [string]$Region = $(if ($env:AWS_REGION) { $env:AWS_REGION } else { "us-east-1" }),
    [string]$Tag = "latest",
    [string]$TerraformDir = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
if (-not $TerraformDir) { $TerraformDir = Join-Path $Root "infra\terraform" }

Push-Location $TerraformDir
try {
    $Repo = terraform output -raw ecr_repository_url 2>$null
    if (-not $Repo) {
        throw "ecr_repository_url missing — run terraform apply first (infra creates ECR)."
    }
}
finally {
    Pop-Location
}

Write-Host "Logging into ECR $Repo ($Region)…"
aws ecr get-login-password --region $Region `
    | docker login --username AWS --password-stdin ($Repo.Split('/')[0])

$Local = "synapse-api:$Tag"
Write-Host "Building $Local…"
docker build -t $Local $Root
docker tag $Local "${Repo}:${Tag}"

Write-Host "Pushing ${Repo}:${Tag}…"
docker push "${Repo}:${Tag}"

$Meta = aws ecr describe-images `
    --repository-name ($Repo.Split('/')[-1]) `
    --image-ids imageTag=$Tag `
    --region $Region `
    --query "imageDetails[0].imageDigest" `
    --output text

Write-Host "Pushed digest: $Meta"
Write-Host "Next: .\scripts\deploy\rollout.ps1 -DesiredCount 1 -ImageTag $Tag"
if ($Meta -and $Meta -ne "None") {
    Write-Host "Or pin digest: terraform apply -var=api_image_digest=$Meta -var=api_desired_count=1"
}

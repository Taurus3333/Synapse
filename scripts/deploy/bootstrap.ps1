#!/usr/bin/env pwsh
<#
.SYNOPSIS
  Print (and optionally run) post-RDS bootstrap: pgvector + seed + ingest.

  Full automation needs ECS Exec or a one-off task with network access to RDS.
  This script documents the exact commands and can run them locally against
  a tunnel / public bastion if DATABASE_URL is set.
#>
param(
    [switch]$Local,
    [string]$Profile = "ci",
    [int]$Seed = 42
)

$ErrorActionPreference = "Stop"

Write-Host @"
=== Synapse RDS bootstrap (Chunk 19) ===

1) Enable pgvector (once per database):
   psql "`$DATABASE_URL" -c "CREATE EXTENSION IF NOT EXISTS vector;"

2) Seed + ingest (from an environment that can reach RDS + OpenAI):
   `$env:SYNAPSE_ENV = "prod"   # or local with prod DB URL
   synapse-seed --profile $Profile --seed $Seed
   synapse-ingest

3) Smoke:
   curl `$API_URL/health
   curl -X POST `$API_URL/v1/auth/token -H "content-type: application/json" ``
     -d '{"email":"uma.berg.0@northwind.example","password":"synapse-demo"}'

"@

if ($Local) {
    if (-not $env:SYNAPSE_DATABASE_URL) {
        throw "Set SYNAPSE_DATABASE_URL to run -Local bootstrap."
    }
    Write-Host "Running local seed ($Profile / $Seed)…"
    synapse-seed --profile $Profile --seed $Seed
    Write-Host "Running ingest (needs SYNAPSE_OPENAI_API_KEY)…"
    synapse-ingest
}

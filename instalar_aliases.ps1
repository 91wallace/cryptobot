# Instala os aliases /iniciar, /status e /dash no perfil do PowerShell (idempotente).
$RootDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Aliases = Join-Path $RootDir "aliases.ps1"

foreach ($perfil in @($PROFILE.CurrentUserAllHosts)) {
    New-Item -ItemType File -Force -Path $perfil | Out-Null
    $conteudo = Get-Content $perfil -Raw -ErrorAction SilentlyContinue
    $marca = "# >>> cryptobot aliases >>>"
    if ($conteudo -and $conteudo.Contains($marca)) {
        Write-Host "Ja instalado em $perfil" -ForegroundColor Yellow
        continue
    }
    Add-Content -Path $perfil -Value @"

$marca
if (Test-Path '$Aliases') { . '$Aliases' }
# <<< cryptobot aliases <<<
"@
    Write-Host "Aliases instalados em $perfil" -ForegroundColor Green
}
Write-Host "Abra um novo terminal (ou rode: . .\aliases.ps1) e use /iniciar, /status, /dash dentro do projeto." -ForegroundColor Cyan

# Native PowerShell negative controls for scripts/init.ps1 security validation.

$ErrorActionPreference = "Stop"
if (Test-Path Variable:PSNativeCommandUseErrorActionPreference) {
    $PSNativeCommandUseErrorActionPreference = $false
}
$PowerShellPath = (Get-Process -Id $PID).Path
$InitScript = (Resolve-Path (Join-Path $PSScriptRoot "../init.ps1")).Path

function Invoke-SecurityValidation([string]$EnvContent) {
    $caseDirectory = Join-Path ([IO.Path]::GetTempPath()) ("yuxi-init-security-" + [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $caseDirectory | Out-Null
    try {
        Set-Content -Path (Join-Path $caseDirectory ".env") -Value $EnvContent -Encoding utf8NoBOM
        Push-Location $caseDirectory
        try {
            & $PowerShellPath -NoProfile -NonInteractive -File $InitScript -ValidateSecurityEnv *> $null
            return $LASTEXITCODE
        } finally {
            Pop-Location
        }
    } finally {
        Remove-Item -LiteralPath $caseDirectory -Recurse -Force
    }
}

function Assert-SecurityValidation([string]$Name, [string]$EnvContent, [bool]$ShouldPass) {
    $exitCode = Invoke-SecurityValidation $EnvContent
    if ($ShouldPass -and $exitCode -ne 0) {
        throw "$Name should pass security validation but exited with $exitCode"
    }
    if (-not $ShouldPass -and $exitCode -eq 0) {
        throw "$Name should fail security validation"
    }
}

function New-SecurityEnv([string]$JwtSecret, [string]$ApiSecret) {
    return @(
        "JWT_SECRET_KEY=$JwtSecret",
        "API_KEY_DERIVATION_SECRET=$ApiSecret"
    ) -join [Environment]::NewLine
}

$validJwt = "jwt-secret-value-that-is-at-least-32-characters"
$validApi = "api-secret-value-that-is-at-least-32-characters"

Assert-SecurityValidation "valid distinct secrets" (New-SecurityEnv $validJwt $validApi) $true
Assert-SecurityValidation "short JWT secret" (New-SecurityEnv "short" $validApi) $false
Assert-SecurityValidation "reused API derivation secret" (New-SecurityEnv $validJwt $validJwt) $false
Assert-SecurityValidation "leading whitespace" (New-SecurityEnv " $validJwt" $validApi) $false
Assert-SecurityValidation "trailing whitespace" (New-SecurityEnv $validJwt "$validApi ") $false
Assert-SecurityValidation "quoted short value" (New-SecurityEnv '"123456789012345678901234567890"' $validApi) $false

Write-Host "PowerShell security environment validation matrix passed."

# 镜像准备不是本测试的观察边界；模拟 Docker，回读真实初始化写入的文件。
function global:docker { $global:LASTEXITCODE = 0 }

function Invoke-SandboxInitialization([string]$ExtraEnv = "") {
    $caseDirectory = Join-Path ([IO.Path]::GetTempPath()) ("yuxi-init-sandbox-" + [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $caseDirectory | Out-Null
    try {
        $content = (New-SecurityEnv $validJwt $validApi) + "`nSILICONFLOW_API_KEY=test-key`nYUXI_INSTANCE_ID=test-instance`n" + $ExtraEnv
        Set-Content -Path (Join-Path $caseDirectory ".env") -Value $content -Encoding utf8NoBOM
        Push-Location $caseDirectory
        try {
            & $InitScript *> $null
            $first = Get-Content .env -Raw
            & $InitScript *> $null
            if ((Get-Content .env -Raw) -ne $first) { throw "Repeated initialization changed existing configuration" }
            return $first
        } finally {
            Pop-Location
        }
    } finally {
        Remove-Item -LiteralPath $caseDirectory -Recurse -Force
    }
}

$containerEnv = Invoke-SandboxInitialization
if ($containerEnv -notmatch '(?m)^SANDBOX_BACKEND=container\r?$' -or $containerEnv -notmatch '(?m)^SANDBOX_RUNNER_TOKEN=[a-f0-9]{64}\r?$') {
    throw "Default initialization must select container and persist a runner token"
}
$cubeEnv = Invoke-SandboxInitialization "SANDBOX_BACKEND=cube"
if ($cubeEnv -notmatch '(?m)^SANDBOX_BACKEND=cube\r?$' -or $cubeEnv -match 'SANDBOX_RUNNER_TOKEN=') {
    throw "Explicit Cube selection must be preserved without a container token"
}
foreach ($invalid in @("SANDBOX_BACKEND=invalid", "SANDBOX_BACKEND=container`nSANDBOX_RUNNER_TOKEN=short")) {
    $rejected = $false
    try { Invoke-SandboxInitialization $invalid | Out-Null } catch {
        if ($_.Exception.Message -notmatch 'SANDBOX_BACKEND|SANDBOX_RUNNER_TOKEN') { throw }
        $rejected = $true
    }
    if (-not $rejected) { throw "Invalid sandbox configuration must fail initialization" }
}
Write-Host "PowerShell default container initialization and negative controls passed."

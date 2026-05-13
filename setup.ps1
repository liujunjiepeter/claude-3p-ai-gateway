# AI Gateway 一键配置 (Windows PowerShell)
# 用法: .\setup.ps1
# 需要先启动 docker-compose up -d

$ONEAPI_URL = if ($env:ONEAPI_URL) { $env:ONEAPI_URL } else { "http://localhost:3000" }
$ONEAPI_USER = if ($env:ONEAPI_USER) { $env:ONEAPI_USER } else { "root" }
$ONEAPI_PASS = if ($env:ONEAPI_PASS) { $env:ONEAPI_PASS } else { "123456" }

Write-Host "AI Gateway 一键配置" -ForegroundColor Cyan
Write-Host "======================" -ForegroundColor Cyan
Write-Host ""

# 登录
Write-Host "📡 登录 One-API..."
$loginBody = @{username=$ONEAPI_USER; password=$ONEAPI_PASS} | ConvertTo-Json
$loginResp = Invoke-RestMethod -Uri "$ONEAPI_URL/api/user/login" -Method Post -ContentType "application/json" -Body $loginBody -SessionVariable session
Write-Host "   登录成功"

# 添加 DeepSeek 渠道
if ($env:DEEPSEEK_API_KEY) {
    Write-Host "➕ 添加 DeepSeek 渠道..."
    $dsBody = @{
        type = 1
        name = "DeepSeek"
        base_url = "https://api.deepseek.com"
        key = $env:DEEPSEEK_API_KEY
        models = if ($env:DEEPSEEK_MODEL) { $env:DEEPSEEK_MODEL } else { "deepseek-v4-pro" }
    } | ConvertTo-Json
    Invoke-RestMethod -Uri "$ONEAPI_URL/api/channel/" -Method Post -ContentType "application/json" -Body $dsBody -WebSession $session | Out-Null
    Write-Host "   ✅ DeepSeek ($($env:DEEPSEEK_MODEL))"
} else {
    Write-Host "⚠️  跳过 DeepSeek: DEEPSEEK_API_KEY 未设置" -ForegroundColor Yellow
}

# 添加小米渠道
if ($env:XIAOMI_API_KEY) {
    Write-Host "➕ 添加 Xiaomi 渠道..."
    $xmBody = @{
        type = 1
        name = "Xiaomi"
        base_url = if ($env:XIAOMI_BASE_URL) { $env:XIAOMI_BASE_URL } else { "https://token-plan-cn.xiaomimimo.com" }
        key = $env:XIAOMI_API_KEY
        models = if ($env:XIAOMI_MODEL) { $env:XIAOMI_MODEL } else { "mimo-v2.5" }
    } | ConvertTo-Json
    Invoke-RestMethod -Uri "$ONEAPI_URL/api/channel/" -Method Post -ContentType "application/json" -Body $xmBody -WebSession $session | Out-Null
    Write-Host "   ✅ Xiaomi ($($env:XIAOMI_MODEL))"
} else {
    Write-Host "⚠️  跳过 Xiaomi: XIAOMI_API_KEY 未设置" -ForegroundColor Yellow
}

# 创建令牌
Write-Host "🔑 创建 API 令牌..."
$tokenBody = @{name="gateway"; remain_quota=50000000; unlimited_quota=$true; expired_time=-1} | ConvertTo-Json
$tokenResp = Invoke-RestMethod -Uri "$ONEAPI_URL/api/token/" -Method Post -ContentType "application/json" -Body $tokenBody -WebSession $session
$token = $tokenResp.data.key

Write-Host ""
Write-Host "======================" -ForegroundColor Cyan
Write-Host "✅ 配置完成！" -ForegroundColor Green
Write-Host ""
Write-Host "📋 One-API 令牌: $token" -ForegroundColor Yellow
Write-Host ""
Write-Host "📝 请在 .env 中设置:"
Write-Host "   ONEAPI_TOKEN=$token"
Write-Host ""
Write-Host "🚀 然后重启代理:"
Write-Host "   docker-compose restart proxy"
Write-Host ""
Write-Host "🌐 One-API 管理后台: $ONEAPI_URL (默认 root/123456)"
Write-Host ""

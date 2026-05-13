#!/bin/bash
# 一键配置 One-API 渠道和令牌
# 用法: bash setup.sh
# 需要先启动 docker-compose up -d

set -e

ONEAPI_URL="${ONEAPI_URL:-http://localhost:3000}"
ONEAPI_USER="${ONEAPI_USER:-root}"
ONEAPI_PASS="${ONEAPI_PASS:-123456}"

echo "🔧 AI Gateway 一键配置"
echo "======================"
echo ""

# 登录
echo "📡 登录 One-API..."
COOKIE_JAR=$(mktemp)
curl -s -c "$COOKIE_JAR" -X POST "$ONEAPI_URL/api/user/login" \
  -H "Content-Type: application/json" \
  -d "{\"username\":\"$ONEAPI_USER\",\"password\":\"$ONEAPI_PASS\"}" > /dev/null

# 添加 DeepSeek 渠道
if [ -n "$DEEPSEEK_API_KEY" ]; then
  echo "➕ 添加 DeepSeek 渠道..."
  curl -s -b "$COOKIE_JAR" -X POST "$ONEAPI_URL/api/channel/" \
    -H "Content-Type: application/json" \
    -d "{\"type\":1,\"name\":\"DeepSeek\",\"base_url\":\"https://api.deepseek.com\",\"key\":\"$DEEPSEEK_API_KEY\",\"models\":\"${DEEPSEEK_MODEL:-deepseek-v4-pro}\"}" > /dev/null
  echo "   ✅ DeepSeek (${DEEPSEEK_MODEL:-deepseek-v4-pro})"
else
  echo "⚠️  跳过 DeepSeek: DEEPSEEK_API_KEY 未设置"
fi

# 添加小米渠道
if [ -n "$XIAOMI_API_KEY" ]; then
  echo "➕ 添加小米 MiMo 渠道..."
  curl -s -b "$COOKIE_JAR" -X POST "$ONEAPI_URL/api/channel/" \
    -H "Content-Type: application/json" \
    -d "{\"type\":1,\"name\":\"Xiaomi\",\"base_url\":\"${XIAOMI_BASE_URL:-https://token-plan-cn.xiaomimimo.com}\",\"key\":\"$XIAOMI_API_KEY\",\"models\":\"${XIAOMI_MODEL:-mimo-v2.5}\"}" > /dev/null
  echo "   ✅ Xiaomi (${XIAOMI_MODEL:-mimo-v2.5})"
else
  echo "⚠️  跳过 Xiaomi: XIAOMI_API_KEY 未设置"
fi

# 创建令牌
echo "🔑 创建 API 令牌..."
TOKEN=$(curl -s -b "$COOKIE_JAR" -X POST "$ONEAPI_URL/api/token/" \
  -H "Content-Type: application/json" \
  -d '{"name":"gateway","remain_quota":50000000,"unlimited_quota":true,"expired_time":-1}' | python3 -c "import sys,json;print(json.load(sys.stdin)['data']['key'])")
echo ""
echo "======================"
echo "✅ 配置完成！"
echo ""
echo "📋 One-API 令牌: $TOKEN"
echo ""
echo "📝 请在 .env 中设置:"
echo "   ONEAPI_TOKEN=$TOKEN"
echo ""
echo "🚀 然后重启代理:"
echo "   docker-compose restart proxy"
echo ""
echo "🌐 One-API 管理后台: $ONEAPI_URL (默认 root/123456)"
echo ""

rm -f "$COOKIE_JAR"

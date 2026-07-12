#!/usr/bin/env bash
# 构建前端到独立目录 dist-prod，并校验所有懒加载 chunk 存在
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FRONTEND="$ROOT/frontend"
OUT="$FRONTEND/dist-prod"

cd "$FRONTEND"
rm -rf "$OUT"
npm run build -- --outDir dist-prod

python3 - <<PY
import re
from pathlib import Path
root = Path("$OUT")
html = (root / "index.html").read_text()
main = re.search(r'src="(/assets/index-[^"]+\\.js)"', html).group(1).lstrip("/")
js = (root / main).read_text()
chunks = sorted(set(re.findall(r'assets/[^"\\']+\\.(?:js|css)', js)))
missing = [c for c in chunks if not (root / c).exists()]
if missing:
    print("✗ 缺失 chunk:", *missing, sep="\n  ")
    raise SystemExit(1)
print(f"✓ 构建完成: {main}, {len(chunks)} 个 chunk 齐全")
PY

if command -v caddy >/dev/null 2>&1; then
  caddy reload --config "$ROOT/Caddyfile" 2>/dev/null && echo "✓ Caddy 已 reload"
fi

echo "访问: https://aiops.tiancaizhaozhao.dpdns.org/#/login"

#!/usr/bin/env python3
"""Launch the independent Pi service using the host's explicit default LLM profile.

Run with backend/.venv/bin/python. Credentials stay in the child environment;
never serialize the API key into an HTTP run payload or print it.
"""
import argparse
import os
from pathlib import Path
import signal
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app.agent.llm import default_endpoint
from app.core.config import settings


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gateway-origin', default='http://127.0.0.1:8000')
    parser.add_argument('--port', type=int, default=8810)
    args=parser.parse_args()
    if len(settings.pi_service_secret)<32:
        parser.error('PI_SERVICE_SECRET must be at least 32 characters')
    endpoint=default_endpoint()
    if not all((endpoint.model, endpoint.base_url, endpoint.api_key)):
        parser.error('Default LLM profile must be configured')
    env={'PI_PRICE_PROFILE':settings.pi_price_profile,'PATH':os.environ['PATH'],'PI_SERVICE_SECRET':settings.pi_service_secret,
         'PI_TOOL_GATEWAY_ORIGIN':args.gateway_origin,'PI_PORT':str(args.port),
         'PI_MODEL_ID':endpoint.model,'PI_MODEL_BASE_URL':endpoint.base_url,'PI_MODEL_API_KEY':endpoint.api_key}
    child=subprocess.Popen(['node','--max-old-space-size=256','src/server.js'],cwd=ROOT/'agent-runtime',env=env)
    def stop(_signal,_frame):
        child.terminate()
    for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,stop)
    try:return child.wait()
    finally:
        if child.poll() is None:
            child.terminate()
            try:child.wait(6)
            except subprocess.TimeoutExpired:child.kill();child.wait()

if __name__=='__main__':sys.exit(main())

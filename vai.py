python -c "
import sys
sys.path.insert(0, '.')
import config_backtest as cfg
import requests
print('Key:', cfg.GEMINI_API_KEY[:10], '...')
url = f'https://generativelanguage.googleapis.com/v1beta/models/{cfg.GEMINI_MODEL}:generateContent?key={cfg.GEMINI_API_KEY}'
r = requests.post(url, json={'contents': [{'parts': [{'text': 'teste'}]}]}, timeout=10)
print('Status:', r.status_code)
print('Resposta:', r.text[:300])
"
import sys
sys.path.insert(0, '.')
import config_backtest as cfg
import requests

print('Key configurada:', cfg.GEMINI_API_KEY[:15], '...')
print('Model:', cfg.GEMINI_MODEL)

url = (f'https://generativelanguage.googleapis.com/v1beta/models/'
       f'{cfg.GEMINI_MODEL}:generateContent?key={cfg.GEMINI_API_KEY}')

payload = {
    'contents': [{'parts': [{'text': 'Responda apenas: OK'}]}],
    'generationConfig': {'maxOutputTokens': 10}
}

try:
    r = requests.post(url, json=payload, timeout=15)
    print('Status HTTP:', r.status_code)
    print('Resposta:', r.text[:500])
except Exception as e:
    print('Erro:', e)

"""
diagnostico_gemini.py — Testa a API Gemini e mostra informações detalhadas
Execute: python diagnostico_gemini.py
"""
import sys
sys.path.insert(0, '.')
import requests
import config_backtest as cfg
import json

print("=" * 60)
print("DIAGNÓSTICO GEMINI")
print("=" * 60)
print(f"Model: {cfg.GEMINI_MODEL}")
print(f"Max tokens: {cfg.GEMINI_MAX_TOKENS}")
print(f"Key (primeiros 20 chars): {cfg.GEMINI_API_KEY[:20]}...")

# Testa com prompt mínimo
url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
       f"{cfg.GEMINI_MODEL}:generateContent?key={cfg.GEMINI_API_KEY}")

payload = {
    "contents": [{"parts": [{"text": "Responda em português com exatamente 3 frases sobre day trade."}]}],
    "generationConfig": {
        "maxOutputTokens": cfg.GEMINI_MAX_TOKENS,
        "temperature": 0.3
    }
}

print(f"\nEnviando request para: {url[:80]}...")
r = requests.post(url, json=payload, timeout=30)

print(f"\nStatus HTTP: {r.status_code}")
print(f"Headers relevantes:")
for k in ['Retry-After', 'X-RateLimit-Limit', 'X-RateLimit-Remaining', 'X-RateLimit-Reset']:
    if k in r.headers:
        print(f"  {k}: {r.headers[k]}")

data = r.json()
print(f"\nResposta completa:")
print(json.dumps(data, indent=2, ensure_ascii=False)[:2000])

if r.status_code == 200:
    candidates = data.get('candidates', [])
    if candidates:
        content = candidates[0].get('content', {})
        parts = content.get('parts', [])
        texto = ' '.join(p.get('text', '') for p in parts if p.get('text')).strip()
        finish = candidates[0].get('finishReason', '')
        usage = data.get('usageMetadata', {})
        
        print(f"\n{'='*60}")
        print(f"TEXTO EXTRAÍDO ({len(texto)} chars):")
        print(texto)
        print(f"\nfinishReason: {finish}")
        print(f"usageMetadata: {usage}")
        
        if finish == 'MAX_TOKENS':
            print("\n⚠️  TRUNCADO — maxOutputTokens insuficiente para resposta completa")
            print(f"   Tokens prompt: {usage.get('promptTokenCount', '?')}")
            print(f"   Tokens resposta: {usage.get('candidatesTokenCount', '?')}")
            print(f"   Tokens reasoning: {usage.get('thoughtsTokenCount', '?')}")
            print(f"   TOTAL: {usage.get('totalTokenCount', '?')}")
            print(f"\n   Solução: aumentar GEMINI_MAX_TOKENS ou usar gemini-2.0-flash")

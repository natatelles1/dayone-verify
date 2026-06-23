"""
Passo 2c — Importa data.json para a tabela 'empresas' do Supabase.
Precisa de SUPABASE_URL e SUPABASE_SERVICE_KEY no .env (service_role, nunca a anon).
Roda UMA VEZ. Seguro re-rodar se a tabela estiver vazia.
"""
import json, os, sys
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    import requests
except ImportError:
    sys.exit("pip install requests")

URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")  # service_role — nunca a anon

if not URL or not KEY:
    sys.exit(
        "Configure SUPABASE_URL e SUPABASE_SERVICE_KEY no .env\n"
        "  SUPABASE_URL=https://xyzxyz.supabase.co\n"
        "  SUPABASE_SERVICE_KEY=eyJ...   (service_role — NÃO a anon)"
    )

data_path = Path(__file__).parent / "data.json"
data = json.loads(data_path.read_text(encoding="utf-8"))
print(f"data.json lido: {len(data)} empresas")

def has(v):
    return bool(v and str(v).strip())

rows = []
for r in data:
    pronta = all(has(r.get(c)) for c in ("nome", "endereco", "ein", "documento"))
    rows.append({
        "estado":    "FL",
        "nicho":     "contabilidade",
        "nome":      r.get("nome", ""),
        "endereco":  r.get("endereco", ""),
        "ein":       r.get("ein", ""),
        "documento": r.get("documento", ""),
        "telefone":  r.get("telefone", ""),
        "email":     r.get("email", ""),
        "pronta":    pronta,
    })

headers = {
    "apikey":        KEY,
    "Authorization": f"Bearer {KEY}",
    "Content-Type":  "application/json",
    "Prefer":        "return=representation",
}

# Insert in batches of 50
BATCH = 50
inserted = 0
for i in range(0, len(rows), BATCH):
    batch = rows[i:i+BATCH]
    r = requests.post(f"{URL}/rest/v1/empresas", json=batch, headers=headers, timeout=30)
    if not r.ok:
        print(f"ERRO batch {i//BATCH+1}: {r.status_code} — {r.text[:300]}")
        sys.exit(1)
    inserted += len(r.json())
    print(f"  Inserted batch {i//BATCH+1}: {inserted}/{len(rows)}")

print(f"\n✓ {inserted} linhas inseridas na tabela 'empresas'")

# Verify: fetch 3 examples
check = requests.get(
    f"{URL}/rest/v1/empresas?select=nome,ein,pronta&estado=eq.FL&limit=3",
    headers={"apikey": KEY, "Authorization": f"Bearer {KEY}"},
    timeout=15,
)
print("\n3 exemplos do banco:")
for row in check.json():
    print(f"  {row['nome'][:50]} | EIN {row['ein']} | pronta={row['pronta']}")

# API externa — integração Gabriel

Endpoint server-to-server para puxar as empresas **prontas** (California) para o
Supabase do Gabriel. Chave secreta fixa, um único cliente.

## Endpoint

```
GET https://dayone-verify.vercel.app/api/external/companies
```

## Autenticação

Envie a chave em UM dos dois headers (qualquer um funciona):

```
Authorization: Bearer <EXTERNAL_API_KEY>
```
ou
```
x-api-key: <EXTERNAL_API_KEY>
```

Sem a chave, ou com chave errada → `401 {"error":"Unauthorized"}`.

A chave é enviada por fora (não vai neste repo). Guardar como secret no lado do Gabriel.

## Exemplo de chamada

```bash
curl -s https://dayone-verify.vercel.app/api/external/companies \
  -H "Authorization: Bearer <EXTERNAL_API_KEY>"
```

## Resposta

```json
{
  "count": 30,
  "companies": [
    {
      "legal_name": "AllPoint Electric LLC",
      "owner_name": "Travis McFarland",
      "email": "info@allpointelectric.com",
      "phone": null,
      "address": "355 S. GRAND AVE., SUITE 2450-100A, Los Angeles, CA, 90071",
      "entity_number": "B20260224824",
      "website": "http://www.allpointelectric.com/",
      "city": "Los Angeles",
      "status": "READY_NO_PDF",
      "verified_at": null
    }
  ]
}
```

## Campos

| Campo | Tipo | Descrição |
|---|---|---|
| `legal_name` | string | Nome legal registrado (CA SOS) |
| `owner_name` | string \| null | Nome do dono/responsável |
| `email` | string \| null | E-mail de contato |
| `phone` | string \| null | Telefone (formato E.164) |
| `address` | string \| null | Endereço principal completo (linha, suite, cidade/estado, CEP) |
| `entity_number` | string | Número de registro na CA SOS |
| `website` | string \| null | Site da empresa |
| `city` | string \| null | Cidade (extraída do endereço principal) |
| `status` | string | `READY` ou `READY_NO_PDF` |
| `verified_at` | string \| null | **Sempre `null` por enquanto** — ainda não há um timestamp confiável de "quando ficou pronta" disponível na API. Reservado para o futuro. |

## O que NÃO vem

Só empresas com `dossier_status` em `READY` ou `READY_NO_PDF`. `PARTIAL` e
`DISCOVERED` não aparecem. Nenhum campo interno (snapshot key, evidência,
match_score, jargão de pipeline).

## Escopo

Só empresas **California** do projeto `dayone-verify`. Nunca inclui dados
FL/Legatus/Impetus.

## Limites

- Sem paginação por enquanto (hoje são 30 empresas; se crescer muito,
  adicionamos `?limit=&offset=` sem quebrar o formato atual).
- Rate limit básico: 30 requisições/minuto por IP (best-effort — reseta se a
  função "esfria" no Vercel).
- Sem CORS liberado — é uma API server-to-server, não deve ser chamada do
  browser.

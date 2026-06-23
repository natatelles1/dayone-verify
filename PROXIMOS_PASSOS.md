# Próximos Passos — DayOne Verify

## 1. Migração Supabase (pronta para ligar)

O `index.html` já suporta Supabase nativamente — basta preencher duas constantes no topo do `<script>`:

```js
const SUPABASE_URL      = "https://SEU_PROJECT.supabase.co";
const SUPABASE_ANON_KEY = "eyJ...";  // chave anon (read-only, segura no front)
```

Quando essas variáveis estão vazias, a ferramenta continua lendo `data.json` normalmente (fallback automático).

### Pré-requisitos para ativar

- Novo projeto Supabase disponível (org nova grátis **ou** upgrade para plano Pro na org `legatus`)
- O token `verify-data` já está configurado em `~/.claude.json` (Personal Access Token da conta Supabase)

### Passos para migrar (uma vez com projeto disponível)

1. Criar projeto `dayone-verify` no Supabase via MCP (`mcp__supabase__create_project`)
2. Rodar o schema SQL:
   ```sql
   create table empresas (
     id         bigserial primary key,
     estado     text not null default 'FL',
     nicho      text not null default 'contabilidade',
     nome       text not null,
     endereco   text,
     ein        text,
     documento  text,
     telefone   text,
     email      text,
     pronta     boolean generated always as (
                  nome <> '' and endereco <> '' and ein <> '' and documento <> ''
                ) stored
   );
   create index on empresas (estado, nicho);
   alter table empresas enable row level security;
   create policy "public read" on empresas for select using (true);
   ```
3. Executar `import_supabase.py` com `SUPABASE_URL` e `SUPABASE_SERVICE_KEY` no `.env`
4. Preencher as constantes no `index.html`, commitar e push → Vercel redesploya automaticamente

---

## 2. Expansão para Califórnia — Master Unload (DBSF)

O seletor de CA já existe no `index.html` marcado como "em breve". Para ativar:

### Fonte de dados oficial

- **CA Secretary of State Business Search / Master Unload** — arquivo CSV oficial com todas as entidades ativas da CA
- Solicitar em: `businesssearch.sos.ca.gov` → seção "Bulk Data"

### Custos estimados

| Item | Custo |
|------|-------|
| Dados básicos (Master Unload CSV — nome, endereço, status, agent) | **US$ 100** (taxa única) |
| PDFs dos registros originais (Articles of Incorporation / Organization) | **US$ 800** (por lote completo) |
| Total | **US$ 900** |

### O que o Master Unload entrega

- Número da entidade, nome legal, tipo (Corp/LLC/LP), status, data de registro
- Endereço do agente registrado
- **Não inclui EIN** — EIN da CA precisa de cruzamento com IRS (não público) ou verificação manual via Sunbiz equivalent (não existe para CA)

### Estratégia sugerida

1. Comprar apenas os dados básicos (US$100) e filtrar segmento contabilidade
2. Cruzar com Google Maps (Apify) para telefone/website — mesma lógica do pipeline FL
3. PDFs (US$800) só se o cliente exigir documento original; o CA SOS tem verificação online gratuita (`bizfileonline.sos.ca.gov`)

### Blocker atual

- Budget Apify esgotado ($0.39 restante em 2026-06-23); renovar em 2026-07-01 (ciclo mensal FREE)
- EIN não disponível publicamente na CA — definir com cliente se é requisito ou se verificação online substitui

---

*Última atualização: 2026-06-23*

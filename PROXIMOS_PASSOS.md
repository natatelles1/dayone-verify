# Próximos Passos — DayOne Verify

---

## FASE 2 — Coleta automática pela plataforma

> **Gatilho:** só construir após "sim" comercial da DayOne. A aba "Nova pesquisa" fica como prévia até lá.

### Arquitetura

```
[Plataforma/site]  --webhook-->  [n8n / VPS Hostinger]  --->  [Apify: Maps + Sunbiz]
       |                               |                              |
       |  <--- polling status ---  [Supabase] <--- salva -------  (PDFs + dados)
       |
       +-- mostra "processando, prazo X" → empresas quando pronta
       Aviso WhatsApp (Evolution API) a cada coleta com custo
```

Tudo na estrutura existente (VPS, n8n, Apify, Evolution). Isolado para transferência futura à DayOne.

### Prazo realista

| Peça | Tempo |
|------|-------|
| 1. Supabase (projeto + tabelas) | ~1h (depois de resolver slot) |
| 2. n8n (workflow ponta a ponta) | vários dias |
| 3. Plataforma (ligar aba) | ~1 dia |
| 4. Proteções de custo | ~1 dia |
| **Total** | **~1 semana focada** |

---

### Peça 1 — Supabase

**Pré-requisito:** resolver slot de projeto (free = 2 ativos; criar org nova grátis ou ir Pro).

```sql
-- Pedidos de coleta
create table pesquisas (
  id                  uuid primary key default gen_random_uuid(),
  nicho               text not null,
  estado              text not null default 'FL',
  quantidade          int  not null,
  tipos               text[] not null default '{LLC}',
  so_com_doc          boolean not null default true,
  status              text not null default 'pendente',  -- pendente|processando|pronta|erro
  prazo_estimado_min  int,
  total_coletadas     int default 0,
  criado_em           timestamptz default now(),
  concluido_em        timestamptz
);

-- Empresas coletadas, ligadas ao pedido
create table empresas (
  id           bigint generated always as identity primary key,
  pesquisa_id  uuid references pesquisas(id),
  estado       text default 'FL',
  nicho        text,
  nome         text not null,
  endereco     text,
  ein          text,
  documento    text,
  telefone     text,
  email        text,
  pronta       boolean default false,
  criado_em    timestamptz default now()
);

create index idx_emp_pesquisa on empresas(pesquisa_id);
create index idx_pesq_status  on pesquisas(status);

-- RLS: leitura pública (site só lê). Escrita só via service_role (n8n).
alter table pesquisas enable row level security;
alter table empresas  enable row level security;
create policy "ler pesquisas" on pesquisas for select to anon using (true);
create policy "ler empresas"  on empresas  for select to anon using (true);
```

**Migração da base estática atual** (106 LLC): rodar `import_supabase.py` com a nova `pesquisa_id` de um registro "pronta" pré-criado, OU manter `data.json` como fallback e só usar Supabase para pesquisas novas.

---

### Peça 2 — n8n (workflow)

Nodes em ordem:

1. **Webhook (trigger)** — POST `{pesquisa_id, nicho, estado, quantidade, tipos, so_com_doc}`
2. **Supabase: marcar "processando"** + gravar `prazo_estimado_min` (regra: 1 min a cada 5 empresas, mín 5)
3. **Apify — Google Maps** (`compass~crawler-google-places`): `"{nicho} {estado}"` nas cidades-alvo; over-fetch `quantidade × 3`
4. **Apify — Sunbiz** (`parseforge~sunbiz-florida-business-scraper`): por empresa → nome legal, endereço principal, EIN/FEI, GetDocument PDF
5. **Function (filtro/dedup)**: só tipos pedidos (LLC), só 4 campos se `so_com_doc=true`, dedup por nome normalizado, cortar na quantidade
6. **Supabase: inserir empresas** (com `pesquisa_id`, `pronta=true`)
7. **Supabase: marcar "pronta"** + `total_coletadas` + `concluido_em`
8. **Evolution API: WhatsApp** — "Coleta X concluída: N empresas, custo ~$Y"
9. **Tratamento de erro**: em qualquer falha → `status='erro'` + aviso WhatsApp

**Notas de implementação:**
- Reaproveitar lógica validada no pipeline FL: normalização de nome, match ~80, GetDocument com `aggregateId+transactionId`
- Rodar Sunbiz em lotes para não estourar timeout do n8n
- Somar `_cost_usd` de cada run Apify e passar no aviso final

---

### Peça 3 — Plataforma (ligar aba "Nova pesquisa")

Substituir `fakeSearch()` no `index.html`:

1. Ao clicar "Iniciar coleta": POST no webhook do n8n com `{nicho, estado, quantidade, tipos, so_com_doc}` → n8n cria o registro com service_role e devolve `pesquisa_id`
2. Guardar `pesquisa_id`; mostrar "Processando — prazo estimado: X min"
3. Polling a cada 10s: `GET /rest/v1/pesquisas?id=eq.{id}` (anon, read-only)
4. Quando `status='pronta'`: buscar empresas do `pesquisa_id`, exibir na aba "Base de dados"
5. Se `status='erro'`: mensagem amigável

---

### Peça 4 — Proteções de custo (obrigatório enquanto for conta própria Apify)

- Teto de quantidade por pesquisa: **máx 200**
- Máx pesquisas simultâneas / por dia (configurar no n8n com counter no Supabase)
- Aviso WhatsApp a cada coleta com custo estimado
- (Opcional) Aprovação manual para coletas > limite: n8n manda WhatsApp e espera "OK" antes de continuar

---

### Sequência de construção

1. Supabase: criar projeto + tabelas
2. n8n: montar workflow e testar disparando manual (sem o site); confirmar coleta, filtro LLC, PDF salvo no banco
3. Teste com `quantidade=10`, verificar custo real
4. Plataforma: ligar webhook + polling de status no `index.html`
5. Proteções de custo + aviso WhatsApp
6. Teste ponta a ponta: pedir "contabilidade, 50, LLC" pela tela → aguardar → empresas com documento
7. Testes de repetição → liberar para DayOne testar

---

### Transferência futura para a DayOne

- Supabase: passar projeto ou criar credenciais para eles
- n8n: migrar workflow isolado para servidor deles
- Apify: trocar token pelo deles (custo passa para conta deles)
- Tudo isolado: transferência não expõe Impetus/Legatus

---

## Migrações menores (já prontas para ligar)

### Supabase — base estática atual

O `index.html` suporta Supabase como fonte primária — basta preencher:

```js
const SUPABASE_URL      = "https://SEU_PROJECT.supabase.co";
const SUPABASE_ANON_KEY = "eyJ...";
```

Sem essas variáveis, lê `data.json` normalmente (fallback automático).

Token `verify-data` já configurado em `~/.claude.json` (Personal Access Token Supabase).

---

## Califórnia — Master Unload (CA SOS)

O seletor de CA já existe no `index.html` como "em breve".

| Item | Custo |
|------|-------|
| Dados básicos (Master Unload CSV) | **US$ 100** (taxa única) |
| PDFs dos registros originais | **US$ 800** (lote completo) |
| **Total** | **US$ 900** |

**O que entrega:** nome legal, tipo (LLC/Corp/LP), status, endereço do agente registrado.
**Não inclui EIN** — definir com cliente se verificação online CA SOS substitui.

**Blockers:**
- Budget Apify renova em 2026-07-01 (ciclo FREE mensal)
- EIN não público na CA — alinhar com cliente antes de comprar

---

*Última atualização: 2026-06-23*

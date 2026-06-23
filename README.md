# DayOne Verify — ferramenta (demo)

Página única, sem backend e sem build. Identidade do Claude Design (azul-marinho #16233D,
creme #F7F4EE, âmbar #F2A93B, logo "amanhecer", Space Grotesk), funcional, focada na
**verificação de anunciante do Google**.

- Login fixo (split screen "O primeiro dia")
- Dashboard com prontidão calculada AO VIVO: **Endereço · EIN · Documento de registro · Prontas para verificar**
  ("pronta" = tem nome legal + endereço + EIN + documento). Telefone e e-mail NÃO contam — são complementares.
- Tabela com busca e filtro (todas / só prontas)
- Exportar planilha · Exportar dados de verificação (CSV)
- Seletor já preparado para **Califórnia** (coleta em preparação — estado "em breve", sem inventar dados)
- Lê data.json (mesma pasta); sem ele, mostra amostra

## Login de teste (topo do <script>)
- Usuário: cliente · Senha: dayone2026 · CLIENT_NAME: nome do prospect

## Formato do data.json
Array de objetos: nome, endereco, ein, documento, email, telefone (string; "" quando vazio).
documento = link direto do PDF do Sunbiz. "Pronta" = nome + endereço + EIN + documento.

---

## Prompt para o Claude Code (gerar data.json + deploy Vercel)

Rode dentro da pasta dayone-verify. Ajuste o caminho do xlsx se o seu for diferente.

```
Tenho a pasta dayone-verify com index.html (ferramenta DayOne Verify) e data.json modelo.
Meu xlsx final esta em: ~/Dev/florida_accounting_pilot/data/v3/fl_contabilidade_PILOTO_FINAL_20260623_032606.xlsx
(141 empresas completas, 124 com link de PDF direto do documento).

1. Converta esse xlsx para um data.json nesta pasta (dayone-verify), formato:
   [{"nome":..,"endereco":..,"ein":..,"documento":..,"email":..,"telefone":..}, ...]
   - documento = o link direto do PDF do Sunbiz (GetDocument). As 17 sem PDF ficam com documento "".
   - Campos sem valor viram "".
2. IMPORTANTE (regras de verificacao do Google, confirme antes de salvar):
   - A coluna "nome" deve ser o NOME LEGAL do FL DOS (com sufixo LLC/Inc/Corp), nunca o nome do Google Maps.
   - A coluna "endereco" deve ser o PRINCIPAL ADDRESS do registro do FL DOS, nao o endereco do listing do Maps.
   - Se alguma linha estiver com nome/endereco do Maps, troque pelo do FL DOS. Me diga quantas corrigiu.
3. Abra index.html, faca login (cliente / dayone2026) e confirme: dashboard mostra Endereco/EIN/Documento/Prontas certos, a tabela lista as 141, o link "documento" abre o PDF, e os dois exports geram CSV.
4. Suba no Vercel: repo no GitHub com esta pasta + deploy como site estatico (sem framework). Me devolva a URL publica.
5. Reporte: total de linhas, quantas "prontas para verificar", quantas com PDF direto, e a URL final.

Nao invente dados nem links. Use o que esta no xlsx e os PDFs reais ja gerados. NAO colete California agora - o seletor de CA ja esta como "em breve" de proposito.
```

# AI Code Review Platform

[![CI](https://github.com/fabioestevam2404/ai-code-review-plataform/actions/workflows/ci.yml/badge.svg)](https://github.com/fabioestevam2404/ai-code-review-plataform/actions/workflows/ci.yml)

Plataforma de code review automatizado para Pull Requests do GitHub. Recebe webhooks, analisa o diff com agentes de segurança, performance e qualidade e aplica um quality gate configurável — com fila persistente, retry, trilha de auditoria e modo read-only por padrão. Nunca executa o código analisado.

```text
GitHub webhook -> FastAPI -> SQLite -> worker -> analyzers -> quality gate
                                         -> auditoria -> comentário opcional
```

## Como funciona

1. O GitHub envia um webhook a cada PR aberto, atualizado ou reaberto.
2. A plataforma valida a assinatura e enfileira o review numa fila persistente.
3. Um worker baixa o diff e o analisa com agentes especializados.
4. Cada achado traz severidade, confiança, evidência e recomendação.
5. O quality gate decide o veredito: `APPROVE`, `APPROVE_WITH_COMMENTS` ou `REQUEST_CHANGES`.
6. Opcionalmente, o resultado é publicado como comentário no PR.

## Para que serve

- **Segurança:** detectar cedo padrões perigosos — `shell=True`, credenciais hard-coded, SQL montado por interpolação.
- **Performance:** apontar chamadas HTTP sem timeout e queries dentro de loops (N+1).
- **Qualidade:** sinalizar TODO/FIXME introduzidos em código de produção.
- **Padronização:** garantir um critério mínimo e consistente em todos os PRs, independente de quem revisa.

## Vantagens

- **Seguro por padrão:** não executa o código do PR, não roda shell, não chama LLM; escrita no GitHub desligada até ser habilitada.
- **Confiável:** webhooks duplicados não geram reviews duplicados; falhas transitórias têm retry com backoff exponencial; commits desatualizados são descartados; falha na publicação nunca gera comentário em dobro.
- **Auditável:** cada etapa (criação, análise, falha, publicação, rerun) é registrada em `audit_events`.
- **Determinístico:** o mesmo diff gera o mesmo veredito — resultados reproduzíveis e explicáveis.
- **Leve:** só Python + SQLite. Sem Redis, filas externas ou serviços pagos; sobe com um `uvicorn`.
- **Extensível:** novos agentes implementam a mesma interface `Agent.analyze()`; a arquitetura prevê adaptadores isolados para LLM, RAG e SAST.

## Quando usar

- Times pequenos/médios que querem uma primeira linha de defesa em PRs sem adotar ferramenta comercial.
- Ambientes regulados ou sensíveis, onde enviar código para LLM externo não é permitido.
- Como base para uma plataforma DevSecOps própria, plugando SAST, testes ou IA de forma controlada.
- Estudo/portfólio: exemplo de arquitetura orientada a eventos com fila, idempotência e auditoria.

## O que está implementado

- validação HMAC-SHA256 de webhooks do GitHub;
- deduplicação por delivery e por `repository + PR + head_sha + profile`;
- persistência de jobs, findings e eventos de auditoria em SQLite;
- worker com claim transacional, retry limitado com backoff exponencial e estados explícitos
  (`RECEIVED → ANALYZING → COMPLETED | RETRY_SCHEDULED | FAILED | STALE`; `FAILED` e `STALE` são terminais);
- coleta do diff pela API do GitHub;
- agentes determinísticos de segurança, performance e qualidade;
- confiança numérica, severidade única e quality gate configurável;
- publicação de comentário somente quando `GITHUB_WRITE_ENABLED=true`;
- execução sem LLM, shell arbitrário ou execução do código analisado.

## Execução local

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
Copy-Item .env.example .env
uvicorn app.main:app --reload
```

Testes: `pytest`

Endpoints principais (quando `API_ADMIN_TOKEN` estiver definido, envie `X-API-Key`):

- `GET /health`
- `POST /webhooks/github`
- `GET /reviews/{review_id}`
- `GET /reviews/{review_id}/findings`
- `POST /reviews/{review_id}/rerun` — recoloca na fila um review terminal (mesmo `review_id`); retorna 409 se ainda estiver em fila/análise

Falha ao publicar o comentário não reprocessa o review: o job permanece `COMPLETED`, o erro fica em `error` e um evento `COMMENT_FAILED` é auditado. Para republicar, use o rerun.

## Configuração no GitHub

1. Crie um GitHub App ou token com menor privilégio e defina `GITHUB_TOKEN`.
2. Configure um webhook para o evento `pull_request` apontando para `/webhooks/github`. Ele deve enviar `X-Hub-Signature-256`, `X-GitHub-Event` e `X-GitHub-Delivery`.
3. Defina `GITHUB_WEBHOOK_SECRET` e, em produção, `API_ADMIN_TOKEN`.
4. Comece em modo read-only (padrão) consultando `GET /reviews/{id}`; quando confiar nos achados, ative `GITHUB_WRITE_ENABLED=true`.

Variáveis disponíveis em [.env.example](.env.example).

A aplicação valida a configuração ao iniciar e recusa subir quando:

- `APP_ENV=production` e `GITHUB_WEBHOOK_SECRET` está vazio ou com valor de exemplo (`change-me`);
- `APP_ENV=production` e `API_ADMIN_TOKEN` não está definido;
- `GITHUB_WRITE_ENABLED=true` sem `GITHUB_TOKEN` (em qualquer ambiente).

## Limitações atuais

- Análise por regras (regex) apenas sobre as linhas adicionadas do diff, sem entender o contexto do código.
- Ainda não há LLM, RAG, SAST externo, execução de testes do PR, patches, merge nem comentários por linha. Esses pontos devem ser adicionados atrás de adaptadores isolados, com sandbox e política de permissões.

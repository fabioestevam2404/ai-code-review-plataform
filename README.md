# AI Code Review Platform

MVP executável de revisão de Pull Requests. O fluxo é read-only por padrão:

```text
GitHub webhook -> FastAPI -> SQLite -> worker -> analyzers -> quality gate
                                         -> auditoria -> comentário opcional
```

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

Endpoints principais (quando `API_ADMIN_TOKEN` estiver definido, envie `X-API-Key`):

- `GET /health`
- `POST /webhooks/github`
- `GET /reviews/{review_id}`
- `GET /reviews/{review_id}/findings`
- `POST /reviews/{review_id}/rerun` — recoloca na fila um review terminal (mesmo `review_id`); retorna 409 se ainda estiver em fila/análise

Falha ao publicar o comentário não reprocessa o review: o job permanece `COMPLETED`, o erro fica em `error` e um evento `COMMENT_FAILED` é auditado. Para republicar, use o rerun.

## GitHub

Configure um GitHub App ou token com menor privilégio e defina `GITHUB_TOKEN`. O webhook deve enviar `X-Hub-Signature-256`, `X-GitHub-Event` e `X-GitHub-Delivery`. A escrita fica desligada por padrão.

Este MVP não executa testes do PR, SAST externo, patches, merge, RAG ou chamadas LLM. Esses pontos devem ser adicionados atrás de adaptadores isolados, com sandbox e política de permissões.

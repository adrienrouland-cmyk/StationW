# MroPilot Email Gateway

Service Express qui reçoit les webhooks email Unipile, normalise le payload, applique des garde-fous (auth, validation, anti-loop, idempotence) puis transfère vers un service AI.

## Installation

```bash
npm install
```

## Variables d’environnement

Copiez `.env.example` en `.env` puis configurez :

- `PORT` : port HTTP.
- `JSON_BODY_LIMIT` : taille maximale du corps JSON (ex: `1mb`).
- `UNIPILE_DSN` : DSN Unipile (**requis** pour tout envoi de mail, y compris les réponses webhook).
- `UNIPILE_ACCESS_TOKEN` : token Unipile (**requis** pour tout envoi de mail, y compris les réponses webhook).
- `WEBHOOK_SECRET` : secret attendu dans le header `unipile-auth` (optionnel).
- `AI_SERVICE_URL` : base URL du service AI (ex: `http://127.0.0.1:8000`). Préférer `127.0.0.1` à `localhost` (Node 18+ résout `localhost` en IPv6).
- `DRY_RUN_SEND_REPLY` : mettre à `true` pour loguer les réponses sans les envoyer via Unipile (test local).

## Lancer en local

```bash
npm run dev
```

## Endpoints

- `GET /health` : healthcheck.
- `POST /webhooks/unipile/email` : webhook Unipile.
- `POST /send-test-email` : envoi d’un mail de test via Unipile.

## Test rapide

Healthcheck :

```bash
curl http://localhost:3000/health
```

Webhook (exemple minimal) :

```bash
curl -X POST http://localhost:3000/webhooks/unipile/email \
  -H "Content-Type: application/json" \
  -H "unipile-auth: <secret>" \
  -d '{
    "event": "mail_received",
    "account_id": "acc_123",
    "email_id": "email_456",
    "message_id": "msg_789",
    "date": "2026-05-05T10:00:00Z",
    "from_attendee": { "identifier": "sender@example.com" },
    "to_attendees": [{ "identifier": "receiver@example.com" }],
    "subject": "Hello",
    "body_plain": "Hello world"
  }'
```

Envoi test via Unipile :

```bash
curl -X POST http://localhost:3000/send-test-email \
  -H "Content-Type: application/json" \
  -d '{"accountId":"<id>","to":"mropilot@gmail.com"}'
```

## Comportements importants

- Auth optionnelle via `WEBHOOK_SECRET` (`unipile-auth`).
- Validation stricte du payload (400 si schéma invalide).
- Idempotence en mémoire (message_id + account_id).
- Retry/backoff sur le forward vers l’AI (3 tentatives).
- `DRY_RUN_SEND_REPLY=true` : log la réponse sans l’envoyer (test local sans spam).
- Le sujet de la réponse est pris dans `aiResponse.reply.subject` si fourni, sinon `Re: <subject original>`.
- `UNIPILE_DSN` et `UNIPILE_ACCESS_TOKEN` sont requis dès qu’une réponse doit être envoyée (action `reply`).


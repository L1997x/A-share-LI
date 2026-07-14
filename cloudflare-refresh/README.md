# Cloudflare refresh trigger

This Worker is the independent scheduler for A-share-LI. Its Cron Triggers call
the existing GitHub Actions `workflow_dispatch` endpoint. GitHub's own schedule
remains enabled as a fallback.

The Worker has no public fetch handler. Store `GITHUB_ACTIONS_TOKEN` with
Wrangler as an encrypted secret; never commit it. Use a fine-grained GitHub
token limited to the `L1997x/A-share-LI` repository with only `Actions: write`.

```powershell
npm install
npx wrangler login
npx wrangler secret put GITHUB_ACTIONS_TOKEN
npm test
npm run deploy
```

Cloudflare cron expressions use UTC. Its five primary triggers map to Beijing
times 10:00, 11:20, 13:30, 14:30, and 20:00. GitHub Actions retains the backup
triggers and watchdog so the free Cloudflare account stays within its five-cron
limit.

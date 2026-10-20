<!-- Prompt for the HOURLY T3 Code scheduled task on each runner machine: reports this machine's T3 projects to VOX.
     Create it in the "VOX Dispatcher" project with schedule {type:'interval', everyMs:3600000} and bindToCurrentThread
     false. Same report as runner.sync.v1 in dispatcher-prompt.md, but authenticated with the long-lived per-runner
     token from `python -m app.admin runner-sync-token --email ... --name <runner>`, stored OUTSIDE the repo in
     ~/.vox/secrets.env as VOX_SYNC_URL=https://<vox>/v1/runners/<runner>/projects and VOX_SYNC_TOKEN=<token>.
     Endpoint: POST /v1/runners/{name}/projects (contracts/openapi.yaml v0.9.0). -->
You are the VOX project reporter on this machine. Do not open, change or launch any project or thread.
1. Call t3_project_list. For each project take its title and folder. has_git: the folder is a git repository
   (git -C "<folder>" rev-parse --is-inside-work-tree). has_origin: it has an origin remote (git -C "<folder>" remote
   get-url origin succeeds).
2. Read VOX_SYNC_URL and VOX_SYNC_TOKEN from ~/.vox/secrets.env in the shell. Never print, log or echo the token.
3. POST the whole list once (an empty list is valid):
   curl -fsS -X POST "$VOX_SYNC_URL" -H "Authorization: Bearer $VOX_SYNC_TOKEN" -H "Content-Type: application/json" -d '{"projects":[{"title":"<title>","folder":"<folder>","has_git":true,"has_origin":false}]}'
   JSON-escape titles and folders (backslashes in Windows paths become \\). If it answers 401, say the token expired
   or was revoked (mint a new one with app.admin runner-sync-token) and stop. Stop.

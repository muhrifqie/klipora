# Security

## Reporting a vulnerability

Please **do not open a public issue** for security problems. Use GitHub's private
[security advisory form](https://github.com/muhrifqie/klipora/security/advisories/new) instead, with:

- what an attacker could do and under which conditions,
- steps to reproduce (Klipora version or commit, Premiere and Windows version),
- whether any key, file or prompt leaves the machine.

Never include real API keys, `.env` files, cookies or HAR captures in a report. You should get a first reply within
7 days. Fixes land on `main`; there are no separately maintained release branches yet.

## How secrets are handled

| Secret | Where it lives | Notes |
|---|---|---|
| AI provider keys | `%APPDATA%\Klipora\ai_keys.bin` | Encrypted with Windows DPAPI (CurrentUser scope + app-specific entropy). Only the same Windows user on the same PC can decrypt. Entered once in **Settings > AI**, sent to the engine over stdin, never returned to the panel. On non-Windows systems the keystore refuses to work rather than storing plaintext. |
| `.env` in the repo root | your working copy only (gitignored) | Optional. `AI_API_KEY` is used **only** by the built-in local proxy profile and only when its URL is loopback (`127.0.0.1`) or the `.env` URL itself, so it is never sent to a remote provider. |
| Pexels API key (B-Roll) | `%APPDATA%\Klipora\settings.json` | Write-only from the panel (it can tell whether a key is set, not read it back). Stored as plain text in your user profile; treat that file as private. |

Other rules the code follows:

- Keys are never printed, logged, returned in job results or written to review files. The engine log
  (`%LOCALAPPDATA%\Klipora\logs\engine.log`) redacts every known key value.
- AI requests never follow HTTP redirects, so a key and a prompt cannot be bounced to another host.
- Custom provider headers whose names look like credentials (`authorization`, `cookie`, `*key*`, `*token*`,
  `*secret*`) are refused; keys go through the keystore only.
- AI output is treated as untrusted data: it is parsed, validated against a schema and shown for review before
  anything changes on the timeline.

## Things to know

- **PlayerDebugMode.** Klipora is unsigned, so Premiere only loads it with
  `HKCU\Software\Adobe\CSXS.12\PlayerDebugMode = 1`. That setting lets *any* unsigned CEP extension in your
  extensions folders load. Only install extensions you trust, and set it back to `0` if you stop using Klipora.
- **CEP debug port.** `panel/.debug` opens Chrome DevTools for the panel on `localhost:8088` while Premiere runs.
  It listens on the local machine only. Delete `panel/.debug` if you do not develop on the panel.
- **The panel runs Node.js** (CEP `--enable-nodejs`) with your user's rights, like every CEP extension, and
  starts the Python engine as a child process. Review changes to `panel/` and `engine/` before pulling them from
  untrusted forks.
- Media, transcripts and renders stay on your PC. With an AI provider enabled, transcript text (and for a few
  vision features, single frames) goes to the provider you configured.

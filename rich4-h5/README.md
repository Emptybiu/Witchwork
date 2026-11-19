# Original Rich4 mobile H5 private multiplayer

This repository branch holds a private deployment wrapper for **iam567/rich4-renew-app v1.2.4**. It does not replace the original rules/characters/stock/cards. All code modifications are in `patch.py` and run over the official source during deployment.

- `build.sh` clones upstream, applies sync fixes and compiles Vite/TypeScript.
- `server.mjs` serves the actual original game, runs a wire-compatible private WebSocket relay and gates files behind a shared invite secret.
- Private site: visit `https://<assigned-domain>/?access=<secret>` once, then use the same link for a friend; the browser stores an HTTP-only session cookie.
- `RICH4_PRIVATE_KEY` must be set. Never commit the secret to Git.
- `RICH4_HOSTNAME` is the Render app's actual hostname without https://. A fixed WSS server uses this host. Everyone joins the same server.
- `RICH4_FETCH_ASSETS=1` pulls the upstream release's separate 1.52-GB asset pack; only do this in a private-use environment with appropriate rights. The pack is not in Git and never committed.
- `GET /health` returns whether the service is running and whether game assets are mounted. Do not treat a server returning 200 as proof of game usability.
- Render's free service will sleep when idle and its disk is ephemeral. A stable always-on production solution requires an appropriate host.

**License**: The upstream GPL-3.0-or-later source remains licensed under GPL-3.0-or-later. Its copyrighted visual/audio assets are separate and are explicitly described as for private use only. Do not publicly distribute those assets without permission.

**Remaining QA**: original game's browser assets/entry UI, two clients in mixed networks, map load barrier, messages during gameplay, disconnect/resume and release-version checks. An initial CI build can run without assets to confirm the source patch compiles.

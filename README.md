# Velmora Ghosts

One shared ghost engine, many Velmora spirits. Character voice and slash-command
flavor live in `characters/<id>.yaml`; shared backstory lives in `data/lore/`.
Boot any ghost with `GHOST_ID`:

```bash
GHOST_ID=mordy python bot.py
```

Mordy is the first character ported from
[mordyvelmora-ghost](https://github.com/rherschel35/mordyvelmora-ghost). With
`GHOST_ID=mordy`, his system prompt, moods, keyword reactions, lore fragments,
history pairings, and `/interact` partners match that bot.

## Layout

```
bot.py                 # thin entrypoint
engine/                # shared Discord + Claude engine
  bot.py               # client setup, cog loading, presence
  config.py            # GHOST_ID → characters/<id>.yaml
  cogs/                # personality, haunting, commands, diary
characters/
  mordy.yaml           # Mordy-specific voice + config
data/lore/             # shared across every ghost
  velmora_lore.json    # canonical biographies
  shared_history.json  # cross-ghost story bank
Dockerfile             # one image for every Railway service
```

## One image, six Railway services

Railway deploys services from a container image. This repo builds **one**
image (the Dockerfile at the repo root) and runs it as **six** services — one
Discord bot process per ghost:

| Railway service | `GHOST_ID` | Character file |
| --- | --- | --- |
| Mordy | `mordy` | `characters/mordy.yaml` |
| Finley | `finley` | *(add `characters/finley.yaml`)* |
| Cassy | `cassy` | *(add `characters/cassy.yaml`)* |
| Sebastian | `sebastian` | *(add `characters/sebastian.yaml`)* |
| Maynard | `maynard` | *(add `characters/maynard.yaml`)* |
| Vida | `vida` | *(add `characters/vida.yaml`)* |

Each service gets its own variables and (recommended) its own volume:

- **Shared by the image:** engine code, `data/lore/`, every `characters/*.yaml`.
- **Per service:** `GHOST_ID`, `DISCORD_TOKEN`, optional `OTHER_GHOST_*` peer
  bot IDs, `HAUNT_CHANNEL_IDS`, `ALLOWED_GUILD_IDS`, `STATE_DIR`.
- **Per service volume:** mount at `STATE_DIR` (e.g. `/data`) so
  `memory_store.json` survives redeploys. Do **not** mount over `data/lore/`
  or the shared biographies disappear at runtime.

Typical Railway setup:

1. Create one Railway project for Velmora.
2. Connect this GitHub repo; Railway builds the Dockerfile once per deploy.
3. Duplicate the service five more times (or add five empty services pointing
   at the same repo / same image).
4. On each service, set `GHOST_ID` and that ghost's `DISCORD_TOKEN` (and
   `ANTHROPIC_API_KEY`, usually as a shared variable).
5. Attach a volume to each service and set `STATE_DIR` to the mount path.

Same commit → same image → six independent bots. Adding a seventh ghost is a
new YAML under `characters/` plus another Railway service with a new
`GHOST_ID`, not a new codebase.

## Local setup

1. Create a Discord application + bot at https://discord.com/developers/applications
   - Enable the **Message Content Intent** under Bot settings.
   - Invite it with the `bot` and `applications.commands` scopes, and at least:
     View Channels, Send Messages, Read Message History.
2. `cp .env.example .env` and fill in `DISCORD_TOKEN`, `ANTHROPIC_API_KEY`,
   and `GHOST_ID=mordy`.
3. `pip install -r requirements.txt`
4. `python bot.py` (or `python -m engine`)

Slash commands sync on startup (guild-instant if you set `DEV_GUILD_ID`,
otherwise global sync which can take up to an hour the first time).

## Commands (Mordy)

- `/seance question:<text>` — ask the ghost something; it answers in character.
- `/haunt user:<@member>` — it fixates on that member for a while.
- `/lore` — next unrevealed fragment of Velmora's backstory.
- `/mood` — (admin) peek at the current mood.
- `/interact who:<ghost>` — brief public exchange with Finley or Cassy.

## Notes

- All dialogue is generated at request time by Claude (model overridable via
  `VELMORA_MODEL`). Fallback lines from the character YAML are used only if
  the API key is missing or a call fails.
- Academy rumors (`/rumor` and scheduled posts) live in Housecup, not here.

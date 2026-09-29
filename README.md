# Velmora Ghosts

One shared ghost engine, many Velmora spirits. Character voice and slash-command
flavor live in `characters/<id>.yaml`; shared backstory lives in `data/lore/`.
Boot any ghost with `GHOST_ID`:

```bash
GHOST_ID=mordy python bot.py
GHOST_ID=finley python bot.py
GHOST_ID=maynard python bot.py
GHOST_ID=sebastian python bot.py
GHOST_ID=vida python bot.py
GHOST_ID=cassy python bot.py
```

Characters ported so far (slash commands trimmed for live use):

- **Mordy** — `/haunt` + `/stophaunt` (**Headmasters only**).
- **Finley** — `/flirt` + `/stopflirt` (**Headmasters only**).
- **Cassy** — `/invention` (anyone).
- **Sebastian** — `/pun` (anyone).
- **Vida** — `/tend` (anyone).
- **Maynard** — **no slash commands**; short chaotic asides + puns in chat only.

Passive keywords, moods, and system prompts still live in each
`characters/<id>.yaml`.

## Layout

```
bot.py                 # thin entrypoint
engine/                # shared Discord + Claude engine
  bot.py               # client setup, cog loading, presence
  config.py            # GHOST_ID → characters/<id>.yaml
  cogs/                # personality, haunting, commands, diary
characters/
  mordy.yaml           # Mordy: /haunt /stophaunt (headmasters)
  finley.yaml          # Finley: /flirt /stopflirt (headmasters)
  maynard.yaml         # Maynard: no slash commands
  sebastian.yaml       # Sebastian: /pun
  vida.yaml            # Vida: /tend
  cassy.yaml           # Cassy: /invention
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
| Finley | `finley` | `characters/finley.yaml` |
| Cassy | `cassy` | `characters/cassy.yaml` |
| Sebastian | `sebastian` | `characters/sebastian.yaml` |
| Maynard | `maynard` | `characters/maynard.yaml` |
| Vida | `vida` | `characters/vida.yaml` |

Each service gets its own variables and (recommended) its own volume:

- **Shared by the image:** engine code, `data/lore/`, every `characters/*.yaml`.
- **Per service:** `GHOST_ID`, `DISCORD_TOKEN`, optional `HAUNT_CHANNEL_IDS`,
  `ALLOWED_GUILD_IDS`, `STATE_DIR`, `HEADMASTER_ROLE_ID` (Mordy/Finley/Maynard).
- **Per service volume:** mount at `STATE_DIR` (e.g. `/data`) so
  `memory_store.json` survives redeploys. Do **not** mount over `data/lore/`
  or the shared biographies disappear at runtime.

### Service 1 — Mordy

| Variable | Example / notes |
| --- | --- |
| `GHOST_ID` | `mordy` |
| `DISCORD_TOKEN` | Mordy's Discord bot token |
| `ANTHROPIC_API_KEY` | shared project variable is fine |
| `HEADMASTER_ROLE_ID` | optional; YAML defaults to the Headmasters role |
| `STATE_DIR` | volume mount path, e.g. `/data` |
| `HAUNT_CHANNEL_IDS` | optional channel allowlist |
| `ALLOWED_GUILD_IDS` | optional guild allowlist |

### Service 2 — Finley

Same image as Mordy; only the service variables change:

| Variable | Example / notes |
| --- | --- |
| `GHOST_ID` | `finley` |
| `DISCORD_TOKEN` | Finley's Discord bot token (different app from Mordy) |
| `ANTHROPIC_API_KEY` | shared project variable is fine |
| `HEADMASTER_ROLE_ID` | optional; YAML defaults to the Headmasters role |
| `STATE_DIR` | **separate** volume from Mordy's, e.g. `/data` |
| `HAUNT_CHANNEL_IDS` | optional channel allowlist |
| `ALLOWED_GUILD_IDS` | optional guild allowlist |

`GHOST_ID=finley` loads `characters/finley.yaml` — **`/flirt`** and
**`/stopflirt`** for Headmasters only.

### Service 3 — Cassy

| Variable | Example / notes |
| --- | --- |
| `GHOST_ID` | `cassy` |
| `DISCORD_TOKEN` | Cassy's Discord bot token (her own Discord app) |
| `ANTHROPIC_API_KEY` | shared project variable is fine |
| `STATE_DIR` | **separate** volume (e.g. mount as `/data` or your existing `cassy-memory` path) |
| `HAUNT_CHANNEL_IDS` | optional channel allowlist |
| `ALLOWED_GUILD_IDS` | optional guild allowlist |

`GHOST_ID=cassy` registers **`/invention` only** (anyone can use it).

### Service 5 — Maynard

Same image again; Maynard does **not** use `OTHER_GHOST_*` (no `/interact`):

| Variable | Example / notes |
| --- | --- |
| `GHOST_ID` | `maynard` |
| `DISCORD_TOKEN` | Maynard's Discord bot token (his own Discord app) |
| `ANTHROPIC_API_KEY` | shared project variable is fine |
| `STATE_DIR` | **separate** volume from the other ghosts, e.g. `/data` |
| `HAUNT_CHANNEL_IDS` | optional channel allowlist |
| `ALLOWED_GUILD_IDS` | optional guild allowlist |
| `HEADMASTER_ROLE_ID` | optional; YAML defaults to the Velmora Headmasters role |

`GHOST_ID=maynard` loads `characters/maynard.yaml` with **no slash commands**.
He still reacts in chat (name / rare keywords) — short chaos and puns only.

### Service 4 — Sebastian

| Variable | Example / notes |
| --- | --- |
| `GHOST_ID` | `sebastian` |
| `DISCORD_TOKEN` | Sebastian's Discord bot token (his own Discord app) |
| `ANTHROPIC_API_KEY` | shared project variable is fine |
| `STATE_DIR` | **separate** volume from the other ghosts, e.g. `/data` |
| `HAUNT_CHANNEL_IDS` | optional channel allowlist |
| `ALLOWED_GUILD_IDS` | optional guild allowlist |
| `HOUSE_ROLE_ID` | optional; defaults to the Thornmere house role in YAML |

`GHOST_ID=sebastian` registers **`/pun` only**. Passive reactions may still
`SKIP` (send nothing).

### Service 6 — Vida

| Variable | Example / notes |
| --- | --- |
| `GHOST_ID` | `vida` |
| `DISCORD_TOKEN` | Vida's Discord bot token (her own Discord app) |
| `ANTHROPIC_API_KEY` | shared project variable is fine |
| `STATE_DIR` | **separate** volume from the other ghosts, e.g. `/data` |
| `HAUNT_CHANNEL_IDS` | optional channel allowlist |
| `ALLOWED_GUILD_IDS` | optional guild allowlist |

`GHOST_ID=vida` registers **`/tend` only**.

Typical Railway setup:

1. Create one Railway project for Velmora.
2. Connect this GitHub repo; Railway builds the Dockerfile once per deploy.
3. Duplicate the service five more times (or add five empty services pointing
   at the same repo / same image).
4. On each service, set `GHOST_ID` and that ghost's `DISCORD_TOKEN` (and
   `ANTHROPIC_API_KEY`, usually as a shared variable). Copy the tables above
   for all six ghosts (Mordy, Finley, Cassy, Sebastian, Maynard, Vida).
5. Attach a **separate** volume to each service and set `STATE_DIR` to the
   mount path.

Same commit → same image → six independent bots. Adding a seventh ghost is a
new YAML under `characters/` plus another Railway service with a new
`GHOST_ID`, not a new codebase.

## Local setup

1. Create a Discord application + bot at https://discord.com/developers/applications
   - Enable the **Message Content Intent** under Bot settings.
   - Invite it with the `bot` and `applications.commands` scopes, and at least:
     View Channels, Send Messages, Read Message History.
2. `cp .env.example .env` and fill in `DISCORD_TOKEN`, `ANTHROPIC_API_KEY`,
   and `GHOST_ID` (`mordy`, `finley`, `cassy`, `maynard`, `sebastian`, or `vida`).
3. `pip install -r requirements.txt`
4. `python bot.py` (or `python -m engine`)

Slash commands sync on startup (guild-instant if you set `DEV_GUILD_ID`,
otherwise global sync which can take up to an hour the first time).

## Commands

**Mordy** (`GHOST_ID=mordy`) — Headmasters only:

- `/haunt user:<@member>` — sets him loose on that member for a while.
- `/stophaunt user:<@member>` — calls him off.

**Finley** (`GHOST_ID=finley`) — Headmasters only:

- `/flirt user:<@member>` — he flirts with that member for a while.
- `/stopflirt user:<@member>` — tells him to stop.

**Cassy** (`GHOST_ID=cassy`) — anyone:

- `/invention` — one of her old patents or inventions.

**Sebastian** (`GHOST_ID=sebastian`) — anyone:

- `/pun [topic]` — one gentle groan-worthy pun.

**Vida** (`GHOST_ID=vida`) — anyone:

- `/tend member:<@member>` — she quietly looks in on them for a while.

**Maynard** (`GHOST_ID=maynard`):

- No slash commands. Chat-only: short chaos / puns; rare keyword hits.

## Notes

- Prompt caching: each Claude call marks the stable personality + lore block
  as reusable (`cache_control: ephemeral`), so repeat calls pay ~1/10 on that
  prefix. Mood and per-call memory stay uncached.
- Context caps (all ghosts): last **3** diary days (was 7), last **10**
  channel messages (was 20), **15** running notes kept (was 30).
- Chat cooldown (all ghosts): after **10** replies to the same person in
  **10 minutes**, the ghost sends `*{name} flickers and fades…*` and stays
  quiet toward them for **3 minutes**.
- All dialogue is generated at request time by Claude (model overridable via
  `VELMORA_MODEL`). Fallback lines from the character YAML are used only if
  the API key is missing or a call fails.
- Academy rumors (`/rumor` and scheduled posts) live in Housecup, not here.

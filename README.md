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
```

Characters ported so far:

- **Mordy** from [mordyvelmora-ghost](https://github.com/rherschel35/mordyvelmora-ghost) —
  `/seance`, `/haunt`, `/lore`, restless voice.
- **Finley** from [FinleyVeyren-ghost](https://github.com/rherschel35/FinleyVeyren-ghost) —
  `/seance`, `/watch` (protective), `/lore`, warmer voice.
- **Maynard** from [Maynard-moonveil](https://github.com/rherschel35/Maynard-moonveil) —
  `/ask`, `/watch` (mischief), `/experiment`, chaotic House Moonveil voice.
  No `/interact` — he does not talk to the other ghosts.
- **Sebastian** from [Sebastian-Thornmere](https://github.com/rherschel35/Sebastian-Thornmere) —
  `/ask`, `/pun`, `/mazejournal`, shy Thornmere voice. No `/interact` and
  no attention command (`/haunt`/`/watch`). Replies `SKIP` to stay quiet.
- **Vida** from [Vida-Vashara](https://github.com/rherschel35/Vida-Vashara) —
  `/ask`, `/tend` (gentle check-ins), `/remedy`, warm House Vashara voice.
  No `/interact` — she ignores other ghost bots. Keyword phrases include
  `vida`, `i don't feel well`, and `can't sleep` (all always-on).

With the matching `GHOST_ID`, each ghost's system prompt, moods, keywords,
lore fragments, history pairings, and slash-command names match its original
bot. Keyword triggers may carry optional `chance` weights (Maynard's
`what if` / `prank` fire ~25%; his name is always-on).

## Layout

```
bot.py                 # thin entrypoint
engine/                # shared Discord + Claude engine
  bot.py               # client setup, cog loading, presence
  config.py            # GHOST_ID → characters/<id>.yaml
  cogs/                # personality, haunting, commands, diary
characters/
  mordy.yaml           # Mordy: /seance /haunt /lore
  finley.yaml          # Finley: /seance /watch /lore
  maynard.yaml         # Maynard: /ask /watch /experiment
  sebastian.yaml       # Sebastian: /ask /pun /mazejournal
  vida.yaml            # Vida: /ask /tend /remedy
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
| Cassy | `cassy` | *(add `characters/cassy.yaml`)* |
| Sebastian | `sebastian` | `characters/sebastian.yaml` |
| Maynard | `maynard` | `characters/maynard.yaml` |
| Vida | `vida` | `characters/vida.yaml` |

Each service gets its own variables and (recommended) its own volume:

- **Shared by the image:** engine code, `data/lore/`, every `characters/*.yaml`.
- **Per service:** `GHOST_ID`, `DISCORD_TOKEN`, optional `OTHER_GHOST_*` peer
  bot IDs, `HAUNT_CHANNEL_IDS`, `ALLOWED_GUILD_IDS`, `STATE_DIR`.
- **Per service volume:** mount at `STATE_DIR` (e.g. `/data`) so
  `memory_store.json` survives redeploys. Do **not** mount over `data/lore/`
  or the shared biographies disappear at runtime.

### Service 1 — Mordy

| Variable | Example / notes |
| --- | --- |
| `GHOST_ID` | `mordy` |
| `DISCORD_TOKEN` | Mordy's Discord bot token |
| `ANTHROPIC_API_KEY` | shared project variable is fine |
| `OTHER_GHOST_1_ID` | Finley's Discord **user** id (bot account) |
| `OTHER_GHOST_1_NAME` | `Finley Veyren` (optional; YAML default) |
| `OTHER_GHOST_2_ID` | Cassy's Discord user id |
| `OTHER_GHOST_2_NAME` | `Cassy Caldrin` (optional) |
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
| `OTHER_GHOST_1_ID` | Mordy's Discord **user** id |
| `OTHER_GHOST_1_NAME` | `Mordy Velmora` (optional; YAML default) |
| `OTHER_GHOST_2_ID` | Cassy's Discord user id |
| `OTHER_GHOST_2_NAME` | `Cassy Caldrin` (optional) |
| `STATE_DIR` | **separate** volume from Mordy's, e.g. `/data` |
| `HAUNT_CHANNEL_IDS` | optional channel allowlist |
| `ALLOWED_GUILD_IDS` | optional guild allowlist |

`GHOST_ID=finley` loads `characters/finley.yaml`, so this service exposes
`/watch` (not `/haunt`), Finley's moods/keywords, and House Veyren lore.

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

`GHOST_ID=maynard` loads `characters/maynard.yaml` and registers
`/ask`, `/watch`, `/experiment`, `/mood` (no `/interact`). His `/watch` is
mischief-interest, not Finley's protective watch. Keyword cues: `maynard`
(always), `what if` / `prank` (~25% chance each).

### Service 4 — Sebastian

Same image; Sebastian has **no** attention command and **no** `/interact`:

| Variable | Example / notes |
| --- | --- |
| `GHOST_ID` | `sebastian` |
| `DISCORD_TOKEN` | Sebastian's Discord bot token (his own Discord app) |
| `ANTHROPIC_API_KEY` | shared project variable is fine |
| `STATE_DIR` | **separate** volume from the other ghosts, e.g. `/data` |
| `HAUNT_CHANNEL_IDS` | optional channel allowlist |
| `ALLOWED_GUILD_IDS` | optional guild allowlist |
| `HOUSE_ROLE_ID` | optional; defaults to the Thornmere house role in YAML |

`GHOST_ID=sebastian` loads `characters/sebastian.yaml` and registers
**`/ask`**, **`/pun`**, **`/mazejournal`**, **`/mood` only**. He answers to
`sebastian` (shy, or passionate if the talk is about the tournament), rarely
chimes in with a pun (`SKIP` = stay quiet), and is warmer with Thornmere
housemates. He ignores every other bot.

### Service 6 — Vida

Same image; Vida has **no** `/interact` and **no** `OTHER_GHOST_*` vars:

| Variable | Example / notes |
| --- | --- |
| `GHOST_ID` | `vida` |
| `DISCORD_TOKEN` | Vida's Discord bot token (her own Discord app) |
| `ANTHROPIC_API_KEY` | shared project variable is fine |
| `STATE_DIR` | **separate** volume from the other ghosts, e.g. `/data` |
| `HAUNT_CHANNEL_IDS` | optional channel allowlist |
| `ALLOWED_GUILD_IDS` | optional guild allowlist |

`GHOST_ID=vida` loads `characters/vida.yaml` and registers **`/ask`**,
**`/tend`**, **`/remedy`**, **`/mood`**. She reacts to her name and the
phrases `i don't feel well` and `can't sleep`, gives gentler passive
check-ins after `/tend`, and ignores every other bot.

Typical Railway setup:

1. Create one Railway project for Velmora.
2. Connect this GitHub repo; Railway builds the Dockerfile once per deploy.
3. Duplicate the service five more times (or add five empty services pointing
   at the same repo / same image).
4. On each service, set `GHOST_ID` and that ghost's `DISCORD_TOKEN` (and
   `ANTHROPIC_API_KEY`, usually as a shared variable). Copy the tables above
   for Mordy, Finley, Maynard, Sebastian, and Vida; add Cassy when her YAML lands.
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
   and `GHOST_ID` (`mordy`, `finley`, `maynard`, `sebastian`, or `vida`).
3. `pip install -r requirements.txt`
4. `python bot.py` (or `python -m engine`)

Slash commands sync on startup (guild-instant if you set `DEV_GUILD_ID`,
otherwise global sync which can take up to an hour the first time).

## Commands

**Mordy** (`GHOST_ID=mordy`):

- `/seance question:<text>` — ask the ghost; cryptic in-character answer.
- `/haunt user:<@member>` — fixates on that member for a while.
- `/lore` — next unrevealed fragment of Velmora's backstory.
- `/mood` — (admin) peek at the current mood.
- `/interact who:<ghost>` — brief public exchange with Finley or Cassy.

**Finley** (`GHOST_ID=finley`):

- `/seance question:<text>` — ask the Veyren ghost; warm, direct answer.
- `/watch user:<@member>` — quietly watches over that member for a while.
- `/lore` — next piece of House Veyren's history.
- `/mood` — (admin) peek at the current mood.
- `/interact who:<ghost>` — brief public exchange with Mordy or Cassy.

**Maynard** (`GHOST_ID=maynard`):

- `/ask question:<text>` — ask Maynard; gleeful, curious answer.
- `/watch member:<@member>` — picks them as his next harmless-mischief target.
- `/experiment` — an entry from his old journals of (alleged) experiments.
- `/mood` — (admin) peek at the current mood.
- No `/interact` — he keeps to the students and ignores other ghost bots.

**Sebastian** (`GHOST_ID=sebastian`):

- `/ask question:<text>` — ask Sebastian; shy, gentle answer (tournament talk unlocks passion).
- `/pun [topic]` — coax one gentle groan-worthy pun out of him.
- `/mazejournal` — a page from his old maze journals plus his shy reaction.
- `/mood` — (admin) peek at the current mood.
- No `/haunt` or `/watch`, and no `/interact` — he ignores other bots entirely.
  Unasked reactions may reply `SKIP` (send nothing).

**Vida** (`GHOST_ID=vida`):

- `/ask question:<text>` — ask Vida; warm, affectionate answer.
- `/tend member:<@member>` — she quietly looks in on them for a while.
- `/remedy` — an old memory or remedy from her healing days.
- `/mood` — (admin) peek at the current mood.
- No `/interact` — she keeps to the students and ignores other ghost bots.
  Passive reactions follow `/tend`; keywords include `vida`, `i don't feel well`,
  and `can't sleep`.

## Notes

- All dialogue is generated at request time by Claude (model overridable via
  `VELMORA_MODEL`). Fallback lines from the character YAML are used only if
  the API key is missing or a call fails.
- Academy rumors (`/rumor` and scheduled posts) live in Housecup, not here.

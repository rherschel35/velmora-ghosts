"""Entrypoint for a Velmora ghost.

Set GHOST_ID to select characters/<id>.yaml. For Mordy:

    GHOST_ID=mordy python bot.py
"""

from engine.bot import main

if __name__ == "__main__":
    main()

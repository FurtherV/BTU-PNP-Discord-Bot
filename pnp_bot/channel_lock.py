from __future__ import annotations

from collections.abc import Mapping

import discord


WRITE_PERMISSIONS = ("send_messages", "send_messages_in_threads")


def _copy_overwrite(overwrite: discord.PermissionOverwrite) -> discord.PermissionOverwrite:
    allow, deny = overwrite.pair()
    return discord.PermissionOverwrite.from_pair(allow, deny)


def set_everyone_write_overwrite(
    current: Mapping[discord.Role | discord.Member, discord.PermissionOverwrite],
    everyone: discord.Role,
    value: bool | None,
) -> tuple[dict[discord.Role | discord.Member, discord.PermissionOverwrite], bool]:
    """Change only @everyone's write permissions and preserve all other overwrites."""
    updated = {target: _copy_overwrite(overwrite) for target, overwrite in current.items()}
    overwrite = updated.get(everyone)
    if overwrite is None:
        if value is None:
            return updated, False
        overwrite = discord.PermissionOverwrite()

    changed = False
    for permission in WRITE_PERMISSIONS:
        if getattr(overwrite, permission) != value:
            setattr(overwrite, permission, value)
            changed = True

    if overwrite.is_empty():
        updated.pop(everyone, None)
    else:
        updated[everyone] = overwrite
    return updated, changed

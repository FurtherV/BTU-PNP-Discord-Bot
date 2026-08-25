from dataclasses import dataclass, field
from types import SimpleNamespace

import discord

from pnp_bot.channel_lock import set_everyone_write_overwrite


@dataclass(frozen=True)
class Target:
    id: int
    name: str
    permissions: object = field(
        default_factory=lambda: SimpleNamespace(administrator=False), compare=False, hash=False
    )


def test_lock_changes_only_everyones_write_permissions() -> None:
    everyone = Target(1, "everyone")
    member = Target(2, "member")
    original = discord.PermissionOverwrite(view_channel=False, add_reactions=True)
    member_overwrite = discord.PermissionOverwrite(view_channel=True, send_messages=True)

    updated, changed = set_everyone_write_overwrite(
        {everyone: original, member: member_overwrite}, everyone, False
    )

    assert changed
    assert updated[everyone].view_channel is False
    assert updated[everyone].add_reactions is True
    assert updated[everyone].send_messages is False
    assert updated[everyone].send_messages_in_threads is False
    assert updated[member].send_messages is True
    assert updated[member].view_channel is True
    assert original.send_messages is None


def test_unlock_preserves_everyones_other_permissions() -> None:
    everyone = Target(1, "everyone")
    current = {
        everyone: discord.PermissionOverwrite(
            view_channel=False,
            send_messages=False,
            send_messages_in_threads=False,
        ),
    }

    updated, changed = set_everyone_write_overwrite(current, everyone, None)

    assert changed
    assert updated[everyone].view_channel is False
    assert updated[everyone].send_messages is None
    assert updated[everyone].send_messages_in_threads is None


def test_unlock_removes_an_empty_everyone_overwrite() -> None:
    everyone = Target(1, "everyone")
    current = {
        everyone: discord.PermissionOverwrite(
            send_messages=False,
            send_messages_in_threads=False,
        ),
    }

    updated, changed = set_everyone_write_overwrite(current, everyone, None)

    assert changed
    assert everyone not in updated


def test_repeating_same_lock_is_idempotent() -> None:
    everyone = Target(1, "everyone")
    current, _changed = set_everyone_write_overwrite({}, everyone, False)

    repeated, changed = set_everyone_write_overwrite(current, everyone, False)

    assert not changed
    assert repeated.keys() == current.keys()

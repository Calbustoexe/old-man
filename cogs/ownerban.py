import time

import discord
from discord.ext import commands

import utils

OWNER_ID = utils.OWNER_ID

_ownerban = utils.load_ownerban()


def _save():
    utils.save_ownerban(_ownerban)


def _clean_expired():
    now = time.time()
    changed = False
    for guild_id in list(_ownerban):
        for user_id in list(_ownerban[guild_id]):
            entry = _ownerban[guild_id][user_id]
            if entry["until"] and now >= entry["until"]:
                del _ownerban[guild_id][user_id]
                changed = True
        if not _ownerban[guild_id]:
            del _ownerban[guild_id]
            changed = True
    if changed:
        _save()


def _is_ownerbanned(guild_id: int, user_id: int) -> bool:
    gid = str(guild_id)
    uid = str(user_id)
    entry = _ownerban.get(gid, {}).get(uid)
    if entry is None:
        return False
    if entry["until"] and time.time() >= entry["until"]:
        del _ownerban[gid][uid]
        if not _ownerban[gid]:
            del _ownerban[gid]
        _save()
        return False
    return True


class OwnerBanCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        if not isinstance(message.author, discord.Member):
            return
        if not _is_ownerbanned(message.guild.id, message.author.id):
            return
        try:
            await message.delete()
        except (discord.Forbidden, discord.HTTPException):
            pass

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        if message.author.bot:
            return
        if not isinstance(message.author, discord.Member):
            return
        if not _is_ownerbanned(message.guild.id, message.author.id):
            return
        async for entry in message.guild.audit_logs(limit=5, action=discord.AuditLogAction.message_delete):
            if entry.target.id == message.author.id and entry.user.id == message.author.id:
                try:
                    await message.channel.send(
                        content=message.content,
                        embeds=message.embeds,
                        files=[await a.to_file() for a in message.attachments] if message.attachments else None,
                    )
                except (discord.Forbidden, discord.HTTPException):
                    pass
                return

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        if member.bot:
            return
        if not _is_ownerbanned(member.guild.id, member.id):
            return
        if after.channel is not None:
            try:
                await member.move_to(None)
            except (discord.Forbidden, discord.HTTPException):
                pass

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if after.bot:
            return
        if not _is_ownerbanned(after.guild.id, after.id):
            return
        if after.nick != before.nick:
            async for entry in after.guild.audit_logs(limit=3, action=discord.AuditLogAction.member_update):
                if entry.target.id == after.id and entry.user.id == self.bot.user.id:
                    return
            try:
                await after.edit(nick=before.nick)
            except (discord.Forbidden, discord.HTTPException):
                pass
        if after.is_timed_out() and not before.is_timed_out():
            async for entry in after.guild.audit_logs(limit=3, action=discord.AuditLogAction.member_update):
                if entry.target.id == after.id and entry.user.id == after.id:
                    try:
                        await after.edit(timeout=None, reason="Ownerbanned user action blocked")
                    except (discord.Forbidden, discord.HTTPException):
                        pass
                    return

    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role):
        async for entry in role.guild.audit_logs(limit=5, action=discord.AuditLogAction.role_create):
            if entry.user and not entry.user.bot and _is_ownerbanned(role.guild.id, entry.user.id):
                try:
                    await role.delete(reason="Ownerbanned user action blocked")
                except (discord.Forbidden, discord.HTTPException):
                    pass
                return

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role):
        async for entry in role.guild.audit_logs(limit=5, action=discord.AuditLogAction.role_delete):
            if entry.user and not entry.user.bot and _is_ownerbanned(role.guild.id, entry.user.id):
                try:
                    await role.guild.create_role(
                        name=role.name,
                        permissions=role.permissions,
                        colour=role.colour,
                        hoist=role.hoist,
                        mentionable=role.mentionable,
                        reason="Ownerbanned user action blocked"
                    )
                except (discord.Forbidden, discord.HTTPException):
                    pass
                return

    @commands.Cog.listener()
    async def on_guild_role_update(self, before: discord.Role, after: discord.Role):
        async for entry in after.guild.audit_logs(limit=5, action=discord.AuditLogAction.role_update):
            if entry.user and not entry.user.bot and _is_ownerbanned(after.guild.id, entry.user.id):
                try:
                    await after.edit(
                        name=before.name,
                        permissions=before.permissions,
                        colour=before.colour,
                        hoist=before.hoist,
                        mentionable=before.mentionable,
                        reason="Ownerbanned user action blocked"
                    )
                except (discord.Forbidden, discord.HTTPException):
                    pass
                return

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel):
        async for entry in channel.guild.audit_logs(limit=5, action=discord.AuditLogAction.channel_create):
            if entry.user and not entry.user.bot and _is_ownerbanned(channel.guild.id, entry.user.id):
                try:
                    await channel.delete(reason="Ownerbanned user action blocked")
                except (discord.Forbidden, discord.HTTPException):
                    pass
                return

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        async for entry in channel.guild.audit_logs(limit=5, action=discord.AuditLogAction.channel_delete):
            if entry.user and not entry.user.bot and _is_ownerbanned(channel.guild.id, entry.user.id):
                try:
                    if isinstance(channel, discord.VoiceChannel):
                        await channel.guild.create_voice_channel(
                            name=channel.name,
                            bitrate=channel.bitrate,
                            user_limit=channel.user_limit,
                            rtc_region=channel.rtc_region,
                            video_quality_mode=channel.video_quality_mode,
                            overwrites=channel.overwrites,
                            reason="Ownerbanned user action blocked"
                        )
                    elif isinstance(channel, discord.TextChannel):
                        await channel.guild.create_text_channel(
                            name=channel.name,
                            topic=channel.topic,
                            nsfw=channel.nsfw,
                            slowmode_delay=channel.slowmode_delay,
                            default_auto_archive_duration=channel.default_auto_archive_duration,
                            overwrites=channel.overwrites,
                            reason="Ownerbanned user action blocked"
                        )
                except (discord.Forbidden, discord.HTTPException):
                    pass
                return

    @commands.Cog.listener()
    async def on_guild_channel_update(self, before: discord.abc.GuildChannel, after: discord.abc.GuildChannel):
        async for entry in after.guild.audit_logs(limit=5, action=discord.AuditLogAction.channel_update):
            if entry.user and not entry.user.bot and _is_ownerbanned(after.guild.id, entry.user.id):
                try:
                    if isinstance(before, discord.VoiceChannel) and isinstance(after, discord.VoiceChannel):
                        await after.edit(
                            name=before.name,
                            bitrate=before.bitrate,
                            user_limit=before.user_limit,
                            rtc_region=before.rtc_region,
                            video_quality_mode=before.video_quality_mode,
                            overwrites=before.overwrites,
                            reason="Ownerbanned user action blocked"
                        )
                    elif isinstance(before, discord.TextChannel) and isinstance(after, discord.TextChannel):
                        await after.edit(
                            name=before.name,
                            topic=before.topic,
                            nsfw=before.nsfw,
                            slowmode_delay=before.slowmode_delay,
                            default_auto_archive_duration=before.default_auto_archive_duration,
                            overwrites=before.overwrites,
                            reason="Ownerbanned user action blocked"
                        )
                    else:
                        await after.edit(name=before.name, reason="Ownerbanned user action blocked")
                except (discord.Forbidden, discord.HTTPException):
                    pass
                return

    @commands.Cog.listener()
    async def on_guild_update(self, before: discord.Guild, after: discord.Guild):
        async for entry in after.audit_logs(limit=5, action=discord.AuditLogAction.guild_update):
            if entry.user and not entry.user.bot and _is_ownerbanned(after.id, entry.user.id):
                try:
                    await after.edit(name=before.name, reason="Ownerbanned user action blocked")
                except (discord.Forbidden, discord.HTTPException):
                    pass
                return

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User):
        async for entry in guild.audit_logs(limit=5, action=discord.AuditLogAction.ban):
            if entry.user and not entry.user.bot and _is_ownerbanned(guild.id, entry.user.id):
                try:
                    await guild.unban(user, reason="Ownerbanned user action blocked")
                except (discord.Forbidden, discord.HTTPException):
                    pass
                return

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.User):
        pass

    @commands.Cog.listener()
    async def on_member_kick(self, member: discord.Member):
        async for entry in member.guild.audit_logs(limit=5, action=discord.AuditLogAction.kick):
            if entry.user and not entry.user.bot and _is_ownerbanned(member.guild.id, entry.user.id):
                try:
                    await member.guild.add_member(member, reason="Ownerbanned user action blocked")
                except (discord.Forbidden, discord.HTTPException):
                    pass
                return

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        pass

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if _is_ownerbanned(member.guild.id, member.id):
            for channel in member.guild.text_channels:
                try:
                    async for msg in channel.history(limit=100):
                        if msg.author.id == member.id:
                            try:
                                await msg.delete()
                            except:
                                pass
                except:
                    pass

    @commands.Cog.listener()
    async def on_invite_create(self, invite: discord.Invite):
        if not invite.guild:
            return
        async for entry in invite.guild.audit_logs(limit=5, action=discord.AuditLogAction.invite_create):
            if entry.user and not entry.user.bot and _is_ownerbanned(invite.guild.id, entry.user.id):
                try:
                    await invite.delete(reason="Ownerbanned user action blocked")
                except (discord.Forbidden, discord.HTTPException):
                    pass
                return

    @commands.Cog.listener()
    async def on_invite_delete(self, invite: discord.Invite):
        if not invite.guild:
            return
        async for entry in invite.guild.audit_logs(limit=5, action=discord.AuditLogAction.invite_delete):
            if entry.user and not entry.user.bot and _is_ownerbanned(invite.guild.id, entry.user.id):
                try:
                    await invite.guild.create_invite(
                        max_age=invite.max_age,
                        max_uses=invite.max_uses,
                        temporary=invite.temporary,
                        unique=True,
                        reason="Ownerbanned user action blocked"
                    )
                except (discord.Forbidden, discord.HTTPException):
                    pass
                return

    @commands.Cog.listener()
    async def on_webhooks_update(self, channel: discord.abc.GuildChannel):
        async for entry in channel.guild.audit_logs(limit=5, action=discord.AuditLogAction.webhook_create):
            if entry.target and entry.target.channel_id == channel.id and entry.user and not entry.user.bot and _is_ownerbanned(channel.guild.id, entry.user.id):
                try:
                    await entry.target.delete(reason="Ownerbanned user action blocked")
                except (discord.Forbidden, discord.HTTPException):
                    pass
                return
        async for entry in channel.guild.audit_logs(limit=5, action=discord.AuditLogAction.webhook_update):
            if entry.target and entry.target.channel_id == channel.id and entry.user and not entry.user.bot and _is_ownerbanned(channel.guild.id, entry.user.id):
                try:
                    await entry.target.delete(reason="Ownerbanned user action blocked")
                except (discord.Forbidden, discord.HTTPException):
                    pass
                return
        async for entry in channel.guild.audit_logs(limit=5, action=discord.AuditLogAction.webhook_delete):
            if entry.user and not entry.user.bot and _is_ownerbanned(channel.guild.id, entry.user.id):
                pass

    @commands.Cog.listener()
    async def on_integration_create(self, integration: discord.Integration):
        async for entry in integration.guild.audit_logs(limit=5, action=discord.AuditLogAction.integration_create):
            if entry.user and not entry.user.bot and _is_ownerbanned(integration.guild.id, entry.user.id):
                try:
                    await integration.delete(reason="Ownerbanned user action blocked")
                except (discord.Forbidden, discord.HTTPException):
                    pass
                return

    @commands.Cog.listener()
    async def on_integration_update(self, integration: discord.Integration):
        async for entry in integration.guild.audit_logs(limit=5, action=discord.AuditLogAction.integration_update):
            if entry.user and not entry.user.bot and _is_ownerbanned(integration.guild.id, entry.user.id):
                try:
                    await integration.delete(reason="Ownerbanned user action blocked")
                except (discord.Forbidden, discord.HTTPException):
                    pass
                return

    @commands.Cog.listener()
    async def on_guild_emoji_create(self, emoji: discord.Emoji):
        async for entry in emoji.guild.audit_logs(limit=5, action=discord.AuditLogAction.emoji_create):
            if entry.user and not entry.user.bot and _is_ownerbanned(emoji.guild.id, entry.user.id):
                try:
                    await emoji.delete(reason="Ownerbanned user action blocked")
                except (discord.Forbidden, discord.HTTPException):
                    pass
                return

    @commands.Cog.listener()
    async def on_guild_emoji_delete(self, emoji: discord.Emoji):
        pass

    @commands.Cog.listener()
    async def on_guild_emoji_update(self, before: discord.Emoji, after: discord.Emoji):
        async for entry in after.guild.audit_logs(limit=5, action=discord.AuditLogAction.emoji_update):
            if entry.user and not entry.user.bot and _is_ownerbanned(after.guild.id, entry.user.id):
                try:
                    await after.edit(
                        name=before.name,
                        reason="Ownerbanned user action blocked"
                    )
                except (discord.Forbidden, discord.HTTPException):
                    pass
                return

    @commands.Cog.listener()
    async def on_guild_sticker_create(self, sticker: discord.Sticker):
        async for entry in sticker.guild.audit_logs(limit=5, action=discord.AuditLogAction.sticker_create):
            if entry.user and not entry.user.bot and _is_ownerbanned(sticker.guild.id, entry.user.id):
                try:
                    await sticker.delete(reason="Ownerbanned user action blocked")
                except (discord.Forbidden, discord.HTTPException):
                    pass
                return

    @commands.Cog.listener()
    async def on_guild_sticker_delete(self, sticker: discord.Sticker):
        pass

    @commands.Cog.listener()
    async def on_guild_sticker_update(self, before: discord.Sticker, after: discord.Sticker):
        async for entry in after.guild.audit_logs(limit=5, action=discord.AuditLogAction.sticker_update):
            if entry.user and not entry.user.bot and _is_ownerbanned(after.guild.id, entry.user.id):
                try:
                    await after.edit(
                        name=before.name,
                        description=before.description,
                        tags=before.tags,
                        reason="Ownerbanned user action blocked"
                    )
                except (discord.Forbidden, discord.HTTPException):
                    pass
                return

    @commands.Cog.listener()
    async def on_member_role_add(self, member: discord.Member, role: discord.Role):
        if not _is_ownerbanned(member.guild.id, member.id):
            return
        async for entry in member.guild.audit_logs(limit=5, action=discord.AuditLogAction.role_update):
            if entry.target.id == role.id and entry.user.id == member.id:
                try:
                    await member.remove_roles(role, reason="Ownerbanned user action blocked")
                except (discord.Forbidden, discord.HTTPException):
                    pass
                return

    @commands.Cog.listener()
    async def on_member_role_remove(self, member: discord.Member, role: discord.Role):
        if not _is_ownerbanned(member.guild.id, member.id):
            return
        async for entry in member.guild.audit_logs(limit=5, action=discord.AuditLogAction.role_update):
            if entry.target.id == role.id and entry.user.id == member.id:
                try:
                    await member.add_roles(role, reason="Ownerbanned user action blocked")
                except (discord.Forbidden, discord.HTTPException):
                    pass
                return

    @commands.Cog.listener()
    async def on_bulk_message_delete(self, messages: list[discord.Message]):
        for message in messages:
            if message.author.bot:
                continue
            if not isinstance(message.author, discord.Member):
                continue
            if not _is_ownerbanned(message.guild.id, message.author.id):
                continue
            try:
                await message.channel.send(
                    content=message.content,
                    embeds=message.embeds,
                    files=[await a.to_file() for a in message.attachments] if message.attachments else None,
                )
            except (discord.Forbidden, discord.HTTPException):
                pass

    @commands.Cog.listener()
    async def on_stage_instance_create(self, stage_instance):
        async for entry in stage_instance.guild.audit_logs(limit=5, action=discord.AuditLogAction.stage_instance_create):
            if entry.user and not entry.user.bot and _is_ownerbanned(stage_instance.guild.id, entry.user.id):
                try:
                    await stage_instance.delete(reason="Ownerbanned user action blocked")
                except (discord.Forbidden, discord.HTTPException):
                    pass
                return

    @commands.Cog.listener()
    async def on_stage_instance_delete(self, stage_instance):
        pass

    @commands.Cog.listener()
    async def on_stage_instance_update(self, before, after):
        async for entry in after.guild.audit_logs(limit=5, action=discord.AuditLogAction.stage_instance_update):
            if entry.user and not entry.user.bot and _is_ownerbanned(after.guild.id, entry.user.id):
                try:
                    await after.edit(topic=before.topic, reason="Ownerbanned user action blocked")
                except (discord.Forbidden, discord.HTTPException):
                    pass
                return

    @commands.Cog.listener()
    async def on_scheduled_event_create(self, event):
        async for entry in event.guild.audit_logs(limit=5, action=discord.AuditLogAction.scheduled_event_create):
            if entry.user and not entry.user.bot and _is_ownerbanned(event.guild.id, entry.user.id):
                try:
                    await event.delete(reason="Ownerbanned user action blocked")
                except (discord.Forbidden, discord.HTTPException):
                    pass
                return

    @commands.Cog.listener()
    async def on_scheduled_event_delete(self, event):
        pass

    @commands.Cog.listener()
    async def on_scheduled_event_update(self, before, after):
        async for entry in after.guild.audit_logs(limit=5, action=discord.AuditLogAction.scheduled_event_update):
            if entry.user and not entry.user.bot and _is_ownerbanned(after.guild.id, entry.user.id):
                try:
                    await after.edit(
                        name=before.name,
                        description=before.description,
                        start_time=before.start_time,
                        end_time=before.end_time,
                        entity_type=before.entity_type,
                        status=before.status,
                        location=before.location,
                        reason="Ownerbanned user action blocked"
                    )
                except (discord.Forbidden, discord.HTTPException):
                    pass
                return

    @commands.Cog.listener()
    async def on_thread_create(self, thread: discord.Thread):
        async for entry in thread.guild.audit_logs(limit=5, action=discord.AuditLogAction.thread_create):
            if entry.user and not entry.user.bot and _is_ownerbanned(thread.guild.id, entry.user.id):
                try:
                    await thread.delete(reason="Ownerbanned user action blocked")
                except (discord.Forbidden, discord.HTTPException):
                    pass
                return

    @commands.Cog.listener()
    async def on_thread_delete(self, thread: discord.Thread):
        pass

    @commands.Cog.listener()
    async def on_thread_update(self, before: discord.Thread, after: discord.Thread):
        async for entry in after.guild.audit_logs(limit=5, action=discord.AuditLogAction.thread_update):
            if entry.user and not entry.user.bot and _is_ownerbanned(after.guild.id, entry.user.id):
                try:
                    await after.edit(name=before.name, archived=before.archived, locked=before.locked, reason="Ownerbanned user action blocked")
                except (discord.Forbidden, discord.HTTPException):
                    pass
                return

    @commands.Cog.listener()
    async def on_thread_member_join(self, member: discord.ThreadMember):
        pass

    @commands.Cog.listener()
    async def on_thread_member_remove(self, member: discord.ThreadMember):
        pass

    @commands.Cog.listener()
    async def on_typing(self, channel: discord.abc.Messageable, user: discord.User, when):
        pass

    @commands.Cog.listener()
    async def on_reaction_add(self, reaction: discord.Reaction, user: discord.User):
        if user.bot:
            return
        if not isinstance(user, discord.Member):
            return
        if not _is_ownerbanned(user.guild.id, user.id):
            return
        try:
            await reaction.remove(user)
        except (discord.Forbidden, discord.HTTPException):
            pass

    @commands.Cog.listener()
    async def on_reaction_remove(self, reaction: discord.Reaction, user: discord.User):
        pass

    @commands.Cog.listener()
    async def on_reaction_clear(self, message: discord.Message, reactions: list):
        pass

    @commands.Cog.listener()
    async def on_reaction_clear_emoji(self, reaction: discord.Reaction):
        pass

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if payload.user_id == self.bot.user.id:
            return
        if not payload.guild_id:
            return
        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return
        if _is_ownerbanned(payload.guild_id, payload.user_id):
            channel = guild.get_channel(payload.channel_id)
            if channel:
                try:
                    message = await channel.fetch_message(payload.message_id)
                    await message.remove_reaction(payload.emoji, payload.member or guild.get_member(payload.user_id))
                except (discord.Forbidden, discord.HTTPException):
                    pass

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        pass

    @commands.Cog.listener()
    async def on_application_command_permissions_update(self, command, permissions):
        pass

    @commands.Cog.listener()
    async def on_automod_rule_create(self, rule):
        async for entry in rule.guild.audit_logs(limit=5, action=discord.AuditLogAction.automod_rule_create):
            if entry.user and not entry.user.bot and _is_ownerbanned(rule.guild.id, entry.user.id):
                try:
                    await rule.delete(reason="Ownerbanned user action blocked")
                except (discord.Forbidden, discord.HTTPException):
                    pass
                return

    @commands.Cog.listener()
    async def on_automod_rule_update(self, before, after):
        async for entry in after.guild.audit_logs(limit=5, action=discord.AuditLogAction.automod_rule_update):
            if entry.user and not entry.user.bot and _is_ownerbanned(after.guild.id, entry.user.id):
                try:
                    await after.edit(name=before.name, enabled=before.enabled, event_type=before.event_type, actions=before.actions, exempt_roles=before.exempt_roles, exempt_channels=before.exempt_channels, trigger=before.trigger, reason="Ownerbanned user action blocked")
                except (discord.Forbidden, discord.HTTPException):
                    pass
                return

    @commands.Cog.listener()
    async def on_automod_rule_delete(self, rule):
        pass

    @commands.Cog.listener()
    async def on_automod_action(self, execution):
        pass

    @commands.command(name="ownerban")
    @commands.guild_only()
    async def ownerban(self, ctx: commands.Context, *, args: str = ""):
        if ctx.author.id != OWNER_ID:
            return

        await ctx.message.delete()
        _clean_expired()

        if not args:
            return await ctx.send(embed=utils.err(
                f"Usage : `{ctx.prefix}ownerban @membre [temps] \"raison\"`",
                "❌ Arguments manquants"
            ), delete_after=10)

        parts = args.split(None, 1)
        member = await utils.find_member(ctx, parts[0])
        if not member:
            return await ctx.send(embed=utils.err(
                f"Membre `{parts[0]}` introuvable.",
                "❌ Introuvable"
            ), delete_after=10)

        if member.id == OWNER_ID:
            return await ctx.send(embed=utils.err(
                "Tu ne peux pas te ownerban toi-même.",
                "❌ Cible invalide"
            ), delete_after=10)

        if member.bot:
            return await ctx.send(embed=utils.err(
                "Tu ne peux pas ownerban un bot.",
                "❌ Cible invalide"
            ), delete_after=10)

        remaining = parts[1] if len(parts) > 1 else ""
        duration = None
        reason = remaining

        if remaining:
            words = remaining.split()
            if words:
                dur = utils.parse_duration(words[0])
                if dur is not None:
                    duration = dur
                    reason = " ".join(words[1:]) if len(words) > 1 else ""

        if not reason:
            return await ctx.send(embed=utils.err(
                "Tu dois spécifier une raison entre guillemets.",
                "❌ Raison manquante"
            ), delete_after=10)

        guild_id = str(ctx.guild.id)
        user_id = str(member.id)

        _ownerban.setdefault(guild_id, {})[user_id] = {
            "reason": reason,
            "until": time.time() + duration if duration else None,
            "banned_by": ctx.author.id,
        }
        _save()

        desc = f"**{member.mention}** est ownerbannis"
        desc += f"\n📝 Raison : {reason}"
        if duration:
            desc += f"\n⏱️ Durée : {utils.fmt_duration(duration)}"
        else:
            desc += "\n⏱️ Durée : permanente"
        desc += "\n🔒 Conséquence pour pas avoir écouter busto"

        await ctx.send(embed=discord.Embed(
            title="🔨 Ownerban",
            description=desc,
            color=discord.Color.dark_red(),
        ).set_footer(text=f"Par {ctx.author.display_name}"), delete_after=15)

        if member.voice and member.voice.channel:
            try:
                await member.move_to(None)
            except (discord.Forbidden, discord.HTTPException):
                pass

        try:
            await member.edit(nick=f"[BANNED] {member.display_name[:27]}")
        except (discord.Forbidden, discord.HTTPException):
            pass

    @commands.command(name="ownerunban")
    @commands.guild_only()
    async def ownerunban(self, ctx: commands.Context, *, query: str = ""):
        if ctx.author.id != OWNER_ID:
            return

        await ctx.message.delete()

        if not query:
            return await ctx.send(embed=utils.err(
                f"Usage : `{ctx.prefix}ownerunban @membre`",
                "❌ Arguments manquants"
            ), delete_after=10)

        member = await utils.find_member(ctx, query)
        if not member:
            return await ctx.send(embed=utils.err(
                f"Membre `{query}` introuvable.",
                "❌ Introuvable"
            ), delete_after=10)

        guild_id = str(ctx.guild.id)
        user_id = str(member.id)

        if guild_id not in _ownerban or user_id not in _ownerban[guild_id]:
            return await ctx.send(embed=utils.info(
                f"{member.mention} n'est pas ownerbannis.",
                "ℹ️ Aucun ownerban"
            ), delete_after=10)

        entry = _ownerban[guild_id][user_id]
        del _ownerban[guild_id][user_id]
        if not _ownerban[guild_id]:
            del _ownerban[guild_id]
        _save()

        try:
            if member.nick and member.nick.startswith("[BANNED] "):
                await member.edit(nick=None)
        except (discord.Forbidden, discord.HTTPException):
            pass

        await ctx.send(embed=discord.Embed(
            title="🔓 Ownerunban",
            description=f"**{member.mention}** n'est plus ownerbannis.\n📝 Ancienne raison : {entry.get('reason', 'Aucune')}",
            color=discord.Color.green(),
        ).set_footer(text=f"Par {ctx.author.display_name}"), delete_after=15)

    @commands.command(name="ownerbanlist")
    @commands.guild_only()
    async def ownerbanlist(self, ctx: commands.Context):
        if ctx.author.id != OWNER_ID:
            return

        await ctx.message.delete()
        _clean_expired()

        guild_id = str(ctx.guild.id)
        bans = _ownerban.get(guild_id, {})

        if not bans:
            return await ctx.send(embed=utils.info(
                "Aucun membre n'est ownerbannis sur ce serveur.",
                "ℹ️ Liste vide"
            ), delete_after=10)

        desc = ""
        for uid, entry in bans.items():
            member = ctx.guild.get_member(int(uid))
            name = member.mention if member else f"ID:{uid}"
            reason = entry.get("reason", "Aucune raison")
            until = entry.get("until")
            if until:
                remaining = int(until - time.time())
                if remaining > 0:
                    dur = utils.fmt_duration(remaining)
                    desc += f"🔨 {name} — {reason} (⏱️ {dur})\n"
                else:
                    desc += f"🔨 {name} — {reason} (⏱️ expire bientôt)\n"
            else:
                desc += f"🔨 {name} — {reason} (♾️ permanent)\n"

        await ctx.send(embed=discord.Embed(
            title=f"🔨 Ownerbannis ({len(bans)})",
            description=desc,
            color=discord.Color.dark_red(),
        ).set_footer(text=f"Par {ctx.author.display_name}"), delete_after=30)


async def setup(bot: commands.Bot):
    await bot.add_cog(OwnerBanCog(bot))

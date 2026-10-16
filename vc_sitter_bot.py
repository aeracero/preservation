"""
VC居座りbot
- 「#(VC名)に入れ！」 → そのVCに参加して居座る
- 「終われ」           → VCから退出する
蹴られたり別VCに移動させられても、「終われ」と言われるまで元のVCに戻ります。
"""

import asyncio
import os
import re

import discord

TOKEN = os.environ["DISCORD_TOKEN"]

intents = discord.Intents.default()
intents.message_content = True  # Developer Portal で MESSAGE CONTENT INTENT をONにしてください
intents.voice_states = True

client = discord.Client(intents=intents)

# 「<#123...>に入れ！」（チャンネルメンション）と「#雑談に入れ！」（ただの文字）の両方に対応
JOIN_RE = re.compile(r"^\s*(?:<#(\d+)>|#\s*(.+?))\s*に入れ\s*[！!]*\s*$")
LEAVE_RE = re.compile(r"^\s*終われ\s*[！!。]*\s*$")

# guild_id -> 居座る予定の VC の channel_id
targets: dict[int, int] = {}


def find_voice_channel(guild: discord.Guild, channel_id: str | None, name: str | None):
    if channel_id:
        ch = guild.get_channel(int(channel_id))
        return ch if isinstance(ch, (discord.VoiceChannel, discord.StageChannel)) else None
    channels = list(guild.voice_channels) + list(guild.stage_channels)
    exact = discord.utils.get(channels, name=name)
    if exact:
        return exact
    return discord.utils.find(lambda c: c.name.lower() == name.lower(), channels)


async def ensure_connected(guild: discord.Guild, channel: discord.abc.Connectable):
    vc = guild.voice_client
    if vc and vc.is_connected():
        if vc.channel.id != channel.id:
            await vc.move_to(channel)
    else:
        if vc:  # 中途半端な状態のクライアントを掃除
            await vc.disconnect(force=True)
        await channel.connect(self_deaf=True, reconnect=True)


@client.event
async def on_ready():
    print(f"ログインしました: {client.user} (ID: {client.user.id})")


@client.event
async def on_message(message: discord.Message):
    if message.author.bot or message.guild is None:
        return

    guild = message.guild
    content = message.content

    # --- 終われ ---
    if LEAVE_RE.match(content):
        targets.pop(guild.id, None)  # 先に消しておくと自動復帰が走らない
        vc = guild.voice_client
        if vc:
            await vc.disconnect(force=True)
            await message.reply("👋 退出しました。")
        else:
            await message.reply("今はどのVCにも入っていません。")
        return

    # --- #(VC名)に入れ！ ---
    m = JOIN_RE.match(content)
    if not m:
        return

    channel = find_voice_channel(guild, m.group(1), m.group(2))
    if channel is None:
        await message.reply("そのボイスチャンネルが見つかりませんでした。")
        return

    perms = channel.permissions_for(guild.me)
    if not (perms.view_channel and perms.connect):
        await message.reply(f"{channel.mention} に入る権限がありません。")
        return

    targets[guild.id] = channel.id
    try:
        await ensure_connected(guild, channel)
    except Exception as e:
        targets.pop(guild.id, None)
        await message.reply(f"接続に失敗しました: `{e}`")
        return

    await message.reply(f"🔊 {channel.mention} に入りました！「終われ」と言うまで居座ります。")


@client.event
async def on_voice_state_update(member: discord.Member, before, after):
    """bot自身が蹴られた/移動させられたら、居座り先に戻る。"""
    if client.user is None or member.id != client.user.id:
        return

    guild = member.guild
    target_id = targets.get(guild.id)
    if target_id is None:
        return
    if after.channel is not None and after.channel.id == target_id:
        return

    await asyncio.sleep(2)  # 連続イベントや一時的な切断を少し待つ

    target_id = targets.get(guild.id)  # 待っている間に「終われ」が来たかもしれない
    if target_id is None:
        return
    channel = guild.get_channel(target_id)
    if channel is None:  # VCが削除された
        targets.pop(guild.id, None)
        return

    try:
        await ensure_connected(guild, channel)
        print(f"[{guild.name}] {channel.name} に戻りました")
    except Exception as e:
        print(f"[{guild.name}] 再接続に失敗: {e}")


client.run(TOKEN)

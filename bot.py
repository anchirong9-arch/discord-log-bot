"""백수톡 서버 로그 봇.

서버에서 일어나는 일을 #서버-로그 같은 비공개 채널에 차곡차곡 남긴다.
사진·파일은 **원본 그대로** 다시 첨부해서, 원래 방에서 메시지를 지워도
로그 방에는 남아 있게 한다.

실행:  python bot.py
설정:  .env 파일의 DISCORD_TOKEN / LOG_CHANNEL_ID / LOG_USER_ID
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable, Optional

import discord
from dotenv import load_dotenv

import logfmt

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("logbot")

TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
# 감시할 서버: 백수톡. 이 서버에서 일어난 일만 기록한다.
SOURCE_GUILD_ID = int(os.getenv("SOURCE_GUILD_ID", "386882960403726337").strip() or 0)
# 로그를 쌓을 채널: 개인 서버(paranoia)의 #백수톡-로그
LOG_CHANNEL_ID = int(os.getenv("LOG_CHANNEL_ID", "1572394445142360064").strip() or 0)
# 선택: 로그를 디엠으로도 받을 사용자 ID (0 이면 끔)
LOG_USER_ID = int(os.getenv("LOG_USER_ID", "0").strip() or 0)

MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 디스코드 기본 업로드 한도
MAX_FILES_PER_MESSAGE = 10
CACHE_MAX_FILES = 300
CACHE_MAX_BYTES = 300 * 1024 * 1024

COLOR = {
    "attachment": 0x5865F2,
    "delete": 0xED4245,
    "edit": 0xFEE75C,
    "join": 0x57F287,
    "leave": 0x95A5A6,
    "ban": 0xEB459E,
    "voice": 0x1ABC9C,
    "channel": 0xE67E22,
    "role": 0x9B59B6,
    "member": 0x3498DB,
    "system": 0x5865F2,
}

IMAGE_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".heic", ".avif")


# ---------------------------------------------------------------------------
# 임베드 만들기 (디스코드 연결 없이도 테스트 가능한 순수 함수들)
# ---------------------------------------------------------------------------

def _user_text(user) -> str:
    name = getattr(user, "display_name", None) or getattr(user, "name", None) or "알 수 없음"
    user_id = getattr(user, "id", "?")
    return f"{name} ({user_id})"


def _channel_text(channel) -> str:
    if channel is None:
        return "알 수 없는 채널"
    name = getattr(channel, "name", None) or "이름 없음"
    return f"#{name} ({getattr(channel, 'id', '?')})"


def _guild_name(message_or_guild) -> str:
    guild = getattr(message_or_guild, "guild", None) or message_or_guild
    return getattr(guild, "name", "백수톡")


def from_source_guild(obj, source_id: int = None) -> bool:
    """이 객체가 감시 대상 서버(기본: 백수톡)의 것인지.

    봇이 여러 서버에 들어가 있으므로, 로그 방이 있는 개인 서버에서 일어난 일은
    기록하지 않고 백수톡에서 일어난 일만 남긴다.
    """
    target = SOURCE_GUILD_ID if source_id is None else source_id
    if not target:
        return True
    guild = getattr(obj, "guild", None)
    return getattr(guild, "id", None) == target


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _footer(embed: discord.Embed, guild_name: str, tail: str = "") -> discord.Embed:
    text = f"{guild_name}"
    if tail:
        text = f"{text} · {tail}"
    embed.set_footer(text=text[:2048])
    return embed


def build_attachment_embed(message, saved, skipped) -> discord.Embed:
    """사진·파일이 올라왔을 때 남기는 로그."""
    guild_name = _guild_name(message)
    embed = discord.Embed(
        title="📎 사진·파일 원본 보존",
        colour=COLOR["attachment"],
        timestamp=getattr(message, "created_at", None) or _now(),
    )
    embed.add_field(name="작성자", value=logfmt.shorten(_user_text(message.author), 256), inline=True)
    embed.add_field(name="채널", value=logfmt.shorten(_channel_text(getattr(message, "channel", None)), 256), inline=True)

    content = logfmt.shorten(getattr(message, "content", None), 1000)
    embed.add_field(name="메시지", value=content, inline=False)

    if saved:
        lines = [f"`{name}` · {logfmt.format_size(size)}" for name, size in saved]
        embed.add_field(name="원본으로 보관한 파일", value=logfmt.shorten("\n".join(lines), 1000), inline=False)

    if skipped:
        lines = [
            f"`{getattr(a, 'filename', '파일')}` · {logfmt.format_size(getattr(a, 'size', None))}"
            for a in skipped
        ]
        embed.add_field(
            name="원본 보관 실패 (25MB 초과)",
            value=logfmt.shorten("\n".join(lines) + "\n원본 링크로만 남겨 둡니다.", 1000),
            inline=False,
        )

    url = logfmt.jump_url(
        getattr(getattr(message, "guild", None), "id", "@me"),
        getattr(getattr(message, "channel", None), "id", 0),
        getattr(message, "id", 0),
    )
    embed.add_field(name="원본 메시지", value=f"[바로가기]({url})", inline=False)
    return _footer(embed, guild_name, f"메시지 {getattr(message, 'id', '?')}")


def build_delete_embed(message, attachment_names) -> discord.Embed:
    """메시지가 삭제됐을 때 남기는 로그."""
    guild_name = _guild_name(message)
    embed = discord.Embed(
        title="🗑️ 메시지 삭제됨",
        colour=COLOR["delete"],
        timestamp=_now(),
    )
    embed.add_field(name="작성자", value=logfmt.shorten(_user_text(getattr(message, "author", None)), 256), inline=True)
    embed.add_field(name="채널", value=logfmt.shorten(_channel_text(getattr(message, "channel", None)), 256), inline=True)
    embed.add_field(
        name="삭제 전 내용",
        value=logfmt.shorten(getattr(message, "content", None), 1000),
        inline=False,
    )
    if attachment_names:
        embed.add_field(
            name="첨부돼 있던 파일",
            value=logfmt.shorten(", ".join(f"`{n}`" for n in attachment_names), 1000),
            inline=False,
        )
    created = getattr(message, "created_at", None)
    if created:
        embed.add_field(name="원래 작성 시각", value=discord.utils.format_dt(created, "F"), inline=False)
    return _footer(embed, guild_name, f"메시지 {getattr(message, 'id', '?')}")


def build_edit_embed(before, after) -> discord.Embed:
    """메시지가 수정됐을 때 남기는 로그."""
    guild_name = _guild_name(after)
    embed = discord.Embed(
        title="✏️ 메시지 수정됨",
        colour=COLOR["edit"],
        timestamp=_now(),
    )
    embed.add_field(name="작성자", value=logfmt.shorten(_user_text(getattr(after, "author", None)), 256), inline=True)
    embed.add_field(name="채널", value=logfmt.shorten(_channel_text(getattr(after, "channel", None)), 256), inline=True)
    embed.add_field(name="수정 전", value=logfmt.shorten(getattr(before, "content", None), 1000), inline=False)
    embed.add_field(name="수정 후", value=logfmt.shorten(getattr(after, "content", None), 1000), inline=False)

    url = logfmt.jump_url(
        getattr(getattr(after, "guild", None), "id", "@me"),
        getattr(getattr(after, "channel", None), "id", 0),
        getattr(after, "id", 0),
    )
    embed.add_field(name="메시지", value=f"[바로가기]({url})", inline=False)
    return _footer(embed, guild_name, f"메시지 {getattr(after, 'id', '?')}")


def build_member_join_embed(member) -> discord.Embed:
    guild_name = getattr(getattr(member, "guild", None), "name", "백수톡")
    embed = discord.Embed(
        title="✅ 멤버 입장",
        description=f"{logfmt.shorten(_user_text(member), 256)} 님이 들어왔어요.",
        colour=COLOR["join"],
        timestamp=_now(),
    )
    created = getattr(member, "created_at", None)
    if created:
        embed.add_field(name="계정 만든 날", value=discord.utils.format_dt(created, "F"), inline=True)
    count = getattr(getattr(member, "guild", None), "member_count", None)
    if count:
        embed.add_field(name="현재 멤버 수", value=f"{count}명", inline=True)
    return _footer(embed, guild_name, f"사용자 {getattr(member, 'id', '?')}")


def build_member_leave_embed(member) -> discord.Embed:
    guild_name = getattr(getattr(member, "guild", None), "name", "백수톡")
    embed = discord.Embed(
        title="👋 멤버 퇴장",
        description=f"{logfmt.shorten(_user_text(member), 256)} 님이 나갔어요(또는 추방됐어요).",
        colour=COLOR["leave"],
        timestamp=_now(),
    )
    joined = getattr(member, "joined_at", None)
    if joined:
        embed.add_field(name="서버에 들어온 날", value=discord.utils.format_dt(joined, "F"), inline=False)
    roles = [r.name for r in getattr(member, "roles", []) if getattr(r, "name", None) != "@everyone"]
    if roles:
        embed.add_field(name="가지고 있던 역할", value=logfmt.shorten(", ".join(roles), 1000), inline=False)
    return _footer(embed, guild_name, f"사용자 {getattr(member, 'id', '?')}")


def build_member_ban_embed(guild, user) -> discord.Embed:
    guild_name = getattr(guild, "name", "백수톡")
    embed = discord.Embed(
        title="🚫 멤버 차단(밴)",
        description=f"{logfmt.shorten(_user_text(user), 256)} 님이 서버에서 차단됐어요.",
        colour=COLOR["ban"],
        timestamp=_now(),
    )
    return _footer(embed, guild_name, f"사용자 {getattr(user, 'id', '?')}")


def build_voice_embed(member, kind, description) -> discord.Embed:
    guild_name = getattr(getattr(member, "guild", None), "name", "백수톡")
    embed = discord.Embed(
        title=f"🎧 음성 {kind}",
        description=logfmt.shorten(description, 1000),
        colour=COLOR["voice"],
        timestamp=_now(),
    )
    embed.add_field(name="멤버", value=logfmt.shorten(_user_text(member), 256), inline=False)
    return _footer(embed, guild_name, f"사용자 {getattr(member, 'id', '?')}")


def _channel_type_name(channel) -> str:
    ctype = getattr(channel, "type", None)
    name = getattr(ctype, "name", None) or str(ctype)
    return {
        "text": "텍스트 채널",
        "voice": "음성 채널",
        "category": "카테고리",
        "news": "공지 채널",
        "forum": "포럼",
        "stage": "스테이지",
    }.get(name, name)


def build_channel_embed(kind, channel, changes: Optional[Iterable[str]] = None) -> discord.Embed:
    guild_name = getattr(getattr(channel, "guild", None), "name", "백수톡")
    title = {"생성": "📁 채널 생성", "삭제": "📂 채널 삭제", "변경": "🔧 채널 설정 변경"}[kind]
    colour = COLOR["channel"]
    embed = discord.Embed(title=title, colour=colour, timestamp=_now())
    embed.add_field(name="채널", value=logfmt.shorten(_channel_text(channel), 256), inline=True)
    embed.add_field(name="종류", value=_channel_type_name(channel), inline=True)
    category = getattr(channel, "category", None)
    if category:
        embed.add_field(name="카테고리", value=logfmt.shorten(str(getattr(category, "name", category)), 256), inline=True)
    if changes:
        embed.add_field(name="바뀐 것", value=logfmt.shorten("\n".join(changes), 1000), inline=False)
    return _footer(embed, guild_name, f"채널 {getattr(channel, 'id', '?')}")


def build_role_embed(kind, role, changes: Optional[Iterable[str]] = None) -> discord.Embed:
    guild_name = getattr(getattr(role, "guild", None), "name", "백수톡")
    title = {"생성": "🎭 역할 생성", "삭제": "🎭 역할 삭제", "변경": "🎭 역할 변경"}[kind]
    embed = discord.Embed(title=title, colour=COLOR["role"], timestamp=_now())
    embed.add_field(name="역할", value=f"{getattr(role, 'name', '?')} ({getattr(role, 'id', '?')})", inline=True)
    members = getattr(role, "members", None)
    if members is not None:
        try:
            embed.add_field(name="멤버 수", value=f"{len(members)}명", inline=True)
        except TypeError:
            pass
    if changes:
        embed.add_field(name="바뀐 것", value=logfmt.shorten("\n".join(changes), 1000), inline=False)
    return _footer(embed, guild_name, f"역할 {getattr(role, 'id', '?')}")


def build_member_update_embed(before, after, changes: Iterable[str]) -> discord.Embed:
    guild_name = getattr(getattr(after, "guild", None), "name", "백수톡")
    embed = discord.Embed(title="👤 멤버 정보 변경", colour=COLOR["member"], timestamp=_now())
    embed.add_field(name="멤버", value=logfmt.shorten(_user_text(after), 256), inline=False)
    embed.add_field(name="바뀐 것", value=logfmt.shorten("\n".join(changes), 1000), inline=False)
    return _footer(embed, guild_name, f"사용자 {getattr(after, 'id', '?')}")


def permission_diff(before, after) -> list[str]:
    """역할 권한에서 켜지고 꺼진 것만 뽑는다."""
    lines = []
    old = logfmt.permission_names(getattr(before, "permissions", None))
    new = logfmt.permission_names(getattr(after, "permissions", None))
    added = [p for p in new if p not in old]
    removed = [p for p in old if p not in new]
    if added:
        lines.append("권한 켜짐: " + ", ".join(added))
    if removed:
        lines.append("권한 꺼짐: " + ", ".join(removed))
    return lines


# ---------------------------------------------------------------------------
# 봇 본체
# ---------------------------------------------------------------------------

@dataclass
class LogItem:
    embed: discord.Embed
    files: list = field(default_factory=list)
    image_name: Optional[str] = None


class LogBot(discord.Client):
    def __init__(self, **kwargs):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        intents.voice_states = True
        intents.guilds = True
        intents.moderation = True
        super().__init__(intents=intents, **kwargs)
        self.queue: "asyncio.Queue[LogItem]" = asyncio.Queue()
        self.cache = logfmt.AttachmentCache(CACHE_MAX_FILES, CACHE_MAX_BYTES)
        self.log_channel: Optional[discord.abc.Messageable] = None
        self.dm_user: Optional[discord.User] = None
        self._sender_task: Optional[asyncio.Task] = None

    # -- 시작 ---------------------------------------------------------------

    async def setup_hook(self) -> None:
        self._sender_task = asyncio.create_task(self._sender(), name="log-sender")
        if os.getenv("PORT"):
            asyncio.create_task(_start_health_server(), name="health-server")

    async def on_ready(self) -> None:
        await self._resolve_targets()
        channel = self.log_channel
        where = getattr(channel, "name", "?") if channel else "아직 못 찾음"
        log.info("로그인 완료: %s (서버 %d곳)", self.user, len(self.guilds))
        log.info("감시할 서버: %s", SOURCE_GUILD_ID)
        log.info("로그를 보낼 채널: #%s (%s)", where, LOG_CHANNEL_ID)
        if SOURCE_GUILD_ID and self.get_guild(SOURCE_GUILD_ID) is None:
            log.warning("감시 대상 서버(%s)에 봇이 없습니다. 초대 링크로 봇을 초대해 주세요.", SOURCE_GUILD_ID)
        if LOG_USER_ID:
            log.info("디엠 사본: 사용자 ID %s", LOG_USER_ID)

    async def _resolve_targets(self) -> None:
        if self.log_channel is None and LOG_CHANNEL_ID:
            try:
                self.log_channel = self.get_channel(LOG_CHANNEL_ID) or await self.fetch_channel(LOG_CHANNEL_ID)
            except Exception:
                log.exception("로그 채널(%s)을 찾지 못했습니다. LOG_CHANNEL_ID 를 확인해 주세요.", LOG_CHANNEL_ID)
        if LOG_USER_ID and self.dm_user is None:
            try:
                self.dm_user = self.get_user(LOG_USER_ID) or await self.fetch_user(LOG_USER_ID)
            except Exception:
                log.exception("디엠 받을 사용자(%s)를 찾지 못했습니다.", LOG_USER_ID)

    # -- 전송 ---------------------------------------------------------------

    def enqueue(self, item: LogItem) -> None:
        self.queue.put_nowait(item)

    async def _sender(self) -> None:
        await self.wait_until_ready()
        while not self.is_closed():
            item = await self.queue.get()
            try:
                await self._deliver(item)
            except Exception:
                log.exception("로그 전송 실패")
            finally:
                self.queue.task_done()

    async def _deliver(self, item: LogItem) -> None:
        if self.log_channel is None:
            await self._resolve_targets()
        if self.log_channel is not None:
            await self._send(self.log_channel, item)
        if LOG_USER_ID:
            if self.dm_user is None:
                await self._resolve_targets()
            if self.dm_user is not None:
                try:
                    await self._send(self.dm_user, item)
                except Exception:
                    log.warning("디엠 사본 전송 실패", exc_info=True)

    async def _send(self, target, item: LogItem) -> None:
        batches = [item.files[i:i + MAX_FILES_PER_MESSAGE] for i in range(0, len(item.files), MAX_FILES_PER_MESSAGE)]
        if not batches:
            batches = [[]]
        for index, batch in enumerate(batches):
            files = [discord.File(io.BytesIO(data), filename=name) for name, data in batch]
            embed = item.embed if index == 0 else None
            await target.send(embed=embed, files=files or None)

    # -- 메시지 -------------------------------------------------------------

    def _skip(self, message) -> bool:
        if getattr(message, "guild", None) is None:
            return True
        if not from_source_guild(message):
            return True
        if getattr(getattr(message, "author", None), "id", None) == getattr(self.user, "id", None):
            return True
        if getattr(getattr(message, "channel", None), "id", None) == LOG_CHANNEL_ID:
            return True
        return False

    async def on_message(self, message: discord.Message) -> None:
        if self._skip(message):
            return
        attachments = list(getattr(message, "attachments", []) or [])
        if not attachments:
            return

        saved: list[tuple[str, int]] = []
        kept: list[tuple[str, bytes]] = []
        skipped = []
        for attachment in attachments:
            if not logfmt.should_reupload(getattr(attachment, "size", None), MAX_UPLOAD_BYTES):
                skipped.append(attachment)
                continue
            try:
                data = await attachment.read()
            except Exception:
                log.warning("첨부파일 내려받기 실패: %s", getattr(attachment, "filename", "?"), exc_info=True)
                skipped.append(attachment)
                continue
            name = getattr(attachment, "filename", "file")
            kept.append((name, data))
            saved.append((name, len(data)))

        if kept:
            self.cache.put(getattr(message, "id", 0), kept)

        embed = build_attachment_embed(message, saved, skipped)
        image_name = None
        for name, _ in kept:
            if name.lower().endswith(IMAGE_EXT):
                image_name = name
                break
        if image_name:
            embed.set_image(url=f"attachment://{image_name}")
        self.enqueue(LogItem(embed, kept, image_name))

    async def on_message_delete(self, message: discord.Message) -> None:
        if self._skip(message):
            return
        cached = self.cache.get(getattr(message, "id", 0))
        self.cache.drop(getattr(message, "id", 0))
        names = [name for name, _ in cached] or [
            getattr(a, "filename", "파일") for a in (getattr(message, "attachments", []) or [])
        ]
        embed = build_delete_embed(message, names)
        self.enqueue(LogItem(embed, cached))

    async def on_message_edit(self, before: discord.Message, after: discord.Message) -> None:
        if self._skip(after):
            return
        if getattr(before, "content", None) == getattr(after, "content", None):
            if len(getattr(before, "attachments", []) or []) == len(getattr(after, "attachments", []) or []):
                return
        embed = build_edit_embed(before, after)
        self.enqueue(LogItem(embed))

    # -- 멤버 ---------------------------------------------------------------

    async def on_member_join(self, member: discord.Member) -> None:
        if not from_source_guild(member):
            return
        self.enqueue(LogItem(build_member_join_embed(member)))

    async def on_member_remove(self, member: discord.Member) -> None:
        if not from_source_guild(member):
            return
        self.enqueue(LogItem(build_member_leave_embed(member)))

    async def on_member_ban(self, guild: discord.Guild, user: discord.User) -> None:
        if SOURCE_GUILD_ID and getattr(guild, "id", None) != SOURCE_GUILD_ID:
            return
        self.enqueue(LogItem(build_member_ban_embed(guild, user)))

    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        if not from_source_guild(after):
            return
        changes = []
        if getattr(before, "nick", None) != getattr(after, "nick", None):
            changes.append(f"닉네임: {before.nick or before.name} → {after.nick or after.name}")
        old_roles = {r.id: r.name for r in getattr(before, "roles", [])}
        new_roles = {r.id: r.name for r in getattr(after, "roles", [])}
        added = [name for rid, name in new_roles.items() if rid not in old_roles]
        removed = [name for rid, name in old_roles.items() if rid not in new_roles]
        if added:
            changes.append("역할 추가: " + ", ".join(added))
        if removed:
            changes.append("역할 제거: " + ", ".join(removed))
        if changes:
            self.enqueue(LogItem(build_member_update_embed(before, after, changes)))

    # -- 음성 ---------------------------------------------------------------

    async def on_voice_state_update(self, member: discord.Member, before, after) -> None:
        if getattr(member, "bot", False):
            return
        if not from_source_guild(member):
            return
        described = logfmt.describe_voice(before, after)
        if described is None:
            return
        kind, text = described
        self.enqueue(LogItem(build_voice_embed(member, kind, text)))

    # -- 채널 / 역할 --------------------------------------------------------

    async def on_guild_channel_create(self, channel) -> None:
        if getattr(channel, "id", None) == LOG_CHANNEL_ID:
            return
        if not from_source_guild(channel):
            return
        self.enqueue(LogItem(build_channel_embed("생성", channel)))

    async def on_guild_channel_delete(self, channel) -> None:
        if getattr(channel, "id", None) == LOG_CHANNEL_ID:
            return
        if not from_source_guild(channel):
            return
        self.enqueue(LogItem(build_channel_embed("삭제", channel)))

    async def on_guild_channel_update(self, before, after) -> None:
        if getattr(after, "id", None) == LOG_CHANNEL_ID:
            return
        if not from_source_guild(after):
            return
        changes = logfmt.describe_changes(
            before, after, ("name", "topic", "nsfw", "slowmode_delay", "category_id", "position")
        )
        if changes:
            self.enqueue(LogItem(build_channel_embed("변경", after, changes)))

    async def on_guild_role_create(self, role) -> None:
        if not from_source_guild(role):
            return
        self.enqueue(LogItem(build_role_embed("생성", role)))

    async def on_guild_role_delete(self, role) -> None:
        if not from_source_guild(role):
            return
        self.enqueue(LogItem(build_role_embed("삭제", role)))

    async def on_guild_role_update(self, before, after) -> None:
        if not from_source_guild(after):
            return
        changes = logfmt.describe_changes(before, after, ("name", "color", "hoist", "mentionable", "position"))
        changes.extend(permission_diff(before, after))
        if changes:
            self.enqueue(LogItem(build_role_embed("변경", after, changes)))


# ---------------------------------------------------------------------------
# 24시간 무료 클라우드(Render 웹 서비스)용 초간단 상태 확인 서버
# ---------------------------------------------------------------------------

async def _start_health_server() -> None:
    try:
        from aiohttp import web
    except Exception:
        log.warning("aiohttp 가 없어 상태 확인 서버를 켜지 못했습니다.")
        return
    port = int(os.getenv("PORT", "0") or 0)
    if not port:
        return

    async def ping(_request):
        return web.Response(text="log bot is alive")

    app = web.Application()
    app.router.add_get("/", ping)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    log.info("상태 확인 서버 켜짐 (포트 %s)", port)


def main() -> None:
    if not TOKEN:
        raise SystemExit(
            "DISCORD_TOKEN 이 없습니다.\n"
            "실행하기.bat 을 실행하면 토큰을 물어보고 .env 파일을 만들어 줍니다.\n"
            "직접 넣으려면 .env.example 을 복사해 .env 로 이름을 바꾸고 DISCORD_TOKEN= 뒤에 붙여넣으세요."
        )
    if not LOG_CHANNEL_ID:
        raise SystemExit("LOG_CHANNEL_ID 가 없습니다. 로그를 남길 채널 ID를 .env 에 넣어 주세요.")

    bot = LogBot()
    try:
        bot.run(TOKEN, log_handler=None)
    except discord.LoginFailure:
        raise SystemExit("봇 토큰이 올바르지 않습니다. 개발자 포털에서 토큰을 다시 확인해 주세요.")


if __name__ == "__main__":
    main()

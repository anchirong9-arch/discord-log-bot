"""봇 로직 테스트 (디스코드에 접속하지 않고 돌아감).

    python -m unittest -v test_bot.py
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

import logfmt
import bot


def fake_user(user_id=1234, name="안え"):
    return SimpleNamespace(id=user_id, name=name, display_name=name, bot=False)


def fake_channel(channel_id=9999, name="채팅"):
    return SimpleNamespace(id=channel_id, name=name, type=SimpleNamespace(name="text"))


def fake_message(content="안녕", attachments=None, channel=None, author=None, message_id=555):
    channel = channel or fake_channel()
    guild = SimpleNamespace(id=1111, name="백수톡")
    channel.guild = guild
    return SimpleNamespace(
        id=message_id,
        content=content,
        attachments=attachments or [],
        channel=channel,
        author=author or fake_user(),
        guild=guild,
        created_at=datetime(2026, 11, 18, 0, 0, tzinfo=timezone.utc),
    )


class TestFormatting(unittest.TestCase):
    def test_format_size(self):
        self.assertEqual(logfmt.format_size(0), "0 B")
        self.assertEqual(logfmt.format_size(1024), "1.0 KB")
        self.assertEqual(logfmt.format_size(1536), "1.5 KB")
        self.assertEqual(logfmt.format_size(25 * 1024 * 1024), "25.0 MB")
        self.assertEqual(logfmt.format_size(None), "크기 알 수 없음")

    def test_shorten(self):
        self.assertEqual(logfmt.shorten(None), "(내용 없음)")
        self.assertEqual(logfmt.shorten("   "), "(내용 없음)")
        self.assertEqual(logfmt.shorten("짧은 글"), "짧은 글")
        long_text = "가" * 2000
        result = logfmt.shorten(long_text, 100)
        self.assertEqual(len(result), 100)
        self.assertTrue(result.endswith("…(생략)"))

    def test_jump_url(self):
        self.assertEqual(
            logfmt.jump_url(1, 2, 3), "https://discord.com/channels/1/2/3"
        )

    def test_should_reupload(self):
        self.assertTrue(logfmt.should_reupload(1024))
        self.assertTrue(logfmt.should_reupload(logfmt.DEFAULT_UPLOAD_LIMIT))
        self.assertFalse(logfmt.should_reupload(logfmt.DEFAULT_UPLOAD_LIMIT + 1))
        self.assertFalse(logfmt.should_reupload(0))
        self.assertFalse(logfmt.should_reupload(None))


class TestVoice(unittest.TestCase):
    def voice(self, channel=None, self_mute=False, self_deaf=False, mute=False, deaf=False,
              self_stream=False, self_video=False):
        return SimpleNamespace(
            channel=SimpleNamespace(name=channel) if channel else None,
            self_mute=self_mute, self_deaf=self_deaf, mute=mute, deaf=deaf,
            self_stream=self_stream, self_video=self_video,
        )

    def test_join(self):
        kind, text = logfmt.describe_voice(self.voice(), self.voice("엄"))
        self.assertEqual(kind, "입장")
        self.assertIn("엄", text)

    def test_leave(self):
        kind, _ = logfmt.describe_voice(self.voice("엄"), self.voice())
        self.assertEqual(kind, "퇴장")

    def test_move(self):
        kind, text = logfmt.describe_voice(self.voice("엄"), self.voice("준"))
        self.assertEqual(kind, "이동")
        self.assertIn("엄", text)
        self.assertIn("준", text)

    def test_mute_change(self):
        kind, text = logfmt.describe_voice(self.voice("엄"), self.voice("엄", self_mute=True))
        self.assertEqual(kind, "상태 변경")
        self.assertEqual(text, "**엄** 채널에서 마이크 끔")

    def test_no_change(self):
        self.assertIsNone(logfmt.describe_voice(self.voice("엄"), self.voice("엄")))

    def test_stream_start(self):
        kind, text = logfmt.describe_voice(
            self.voice("엄"), self.voice("엄", self_stream=True)
        )
        self.assertEqual(kind, "상태 변경")
        self.assertIn("화면 공유 시작", text)


class TestAttachmentCache(unittest.TestCase):
    def test_put_get_drop(self):
        cache = logfmt.AttachmentCache()
        cache.put(1, [("a.png", b"x" * 10)])
        self.assertEqual(cache.get(1), [("a.png", b"x" * 10)])
        self.assertEqual(len(cache), 1)
        self.assertEqual(cache.total_bytes, 10)
        cache.drop(1)
        self.assertEqual(cache.get(1), [])
        self.assertEqual(cache.total_bytes, 0)

    def test_evict_by_count(self):
        cache = logfmt.AttachmentCache(max_files=2, max_bytes=10_000)
        for i in range(3):
            cache.put(i, [(f"{i}.png", b"x" * 10)])
        self.assertEqual(len(cache), 2)
        self.assertEqual(cache.get(0), [])  # 가장 오래된 것부터 버림
        self.assertEqual(len(cache.get(2)), 1)

    def test_evict_by_bytes(self):
        cache = logfmt.AttachmentCache(max_files=10, max_bytes=25)
        cache.put(1, [("a", b"x" * 10)])
        cache.put(2, [("b", b"x" * 10)])
        cache.put(3, [("c", b"x" * 10)])
        self.assertLessEqual(cache.total_bytes, 25)
        self.assertEqual(cache.get(1), [])

    def test_oversized_message_not_stored(self):
        cache = logfmt.AttachmentCache(max_files=10, max_bytes=5)
        cache.put(1, [("big", b"x" * 10)])
        self.assertEqual(cache.get(1), [])
        self.assertEqual(cache.total_bytes, 0)

    def test_replace_same_message(self):
        cache = logfmt.AttachmentCache()
        cache.put(1, [("a", b"x" * 10)])
        cache.put(1, [("b", b"y" * 5)])
        self.assertEqual(cache.get(1), [("b", b"y" * 5)])
        self.assertEqual(cache.total_bytes, 5)


class TestEmbeds(unittest.TestCase):
    def test_attachment_embed(self):
        att = SimpleNamespace(filename="cat.png", size=2048, url="https://cdn/x.png")
        message = fake_message(content="고양이", attachments=[att])
        embed = bot.build_attachment_embed(message, [("cat.png", 2048)], [])
        names = [f.name for f in embed.fields]
        self.assertIn("작성자", names)
        self.assertIn("원본으로 보관한 파일", names)
        self.assertIn("cat.png", dict((f.name, f.value) for f in embed.fields)["원본으로 보관한 파일"])
        self.assertIn("원본 메시지", names)
        self.assertNotIn("cat.png", names)

    def test_attachment_embed_skipped(self):
        att = SimpleNamespace(filename="huge.zip", size=30 * 1024 * 1024)
        message = fake_message(content="", attachments=[att])
        embed = bot.build_attachment_embed(message, [], [att])
        fields = {f.name: f.value for f in embed.fields}
        self.assertIn("원본 보관 실패 (25MB 초과)", fields)
        self.assertIn("huge.zip", fields["원본 보관 실패 (25MB 초과)"])
        self.assertEqual(fields["메시지"], "(내용 없음)")

    def test_delete_embed(self):
        message = fake_message(content="지울 메시지")
        embed = bot.build_delete_embed(message, ["a.png"])
        fields = {f.name: f.value for f in embed.fields}
        self.assertEqual(embed.title, "🗑️ 메시지 삭제됨")
        self.assertEqual(fields["삭제 전 내용"], "지울 메시지")
        self.assertIn("a.png", fields["첨부돼 있던 파일"])

    def test_edit_embed(self):
        before = fake_message(content="원본")
        after = fake_message(content="수정본")
        embed = bot.build_edit_embed(before, after)
        fields = {f.name: f.value for f in embed.fields}
        self.assertEqual(fields["수정 전"], "원본")
        self.assertEqual(fields["수정 후"], "수정본")

    def test_member_join_embed(self):
        member = SimpleNamespace(
            id=77, name="새사람", display_name="새사람",
            guild=SimpleNamespace(name="백수톡", member_count=73),
            created_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
        )
        embed = bot.build_member_join_embed(member)
        fields = {f.name: f.value for f in embed.fields}
        self.assertEqual(fields["현재 멤버 수"], "73명")

    def test_member_leave_embed(self):
        member = SimpleNamespace(
            id=78, name="나간사람", display_name="나간사람",
            guild=SimpleNamespace(name="백수톡"),
            joined_at=datetime(2024, 5, 5, tzinfo=timezone.utc),
            roles=[SimpleNamespace(name="@everyone"), SimpleNamespace(name="손님")],
        )
        embed = bot.build_member_leave_embed(member)
        fields = {f.name: f.value for f in embed.fields}
        self.assertEqual(fields["가지고 있던 역할"], "손님")

    def test_permission_diff(self):
        before = SimpleNamespace(permissions=_Perms(administrator=True, manage_guild=False))
        after = SimpleNamespace(permissions=_Perms(administrator=False, manage_guild=True))
        lines = bot.permission_diff(before, after)
        joined = " / ".join(lines)
        self.assertIn("권한 켜짐", joined)
        self.assertIn("manage_guild", joined)
        self.assertIn("권한 꺼짐", joined)
        self.assertIn("administrator", joined)

    def test_channel_embed_change(self):
        guild = SimpleNamespace(name="백수톡")
        channel = SimpleNamespace(
            id=5, name="새이름", guild=guild,
            type=SimpleNamespace(name="text"),
            category=SimpleNamespace(name="Text Channels"),
        )
        embed = bot.build_channel_embed("변경", channel, ["이름: 옛이름 → 새이름"])
        fields = {f.name: f.value for f in embed.fields}
        self.assertEqual(fields["종류"], "텍스트 채널")
        self.assertEqual(fields["카테고리"], "Text Channels")
        self.assertIn("옛이름", fields["바뀐 것"])


class _Perms:
    """discord.Permissions 처럼 (이름, 값) 을 돌려주는 가짜 객체."""

    def __init__(self, **values):
        known = {
            "administrator": False,
            "manage_guild": False,
            "manage_roles": False,
            "send_messages": False,
        }
        known.update(values)
        self._items = list(known.items())  # (이름, 값) 쌍

    def __iter__(self):
        return iter(self._items)


class TestSourceGuild(unittest.TestCase):
    def test_from_source(self):
        msg = fake_message()
        msg.guild = SimpleNamespace(id=1111, name="백수톡")
        self.assertTrue(bot.from_source_guild(msg, source_id=1111))
        self.assertFalse(bot.from_source_guild(msg, source_id=2222))

    def test_other_guild_ignored(self):
        other = SimpleNamespace(guild=SimpleNamespace(id=2222, name="paranoia"))
        self.assertFalse(bot.from_source_guild(other, source_id=1111))

    def test_no_guild(self):
        self.assertFalse(bot.from_source_guild(SimpleNamespace(guild=None), source_id=1111))

    def test_defaults(self):
        # 기본값: 감시는 백수톡, 저장은 개인 서버 #백수톡-로그
        self.assertEqual(bot.SOURCE_GUILD_ID, 386882960403726337)
        self.assertEqual(bot.LOG_CHANNEL_ID, 1572394445142360064)


class TestChannelDescribe(unittest.TestCase):
    def test_describe_changes(self):
        before = SimpleNamespace(name="옛이름", topic=None, slowmode_delay=0)
        after = SimpleNamespace(name="새이름", topic="설명", slowmode_delay=5)
        lines = logfmt.describe_changes(before, after, ("name", "topic", "slowmode_delay"))
        self.assertEqual(len(lines), 3)
        self.assertIn("이름: 옛이름 → 새이름", lines)
        self.assertIn("주제: 없음 → 설명", lines)


if __name__ == "__main__":
    unittest.main()

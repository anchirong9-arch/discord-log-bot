"""로그 봇에서 쓰는 순수 함수 모음.

디스코드에 연결하지 않고도 테스트할 수 있도록, 문자열·크기·보관함 계산처럼
값만 다루는 로직을 여기에 모아 둔다. bot.py 가 이 모듈을 가져다 쓴다.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Iterable, Optional

MAX_FIELD = 1024  # 디스코드 임베드 필드 글자 수 한도
DEFAULT_UPLOAD_LIMIT = 25 * 1024 * 1024  # 디스코드 기본 업로드 한도 (25MB)


def format_size(size) -> str:
    """바이트 수를 사람이 읽기 쉬운 문자열로 바꾼다. (1536 -> '1.5 KB')"""
    if size is None:
        return "크기 알 수 없음"
    try:
        value = float(size)
    except (TypeError, ValueError):
        return "크기 알 수 없음"
    if value < 0:
        return "크기 알 수 없음"
    units = ("B", "KB", "MB", "GB")
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} B"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def shorten(text: Optional[str], limit: int = MAX_FIELD) -> str:
    """임베드 필드에 넣을 수 있게 자른다. 빈 값은 '(내용 없음)'."""
    if text is None:
        return "(내용 없음)"
    cleaned = str(text).strip()
    if not cleaned:
        return "(내용 없음)"
    if len(cleaned) <= limit:
        return cleaned
    suffix = " …(생략)"
    return cleaned[: limit - len(suffix)] + suffix


def jump_url(guild_id, channel_id, message_id) -> str:
    """메시지 바로가기 주소."""
    return f"https://discord.com/channels/{guild_id}/{channel_id}/{message_id}"


def should_reupload(size, limit: int = DEFAULT_UPLOAD_LIMIT) -> bool:
    """이 크기의 파일을 봇이 다시 올려서 보존할 수 있는지."""
    if size is None:
        return False
    try:
        return 0 < int(size) <= limit
    except (TypeError, ValueError):
        return False


def describe_voice(before, after):
    """음성 상태 변화를 (종류, 설명) 으로 돌려준다. 바뀐 게 없으면 None.

    before/after 는 discord.VoiceState 와 같은 속성을 가진 객체면 된다.
    """
    before_channel = getattr(getattr(before, "channel", None), "name", None)
    after_channel = getattr(getattr(after, "channel", None), "name", None)

    if before_channel is None and after_channel is not None:
        return ("입장", f"🔊 **{after_channel}** 채널에 입장")
    if before_channel is not None and after_channel is None:
        return ("퇴장", f"🔇 **{before_channel}** 채널에서 퇴장")
    if before_channel != after_channel:
        return ("이동", f"🔀 **{before_channel}** → **{after_channel}**")

    changes = []
    pairs = (
        ("self_mute", "마이크 끔", "마이크 켬"),
        ("self_deaf", "헤드셋 끔", "헤드셋 켬"),
        ("mute", "서버에서 마이크 끔", "서버에서 마이크 켬"),
        ("deaf", "서버에서 헤드셋 끔", "서버에서 헤드셋 켬"),
    )
    for attr, on_text, off_text in pairs:
        old = getattr(before, attr, None)
        new = getattr(after, attr, None)
        if old != new:
            changes.append(on_text if new else off_text)

    for attr, on_text, off_text in (
        ("self_stream", "화면 공유 시작", "화면 공유 중지"),
        ("self_video", "카메라 켬", "카메라 끔"),
    ):
        old = getattr(before, attr, None)
        new = getattr(after, attr, None)
        if old != new:
            changes.append(on_text if new else off_text)

    if not changes:
        return None
    where = f"**{after_channel or before_channel}** 채널에서 " if (after_channel or before_channel) else ""
    return ("상태 변경", where + ", ".join(changes))


def permission_names(permissions) -> list[str]:
    """Permissions 객체에서 켜져 있는 권한 이름 목록."""
    if permissions is None:
        return []
    names = []
    for name, value in permissions:
        if value:
            names.append(name)
    return sorted(names)


def describe_changes(before, after, attrs: Iterable[str]) -> list[str]:
    """두 객체의 지정한 속성이 바뀐 것만 '이름: 전 → 후' 로 만든다."""
    lines = []
    for attr in attrs:
        old = getattr(before, attr, None)
        new = getattr(after, attr, None)
        if old != new:
            label = ATTR_LABELS.get(attr, attr)
            lines.append(f"{label}: {_display(old)} → {_display(new)}")
    return lines


ATTR_LABELS = {
    "name": "이름",
    "topic": "주제",
    "nsfw": "NSFW",
    "slowmode_delay": "슬로우 모드",
    "category_id": "카테고리",
    "position": "순서",
    "color": "색상",
    "hoist": "멤버와 분리 표시",
    "mentionable": "멘션 허용",
    "nick": "닉네임",
    "display_name": "표시 이름",
}


def _display(value) -> str:
    if value is None or value == "":
        return "없음"
    if isinstance(value, bool):
        return "켜짐" if value else "꺼짐"
    if isinstance(value, (list, tuple, set)):
        return ", ".join(str(v) for v in value) if value else "없음"
    return str(value)


class AttachmentCache:
    """메시지 하나에 붙어 있던 파일 원본을 잠시 들고 있는 보관함.

    메시지가 삭제될 때 같은 파일을 다시 올려 주기 위한 것으로,
    개수와 총 용량 상한을 넘으면 오래된 것부터 버린다.
    """

    def __init__(self, max_files: int = 300, max_bytes: int = 300 * 1024 * 1024):
        self.max_files = max_files
        self.max_bytes = max_bytes
        self._items: "OrderedDict[int, list[tuple[str, bytes]]]" = OrderedDict()
        self._total = 0

    def put(self, message_id, files: Iterable[tuple[str, bytes]]) -> None:
        files = [(name, data) for name, data in files if data is not None]
        if not files:
            return
        size = sum(len(data) for _, data in files)
        if size > self.max_bytes:
            return  # 한 메시지가 상한보다 크면 보관하지 않는다
        self.drop(message_id)
        self._items[message_id] = files
        self._total += size
        self._trim()

    def get(self, message_id) -> list[tuple[str, bytes]]:
        return list(self._items.get(message_id, []))

    def drop(self, message_id) -> None:
        files = self._items.pop(message_id, None)
        if files:
            self._total -= sum(len(data) for _, data in files)
            if self._total < 0:
                self._total = 0

    def _trim(self) -> None:
        while self._items and (len(self._items) > self.max_files or self._total > self.max_bytes):
            _, files = self._items.popitem(last=False)
            self._total -= sum(len(data) for _, data in files)
        if self._total < 0:
            self._total = 0

    def __len__(self) -> int:
        return len(self._items)

    @property
    def total_bytes(self) -> int:
        return self._total

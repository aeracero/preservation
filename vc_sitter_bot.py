"""
VC居座りbot（通話記録・レベル・入室メッセージつき）

■ 居座り
- 「#(VC名)に入れ！」           → そのVCに参加して居座る
- 「入れ」                       → 入れるVCをメニューで選ぶ
- 「終われ」                     → VCから退出する
- 切断されたり、botが再起動したりしたら居座りを止め、VCにいた人に@して入れ直しを案内
  （案内には「もう一度入れる」ボタン付き。VCを移動させられた場合は移動先に居座る）

■ お知らせチャンネル（切断の案内・レベルアップ通知の送り先）
- 「お知らせチャンネル」          → 今の設定の確認と、メニューで選択
- 「お知らせチャンネル #チャンネル」→ 指定したチャンネルに設定
- 「お知らせチャンネルここ」      → 今いるチャンネルに設定
- 「お知らせチャンネル解除」      → 解除（VCのチャットなどに送る）
  ※ 変更には「サーバー管理」権限が必要

■ 記録
- 「ステータスオープン @ユーザー」  → 通話記録・レベル・ポイントを表示（全員に見える）
    表示後のボタンで期間（全期間/今日/7日/30日）を切り替え、メニューで別の人に切り替え
    「ステータスオープン」だけなら、見る人をメニューで選ぶ。「ステータスオープン 自分」でも可
- 「@ユーザー の履歴を保存」       → 通話履歴をCSVファイルで送信（「自分の履歴を保存」でも可）
- 「ランキング」                  → サーバー内の経験値ランキング

■ レベル・ポイント（1分ごとに付与）
- VCにいるだけで      +1 XP / +1 pt
- ミュートせず他の人と一緒 +1 XP / +1 pt（＝会話した分のボーナス）
- スピーカーミュート中・AFKチャンネルでは付与なし
- レベルアップごとに  +50 pt

■ 入室メッセージ（VCに入ったとき、そのVCのテキストチャットに表示）
- 「入室メッセージ」          → 一覧（無料10種・有料20種）
- 「入室メッセージ購入 12」    → ポイントで解放（解放するとそのまま設定）
- 「入室メッセージ設定 12」    → 使うメッセージを選ぶ
- 「入室メッセージランダム」    → 解放済みの中からランダム
- 「入室メッセージオフ」        → 表示しない
"""

import asyncio
import csv
import io
import os
import random
import re
import sqlite3
import time
import unicodedata
from datetime import datetime, timedelta, timezone

import discord

TOKEN = os.environ["DISCORD_TOKEN"]
JST = timezone(timedelta(hours=9), "JST")

DATA_DIR = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH") or os.environ.get("DATA_DIR") or "./data"
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "voice_log.sqlite3")

# ---- レベル・ポイントの設定 ----
XP_PRESENT = 1          # VCにいる1分あたり
XP_TALK = 1             # ミュートせず他の人といる1分あたりの追加分
LEVEL_UP_BONUS = 50     # レベルアップ時のポイント
ENTRY_COOLDOWN = 300    # 同じ人の入室メッセージを出す最短間隔（秒）

# ---- 入室メッセージ（{name}=名前, {vc}=VC名, {level}=レベル） ----
# (番号, 必要ポイント, 本文)
ENTRY_MESSAGES = [
    # 無料（10種）
    (1, 0, "👋 {name} が {vc} に入りました"),
    (2, 0, "🚪 {name} がやってきた！"),
    (3, 0, "✨ {name} 参上！"),
    (4, 0, "📞 {name} が通話に参加しました"),
    (5, 0, "🙌 {name} きた！"),
    (6, 0, "🐾 {name} がひょっこり現れた"),
    (7, 0, "☕ {name} が一息つきに来ました"),
    (8, 0, "🎧 {name} が {vc} にログインしました"),
    (9, 0, "🌙 {name} がそっと入ってきた"),
    (10, 0, "🔔 ピンポーン、{name} です"),
    # ★ 100pt（8種）
    (11, 100, "🎉 みんな拍手！{name} の到着だ！"),
    (12, 100, "🦸 待たせたな…{name}、ただいま見参！"),
    (13, 100, "🍙 {name} がおにぎり片手に {vc} へ"),
    (14, 100, "🐈 にゃーん（訳: {name} が来たよ）"),
    (15, 100, "📡 {name} の電波を受信しました"),
    (16, 100, "⚡ {name}、雷のごとく参戦！"),
    (17, 100, "🎮 {name} がパーティに加わった！"),
    (18, 100, "🍵 {name}「お邪魔します」"),
    # ★★ 300pt（6種）
    (19, 300, "👑 Lv.{level} の {name} がお出ましだ"),
    (20, 300, "🚀 {name} 号、{vc} に着陸しました"),
    (21, 300, "🌸 春風とともに {name} が舞い込んだ"),
    (22, 300, "🎺 パッパカパーン！{name} 入場！"),
    (23, 300, "🧙 {name} が魔法陣から召喚された"),
    (24, 300, "🛸 未確認通話物体「{name}」を {vc} にて確認"),
    # ★★★ 600pt（4種）
    (25, 600, "🐉 {vc} に伝説の {name} が降臨した…！"),
    (26, 600, "🏆 Lv.{level} 歴戦の猛者、{name} が帰還した"),
    (27, 600, "🎬 本日の主役、{name} の登場です！"),
    (28, 600, "🌌 銀河の果てから {name} の通信が {vc} に到達した"),
    # ★★★★ 1200pt（2種）
    (29, 1200, "👑✨ 全員起立！Lv.{level} {name} 陛下のご入場です ✨👑"),
    (30, 1200, "🌠 {vc} の空に流れ星…願いが叶った、{name} が来た！"),
]
MSG_BY_ID = {mid: (price, text) for mid, price, text in ENTRY_MESSAGES}
TIERS = [(0, "無料"), (100, "★ 100pt"), (300, "★★ 300pt"), (600, "★★★ 600pt"), (1200, "★★★★ 1200pt")]
SELECT_OFF, SELECT_RANDOM = 0, -1

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

client = discord.Client(intents=intents)

# ================= データベース =================
db = sqlite3.connect(DB_PATH)
db.execute("PRAGMA journal_mode=WAL")
db.executescript("""
CREATE TABLE IF NOT EXISTS sessions(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    channel_name TEXT NOT NULL,
    start REAL NOT NULL,
    end REAL
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(guild_id, user_id, start);
CREATE INDEX IF NOT EXISTS idx_sessions_chan ON sessions(guild_id, channel_id, start);
CREATE TABLE IF NOT EXISTS events(
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_user ON events(guild_id, user_id, ts);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS profiles(
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    xp INTEGER NOT NULL DEFAULT 0,
    points INTEGER NOT NULL DEFAULT 0,
    level INTEGER NOT NULL DEFAULT 0,
    present_min INTEGER NOT NULL DEFAULT 0,
    talk_min INTEGER NOT NULL DEFAULT 0,
    entry_msg INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY(guild_id, user_id)
);
CREATE TABLE IF NOT EXISTS guild_settings(
    guild_id INTEGER PRIMARY KEY,
    announce_channel_id INTEGER,   -- お知らせチャンネル
    sitting_channel_id INTEGER,    -- 居座り中のVC（再起動の検知用）
    notify_channel_id INTEGER      -- 居座りを指示したチャンネル
);
CREATE TABLE IF NOT EXISTS unlocks(
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    msg_id INTEGER NOT NULL,
    ts REAL NOT NULL,
    PRIMARY KEY(guild_id, user_id, msg_id)
);
""")
db.commit()


def meta_get(key: str):
    row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def meta_set(key: str, value: str):
    db.execute("INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


if meta_get("tracking_since") is None:
    meta_set("tracking_since", str(time.time()))
    db.commit()


SETTING_COLUMNS = ("announce_channel_id", "sitting_channel_id", "notify_channel_id")


def get_setting(guild_id: int, column: str):
    assert column in SETTING_COLUMNS
    row = db.execute(f"SELECT {column} FROM guild_settings WHERE guild_id=?", (guild_id,)).fetchone()
    return row[0] if row else None


def set_setting(guild_id: int, column: str, value):
    assert column in SETTING_COLUMNS
    db.execute("INSERT OR IGNORE INTO guild_settings(guild_id) VALUES(?)", (guild_id,))
    db.execute(f"UPDATE guild_settings SET {column}=? WHERE guild_id=?", (value, guild_id))
    db.commit()


def open_session(guild_id: int, user_id: int, channel, ts: float):
    db.execute(
        "INSERT INTO sessions(guild_id, user_id, channel_id, channel_name, start) VALUES(?, ?, ?, ?, ?)",
        (guild_id, user_id, channel.id, channel.name, ts),
    )


def close_session(guild_id: int, user_id: int, ts: float):
    db.execute("UPDATE sessions SET end=? WHERE guild_id=? AND user_id=? AND end IS NULL", (ts, guild_id, user_id))


def add_event(guild_id: int, user_id: int, kind: str, ts: float):
    db.execute("INSERT INTO events(guild_id, user_id, kind, ts) VALUES(?, ?, ?, ?)", (guild_id, user_id, kind, ts))


def get_profile(guild_id: int, user_id: int) -> dict:
    db.execute("INSERT OR IGNORE INTO profiles(guild_id, user_id) VALUES(?, ?)", (guild_id, user_id))
    row = db.execute(
        "SELECT xp, points, level, present_min, talk_min, entry_msg FROM profiles WHERE guild_id=? AND user_id=?",
        (guild_id, user_id),
    ).fetchone()
    return dict(zip(("xp", "points", "level", "present_min", "talk_min", "entry_msg"), row))


def owned_messages(guild_id: int, user_id: int) -> set[int]:
    paid = {r[0] for r in db.execute("SELECT msg_id FROM unlocks WHERE guild_id=? AND user_id=?", (guild_id, user_id))}
    free = {mid for mid, price, _ in ENTRY_MESSAGES if price == 0}
    return free | paid


# ================= レベル計算 =================
def xp_to_next(level: int) -> int:
    """そのレベルから次のレベルに上がるのに必要なXP"""
    return 5 * level * level + 50 * level + 100


def level_info(total_xp: int):
    """(レベル, 今のレベル内のXP, 次までに必要なXP)"""
    level, rest = 0, total_xp
    while rest >= xp_to_next(level):
        rest -= xp_to_next(level)
        level += 1
    return level, rest, xp_to_next(level)


def add_xp(guild_id: int, user_id: int, gain: int, talking: bool):
    """XPとポイントを加算。レベルが上がったら (新レベル, ボーナス) を返す"""
    p = get_profile(guild_id, user_id)
    new_xp = p["xp"] + gain
    new_level = level_info(new_xp)[0]
    bonus = LEVEL_UP_BONUS * max(0, new_level - p["level"])
    db.execute(
        "UPDATE profiles SET xp=?, points=points+?, level=?, present_min=present_min+1, talk_min=talk_min+? "
        "WHERE guild_id=? AND user_id=?",
        (new_xp, gain + bonus, new_level, 1 if talking else 0, guild_id, user_id),
    )
    return (new_level, bonus) if new_level > p["level"] else None


# ================= 文字列まわり =================
JOIN_RE = re.compile(r"^\s*(?:<#(\d+)>|(#)?\s*(.+?))\s*に入れ\s*!*\s*$")
LEAVE_RE = re.compile(r"^\s*終われ\s*[!。]*\s*$")
JOIN_ONLY_RE = re.compile(r"^\s*(?:どこかに)?入れ\s*[!。]*\s*$")
STATUS_RE = re.compile(r"ステータス\s*オープン")
ANNOUNCE_RE = re.compile(r"^\s*(?:お知らせ|アナウンス)\s*チャンネル\s*(設定|変更|解除|ここ|確認)?\s*(?:<#(\d+)>)?\s*$")
RANKING_RE = re.compile(r"^\s*(?:ランキング|順位)\s*[!?]*\s*$")
ENTRY_RE = re.compile(r"^\s*入室メッセージ\s*(一覧|購入|買う|解放|設定|変更|オフ|off|ランダム)?\s*(\d+)?\s*[!]*\s*$", re.I)
SELF_WORDS = ("自分", "私", "わたし", "俺", "おれ", "僕", "ぼく")


def norm(s: str) -> str:
    return unicodedata.normalize("NFKC", s).strip().lower()


def simplify(s: str) -> str:
    return re.sub(r"[\W_]+", "", norm(s))


def fmt_dur(sec: float) -> str:
    sec = int(sec)
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    if h:
        return f"{h}時間{m}分"
    if m:
        return f"{m}分"
    return f"{s}秒"


def fmt_dt(ts: float) -> str:
    return datetime.fromtimestamp(ts, JST).strftime("%m/%d %H:%M")


def fmt_hm(ts: float) -> str:
    return datetime.fromtimestamp(ts, JST).strftime("%H:%M")


def bar(value: float, max_value: float, width: int = 10) -> str:
    n = round(width * value / max_value) if max_value > 0 else 0
    return "█" * n + "░" * (width - n)


PERIODS = {"all": "全期間", "today": "今日", "7d": "直近7日間", "30d": "直近30日間"}


def detect_period(text: str) -> str:
    if "今日" in text:
        return "today"
    if any(w in text for w in ("今週", "7日", "1週間", "一週間")):
        return "7d"
    if any(w in text for w in ("今月", "30日", "1ヶ月", "1か月", "一ヶ月")):
        return "30d"
    return "all"


def period_since(key: str, now: float) -> float:
    if key == "today":
        return datetime.fromtimestamp(now, JST).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    if key == "7d":
        return now - 7 * 86400
    if key == "30d":
        return now - 30 * 86400
    return 0.0


def target_users(message: discord.Message, content: str) -> list:
    ids = [i for i in message.raw_mentions if i != client.user.id]
    users = [m for m in message.mentions if m.id in ids and not m.bot]
    if not users and any(w in content for w in SELF_WORDS):
        users = [message.author]
    return users[:3]


def render_entry(text: str, name: str, vc: str, level: int) -> str:
    return text.format(name=name, vc=vc, level=level)


# ================= VC検索・接続 =================
targets: dict[int, int] = {}  # guild_id -> 居座っているVCの channel_id


def set_target(guild_id: int, channel_id: int | None):
    if channel_id is None:
        targets.pop(guild_id, None)
    else:
        targets[guild_id] = channel_id
    set_setting(guild_id, "sitting_channel_id", channel_id)


def can_send(guild: discord.Guild, channel) -> bool:
    if channel is None or not hasattr(channel, "send"):
        return False
    perms = channel.permissions_for(guild.me)
    return perms.view_channel and perms.send_messages


def announce_target(guild: discord.Guild, *fallbacks):
    """お知らせチャンネル → 指定された候補の順に、送れる最初のチャンネルを返す"""
    candidates = [guild.get_channel(get_setting(guild.id, "announce_channel_id") or 0), *fallbacks]
    for ch in candidates:
        if can_send(guild, ch):
            return ch
    return None


def voice_channels(guild: discord.Guild):
    return list(guild.voice_channels) + list(guild.stage_channels)


def find_voice_channels(guild: discord.Guild, name: str) -> list:
    channels = voice_channels(guild)
    hits = [c for c in channels if norm(c.name) == norm(name)]
    if hits:
        return hits
    key = simplify(name)
    if not key:
        return []
    hits = [c for c in channels if simplify(c.name) == key]
    if hits:
        return hits
    return [c for c in channels if key in simplify(c.name)]


async def ensure_connected(guild: discord.Guild, channel):
    vc = guild.voice_client
    if vc and vc.is_connected():
        if vc.channel.id != channel.id:
            await vc.move_to(channel)
    else:
        if vc:
            await vc.disconnect(force=True)
        await channel.connect(self_deaf=True, reconnect=True)


# ================= ステータス表示 =================
def build_status_embed(guild: discord.Guild, member: discord.abc.User, since: float, label: str) -> discord.Embed:
    g, u, now = guild.id, member.id, time.time()
    tracking_since = float(meta_get("tracking_since"))
    period_start = max(since, tracking_since)

    rows = db.execute(
        "SELECT channel_id, channel_name, start, end FROM sessions "
        "WHERE guild_id=? AND user_id=? AND COALESCE(end, ?) > ? ORDER BY start",
        (g, u, now, period_start),
    ).fetchall()
    sessions = [(cid, name, s, e, max(s, period_start), e if e is not None else now) for cid, name, s, e in rows]

    embed = discord.Embed(
        title=f"📊 {member.display_name} の通話ステータス",
        description=f"**{label}**（{fmt_dt(period_start)} 〜 現在）",
        color=0x5865F2,
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text=f"記録開始: {datetime.fromtimestamp(tracking_since, JST):%Y/%m/%d %H:%M} ／ botが止まっていた間は記録されません ／ 時刻はJST")

    # --- レベル（累計） ---
    p = get_profile(g, u)
    lv, cur, need = level_info(p["xp"])
    sel = p["entry_msg"]
    sel_text = "オフ" if sel == SELECT_OFF else "ランダム" if sel == SELECT_RANDOM else f"#{sel:02d}"
    embed.add_field(
        name=f"🏅 Lv.{lv}",
        value=(
            f"`{bar(cur, need, 12)}` {cur}/{need} XP\n"
            f"累計 **{p['xp']} XP** ／ 所持 **{p['points']} pt**\n"
            f"在室 {fmt_dur(p['present_min'] * 60)} ／ 会話 {fmt_dur(p['talk_min'] * 60)} ／ 入室メッセージ {sel_text}"
        ),
        inline=False,
    )

    # --- 現在の状態 ---
    current = db.execute(
        "SELECT channel_id, channel_name, start FROM sessions WHERE guild_id=? AND user_id=? AND end IS NULL",
        (g, u),
    ).fetchone()
    if current:
        ch = guild.get_channel(current[0])
        ch_text = ch.mention if ch else f"#{current[1]}"
        now_text = f"🟢 {ch_text} で通話中（{fmt_hm(current[2])}〜、{fmt_dur(now - current[2])}経過）"
    else:
        last = db.execute("SELECT MAX(end) FROM sessions WHERE guild_id=? AND user_id=?", (g, u)).fetchone()[0]
        now_text = f"⚪ 通話していません（最後の通話: {fmt_dt(last)}）" if last else "⚪ 通話していません（記録なし）"
    embed.add_field(name="現在", value=now_text, inline=False)

    if not sessions:
        embed.add_field(name="記録", value="この期間の通話記録はありません。", inline=False)
        db.commit()
        return embed

    durations = [e - s for *_, s, e in sessions]
    total = sum(durations)
    counts = dict(db.execute(
        "SELECT kind, COUNT(*) FROM events WHERE guild_id=? AND user_id=? AND ts>=? GROUP BY kind",
        (g, u, period_start),
    ).fetchall())

    embed.add_field(name="合計通話時間", value=fmt_dur(total), inline=True)
    embed.add_field(
        name="入室 / 退出 / VC移動",
        value=f"{counts.get('join', 0)}回 / {counts.get('leave', 0)}回 / {counts.get('move', 0)}回",
        inline=True,
    )
    embed.add_field(
        name="セッション",
        value=f"{len(sessions)}回（平均 {fmt_dur(total / len(sessions))}・最長 {fmt_dur(max(durations))}）",
        inline=True,
    )
    embed.add_field(
        name="画面共有 / カメラ / ミュート",
        value=f"{counts.get('stream', 0)}回 / {counts.get('video', 0)}回 / {counts.get('mute', 0)}回",
        inline=True,
    )
    embed.add_field(name="初回 / 最終の入室", value=f"{fmt_dt(sessions[0][2])} / {fmt_dt(sessions[-1][2])}", inline=True)

    by_hour = [0.0] * 24
    by_wday = [0.0] * 7
    for *_, s, e in sessions:
        t = s
        while t < e:
            dt = datetime.fromtimestamp(t, JST)
            next_hour = (dt.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)).timestamp()
            seg_end = min(e, next_hour)
            by_hour[dt.hour] += seg_end - t
            by_wday[dt.weekday()] += seg_end - t
            t = seg_end

    blocks = [sum(by_hour[i:i + 3]) for i in range(0, 24, 3)]
    peak_hour = max(range(24), key=lambda h: by_hour[h])
    lines = [f"{i * 3:02d}-{i * 3 + 3:02d}時 {bar(v, max(blocks))} {fmt_dur(v) if v else '-'}" for i, v in enumerate(blocks)]
    embed.add_field(name=f"時間帯別（いちばん多いのは {peak_hour}時台）", value="```\n" + "\n".join(lines) + "\n```", inline=False)

    wdays = "月火水木金土日"
    lines = [f"{wdays[i]} {bar(v, max(by_wday))} {fmt_dur(v) if v else '-'}" for i, v in enumerate(by_wday)]
    embed.add_field(name="曜日別", value="```\n" + "\n".join(lines) + "\n```", inline=False)

    per_ch: dict[int, list] = {}
    for cid, name, _s0, _e0, s, e in sessions:
        entry = per_ch.setdefault(cid, [name, 0.0])
        entry[0] = name
        entry[1] += e - s
    lines = []
    for cid, (name, sec) in sorted(per_ch.items(), key=lambda x: -x[1][1])[:3]:
        ch = guild.get_channel(cid)
        lines.append(f"{ch.mention if ch else '#' + name} — {fmt_dur(sec)}（{sec / total:.0%}）")
    embed.add_field(name="よく使うVC", value="\n".join(lines), inline=False)

    together: dict[int, float] = {}
    for cid, _name, _s0, _e0, s, e in sessions:
        others = db.execute(
            "SELECT user_id, start, COALESCE(end, ?) FROM sessions "
            "WHERE guild_id=? AND channel_id=? AND user_id!=? AND start < ? AND COALESCE(end, ?) > ?",
            (now, g, cid, u, e, now, s),
        ).fetchall()
        for ou, os_, oe in others:
            overlap = min(e, oe) - max(s, os_)
            if overlap > 0:
                together[ou] = together.get(ou, 0.0) + overlap
    top_people = sorted(together.items(), key=lambda x: -x[1])[:3]
    embed.add_field(
        name="よく一緒にいた人",
        value="\n".join(f"<@{ou}> — {fmt_dur(sec)}" for ou, sec in top_people) or "ずっと1人でした",
        inline=False,
    )

    lines = []
    for cid, name, s0, e0, s, e in reversed(sessions[-5:]):
        ch = guild.get_channel(cid)
        ch_text = ch.mention if ch else f"#{name}"
        end_text = fmt_hm(e0) if e0 is not None else "通話中"
        lines.append(f"{fmt_dt(s0)}〜{end_text}（{fmt_dur(e - s)}） {ch_text}")
    embed.add_field(name="最近の通話（新しい順）", value="\n".join(lines), inline=False)

    db.commit()
    return embed


def build_history_csv(guild: discord.Guild, user: discord.abc.User) -> discord.File:
    rows = db.execute(
        "SELECT start, end, channel_name FROM sessions WHERE guild_id=? AND user_id=? ORDER BY start",
        (guild.id, user.id),
    ).fetchall()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["開始(JST)", "終了(JST)", "VC", "時間(分)"])
    now = time.time()
    for s, e, name in rows:
        end = e if e is not None else now
        w.writerow([
            datetime.fromtimestamp(s, JST).strftime("%Y-%m-%d %H:%M:%S"),
            datetime.fromtimestamp(e, JST).strftime("%Y-%m-%d %H:%M:%S") if e is not None else "通話中",
            name,
            round((end - s) / 60, 1),
        ])
    data = buf.getvalue().encode("utf-8-sig")  # Excelで文字化けしないようにBOM付き
    return discord.File(io.BytesIO(data), filename=f"voice_history_{user.id}.csv")


def build_ranking_embed(guild: discord.Guild) -> discord.Embed:
    rows = db.execute(
        "SELECT user_id, xp, talk_min FROM profiles WHERE guild_id=? AND xp>0 ORDER BY xp DESC LIMIT 10",
        (guild.id,),
    ).fetchall()
    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for i, (uid, xp, talk) in enumerate(rows):
        head = medals[i] if i < 3 else f"`{i + 1:>2}.`"
        lines.append(f"{head} <@{uid}> — Lv.{level_info(xp)[0]}（{xp} XP・会話 {fmt_dur(talk * 60)}）")
    return discord.Embed(
        title="🏆 通話ランキング（経験値）",
        description="\n".join(lines) or "まだ誰も経験値を持っていません。",
        color=0xF1C40F,
    )


def build_entry_list_embed(guild: discord.Guild, member: discord.Member) -> discord.Embed:
    p = get_profile(guild.id, member.id)
    owned = owned_messages(guild.id, member.id)
    lv = level_info(p["xp"])[0]
    sel = p["entry_msg"]
    sel_text = "オフ" if sel == SELECT_OFF else "ランダム" if sel == SELECT_RANDOM else f"#{sel:02d}"
    embed = discord.Embed(
        title="🚪 入室メッセージ一覧",
        description=(
            f"{member.display_name} の所持ポイント: **{p['points']} pt** ／ 現在の設定: **{sel_text}**\n"
            "✅ 解放済み　⭐ 使用中　🔒 未解放\n"
            "`入室メッセージ購入 番号` で解放、`入室メッセージ設定 番号` で変更"
        ),
        color=0x2ECC71,
    )
    name = discord.utils.escape_markdown(member.display_name)
    for price, title in TIERS:
        lines = []
        for mid, mprice, text in ENTRY_MESSAGES:
            if mprice != price:
                continue
            mark = "⭐" if mid == sel else "✅" if mid in owned else "🔒"
            lines.append(f"{mark} `{mid:02d}` {render_entry(text, name, 'VC', lv)}")
        embed.add_field(name=title, value="\n".join(lines), inline=False)
    db.commit()
    return embed


async def handle_entry_command(message: discord.Message, action: str | None, num: str | None):
    g, u = message.guild.id, message.author.id
    action = (action or "").lower()

    if action in ("", "一覧"):
        await message.reply(embed=build_entry_list_embed(message.guild, message.author), mention_author=False)
        return

    if action in ("オフ", "off"):
        get_profile(g, u)
        db.execute("UPDATE profiles SET entry_msg=? WHERE guild_id=? AND user_id=?", (SELECT_OFF, g, u))
        db.commit()
        await message.reply("🔕 入室メッセージをオフにしました。")
        return

    if action == "ランダム":
        get_profile(g, u)
        db.execute("UPDATE profiles SET entry_msg=? WHERE guild_id=? AND user_id=?", (SELECT_RANDOM, g, u))
        db.commit()
        await message.reply(f"🎲 解放済みの {len(owned_messages(g, u))} 種類からランダムに表示します。")
        return

    if num is None or int(num) not in MSG_BY_ID:
        await message.reply(f"番号は 1〜{len(ENTRY_MESSAGES)} で指定してください（例: `入室メッセージ{action} 12`）。")
        return
    mid = int(num)
    price, text = MSG_BY_ID[mid]
    p = get_profile(g, u)
    owned = owned_messages(g, u)
    preview = render_entry(text, discord.utils.escape_markdown(message.author.display_name), "VC", level_info(p["xp"])[0])

    if action in ("購入", "買う", "解放"):
        if mid in owned:
            await message.reply(f"#{mid:02d} はもう解放済みです。`入室メッセージ設定 {mid}` で使えます。")
            return
        if p["points"] < price:
            await message.reply(f"ポイントが足りません。#{mid:02d} は **{price} pt**、所持は **{p['points']} pt**（あと {price - p['points']} pt）です。")
            return
        db.execute("UPDATE profiles SET points=points-?, entry_msg=? WHERE guild_id=? AND user_id=?", (price, mid, g, u))
        db.execute("INSERT INTO unlocks(guild_id, user_id, msg_id, ts) VALUES(?, ?, ?, ?)", (g, u, mid, time.time()))
        db.commit()
        await message.reply(f"🔓 #{mid:02d} を解放して設定しました！（残り {p['points'] - price} pt）\n> {preview}")
        return

    # 設定・変更
    if mid not in owned:
        await message.reply(f"#{mid:02d} はまだ解放されていません（**{price} pt**）。`入室メッセージ購入 {mid}` で解放できます。")
        return
    db.execute("UPDATE profiles SET entry_msg=? WHERE guild_id=? AND user_id=?", (mid, g, u))
    db.commit()
    await message.reply(f"✅ 入室メッセージを #{mid:02d} にしました。\n> {preview}")


# ================= 定期処理 =================
started = False


def award_tick() -> list:
    """1分ごとにXP・ポイントを配る。レベルアップの通知内容を返す"""
    level_ups = []
    for guild in client.guilds:
        afk_id = guild.afk_channel.id if guild.afk_channel else None
        for ch in voice_channels(guild):
            if ch.id == afk_id:
                continue
            humans = [m for m in ch.members if not m.bot]
            for m in humans:
                vs = m.voice
                if vs is None or vs.self_deaf or vs.deaf:
                    continue
                talking = not vs.self_mute and not vs.mute and len(humans) >= 2
                result = add_xp(guild.id, m.id, XP_PRESENT + (XP_TALK if talking else 0), talking)
                if result:
                    level_ups.append((ch, m, *result))
    db.commit()
    return level_ups


async def background_loop():
    while True:
        await asyncio.sleep(60)
        try:
            meta_set("heartbeat", str(time.time()))
            for vc_ch, member, new_level, bonus in award_tick():
                ch = announce_target(member.guild, vc_ch)
                if ch is None:
                    continue
                try:
                    await ch.send(
                        f"🎉 {discord.utils.escape_markdown(member.display_name)} が **Lv.{new_level}** になりました！（+{bonus} pt）",
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                except discord.HTTPException:
                    pass
        except Exception as e:
            print(f"定期処理でエラー: {e}")


def reconcile_on_startup():
    now = time.time()
    last = float(meta_get("heartbeat") or now)
    db.execute("UPDATE sessions SET end = MAX(start, ?) WHERE end IS NULL", (last,))
    for guild in client.guilds:
        for ch in voice_channels(guild):
            for m in ch.members:
                if not m.bot:
                    open_session(guild.id, m.id, ch, now)
    meta_set("heartbeat", str(now))
    db.commit()


@client.event
async def on_ready():
    global started
    print(f"ログインしました: {client.user} (ID: {client.user.id}) / DB: {DB_PATH} / PID: {os.getpid()} / "
          f"サービス: {os.environ.get('RAILWAY_SERVICE_NAME', 'ローカル')} / レプリカ: {os.environ.get('RAILWAY_REPLICA_ID', '-')}")
    if started:
        return
    started = True
    reconcile_on_startup()
    client.loop.create_task(background_loop())
    # 前回居座っていたのに再起動したサーバーに案内を出す
    for guild in client.guilds:
        cid = get_setting(guild.id, "sitting_channel_id")
        if cid:
            set_target(guild.id, None)
            channel = guild.get_channel(cid)
            if channel:
                await send_disconnect_notice(guild, channel, "botが再起動したため、VCから抜けました。")


# ================= メッセージ =================
@client.event
async def on_message(message: discord.Message):
    if message.author.bot or message.guild is None:
        return

    guild = message.guild
    content = unicodedata.normalize("NFKC", message.content)

    if STATUS_RE.search(content):
        users = target_users(message, content)
        period = detect_period(content)
        if not users:
            view = StatusView(guild, None, period)
            view.message = await message.reply("👤 誰のステータスを見ますか？下のメニューから選んでください。", view=view, mention_author=False)
            return
        for user in users:
            view = StatusView(guild, user, period)
            view.message = await message.reply(embed=view.make_embed(), view=view, mention_author=False)
        return

    if JOIN_ONLY_RE.match(content):
        await show_join_menu(message)
        return

    if "履歴" in content and any(w in content.lower() for w in ("保存", "csv", "ダウンロード")):
        users = target_users(message, content)
        if users:
            files = [build_history_csv(guild, u) for u in users]
            names = "、".join(u.display_name for u in users)
            await message.reply(f"📁 {names} の通話履歴です。", files=files, mention_author=False)
            return

    if RANKING_RE.match(content):
        await message.reply(embed=build_ranking_embed(guild), mention_author=False)
        return

    m = ENTRY_RE.match(content)
    if m:
        await handle_entry_command(message, m.group(1), m.group(2))
        return

    m = ANNOUNCE_RE.match(content)
    if m:
        await handle_announce_command(message, m.group(1), m.group(2))
        return

    if LEAVE_RE.match(content):
        set_target(guild.id, None)
        vc = guild.voice_client
        if vc:
            await vc.disconnect(force=True)
            await message.reply("👋 退出しました。")
        else:
            await message.reply("今はどのVCにも入っていません。")
        return

    m = JOIN_RE.match(content)
    if m:
        await handle_join(message, m.group(1), m.group(2), m.group(3))


async def handle_join(message: discord.Message, mention_id, has_hash, name):
    guild = message.guild
    if mention_id:
        ch = guild.get_channel(int(mention_id))
        candidates = [ch] if isinstance(ch, (discord.VoiceChannel, discord.StageChannel)) else []
    else:
        candidates = find_voice_channels(guild, name)

    if not candidates:
        if not has_hash and not mention_id:
            return
        names = "、".join(f"`{c.name}`" for c in voice_channels(guild)) or "（見えるVCがありません）"
        await message.reply(f"そのボイスチャンネルが見つかりませんでした。\n入れるVC: {names}")
        return
    if len(candidates) > 1:
        names = "、".join(c.mention for c in candidates)
        await message.reply(f"候補が複数あります。どれか1つを指定してください: {names}")
        return

    await message.reply(await join_channel(guild, candidates[0], message.channel))


def can_join(guild: discord.Guild, channel) -> bool:
    perms = channel.permissions_for(guild.me)
    return perms.view_channel and perms.connect


async def join_channel(guild: discord.Guild, channel, notify=None) -> str:
    """VCに入って、結果の文章を返す"""
    if not can_join(guild, channel):
        return f"{channel.mention} に入る権限がありません。"
    if notify is not None:
        set_setting(guild.id, "notify_channel_id", notify.id)
    set_target(guild.id, channel.id)
    try:
        await ensure_connected(guild, channel)
    except Exception as e:
        set_target(guild.id, None)
        return f"接続に失敗しました: `{e}`"
    return f"🔊 {channel.mention} に入りました！「終われ」と言うまで居座ります。"


# ================= メニュー（ボタン・選択肢） =================
class JoinView(discord.ui.View):
    """「入れ」だけ言われたときのVC選択メニュー"""

    def __init__(self, guild: discord.Guild, author: discord.Member):
        super().__init__(timeout=300)
        self.guild = guild
        self.message: discord.Message | None = None

        author_vc = author.voice.channel if author.voice else None
        bot_vc = guild.voice_client.channel if guild.voice_client else None
        channels = [c for c in voice_channels(guild) if can_join(guild, c)]
        # あなたのいるVC → 人が多いVC → サーバーでの並び順
        channels.sort(key=lambda c: (c != author_vc, -sum(not m.bot for m in c.members), c.position))
        self.truncated = len(channels) > 25
        self.count = len(channels)

        options = []
        for c in channels[:25]:
            n = sum(not m.bot for m in c.members)
            if c == author_vc:
                desc = f"あなたが今いるVC（{n}人）"
            elif c == bot_vc:
                desc = f"botが今いるVC（{n}人）"
            else:
                desc = f"{n}人が通話中" if n else "誰もいません"
            options.append(discord.SelectOption(label=c.name[:100], value=str(c.id), description=desc, emoji="🔊"))

        if options:
            select = discord.ui.Select(placeholder="🔊 入るVCを選んでください", options=options)
            select.callback = self.on_select
            self.add_item(select)
        cancel = discord.ui.Button(label="やめる", style=discord.ButtonStyle.secondary)
        cancel.callback = self.on_cancel
        self.add_item(cancel)

    async def on_select(self, interaction: discord.Interaction):
        channel = self.guild.get_channel(int(interaction.data["values"][0]))
        if channel is None:
            await interaction.response.edit_message(content="そのVCは見つかりませんでした。", view=None)
            return
        self.stop()
        await interaction.response.edit_message(content=f"⏳ {channel.mention} に接続中…", view=None)
        result = await join_channel(self.guild, channel, interaction.channel)
        await interaction.edit_original_response(content=f"{result}（選んだ人: {interaction.user.display_name}）")

    async def on_cancel(self, interaction: discord.Interaction):
        self.stop()
        await interaction.response.edit_message(content="キャンセルしました。", view=None)

    async def on_timeout(self):
        if self.message:
            try:
                await self.message.edit(content="⌛ 時間切れです。もう一度「入れ」と送ってください。", view=None)
            except discord.HTTPException:
                pass


async def show_join_menu(message: discord.Message):
    view = JoinView(message.guild, message.author)
    if view.count == 0:
        await message.reply("botが入れるVCがありません（権限を確認してください）。")
        return
    text = "🔊 どのVCに入りますか？"
    if view.truncated:
        text += f"\n（VCが {view.count} 個あるので、上位25個を表示しています。ほかのVCは `#VC名に入れ！` で指定してください）"
    view.message = await message.reply(text, view=view, mention_author=False)


class StatusView(discord.ui.View):
    """ステータス表示の期間ボタンと、人の切り替えメニュー"""

    def __init__(self, guild: discord.Guild, user, period: str = "all"):
        super().__init__(timeout=900)
        self.guild = guild
        self.user = user
        self.period = period
        self.message: discord.Message | None = None
        self.period_buttons: dict[str, discord.ui.Button] = {}

        for key, label in PERIODS.items():
            button = discord.ui.Button(label=label, row=0)
            button.callback = self.make_period_callback(key)
            self.period_buttons[key] = button
            self.add_item(button)

        picker = discord.ui.UserSelect(
            placeholder="👤 見る人を選ぶ" if user is None else "👤 ほかの人のステータスを見る",
            min_values=1, max_values=1, row=1,
        )
        picker.callback = self.on_pick
        self.picker = picker
        self.add_item(picker)
        self.refresh_buttons()

    def refresh_buttons(self):
        for key, button in self.period_buttons.items():
            button.style = discord.ButtonStyle.primary if key == self.period else discord.ButtonStyle.secondary

    def make_embed(self) -> discord.Embed:
        since = period_since(self.period, time.time())
        return build_status_embed(self.guild, self.user, since, PERIODS[self.period])

    def make_period_callback(self, key: str):
        async def callback(interaction: discord.Interaction):
            self.period = key
            self.refresh_buttons()
            if self.user is None:
                await interaction.response.edit_message(
                    content=f"📅 期間を「{PERIODS[key]}」にしました。👤 誰のステータスを見ますか？", view=self
                )
            else:
                await interaction.response.edit_message(embed=self.make_embed(), view=self)
        return callback

    async def on_pick(self, interaction: discord.Interaction):
        user = self.picker.values[0]
        if user.bot:
            await interaction.response.send_message("botの通話は記録していません。人を選んでください。", ephemeral=True)
            return
        self.user = user
        self.picker.placeholder = "👤 ほかの人のステータスを見る"
        await interaction.response.edit_message(content=None, embed=self.make_embed(), view=self)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


# ================= VCの出入り =================
last_entry_msg: dict[tuple[int, int], float] = {}


async def send_entry_message(member: discord.Member, channel):
    key = (member.guild.id, member.id)
    now = time.time()
    if now - last_entry_msg.get(key, 0) < ENTRY_COOLDOWN:
        return

    p = get_profile(member.guild.id, member.id)
    sel = p["entry_msg"]
    if sel == SELECT_OFF:
        db.commit()
        return
    owned = owned_messages(member.guild.id, member.id)
    if sel == SELECT_RANDOM or sel not in owned:
        sel = random.choice(sorted(owned))
    db.commit()

    text = render_entry(
        MSG_BY_ID[sel][1],
        discord.utils.escape_markdown(member.display_name),
        discord.utils.escape_markdown(channel.name),
        level_info(p["xp"])[0],
    )
    last_entry_msg[key] = now
    try:
        await channel.send(text, allowed_mentions=discord.AllowedMentions.none())
    except discord.HTTPException as e:
        print(f"入室メッセージを送れませんでした（{channel.name}）: {e}")


def log_voice_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    now = time.time()
    g, u = member.guild.id, member.id
    b, a = before.channel, after.channel

    if (b.id if b else None) != (a.id if a else None):
        if b:
            close_session(g, u, now)
        if a:
            open_session(g, u, a, now)
        kind = "join" if b is None else "leave" if a is None else "move"
        add_event(g, u, kind, now)
        if kind == "join":
            client.loop.create_task(send_entry_message(member, a))
    elif a:
        if after.self_stream and not before.self_stream:
            add_event(g, u, "stream", now)
        if after.self_video and not before.self_video:
            add_event(g, u, "video", now)
        if after.self_mute and not before.self_mute:
            add_event(g, u, "mute", now)
    db.commit()


class RejoinView(discord.ui.View):
    """切断の案内に付ける「もう一度入れる」ボタン"""

    def __init__(self, guild: discord.Guild, channel):
        super().__init__(timeout=3600)
        self.guild = guild
        self.channel = channel
        self.message: discord.Message | None = None
        button = discord.ui.Button(label=f"🔊 {channel.name[:60]} にもう一度入れる", style=discord.ButtonStyle.primary)
        button.callback = self.on_rejoin
        self.add_item(button)

    async def on_rejoin(self, interaction: discord.Interaction):
        self.stop()
        await interaction.response.edit_message(view=None)
        result = await join_channel(self.guild, self.channel, interaction.channel)
        await interaction.followup.send(f"{result}（入れた人: {interaction.user.display_name}）")

    async def on_timeout(self):
        if self.message:
            try:
                await self.message.edit(view=None)
            except discord.HTTPException:
                pass


async def send_disconnect_notice(guild: discord.Guild, channel, reason: str):
    """VCにいる人に@して、入れ直しを案内する"""
    humans = [m for m in channel.members if not m.bot]
    notify = guild.get_channel(get_setting(guild.id, "notify_channel_id") or 0)
    ch = announce_target(guild, notify, channel)
    if ch is None:
        print(f"[{guild.name}] 案内を送れるチャンネルがありません: {reason}")
        return
    mentions = " ".join(m.mention for m in humans)
    text = (
        f"{mentions}\n" if mentions else ""
    ) + (
        f"⚠️ {reason}\n"
        f"居座りを止めています。{channel.mention} にもう一度入れるには、下のボタンを押すか、"
        f"`#{channel.name}に入れ！` または `入れ` と送ってください。"
    )
    view = RejoinView(guild, channel)
    try:
        view.message = await ch.send(text, view=view, allowed_mentions=discord.AllowedMentions(users=True))
    except discord.HTTPException as e:
        print(f"[{guild.name}] 案内の送信に失敗: {e}")


checking: set[int] = set()


async def check_bot_voice(guild: discord.Guild):
    """botのVC状態が変わったとき、本当に切断されたのかを確かめる"""
    target_id = targets.get(guild.id)
    if target_id is None or guild.id in checking:
        return
    checking.add(guild.id)
    try:
        # 一時的な切断なら discord.py が自動で繋ぎ直すので、少し待つ
        for _ in range(6):
            await asyncio.sleep(1.5)
            if targets.get(guild.id) != target_id:
                return  # 待っている間に「終われ」や別のVC指定があった
            vc = guild.voice_client
            if vc and vc.is_connected() and vc.channel and vc.channel.id == target_id:
                return  # 元のVCに戻れている

        vc = guild.voice_client
        if vc and vc.is_connected() and vc.channel and vc.channel.id != target_id:
            # 別のVCに移動させられた → そこに居座る
            old = guild.get_channel(target_id)
            set_target(guild.id, vc.channel.id)
            ch = announce_target(guild, vc.channel)
            if ch:
                try:
                    await ch.send(f"🔀 botが {old.mention if old else 'VC'} から {vc.channel.mention} に移動させられたので、ここに居座ります。")
                except discord.HTTPException:
                    pass
            return

        # 切断された → 止まって案内する
        set_target(guild.id, None)
        if vc:
            try:
                await vc.disconnect(force=True)
            except Exception:
                pass
        channel = guild.get_channel(target_id)
        print(f"[{guild.name}] VCから切断されたので居座りを止めました")
        if channel:
            await send_disconnect_notice(guild, channel, "botがVCから切断されました。")
    finally:
        checking.discard(guild.id)


async def handle_announce_command(message: discord.Message, action: str | None, channel_id: str | None):
    guild = message.guild
    current = guild.get_channel(get_setting(guild.id, "announce_channel_id") or 0)
    current_text = current.mention if current else "未設定（VCのチャットや、居座りを指示したチャンネルに送ります）"

    if (action in (None, "確認", "設定", "変更")) and channel_id is None:
        view = AnnounceView(guild)
        view.message = await message.reply(
            f"📢 今のお知らせチャンネル: {current_text}\n変更する場合は下のメニューから選んでください（サーバー管理権限が必要です）。",
            view=view, mention_author=False,
        )
        return

    if not message.author.guild_permissions.manage_guild:
        await message.reply("お知らせチャンネルの変更には「サーバー管理」権限が必要です。")
        return

    if action == "解除":
        set_setting(guild.id, "announce_channel_id", None)
        await message.reply("📢 お知らせチャンネルを解除しました。")
        return

    channel = message.channel if action == "ここ" else guild.get_channel(int(channel_id))
    await message.reply(apply_announce_channel(guild, channel))


def apply_announce_channel(guild: discord.Guild, channel) -> str:
    if channel is None:
        return "そのチャンネルが見つかりませんでした。"
    if not can_send(guild, channel):
        return f"{channel.mention} にはbotがメッセージを送れません。権限を確認してください。"
    set_setting(guild.id, "announce_channel_id", channel.id)
    return f"📢 お知らせチャンネルを {channel.mention} にしました。"


class AnnounceView(discord.ui.View):
    def __init__(self, guild: discord.Guild):
        super().__init__(timeout=300)
        self.guild = guild
        self.message: discord.Message | None = None
        select = discord.ui.ChannelSelect(
            placeholder="📢 お知らせを送るチャンネルを選ぶ",
            channel_types=[discord.ChannelType.text, discord.ChannelType.news],
        )
        select.callback = self.on_select
        self.select = select
        self.add_item(select)
        clear = discord.ui.Button(label="解除する", style=discord.ButtonStyle.secondary)
        clear.callback = self.on_clear
        self.add_item(clear)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.guild_permissions.manage_guild:
            return True
        await interaction.response.send_message("変更には「サーバー管理」権限が必要です。", ephemeral=True)
        return False

    async def on_select(self, interaction: discord.Interaction):
        channel = self.guild.get_channel(self.select.values[0].id)
        self.stop()
        await interaction.response.edit_message(content=apply_announce_channel(self.guild, channel), view=None)

    async def on_clear(self, interaction: discord.Interaction):
        set_setting(self.guild.id, "announce_channel_id", None)
        self.stop()
        await interaction.response.edit_message(content="📢 お知らせチャンネルを解除しました。", view=None)

    async def on_timeout(self):
        if self.message:
            try:
                await self.message.edit(view=None)
            except discord.HTTPException:
                pass


@client.event
async def on_voice_state_update(member: discord.Member, before, after):
    if client.user and member.id == client.user.id:
        target_id = targets.get(member.guild.id)
        if target_id is not None and (after.channel is None or after.channel.id != target_id):
            client.loop.create_task(check_bot_voice(member.guild))
        return
    if member.bot:
        return
    log_voice_update(member, before, after)


client.run(TOKEN)

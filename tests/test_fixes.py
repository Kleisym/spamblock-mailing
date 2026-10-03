# -*- coding: utf-8 -*-
"""
Regression suite for the five reported defects.

Each test names the symptom it locks down. They run against the real bot
module, not mocks, so a scorer regression fails here before it ships.
"""
import asyncio
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bot
from telethon.tl.functions.messages import GetDialogFiltersRequest
from telethon.tl.types import InputPeerChannel, InputPeerUser, TextWithEntities
from bot import (
    BroadcasterService,
    calculate_spam_score,
    is_peer_muted,
    mute_cache_rebuild,
    should_auto_read,
)


class TestOrdinaryRepliesAreNotCleared(unittest.TestCase):
    """Defect 1: pings were removed from normal replies, not just ads."""
    # A structured, multi-block catalogue ad. It carries no watermark
    # signature, so it is spam only by accumulated layout evidence, and
    # the ping gate is what decides whether we are allowed to touch it.
    AD_TEXT = (
        "🔥 \u0413\u041e\u0420\u042f\u0427\u0415\u0415\n"
        "1\ufe0f\u20e3 \u0422\u0435\u043b\u0435\u0433\u0440\u0430\u043c\u043c \u0430\u043a\u043a\u0430\u0443\u043d\u0442\u044b\n"
        "2\ufe0f\u20e3 \u0418\u043d\u0441\u0442\u0430\u0433\u0440\u0430\u043c \u0430\u043a\u043a\u0430\u0443\u043d\u0442\u044b\n"
        "3\ufe0f\u20e3 \u0410\u043a\u043a\u0430\u0443\u043d\u0442\u044b \u0421\u0428\u0410\n"
        "\u0426\u0435\u043d\u044b \u0432\u043d\u0438\u0437\u0443"
    )

    def test_ordinary_reply_without_ping_is_not_cleared(self):
        # The reported "ping removed from a normal reply" defect: ad copy that
        # never actually reached us must be left completely alone.
        self.assertFalse(should_auto_read(self.AD_TEXT, is_ping=False))

    def test_ad_ping_is_still_cleared(self):
        self.assertTrue(should_auto_read(self.AD_TEXT, is_ping=True))

    def test_hard_signature_still_clears_without_ping(self):
        # An unmistakable watermark is cleared even when the ping gate says no.
        ad = "Отправлено с помощью @Jrvusvu"
        self.assertTrue(should_auto_read(ad, is_ping=False))

    def test_plain_chatter_is_never_cleared(self):
        for text in (
            "окей, понял тебя",
            "а сколько стоит?",
            "буду через час",
            "спасибо за инфу",
            "а у тебя есть аккаунты?",
        ):
            self.assertFalse(should_auto_read(text, is_ping=True), text)
            self.assertFalse(should_auto_read(text, is_ping=False), text)

    def test_dampeners_are_capped_at_25_each(self):
        text = "привет, а где канал? t.me/some_channel"
        score, _trigger, factors = calculate_spam_score(text)
        self.assertGreaterEqual(score, 0)
        for factor in factors:
            if factor.startswith("question_mark") or factor.startswith("short_single_line"):
                self.assertIn("-25", factor)

    def test_buyer_question_always_vetoes(self):
        self.assertFalse(should_auto_read("продашь рекламу?", is_ping=True))
        self.assertFalse(should_auto_read("почем пост?", is_ping=True))

    def test_hard_spam_clears_even_without_ping(self):
        self.assertTrue(should_auto_read("Отправлено с помощью @Jrvusvu", is_ping=False))
        self.assertTrue(should_auto_read("привет\u200b@channel", is_ping=False))

    def test_legacy_call_signature_still_works(self):
        self.assertTrue(should_auto_read("Отправлено с помощью @Jrvusvu"))


class TestFolderBroadcastsPickUpNewChats(unittest.TestCase):
    """Defect 2: folders joined after a broadcast started never enrolled."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="folder_sync_")
        self.db = bot.Database(Path(self.tmp) / "bot.db")
        self._orig_db = bot.db
        bot.db = self.db

    def tearDown(self):
        bot.db = self._orig_db
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_register_folder_broadcast_persists(self):
        self.db.register_folder_broadcast(
            folder_name="Моя папка",
            template_name="promo",
            mode="interval",
            interval_seconds=60,
            account_id=42,
        )
        rows = self.db.get_active_folder_broadcasts(account_id=42)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["folder_name"], "Моя папка")
        self.assertEqual(rows[0]["template_name"], "promo")
        self.assertEqual(rows[0]["interval_seconds"], 60)

    def test_register_is_idempotent_not_duplicated(self):
        for _ in range(3):
            self.db.register_folder_broadcast(
                folder_name="F", template_name="T", mode="interval",
                interval_seconds=30, account_id=7,
            )
        self.assertEqual(len(self.db.get_active_folder_broadcasts(account_id=7)), 1)

    def test_new_chat_in_folder_gets_a_task(self):
        self.db.save_template("promo", "text", [], [])
        self.db.register_folder_broadcast(
            folder_name="F", template_name="promo", mode="interval",
            interval_seconds=30, account_id=1,
        )
        self.db.add_or_update_broadcast(chat_id=100, template_name="promo",
                                        mode="interval", interval_seconds=30,
                                        folder_name="F", account_id=1)
        before = {t["chat_id"] for t in self.db.get_active_interval_tasks(account_id=1)}
        self.assertEqual(before, {100})

        self.db.add_or_update_broadcast(chat_id=200, template_name="promo",
                                        mode="interval", interval_seconds=30,
                                        folder_name="F", account_id=1)
        after = {t["chat_id"] for t in self.db.get_active_interval_tasks(account_id=1)}
        self.assertEqual(after, {100, 200})

    def test_stop_pauses_folder_registry(self):
        self.db.register_folder_broadcast(
            folder_name="F", template_name="promo", mode="interval",
            interval_seconds=30, account_id=1,
        )
        self.db.set_folder_broadcast_status(folder_name="F", name="promo",
                                            is_active=0, account_id=1)
        self.assertEqual(self.db.get_active_folder_broadcasts(account_id=1), [])
        self.db.set_folder_broadcast_status(folder_name="F", name="promo",
                                            is_active=1, account_id=1)
        self.assertEqual(len(self.db.get_active_folder_broadcasts(account_id=1)), 1)

    def test_delete_template_clears_folder_registry(self):
        self.db.register_folder_broadcast(
            folder_name="F", template_name="promo", mode="interval",
            interval_seconds=30, account_id=1,
        )
        self.db.delete_template("promo")
        self.assertEqual(self.db.get_active_folder_broadcasts(account_id=1), [])


class TestSyncFolderBroadcasts(unittest.TestCase):
    """End-to-end reconciliation against a fake client."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sync_")
        self.db = bot.Database(Path(self.tmp) / "bot.db")
        self._orig_db = bot.db
        bot.db = self.db
        self.client = _FakeClient(folders={"Папка": [10, 20]})
        self.service = BroadcasterService(self.client, account_id=5)
        self.service._folder_cache.clear()

    def tearDown(self):
        bot.db = self._orig_db
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self):
        return asyncio.run(self.service.sync_folder_broadcasts())

    def test_new_chat_joins_running_broadcast(self):
        self.db.save_template("promo", "hi", [], [])
        self.db.register_folder_broadcast(
            folder_name="Папка", template_name="promo", mode="interval",
            interval_seconds=30, account_id=5,
        )
        enrolled = self._run()
        self.assertEqual(enrolled, 2)
        active = {t["chat_id"] for t in self.db.get_active_interval_tasks(account_id=5)}
        self.assertEqual(active, {10, 20})

        self.client.folders["Папка"].append(30)
        enrolled = self._run()
        self.assertEqual(enrolled, 1)
        active = {t["chat_id"] for t in self.db.get_active_interval_tasks(account_id=5)}
        self.assertEqual(active, {10, 20, 30})

    def test_second_sync_of_unchanged_folder_enrols_nothing(self):
        self.db.save_template("promo", "hi", [], [])
        self.db.register_folder_broadcast(
            folder_name="Папка", template_name="promo", mode="interval",
            interval_seconds=30, account_id=5,
        )
        self.assertEqual(self._run(), 2)
        self.assertEqual(self._run(), 0)

    def test_chat_removed_from_folder_is_deactivated(self):
        self.db.save_template("promo", "hi", [], [])
        self.db.register_folder_broadcast(
            folder_name="Папка", template_name="promo", mode="interval",
            interval_seconds=30, account_id=5,
        )
        self._run()
        self.client.folders["Папка"].remove(20)
        self._run()
        active = {t["chat_id"] for t in self.db.get_active_interval_tasks(account_id=5)}
        self.assertEqual(active, {10})

    def test_no_folder_broadcasts_is_noop(self):
        self.assertEqual(self._run(), 0)


class TestMuteList(unittest.TestCase):
    """New feature: per-user mute and unmute."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mute_")
        self.db = bot.Database(Path(self.tmp) / "bot.db")
        self._orig_db = bot.db
        bot.db = self.db
        bot._MUTE_CACHE.clear()
        bot._MUTE_CHAT_CACHE.clear()

    def tearDown(self):
        bot.db = self._orig_db
        bot._MUTE_CACHE.clear()
        bot._MUTE_CHAT_CACHE.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_add_and_lookup(self):
        added = self.db.add_muted_peer(
            peer_id=555000111, peer_key=555000111,
            username="SpamBot", display_name="Spam Bot",
            account_id=1, reason="спам",
        )
        self.assertTrue(added)
        mute_cache_rebuild(1)
        self.assertTrue(is_peer_muted(1, 555000111))
        self.assertFalse(is_peer_muted(1, 999999999))

    def test_add_is_idempotent(self):
        self.db.add_muted_peer(peer_id=42, peer_key=42, account_id=1)
        again = self.db.add_muted_peer(peer_id=42, peer_key=42, account_id=1)
        self.assertFalse(again)
        self.assertEqual(len(self.db.list_muted_peers(account_id=1)), 1)

    def test_muted_id_is_normalised(self):
        self.db.add_muted_peer(peer_id=777, peer_key=777, account_id=1)
        mute_cache_rebuild(1)
        self.assertTrue(is_peer_muted(1, 777))
        self.assertTrue(is_peer_muted(1, 1000000000777))

    def test_unmute_removes(self):
        self.db.add_muted_peer(peer_id=777, peer_key=777, account_id=1)
        mute_cache_rebuild(1)
        self.assertTrue(is_peer_muted(1, 777))
        removed = self.db.remove_muted_peer(777, account_id=1)
        self.assertEqual(removed, 1)
        mute_cache_rebuild(1)
        self.assertFalse(is_peer_muted(1, 777))

    def test_unmute_absent_is_zero(self):
        self.assertEqual(self.db.remove_muted_peer(123, account_id=1), 0)

    def test_find_by_username(self):
        self.db.add_muted_peer(peer_id=31, peer_key=31, username="badactor", account_id=1)
        found = self.db.find_muted_peer_by_username("@badactor", account_id=1)
        self.assertIsNotNone(found)
        self.assertEqual(found["peer_key"], 31)
        self.assertIsNone(self.db.find_muted_peer_by_username("nobody", account_id=1))

    def test_accounts_are_isolated(self):
        self.db.add_muted_peer(peer_id=88, peer_key=88, account_id=1)
        mute_cache_rebuild(1)
        mute_cache_rebuild(2)
        self.assertTrue(is_peer_muted(1, 88))
        self.assertFalse(is_peer_muted(2, 88))

    def test_list_contains_metadata(self):
        self.db.add_muted_peer(
            peer_id=5, peer_key=5, username="handle",
            display_name="Display", account_id=1, reason="спам",
        )
        rows = self.db.list_muted_peers(account_id=1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["display_name"], "Display")
        self.assertEqual(rows[0]["reason"], "спам")


class TestSpamScorerIntegrity(unittest.TestCase):
    def test_mute_command_arguments_are_stripped(self):
        """
        Regression: the handlers receive the full raw line, so the first token
        was the command word itself. That made every argument form
        (".мутить @user", ".мутить 123456789") fail to resolve.
        """
        cases = [
            (".мутить @spammer", "@spammer"),
            (".мутить 123456789", "123456789"),
            (".мутить 123456789 причина", "123456789 причина"),
            (".mute @spammer", "@spammer"),
            (".размутить @spammer", "@spammer"),
            (".мутить", ""),
            ("мутить @spammer", "@spammer"),
        ]
        for raw, expected in cases:
            _, args = bot._extract_quoted_folder(raw)
            self.assertEqual(bot._strip_command_word(args), expected, raw)

    def test_mute_arguments_reach_the_resolver(self):
        """The stripped argument must match the numeric or username branch."""
        for raw in (".мутить @spammer", ".мутить 123456789", ".мутить 123456789 причина"):
            _, args = bot._extract_quoted_folder(raw)
            stripped = bot._strip_command_word(args)
            parts = [p for p in stripped.split() if p]
            token = parts[0]
            resolvable = bool(
                re.fullmatch(r"-?\d{5,}", token)
                or token.startswith("@")
                or re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{3,31}", token)
            )
            self.assertTrue(resolvable, raw)

    def test_mute_reason_is_split_off_a_numeric_id(self):
        _, args = bot._extract_quoted_folder(".мутить 123456789 спамщик")
        stripped = bot._strip_command_word(args)
        parts = [p for p in stripped.split() if p]
        self.assertTrue(re.fullmatch(r"-?\d{5,}", parts[0]))
        self.assertEqual(" ".join(parts[1:]), "спамщик")

    def test_advertisements_still_detected(self):
        ads = [
            "Продам канал эро тематики t.me/+abc Пишите в лс @SuKoW",
            "Телеграмм аккаунты: США (+1) Индия (+91) Купить можно за звезды",
            "Отправлено с помощью @Jrvusvu",
            "Рассылаю через @NINJA",
        ]
        for ad in ads:
            self.assertTrue(should_auto_read(ad, is_ping=True), ad)

    def test_conversation_untouched(self):
        for msg in (
            "привет! как дела?",
            "продашь рекламу?",
            "ку, свободен слот на вечер? дай стату",
        ):
            self.assertFalse(should_auto_read(msg, is_ping=True), msg)


class _FakeClient:
    """Minimal TelegramClient stand-in for folder reconciliation.

    get_chats_in_folder() talks to the real DialogFilter objects, so this
    double has to answer GetDialogFiltersRequest with a real filter shape.
    Returning a bare list from folders= is not enough and silently produced an
    empty membership, which is exactly the class of bug under test.
    """

    def __init__(self, folders=None):
        self.folders = folders or {}

    async def __call__(self, request):
        if isinstance(request, GetDialogFiltersRequest):
            result = type("FiltersResult", (), {})()
            result.filters = [
                _FakeDialogFilter(fid=index + 1, title=title, peers=list(peers))
                for index, (title, peers) in enumerate(self.folders.items())
            ]
            return result
        return None


class _FakeDialogFilter:
    def __init__(self, fid, title, peers):
        self.id = fid
        self.title = TextWithEntities(text=title, entities=[])
        self.pinned_peers = []
        self.include_peers = [
            InputPeerChannel(p, 0) if p < 0 else InputPeerUser(p, 0)
            for p in peers
        ]
        self.exclude_peers = []
        self.contacts = False
        self.non_contacts = False
        self.bots = False
        self.groups = False
        self.broadcasts = False
        self.exclude_muted = False
        self.exclude_archived = False


class _FakePeer:
    """
    Stand-in for the InputPeer objects stored in DialogFilter.include_peers.

    It has to be a real InputPeerChannel/InputPeerUser, because
    utils.get_peer_id() maps those to -1000000000010 / 20 style dialog ids. A
    duck-typed object yields a nonsense id, which silently makes the folder
    look empty instead of failing loudly.
    """

    def __init__(self, peer_id):
        self.peer_id = peer_id


class _FakeMessage:
    def __init__(self, chat_id, msg_id):
        self.chat_id = chat_id
        self.id = msg_id
        self.reply_to = None
        self.entities = None
        self.via_bot_id = None
        self.raw_text = ""


class _OrderingClient:
    """Records the order of operations performed during a clear."""

    def __init__(self):
        self.calls = []
        # Mirrors Telethon's real internal layout: (_local_entities, _local_read)
        self._local_entities = ({}, {})
        self.local_read_marked = False
        self.resolve_fails = False

    async def get_input_entity(self, peer):
        if self.resolve_fails:
            raise ValueError("cannot resolve")
        return peer

    async def get_entity(self, peer):
        if self.resolve_fails:
            raise ValueError("cannot resolve")
        return peer

    async def __call__(self, request):
        name = type(request).__name__
        if "Mention" in name:
            self.calls.append("mentions")
        elif "Reaction" in name:
            self.calls.append("reactions")
        elif "History" in name:
            self.calls.append("history")
        else:
            self.calls.append("network")

    async def send_read_acknowledge(self, *args, **kwargs):
        self.calls.append("acknowledge")


if __name__ == "__main__":
    unittest.main()

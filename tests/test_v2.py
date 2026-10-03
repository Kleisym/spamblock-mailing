# -*- coding: utf-8 -*-
"""Regression tests for the v2 fixes: duplicate sends, precise spam clearing,
multi-account isolation and the broader advertising detector."""
import asyncio
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bot
from bot import BroadcasterService, Database, should_auto_read, calculate_spam_score
from telethon import errors
from telethon.tl.types import InputPeerChannel

SPAM = [
    "Скупаю звёзды дешевле фрагмента, пиши в лс",
    "Нужны звезды? Дешевле чем в боте, отпиши",
    "куплю тон по курсу, пишите @tonbuyer",
    "Продаю NFT подарки ниже флора 🎁 @giftshop",
    "Заработок от 5000₽ в день без вложений! Подробнее в профиле",
    "Ищем людей в команду, удаленная работа, доход от 50$ в день, пиши в лс",
    "🔥 1WIN бонус 500% за регистрацию, промокод SPAM",
    "Казино с моментальным выводом t.me/+abcdefgh",
    "Продам аккаунты РФ по 50₽, tdata/сессии, оптом дешевле",
    "Накрутка подписчиков на канал и просмотров, недорого @smmpanel",
    "Скупка голды Standoff по 0.6₽, пиши",
    "Обменник USDT/RUB p2p, выгодный курс 👉@exch",
    "Купишь звезды дешевле?",
    "Прайс:\n100⭐ — 150₽\n500⭐ — 700₽\n1000⭐ — 1350₽\nПиши @starshop",
    "👋 Привет! Хочешь зарабатывать от 3000 в сутки? Переходи по ссылке в профиле",
    "Сливы приватных каналов 18+, ссылка в био",
    "Набор в команду! Работа с телефона, 2-3 часа в день",
    "Продам канал 5к подписчиков, тематика крипта. Пишите в лс @owner",
    "Отдам аккаунты бесплатно, первым 10 человекам",
    "Ставки на спорт с гарантией, канал t.me/+xyzxyzxyz",
    "Привет, нужны подписчики на канал? Недорого",
    "Продаю звёзды 1.3₽ за штуку, от 100 шт",
]
HAM = [
    "привет, почем реклама?",
    "ку, свободен слот на вечер? дай стату",
    "в лс отпиши по поводу закрепа",
    "а сколько стоит пост у тебя?",
    "есть место на завтра?",
    "окей, понял тебя, скинь реквизиты",
    "я сегодня видел звезды на небе, красиво",
    "глянь прикол https://t.me/funny_channel/123",
    "а у тебя есть аккаунты?",
    "сколько подписчиков в канале?",
    "хочу купить рекламу в твоем канале, сколько?",
    "можно разместить пост на неделю?",
    "давай завтра созвонимся по поводу сотрудничества",
    "привет, я вчера продал свой канал @mytest, теперь отдыхаю",
    "в день по 2 поста можно?",
    "оплатил звездами, проверь",
    "сколько звезд за пост?",
    "го в лс обсудим",
    "у меня канал про крипту, 10к подписчиков, интересно?",
    "ставки сделаны, ждем",
    "это была ошибка, mistake",
]


class TestDetectionCorpus(unittest.TestCase):
    def test_spam_is_caught(self):
        for t in SPAM:
            self.assertTrue(should_auto_read(t, is_ping=True), f"{t!r} {calculate_spam_score(t)}")

    def test_conversation_is_kept(self):
        for t in HAM:
            self.assertFalse(should_auto_read(t, is_ping=True), f"{t!r} {calculate_spam_score(t)}")


class _DBCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._old_db = bot.db
        self.db = Database(db_path=Path(self.tmp.name) / "t.db")
        bot.db = self.db

    def tearDown(self):
        bot.db = self._old_db
        self.tmp.cleanup()


class _SendClient:
    def __init__(self, fail_with=None, fail_on_call=None):
        self.sent = []
        self.fail_with = fail_with
        self.fail_on_call = fail_on_call

    async def get_input_entity(self, peer):
        return peer

    async def send_message(self, peer, message=None, formatting_entities=None):
        n = len(self.sent) + 1
        if self.fail_with is not None and (self.fail_on_call is None or self.fail_on_call == n):
            raise self.fail_with
        self.sent.append((peer, message))


class TestNoDuplicateSends(_DBCase):
    def test_interval_claim_is_exclusive(self):
        self.db.add_or_update_broadcast(chat_id=1, template_name="p", mode="interval", interval_seconds=60, account_id=7)
        tid = self.db.get_active_interval_tasks(account_id=7)[0]["id"]
        now = time.time()
        self.assertTrue(self.db.claim_interval_task(tid, now))
        self.assertFalse(self.db.claim_interval_task(tid, now + 1))

    def test_hybrid_counter_and_loop_cannot_both_fire(self):
        self.db.save_template("p", "hello", [], [])
        self.db.add_or_update_broadcast(chat_id=5, template_name="p", mode="hybrid",
                                        interval_seconds=60, counter_threshold=1, account_id=7)
        client = _SendClient()
        b = BroadcasterService(client, account_id=7)

        async def scenario():
            stale = self.db.get_active_interval_tasks(account_id=7)[0]  # snapshot before
            await b.handle_counter_event(5)                               # counter path sends
            stale["current_count"] = 1
            await b._run_scheduled(stale)                                 # stale loop path
        asyncio.run(scenario())
        self.assertEqual(len(client.sent), 1)

    def test_network_error_mid_send_is_not_retried(self):
        self.db.save_template("p", "hello", [], [])
        self.db.add_or_update_broadcast(chat_id=9, template_name="p", mode="interval", interval_seconds=100, account_id=7)
        b = BroadcasterService(_SendClient(fail_with=ConnectionError("reset")), account_id=7)
        task = self.db.get_active_interval_tasks(account_id=7)[0]
        asyncio.run(b._run_scheduled(task))
        after = self.db.get_active_interval_tasks(account_id=7)[0]
        # last_sent_at stays "now": no quick resend that would duplicate the post
        self.assertGreater(after["last_sent_at"], time.time() - 5)

    def test_partial_bundle_counts_as_sent(self):
        import json
        bundle = [{"text": "one", "entities_hex": [], "media_files": []},
                  {"text": "two", "entities_hex": [], "media_files": []}]
        self.db.save_template("multi", "", [], [], bundle_json=json.dumps(bundle))
        client = _SendClient(fail_with=errors.SlowModeWaitError(request=None, capture=30), fail_on_call=2)
        b = BroadcasterService(client, account_id=7)
        res = asyncio.run(b._send_task(1, 3, "multi"))
        self.assertEqual(res, "sent")
        self.assertEqual(len(client.sent), 1)

    def test_parallel_send_to_same_chat_is_blocked(self):
        self.db.save_template("p", "hello", [], [])
        b = BroadcasterService(_SendClient(), account_id=7)

        async def scenario():
            async with b._chat_lock(4):
                return await b._send_task(1, 4, "p")
        self.assertEqual(asyncio.run(scenario()), "skip")

    def test_legacy_rows_are_adopted_by_one_account(self):
        self.db.add_or_update_broadcast(chat_id=1, template_name="old", mode="interval", interval_seconds=60, account_id=0)
        self.db.adopt_legacy_rows(111)
        self.assertEqual(len(self.db.get_active_interval_tasks(account_id=111)), 1)
        self.assertEqual(len(self.db.get_active_interval_tasks(account_id=222)), 0)


class _ClearClient:
    def __init__(self, read_inbox_max_id=0):
        self.calls = []
        self.read_inbox_max_id = read_inbox_max_id

    async def __call__(self, req):
        name = type(req).__name__
        self.calls.append(name)
        if name == "GetPeerDialogsRequest":
            d = type("D", (), {"read_inbox_max_id": self.read_inbox_max_id})()
            return type("R", (), {"dialogs": [d]})()

    async def get_input_entity(self, peer):
        return InputPeerChannel(channel_id=1234, access_hash=1)


class _Msg:
    def __init__(self, mid, chat_id=-1001234):
        self.id = mid
        self.chat_id = chat_id
        self.input_chat = InputPeerChannel(channel_id=1234, access_hash=1)


class TestPreciseClearing(unittest.TestCase):
    def setUp(self):
        bot._GENUINE_PINGS.clear()

    def test_only_that_message_and_never_reactions(self):
        c = _ClearClient()
        asyncio.run(bot.clear_spam_mention(c, -1001234, _Msg(50), account_id=1, chat_id=-1001234))
        self.assertEqual(c.calls[0], "ReadMessageContentsRequest")
        self.assertNotIn("ReadMentionsRequest", c.calls)
        self.assertNotIn("ReadReactionsRequest", c.calls)
        self.assertIn("ReadHistoryRequest", c.calls)

    def test_unread_real_reply_is_not_marked_read(self):
        bot.note_genuine_ping(1, -1001234, 40)
        c = _ClearClient(read_inbox_max_id=10)
        asyncio.run(bot.clear_spam_mention(c, -1001234, _Msg(50), account_id=1, chat_id=-1001234))
        self.assertNotIn("ReadHistoryRequest", c.calls)

    def test_history_read_once_real_reply_was_seen(self):
        bot.note_genuine_ping(1, -1001234, 40)
        c = _ClearClient(read_inbox_max_id=45)
        asyncio.run(bot.clear_spam_mention(c, -1001234, _Msg(50), account_id=1, chat_id=-1001234))
        self.assertIn("ReadHistoryRequest", c.calls)


if __name__ == "__main__":
    unittest.main()

import json
import os
import shutil
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from telethon.tl.types import MessageEntityBold, MessageEntityCustomEmoji, MessageEntityStrike, DialogFilterChatlist
from bot import (
    Database,
    _extract_collector_flag,
    _extract_folder_title,
    _extract_quoted_folder,
    _parse_interval_str,
    deserialize_entities,
    extract_channel_links,
    extract_payload_and_entities,
    is_gatekeeper_text,
    is_genuine_buyer_question,
    is_spam_message,
    is_verify_button,
    parse_broadcast_params,
    send_broadcast_post,
    send_single_item,
    serialize_entities,
    should_auto_read,
    load_config,
    calculate_spam_score,
    clear_spam_mention,
    BroadcasterService,
    MessageDedupRing,
    register_events,
    is_internal_bot_message,
)

TEST_DB_PATH = Path("test_bot.db")

class TestAntispamProject(unittest.TestCase):
    def setUp(self):
        if TEST_DB_PATH.exists():
            TEST_DB_PATH.unlink()
        self.db = Database(db_path=TEST_DB_PATH)

    def tearDown(self):
        if TEST_DB_PATH.exists():
            TEST_DB_PATH.unlink()

    def test_database_templates(self):
        self.db.save_template("promo1", "Hello world", ["010203"], ["media/1.jpg"])
        tpl = self.db.get_template("promo1")
        self.assertIsNotNone(tpl)
        self.assertEqual(tpl["text"], "Hello world")
        self.assertEqual(tpl["entities_hex"], ["010203"])
        self.assertEqual(tpl["media_files"], ["media/1.jpg"])

        templates = self.db.list_templates()
        self.assertEqual(len(templates), 1)

        self.db.delete_template("promo1")
        self.assertIsNone(self.db.get_template("promo1"))

    def test_database_interval_and_counter_tasks(self):
        self.db.add_or_update_broadcast(
            chat_id=12345,
            template_name="promo1",
            mode="interval",
            interval_seconds=15
        )
        tasks = self.db.get_active_interval_tasks()
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["interval_seconds"], 15)

        self.db.add_or_update_broadcast(
            chat_id=12345,
            template_name="promo2",
            mode="counter",
            counter_threshold=3
        )

        ready = self.db.increment_and_check_counter(12345)
        self.assertEqual(len(ready), 0)

        ready = self.db.increment_and_check_counter(12345)
        self.assertEqual(len(ready), 0)

        ready = self.db.increment_and_check_counter(12345)
        self.assertEqual(len(ready), 1)
        self.assertEqual(ready[0]["template_name"], "promo2")

    def test_database_rotations(self):
        chat_id = 999
        self.db.save_template("post1", "Text 1", [], [])
        self.db.save_template("post2", "Text 2", [], [])
        self.db.save_template("post3", "Text 3", [], [])

        self.db.set_chat_rotation(chat_id, ["post1", "post2", "post3"])
        t1 = self.db.get_next_rotation_template(chat_id)
        t2 = self.db.get_next_rotation_template(chat_id)
        t3 = self.db.get_next_rotation_template(chat_id)
        t4 = self.db.get_next_rotation_template(chat_id)

        self.assertEqual([t1, t2, t3, t4], ["post1", "post2", "post3", "post1"])

        # Test deleting post2 automatically removes it from rotation
        self.db.delete_template("post2")
        rot = self.db.get_chat_rotation(chat_id)
        self.assertEqual(rot["template_names"], ["post1", "post3"])

        # Test deleting all templates purges rotation
        self.db.delete_template("post1")
        self.db.delete_template("post3")
        self.assertIsNone(self.db.get_next_rotation_template(chat_id))

    def test_spam_detection_real_examples(self):
        spam_ad_1 = """Я @vkxay скупаю такие штукенции как:
тон, $, за курсом в лс
нфт подарки ниже флора!!! 
аккаунты рф скупаю за рубли (1$ за аккаунт деньги в рублях )
скупаю голду по 0.40
ПРОДАЖА
продаю смену номера на США -250₽
продаю все номера различных стран
продам сайт где +7 1$
продаю звёзды по 1,4 за звёзду
Отправлено с помощью @Jrvusvu"""

        spam_ad_2 = """📈Телеграмм аккаунты:
🇺🇸 США (+1) - 15 🌟
🇮🇳 Индия (+91) - 15 🌟
🇮🇩 Индонезия (+62) - 25 🌟
🇨🇴 Колумбия (+57) - 25 🌟
Цена за аккаунт:
🔵Без спам блока✔️
🔵Без пароля✔️
Купить можно за звезды ⭐️ / крипту (usdt)
Купить и вопросы за аккаунт - @wollfreeyy
Рассылаю через NINJA"""

        self.assertTrue(should_auto_read(spam_ad_1))
        self.assertTrue(should_auto_read(spam_ad_2))

        legit_1 = "привет, почем реклама?"
        legit_2 = "ку, свободен слот на вечер? дай стату"
        legit_3 = "в лс отпиши по поводу закрепа"

        self.assertFalse(should_auto_read(legit_1))
        self.assertFalse(should_auto_read(legit_2))
        self.assertFalse(should_auto_read(legit_3))
        self.assertTrue(is_genuine_buyer_question(legit_1))

    def test_gatekeeper_detection(self):
        text = "cvobodny | BSC, чтобы писать в чат, необходимо подписаться на каналы: @chat1 | https://t.me/chat2"
        self.assertTrue(is_gatekeeper_text(text))
        links = extract_channel_links(text)
        self.assertIn("chat1", links)
        self.assertIn("chat2", links)
        self.assertTrue(is_verify_button("Я подписался"))
        self.assertTrue(is_verify_button("Проверить"))

    def test_command_time_and_folder_parsing(self):
        self.assertEqual(_parse_interval_str("15 секунд"), 15)
        self.assertEqual(_parse_interval_str("15s"), 15)
        self.assertEqual(_parse_interval_str("10 минут"), 600)
        self.assertEqual(_parse_interval_str("10m"), 600)
        self.assertEqual(_parse_interval_str("1 час"), 3600)
        self.assertEqual(_parse_interval_str("2h"), 7200)

        folder, rest = _extract_quoted_folder('.рассыл каждое 15 секунд промо "Моя папка"')
        self.assertEqual(folder, "Моя папка")
        self.assertEqual(rest, ".рассыл каждое 15 секунд промо")

    def test_entity_serialization(self):
        bold = MessageEntityBold(offset=0, length=5)
        emoji = MessageEntityCustomEmoji(offset=6, length=2, document_id=1234567890123)
        entities = [bold, emoji]

        hex_list = serialize_entities(entities)
        self.assertEqual(len(hex_list), 2)

        restored = deserialize_entities(hex_list)
        self.assertEqual(len(restored), 2)
        self.assertEqual(restored[0].offset, 0)
        self.assertEqual(restored[0].length, 5)
        self.assertEqual(restored[1].document_id, 1234567890123)

    def test_status_toggles_and_deletions(self):
        self.db.add_or_update_broadcast(chat_id=1, template_name="ad1", mode="interval", interval_seconds=15, folder_name="folderA")
        self.db.add_or_update_broadcast(chat_id=2, template_name="ad1", mode="interval", interval_seconds=15, folder_name="folderA")
        self.db.add_or_update_broadcast(chat_id=3, template_name="ad2", mode="interval", interval_seconds=30, folder_name="folderB")

        stopped = self.db.set_broadcast_status(name="ad1", folder_name="folderA", is_active=0)
        self.assertEqual(stopped, 2)

        active_tasks = self.db.get_active_interval_tasks()
        self.assertEqual(len(active_tasks), 1)
        self.assertEqual(active_tasks[0]["template_name"], "ad2")

        resumed = self.db.set_broadcast_status(name="ad1", folder_name="folderA", is_active=1)
        self.assertEqual(resumed, 2)

        deleted = self.db.delete_broadcasts(name="all", folder_name="folderA")
        self.assertEqual(deleted, 2)

        # Test status toggling using explicit chat_ids list (folder chats)
        self.db.add_or_update_broadcast(chat_id=10, template_name="adX", mode="interval", interval_seconds=10)
        self.db.add_or_update_broadcast(chat_id=20, template_name="adX", mode="interval", interval_seconds=10)
        resumed_list = self.db.set_broadcast_status(name="adX", chat_ids=[10, 20], is_active=0)
        self.assertEqual(resumed_list, 2)

    def test_extract_payload_and_entities_multiline_and_singleline(self):
        class DummyMsg:
            def __init__(self, message, entities):
                self.message = message
                self.entities = entities

        raw_multi = '.рассыл каждое 15 секунд <название>\nтест\n вот так 🐾'
        prefix = '.рассыл каждое 15 секунд <название>\n'
        p_len = len(prefix.encode('utf-16le')) // 2
        e1 = MessageEntityStrike(offset=p_len, length=4)
        e2 = MessageEntityCustomEmoji(offset=p_len + 14, length=2, document_id=55555)
        msg_multi = DummyMsg(raw_multi, [e1, e2])

        text, ent_hex = extract_payload_and_entities(msg_multi, raw_multi)
        self.assertEqual(text, 'тест\n вот так 🐾')
        self.assertEqual(len(ent_hex), 2)
        restored = deserialize_entities(ent_hex)
        self.assertEqual(restored[0].offset, 0)
        self.assertEqual(restored[0].length, 4)
        self.assertEqual(restored[1].offset, 14)
        self.assertEqual(restored[1].length, 2)
        self.assertEqual(restored[1].document_id, 55555)

        # Single line with quotes
        raw_single = '.рассыл каждые 10 минут <назв> "Супер скидка 🐾!"'
        quote_target = '"Супер скидка 🐾!"'
        pos = raw_single.find(quote_target)
        s_prefix_len = len(raw_single[:pos + 1].encode('utf-16le')) // 2
        # 'Супер скидка ' is 13 chars
        e_single = MessageEntityCustomEmoji(offset=s_prefix_len + 13, length=2, document_id=99999)
        msg_single = DummyMsg(raw_single, [e_single])

        s_text, s_ent_hex = extract_payload_and_entities(msg_single, raw_single)
        self.assertEqual(s_text, 'Супер скидка 🐾!')
        self.assertEqual(len(s_ent_hex), 1)
        s_restored = deserialize_entities(s_ent_hex)
        self.assertEqual(s_restored[0].offset, 13)
        self.assertEqual(s_restored[0].length, 2)
        self.assertEqual(s_restored[0].document_id, 99999)

    def test_bracket_handling_and_normalization(self):
        # Save with <brackets>
        self.db.save_template("<моя_рассылка>", "Привет мир", [], [])
        
        # Look up without brackets
        tpl1 = self.db.get_template("моя_рассылка")
        self.assertIsNotNone(tpl1)
        self.assertEqual(tpl1["name"], "моя_рассылка")

        # Look up with brackets
        tpl2 = self.db.get_template("<моя_рассылка>")
        self.assertIsNotNone(tpl2)
        self.assertEqual(tpl2["name"], "моя_рассылка")

        # Add task with brackets
        self.db.add_or_update_broadcast(chat_id=777, template_name="<моя_рассылка>", mode="interval", interval_seconds=10)
        tasks = self.db.get_active_interval_tasks()
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["template_name"], "моя_рассылка")

        # Set status with and without brackets
        stopped = self.db.set_broadcast_status(name="<моя_рассылка>", chat_id=777, is_active=0)
        self.assertEqual(stopped, 1)

        # Set rotation with brackets
        self.db.set_chat_rotation(chat_id=777, template_names=["<пост1>", "<пост2>"])
        rot = self.db.get_chat_rotation(chat_id=777)
        self.assertEqual(rot["template_names"], ["пост1", "пост2"])

    def test_parse_broadcast_params(self):
        # 1. Interval with seconds and brackets
        m, s, c, name, f, err = parse_broadcast_params(".рассыл каждое 15 секунд <реклама>")
        self.assertEqual(err, "")
        self.assertEqual(m, "interval")
        self.assertEqual(s, 15)
        self.assertEqual(name, "реклама")
        self.assertEqual(f, "")

        # 2. Forwarded with minutes and folder
        m, s, c, name, f, err = parse_broadcast_params('.рассыл-пересланное каждые 10 минут <канал> "Моя папка"')
        self.assertEqual(err, "")
        self.assertEqual(m, "interval")
        self.assertEqual(s, 600)
        self.assertEqual(name, "канал")
        self.assertEqual(f, "Моя папка")

        # 3. Multi-message with counter without brackets
        m, s, c, name, f, err = parse_broadcast_params('.рассыл-несколько через 1 промо')
        self.assertEqual(err, "")
        self.assertEqual(m, "counter")
        self.assertEqual(c, 1)
        self.assertEqual(name, "промо")
        self.assertEqual(f, "")

        # 4. English broadcast with folder
        m, s, c, name, f, err = parse_broadcast_params('.broadcast every 30s deal "VIP"')
        self.assertEqual(err, "")
        self.assertEqual(m, "interval")
        self.assertEqual(s, 30)
        self.assertEqual(name, "deal")
        self.assertEqual(f, "VIP")

        # 5. Invalid intervals and syntax
        _, _, _, _, _, err = parse_broadcast_params(".рассыл")
        self.assertTrue(err.startswith("⚠️ Ошибка синтаксиса"))

        _, _, _, _, _, err = parse_broadcast_params(".рассыл каждое 0сек <назв>")
        self.assertTrue(err.startswith("⚠️ Неверно указано время"))

    def test_bundle_templates_in_database(self):
        bundle = [
            {
                "text": "Сообщение 1",
                "entities_hex": ["0102"],
                "media_files": [],
                "is_forward": False,
                "forward_chat_id": 0,
                "forward_msg_ids": [],
                "grouped_id": None
            },
            {
                "text": "Сообщение 2 (пересланное)",
                "entities_hex": [],
                "media_files": [],
                "is_forward": True,
                "forward_chat_id": 123456,
                "forward_msg_ids": [789],
                "grouped_id": None
            }
        ]
        bundle_str = json.dumps(bundle)
        self.db.save_template(
            name="multi_pack",
            text="Сообщение 1",
            entities_hex=["0102"],
            media_files=[],
            bundle_json=bundle_str
        )

        tpl = self.db.get_template("multi_pack")
        self.assertIsNotNone(tpl)
        self.assertEqual(tpl["name"], "multi_pack")
        self.assertEqual(len(tpl["bundle"]), 2)
        self.assertEqual(tpl["bundle"][0]["text"], "Сообщение 1")
        self.assertTrue(tpl["bundle"][1]["is_forward"])
        self.assertEqual(tpl["bundle"][1]["forward_msg_ids"], [789])

        # Verify listing templates contains bundle
        templates = self.db.list_templates()
        found = next((t for t in templates if t["name"] == "multi_pack"), None)
        self.assertIsNotNone(found)
        self.assertEqual(len(found["bundle"]), 2)

    def test_broadcast_post_with_bundle(self):
        import asyncio

        class MockTelegramClient:
            def __init__(self):
                self.forwarded = []
                self.sent_messages = []
                self.sent_files = []

            async def forward_messages(self, chat_peer, messages, from_peer):
                self.forwarded.append({"chat": chat_peer, "messages": messages, "from_peer": from_peer})

            async def send_message(self, chat_peer, message, formatting_entities=None):
                self.sent_messages.append({"chat": chat_peer, "message": message, "entities": formatting_entities})

            async def send_file(self, chat_peer, file, caption=None, formatting_entities=None):
                self.sent_files.append({"chat": chat_peer, "file": file, "caption": caption})

        client = MockTelegramClient()
        bundle = [
            {
                "text": "Пост 1",
                "entities_hex": [],
                "media_files": [],
                "is_forward": False,
                "forward_chat_id": 0,
                "forward_msg_ids": [],
            },
            {
                "text": "Пост 2",
                "entities_hex": [],
                "media_files": [],
                "is_forward": True,
                "forward_chat_id": 999,
                "forward_msg_ids": [101, 102],
            }
        ]

        asyncio.run(send_broadcast_post(
            client=client,
            chat_peer=111,
            bundle=bundle
        ))

        self.assertEqual(len(client.sent_messages), 1)
        self.assertEqual(client.sent_messages[0]["message"], "Пост 1")
        self.assertEqual(len(client.forwarded), 1)
        self.assertEqual(client.forwarded[0]["messages"], [101, 102])
        self.assertEqual(client.forwarded[0]["from_peer"], 999)

    def test_multi_account_database_isolation(self):
        chat_id = 777
        self.db.save_template("t1", "Template 1", [], [])
        self.db.save_template("t2", "Template 2", [], [])

        # Add tasks for account 1
        self.db.add_or_update_broadcast(
            chat_id=chat_id,
            template_name="t1",
            mode="counter",
            counter_threshold=2,
            account_id=1001
        )

        # Add tasks for account 2
        self.db.add_or_update_broadcast(
            chat_id=chat_id,
            template_name="t2",
            mode="counter",
            counter_threshold=3,
            account_id=2002
        )

        # Increment for account 1
        r1_first = self.db.increment_and_check_counter(chat_id, account_id=1001)
        self.assertEqual(len(r1_first), 0)
        r1_second = self.db.increment_and_check_counter(chat_id, account_id=1001)
        self.assertEqual(len(r1_second), 1)
        self.assertEqual(r1_second[0]["template_name"], "t1")

        # Account 2 counter should still be untouched
        r2 = self.db.get_chat_broadcasts(chat_id, account_id=2002)
        self.assertEqual(len(r2), 1)
        self.assertEqual(r2[0]["current_count"], 0)

        # Check rotations per account
        self.db.set_chat_rotation(chat_id, ["t1", "t2"], account_id=1001)
        self.db.set_chat_rotation(chat_id, ["t2", "t1"], account_id=2002)

        next_a1 = self.db.get_next_rotation_template(chat_id, account_id=1001)
        next_a2 = self.db.get_next_rotation_template(chat_id, account_id=2002)
        self.assertEqual(next_a1, "t1")
        self.assertEqual(next_a2, "t2")

    def test_multi_account_config_loading(self):
        tmp_cfg = Path("test_multi_config.txt")
        tmp_cfg.write_text("""
log_errors_only = true

[account1]
api_id = 111111
api_hash = hash111111
phone = +79991111111
session_name = session_acc1
auto_sub_folder = folder1
auto_read_spam_pings = true
auto_sub_enabled = true

[account2]
api_id = 222222
api_hash = hash222222
phone = +79992222222
session_name = session_acc2
auto_sub_folder = folder2
auto_read_spam_pings = false
auto_sub_enabled = false
""", encoding="utf-8")
        try:
            cfg = load_config(tmp_cfg)
            self.assertTrue(cfg["log_errors_only"])
            self.assertEqual(len(cfg["accounts"]), 2)
            self.assertEqual(cfg["accounts"][0]["name"], "account1")
            self.assertEqual(cfg["accounts"][0]["api_id"], 111111)
            self.assertEqual(cfg["accounts"][0]["auto_sub_folder"], "folder1")
            self.assertTrue(cfg["accounts"][0]["auto_read_spam_pings"])

            self.assertEqual(cfg["accounts"][1]["name"], "account2")
            self.assertEqual(cfg["accounts"][1]["api_id"], 222222)
            self.assertEqual(cfg["accounts"][1]["auto_sub_folder"], "folder2")
            self.assertFalse(cfg["accounts"][1]["auto_read_spam_pings"])
        finally:
            if tmp_cfg.exists():
                tmp_cfg.unlink()

    def test_channel_sales_spam_detection(self):
        spam_msg = """🔥Продам каналы \n\n1️⃣@AzartMalone (казино, азарт)\n2️⃣ https://t.me/+a7SAI2WrDL0zZDFi (эро) \n\n👉Пишите в лс @SuKoW"""
        is_spam, pat = is_spam_message(spam_msg)
        self.assertTrue(is_spam)
        self.assertFalse(is_genuine_buyer_question(spam_msg))
        self.assertTrue(should_auto_read(spam_msg))

    def test_smart_buyer_question_not_detected_as_spam(self):
        # Questions asking user to sell ads or channels must be recognized as buyers, NOT spam
        q1 = "продашь рекламу?"
        self.assertFalse(is_spam_message(q1)[0])
        self.assertTrue(is_genuine_buyer_question(q1))
        self.assertFalse(should_auto_read(q1))

        q2 = "привет, продаешь канал?"
        self.assertFalse(is_spam_message(q2)[0])
        self.assertTrue(is_genuine_buyer_question(q2))
        self.assertFalse(should_auto_read(q2))

        q3 = "почем пост в твоем канале?"
        self.assertFalse(is_spam_message(q3)[0])
        self.assertTrue(is_genuine_buyer_question(q3))
        self.assertFalse(should_auto_read(q3))

        # Disguised spam with a question mark must still be detected as spam and not a buyer
        disguised = "Хочешь купить рекламу? 🔥Продам каналы 1️⃣@AzartMalone 👉Пишите в лс @SuKoW"
        self.assertTrue(is_spam_message(disguised)[0])
        self.assertFalse(is_genuine_buyer_question(disguised))
        self.assertTrue(should_auto_read(disguised))

    def test_any_broadcaster_or_userbot_detected(self):
        # 1. Russian watermarks
        self.assertTrue(is_spam_message("Привет! отправлено через @AnyRandomBot")[0])
        self.assertTrue(is_spam_message("Рассылаю через @custom_userbot")[0])
        self.assertTrue(is_spam_message("Постинг через @FastPostBot")[0])
        self.assertTrue(is_spam_message("Опубликовано с помощью @SenderBot")[0])
        self.assertTrue(is_spam_message("Рассылка через @MyPromoScript")[0])

        # 2. English watermarks
        self.assertTrue(is_spam_message("Check this out! Sent via @AutoPoster")[0])
        self.assertTrue(is_spam_message("Posted with @MailerBot")[0])

        # 3. Mention of mailing tools / scripts
        self.assertTrue(is_spam_message("Лучший софт для рассылок: @super_bot")[0])
        self.assertTrue(is_spam_message("Качественный бот для спама @spambot")[0])
        self.assertTrue(is_spam_message("Заказать рассылку: @agency")[0])

        # 4. Any handle with autopost/mailer/spambot
        self.assertTrue(is_spam_message("Подписывайся на @top_autopost_bot")[0])

        # 5. Ghost ping with invisible zero-width space
        self.assertTrue(is_spam_message("Всем привет\u200b@user")[0])

        # 6. Inline bot message
        class DummyMsg:
            via_bot_id = 123456789
        self.assertTrue(is_spam_message("Обычный текст с кнопками", message=DummyMsg())[0])

    def test_hybrid_broadcast_parsing(self):
        # 1. Standard phrase: через 10 соо минимум 10 минут
        mode, interval, count, name, folder, err = parse_broadcast_params(".рассыл через 10 соо минимум 10 минут promo1")
        self.assertEqual(err, "")
        self.assertEqual(mode, "hybrid")
        self.assertEqual(count, 10)
        self.assertEqual(interval, 600)
        self.assertEqual(name, "promo1")

        # 2. Conversational variation: через 10 соо но что бы минимум 10 минут прошло
        mode, interval, count, name, folder, err = parse_broadcast_params(".рассыл через 10 соо но что бы минимум 10 минут прошло promo1")
        self.assertEqual(err, "")
        self.assertEqual(mode, "hybrid")
        self.assertEqual(count, 10)
        self.assertEqual(interval, 600)
        self.assertEqual(name, "promo1")

        # 3. Short forms: кд, cd, 10м
        mode, interval, count, name, folder, err = parse_broadcast_params(".рассыл через 10 соо кд 10 мин promo1")
        self.assertEqual(mode, "hybrid")
        self.assertEqual(count, 10)
        self.assertEqual(interval, 600)

        mode, interval, count, name, folder, err = parse_broadcast_params(".рассыл через 10 10м promo1")
        self.assertEqual(mode, "hybrid")
        self.assertEqual(count, 10)
        self.assertEqual(interval, 600)

        # 4. Short комби / hybrid
        mode, interval, count, name, folder, err = parse_broadcast_params(".рассыл комби 10 10м promo1")
        self.assertEqual(mode, "hybrid")
        self.assertEqual(count, 10)
        self.assertEqual(interval, 600)

        # 5. With folder
        mode, interval, count, name, folder, err = parse_broadcast_params('.рассыл через 10 соо минимум 10 минут "мои_чаты" promo1')
        self.assertEqual(mode, "hybrid")
        self.assertEqual(folder, "мои_чаты")
        self.assertEqual(name, "promo1")

        # 6. Counter mode with 'соо' token skipped cleanly
        mode, interval, count, name, folder, err = parse_broadcast_params(".рассыл через 10 соо promo1")
        self.assertEqual(mode, "counter")
        self.assertEqual(count, 10)
        self.assertEqual(name, "promo1")

    def test_hybrid_broadcast_execution(self):
        chat_id = 777
        now = time.time()
        self.db.save_template("promo_hyb", "Hybrid Text", [], [])

        self.db.add_or_update_broadcast(
            chat_id=chat_id,
            template_name="promo_hyb",
            mode="hybrid",
            interval_seconds=600,
            counter_threshold=3
        )
        with self.db._conn() as conn:
            conn.execute("UPDATE broadcast_tasks SET last_sent_at = ? WHERE chat_id = ?", (now, chat_id))

        # 1st incoming message
        ready = self.db.increment_and_check_counter(chat_id)
        self.assertEqual(len(ready), 0)

        # 2nd incoming message
        ready = self.db.increment_and_check_counter(chat_id)
        self.assertEqual(len(ready), 0)

        # 3rd incoming message: count reached 3, BUT cooldown of 600s has not passed yet!
        ready = self.db.increment_and_check_counter(chat_id)
        self.assertEqual(len(ready), 0)

        tasks = self.db.get_active_interval_tasks()
        hyb_tasks = [t for t in tasks if t["mode"] == "hybrid"]
        self.assertEqual(len(hyb_tasks), 1)
        self.assertEqual(hyb_tasks[0]["current_count"], 3)

        # Fast forward time: 650s later, cooldown expires
        with self.db._conn() as conn:
            conn.execute("UPDATE broadcast_tasks SET last_sent_at = ? WHERE chat_id = ?", (now - 650, chat_id))

        tasks = self.db.get_active_interval_tasks()
        t = [x for x in tasks if x["mode"] == "hybrid"][0]
        time_elapsed = (time.time() - t["last_sent_at"]) >= t["interval_seconds"]
        count_reached = t["current_count"] >= t["counter_threshold"]
        self.assertTrue(time_elapsed and count_reached)

        # Reset task as _loop does
        self.db.reset_hybrid_task(t["id"])
        tasks_after = self.db.get_active_interval_tasks()
        t_after = [x for x in tasks_after if x["mode"] == "hybrid"][0]
        self.assertEqual(t_after["current_count"], 0)

        # Cooldown already expired: 3rd message triggers immediately
        with self.db._conn() as conn:
            conn.execute("UPDATE broadcast_tasks SET last_sent_at = ? WHERE chat_id = ?", (now - 700, chat_id))

        self.db.increment_and_check_counter(chat_id)
        self.db.increment_and_check_counter(chat_id)
        ready = self.db.increment_and_check_counter(chat_id)
        self.assertEqual(len(ready), 1)
        self.assertEqual(ready[0]["template_name"], "promo_hyb")

    def test_named_rotations(self):
        self.db.save_template("post1", "Post 1", [], [])
        self.db.save_template("post2", "Post 2", [], [])
        self.db.save_template("post3", "Post 3", [], [])

        # Create named rotation
        self.db.set_named_rotation("связка1", ["post1", "post2", "post3"])
        rot = self.db.get_named_rotation("связка1")
        self.assertIsNotNone(rot)
        self.assertEqual(rot["name"], "связка1")
        self.assertEqual(rot["template_names"], ["post1", "post2", "post3"])

        # Cycle through named rotation
        t1 = self.db.get_next_rotation_template(rotation_name="связка1")
        t2 = self.db.get_next_rotation_template(rotation_name="связка1")
        t3 = self.db.get_next_rotation_template(rotation_name="связка1")
        t4 = self.db.get_next_rotation_template(rotation_name="связка1")
        self.assertEqual([t1, t2, t3, t4], ["post1", "post2", "post3", "post1"])

        # List rotations
        all_rots = self.db.list_named_rotations()
        self.assertEqual(len(all_rots), 1)
        self.assertEqual(all_rots[0]["name"], "связка1")

        # Delete named rotation
        deleted = self.db.delete_named_rotation("связка1")
        self.assertTrue(deleted)
        self.assertIsNone(self.db.get_named_rotation("связка1"))

        # Deleting a template updates named rotations
        self.db.set_named_rotation("rot2", ["post1", "post2", "post3"])
        self.db.delete_template("post2")
        rot2 = self.db.get_named_rotation("rot2")
        self.assertEqual(rot2["template_names"], ["post1", "post3"])

    def test_collector_flag_and_user_time_variations(self):
        # 1. Exact user requested syntax: .рассыл через 10 10м <название> "папка"
        mode, interval, count, name, folder, err = parse_broadcast_params('.рассыл через 10 10м promo1 "папка"')
        self.assertEqual(err, "")
        self.assertEqual(mode, "hybrid")
        self.assertEqual(count, 10)
        self.assertEqual(interval, 600)
        self.assertEqual(name, "promo1")
        self.assertEqual(folder, "папка")

        # 2. Time units: минута, час, день, секунда
        units = [
            ("10 минута", 600),
            ("10 минут", 600),
            ("10 часа", 36000),
            ("10 часов", 36000),
            ("10 день", 864000),
            ("10 дня", 864000),
            ("10 дней", 864000),
            ("10 секунда", 10),
            ("10 секунд", 10),
            ("10 сек", 10),
            ("10с", 10),
            ("10ч", 36000),
            ("10д", 864000),
        ]
        for u_str, expected_sec in units:
            cmd = f'.рассыл через 10 {u_str} promo1 "папка"'
            mode, interval, count, name, folder, err = parse_broadcast_params(cmd)
            self.assertEqual(err, "", f"Failed for unit: {u_str}")
            self.assertEqual(interval, expected_sec, f"Wrong interval for unit: {u_str}")
            self.assertEqual(count, 10)
            self.assertEqual(name, "promo1")
            self.assertEqual(folder, "папка")

        # 3. Flag extraction: -мульти, --мульти, -multi, -пересланное
        flag1, cl1 = _extract_collector_flag('.рассыл через 10 10м promo1 "папка" -мульти')
        self.assertEqual(flag1, "multi")
        mode, interval, count, name, folder, err = parse_broadcast_params(cl1)
        self.assertEqual(err, "")
        self.assertEqual(mode, "hybrid")
        self.assertEqual(interval, 600)
        self.assertEqual(count, 10)
        self.assertEqual(name, "promo1")
        self.assertEqual(folder, "папка")

        flag2, cl2 = _extract_collector_flag('.рассыл -multi через 10 10м promo1 "папка"')
        self.assertEqual(flag2, "multi")
        mode, interval, count, name, folder, err = parse_broadcast_params(cl2)
        self.assertEqual(mode, "hybrid")
        self.assertEqual(name, "promo1")

        flag3, cl3 = _extract_collector_flag('.рассыл через 10 10м promo1 "папка" -пересланное')
        self.assertEqual(flag3, "forwarded")
        mode, interval, count, name, folder, err = parse_broadcast_params(cl3)
        self.assertEqual(mode, "hybrid")
        self.assertEqual(name, "promo1")

    def test_composite_emojis_not_ghost_ping(self):
        # Emojis with ZWJ (\u200d) like ❤️‍🔥 and 🤷‍♂️ must NOT be flagged as ghost pings
        normal_with_emoji = "привет, как дела? ❤️‍🔥"
        self.assertFalse(is_spam_message(normal_with_emoji)[0])

        male_shrug = "не знаю 🤷‍♂️"
        self.assertFalse(is_spam_message(male_shrug)[0])

        # Actual ghost ping with zero-width space (\u200b) must be flagged
        ghost_ping = "привет\u200b@channel"
        self.assertTrue(is_spam_message(ghost_ping)[0])

        bom_ping = "всем привет\ufeff@all"
        self.assertTrue(is_spam_message(bom_ping)[0])

    def test_seller_question_detected_as_spam(self):
        # Questions asking user to buy ("купишь рекламу?", "купите канал?") are seller spam, NOT buyer questions
        q1 = "купишь рекламу?"
        self.assertTrue(is_spam_message(q1)[0])
        self.assertFalse(is_genuine_buyer_question(q1))

        q2 = "купите канал пж"
        self.assertTrue(is_spam_message(q2)[0])
        self.assertFalse(is_genuine_buyer_question(q2))

        # Real buyer questions must still be valid
        buyer_q = "продашь рекламу?"
        self.assertFalse(is_spam_message(buyer_q)[0])
        self.assertTrue(is_genuine_buyer_question(buyer_q))

    def test_restore_counter_on_send_failure(self):
        chat_id = 888
        self.db.save_template("tpl_fail", "Text", [], [])
        self.db.add_or_update_broadcast(chat_id=chat_id, template_name="tpl_fail", mode="counter", counter_threshold=5)

        # Increment to trigger
        for _ in range(4):
            self.assertEqual(len(self.db.increment_and_check_counter(chat_id)), 0)
        ready = self.db.increment_and_check_counter(chat_id)
        self.assertEqual(len(ready), 1)

        # Counter is now reset to 0 in db
        tasks = self.db.get_chat_broadcasts(chat_id)
        self.assertEqual(tasks[0]["current_count"], 0)

        # Suppose send failed (e.g. slowmode or network/VPN drop): restore counter and last_sent_at
        self.db.restore_counter(tasks[0]["id"], 5, last_sent_at=123456.0)
        tasks_restored = self.db.get_chat_broadcasts(chat_id)
        self.assertEqual(tasks_restored[0]["current_count"], 5)
        self.assertEqual(tasks_restored[0]["last_sent_at"], 123456.0)

    def test_count_tasks_for_template_and_folder_delete(self):
        self.db.save_template("multi_folder_ad", "Text", [], [])
        self.db.add_or_update_broadcast(chat_id=1, template_name="multi_folder_ad", mode="interval", interval_seconds=10, folder_name="FolderA")
        self.db.add_or_update_broadcast(chat_id=2, template_name="multi_folder_ad", mode="interval", interval_seconds=10, folder_name="FolderA")
        self.db.add_or_update_broadcast(chat_id=3, template_name="multi_folder_ad", mode="interval", interval_seconds=10, folder_name="FolderB")

        self.assertEqual(self.db.count_tasks_for_template("multi_folder_ad"), 3)

        # Delete only FolderA tasks
        deleted = self.db.delete_broadcasts(name="multi_folder_ad", folder_name="FolderA")
        self.assertEqual(deleted, 2)
        self.assertEqual(self.db.count_tasks_for_template("multi_folder_ad"), 1)

        # Template still exists because FolderB uses it!
        self.assertIsNotNone(self.db.get_template("multi_folder_ad"))

    def test_delete_named_rotation_cleans_tasks(self):
        self.db.save_template("postA", "A", [], [])
        self.db.save_template("postB", "B", [], [])
        self.db.set_named_rotation("rot_to_delete", ["postA", "postB"])

        # Add broadcast task running this rotation
        self.db.add_or_update_broadcast(chat_id=999, template_name="rot_to_delete", mode="interval", interval_seconds=15)
        self.assertEqual(len(self.db.get_chat_broadcasts(999)), 1)

        # Delete named rotation: should remove rotation AND tasks running it
        deleted = self.db.delete_named_rotation("rot_to_delete")
        self.assertTrue(deleted)
        self.assertIsNone(self.db.get_named_rotation("rot_to_delete"))
        self.assertEqual(len(self.db.get_chat_broadcasts(999)), 0)

    def test_empty_post_safety(self):
        import asyncio

        class MockClient:
            def __init__(self):
                self.messages = []
            async def send_message(self, chat_peer, message, formatting_entities=None):
                if not message:
                    raise ValueError("Cannot send empty message")
                self.messages.append(message)

        client = MockClient()
        # Should return safely without raising error
        asyncio.run(send_single_item(client=client, chat_peer=123, text="", entities_hex=[], media_files=[]))
    def test_dialog_filter_chatlist_detection(self):
        from telethon.tl.types import TextWithEntities, InputPeerChannel
        # Test folder title extraction with TextWithEntities and string
        f1 = DialogFilterChatlist(
            id=10,
            title=TextWithEntities(text="Папка Чатов", entities=[]),
            pinned_peers=[],
            include_peers=[InputPeerChannel(12345, 67890)],
            has_my_invites=False
        )
        self.assertEqual(_extract_folder_title(f1), "Папка Чатов")

        f2 = DialogFilterChatlist(
            id=11,
            title="Простая папка",
            pinned_peers=[],
            include_peers=[],
            has_my_invites=False
        )
        self.assertEqual(_extract_folder_title(f2), "Простая папка")

    def test_heuristic_spam_recognition_engine(self):
        from telethon.tl.types import MessageEntityMentionName, MessageEntityMention

        # 1. Malone Image 1 (Full promotional structured post)
        malone1 = (
            "🔥 Продам канал эро тематики\n\n"
            "· https://t.me/+a7SAI2WrDL0zZDFi\n\n"
            "Продам чат азартной тематики\n\n"
            "· @piar_chat_azart\n\n"
            "👉Пишите в лс @SuKoW"
        )
        score1, trigger1, factors1 = calculate_spam_score(malone1)
        self.assertGreaterEqual(score1, 50)
        self.assertTrue(is_spam_message(malone1)[0])

        # 2. Malone Image 2 (Recruitment banner with emoji contact)
        malone2 = (
            "🔥 Ищу менеджеров по продажам рекламы в мой канал - @memobaze\n\n"
            "📩@SuKoW"
        )
        score2, trigger2, factors2 = calculate_spam_score(malone2)
        self.assertGreaterEqual(score2, 50)
        self.assertTrue(is_spam_message(malone2)[0])

        # 3. Hidden ghost mention under emoji
        class DummyMsg:
            def __init__(self, entities):
                self.entities = entities

        ghost_text = "🔥 Привет всем!"
        msg_ghost = DummyMsg([MessageEntityMentionName(offset=0, length=1, user_id=987654)])
        score_ghost, trigger_ghost, factors_ghost = calculate_spam_score(ghost_text, message=msg_ghost)
        self.assertEqual(score_ghost, 100)
        self.assertEqual(trigger_ghost, "hidden_ghost_mention")
        self.assertTrue(is_spam_message(ghost_text, message=msg_ghost)[0])

        # 4. Mass entity tagging (tagging multiple users)
        mass_text = "Смотрите все сюда"
        msg_mass = DummyMsg([
            MessageEntityMentionName(offset=0, length=4, user_id=111),
            MessageEntityMentionName(offset=9, length=3, user_id=222)
        ])
        score_mass, trigger_mass, factors_mass = calculate_spam_score(mass_text, message=msg_mass)
        self.assertEqual(score_mass, 100)
        self.assertEqual(trigger_mass, "mass_entity_tagging")
        self.assertTrue(is_spam_message(mass_text, message=msg_mass)[0])

        # 5. Ordinary user messages MUST NOT be flagged as spam
        safe_messages = [
            "продашь рекламу?",
            "почем канал?",
            "привет, я вчера продал свой канал @mytest, теперь отдыхаю",
            "а сколько стоит реклама в твоем канале?",
            "привет! как дела?",
            "глянь прикол https://t.me/funny_channel/123",
        ]
        for safe_msg in safe_messages:
            s, trig, f = calculate_spam_score(safe_msg)
            self.assertLess(s, 50, f"Ordinary message wrongly scored >= 50: {safe_msg} (score={s}, factors={f})")
            self.assertFalse(is_spam_message(safe_msg)[0], f"Ordinary message wrongly flagged: {safe_msg}")

    def test_clear_spam_mention_forum_and_types(self):
        import asyncio
        from telethon.tl.types import InputPeerChannel, MessageReplyHeader

        calls = []

        class MockClient:
            async def __call__(self, request):
                calls.append(type(request).__name__)
                return True

            async def get_input_entity(self, peer):
                return InputPeerChannel(channel_id=12345, access_hash=67890)

            async def send_read_acknowledge(self, entity, max_id=None, clear_mentions=False, clear_reactions=False):
                calls.append("send_read_acknowledge")
                return True

        class MockMessage:
            def __init__(self, msg_id=100, topic_id=None):
                self.id = msg_id
                self.reply_to = MessageReplyHeader(forum_topic=True, reply_to_msg_id=topic_id) if topic_id else None

        client = MockClient()
        msg = MockMessage(msg_id=555, topic_id=42)

        asyncio.run(clear_spam_mention(client, 12345, msg))

        self.assertIn("ReadMentionsRequest", calls)
        self.assertIn("ReadReactionsRequest", calls)
        self.assertIn("ReadHistoryRequest", calls)
        self.assertIn("send_read_acknowledge", calls)

    def test_message_dedup_ring(self):
        ring = MessageDedupRing(maxsize=3)
        # First occurrence: not a duplicate
        self.assertFalse(ring.check_and_add(100, 1))
        # Second occurrence: duplicate!
        self.assertTrue(ring.check_and_add(100, 1))

        # Add more messages up to maxsize
        self.assertFalse(ring.check_and_add(100, 2))
        self.assertFalse(ring.check_and_add(100, 3))
        # (100, 2) and (100, 3) are now seen
        self.assertTrue(ring.check_and_add(100, 2))
        self.assertTrue(ring.check_and_add(100, 3))

        # Exceeding capacity drops oldest (100, 1)
        self.assertFalse(ring.check_and_add(100, 4))
        self.assertTrue(ring.check_and_add(100, 4))
        # (100, 1) was evicted, so check_and_add should now return False
        self.assertFalse(ring.check_and_add(100, 1))

    def test_broadcaster_singleton_instance_cleanup(self):
        import asyncio

        class DummyClient:
            pass

        b1 = BroadcasterService(DummyClient(), account_id=12345)
        b1.start()
        self.assertTrue(b1.is_running)
        self.assertIs(BroadcasterService._ACTIVE_INSTANCES.get(12345), b1)

        # Starting another instance for same account must stop b1
        b2 = BroadcasterService(DummyClient(), account_id=12345)
        b2.start()
        self.assertFalse(b1.is_running)
        self.assertTrue(b2.is_running)
        self.assertIs(BroadcasterService._ACTIVE_INSTANCES.get(12345), b2)

        # Stopping b2 removes it from active map
        b2.stop()
        self.assertFalse(b2.is_running)
        self.assertIsNone(BroadcasterService._ACTIVE_INSTANCES.get(12345))

    def test_broadcaster_sending_locks_and_debounce(self):
        import asyncio

        class DummyClient:
            pass

        self.db.save_template("test_dedup_tpl", "Text", [], [])
        b = BroadcasterService(DummyClient(), account_id=999)

        # 1. In-flight chat lock test
        b._sending_chats.add(555)
        res = asyncio.run(b._send_task(1, 555, "test_dedup_tpl"))
        self.assertFalse(res)  # Must be blocked by in-flight lock
        b._sending_chats.clear()

        # 2. Debounce cooldown test (< 4.0s)
        b._last_sent_chat[555] = time.time()
        res = asyncio.run(b._send_task(1, 555, "test_dedup_tpl"))
        self.assertFalse(res)  # Must be blocked by cooldown debounce

        # 3. Non-existent template test
        res = asyncio.run(b._send_task(1, 777, "non_existent_template"))
        self.assertFalse(res)

    def test_register_events_idempotency(self):
        registered = []

        class DummyMockClient:
            def on(self, event_builder):
                def decorator(f):
                    registered.append(f.__name__)
                    return f
                return decorator

        client = DummyMockClient()
        broadcaster = BroadcasterService(client, account_id=888)

        # 1st call registers handlers
        register_events(client, broadcaster, my_id=888)
        self.assertEqual(len(registered), 2)
        self.assertTrue(getattr(client, "_spambuster_events_registered", False))
        self.assertIs(client._spambuster_broadcaster, broadcaster)

        # 2nd call does NOT register duplicate handlers
        register_events(client, broadcaster, my_id=888)
        self.assertEqual(len(registered), 2)

    def test_is_internal_bot_message(self):
        # The exact message the user saw
        garbage = (
            "📥 Сообщение #2 добавлено в рассылку **раз**.\n"
            "Отправьте следующее сообщение или напишите `.закрыть` (для отмены `.отменить`)."
        )
        self.assertTrue(is_internal_bot_message(garbage))

        garbage_alt = (
            "📥 Сообщение #1 добавлено в рассылку раз.\n"
            "Отправьте следующее сообщение или напишите .закрыть (для отмены .отменить)."
        )
        self.assertTrue(is_internal_bot_message(garbage_alt))

        # Other system messages
        self.assertTrue(is_internal_bot_message("📥 Режим сбора пачки сообщений для рассылки test активирован!"))
        self.assertTrue(is_internal_bot_message("⚠️ Время ожидания сообщений (5 минут) истекло. Сессия сброшена."))
        self.assertTrue(is_internal_bot_message("⚙️ Управление юзерботом\n1. Обычная рассылка"))
        self.assertTrue(is_internal_bot_message("👁 Тестовый предпросмотр: **test**"))

        # Real user advertising posts must NOT be flagged
        real_post = "🔥 Продам канал эро тематики\n· @channel\n👉 Пишите в лс @user"
        self.assertFalse(is_internal_bot_message(real_post))
        self.assertFalse(is_internal_bot_message("Привет всем в чате!"))
        self.assertFalse(is_internal_bot_message("Купите рекламу"))

    def test_template_bundle_auto_purges_bot_messages(self):
        import json

        # Save template with dirty bundle containing real post + garbage bot notification
        dirty_bundle = [
            {"text": "Настоящий рекламный пост #1", "entities_hex": [], "media_files": []},
            {
                "text": "📥 Сообщение #2 добавлено в рассылку **раз**.\nОтправьте следующее сообщение или напишите `.закрыть` (для отмены `.отменить`).",
                "entities_hex": [],
                "media_files": []
            }
        ]
        self.db.save_template("test_dirty", "Root text", [], [], bundle_json=json.dumps(dirty_bundle))

        # get_template must automatically purge the garbage bot notification
        tpl = self.db.get_template("test_dirty")
        self.assertIsNotNone(tpl)
        self.assertEqual(len(tpl["bundle"]), 1)
        self.assertEqual(tpl["bundle"][0]["text"], "Настоящий рекламный пост #1")

        # Database must be updated as well
        with self.db._conn() as conn:
            row = conn.execute("SELECT bundle_json FROM templates WHERE name = 'test_dirty'").fetchone()
            saved_bundle = json.loads(row["bundle_json"])
            self.assertEqual(len(saved_bundle), 1)
            self.assertEqual(saved_bundle[0]["text"], "Настоящий рекламный пост #1")

    def test_other_user_commands_ignored(self):
        import asyncio

        executed_actions = []

        class DummyMockClient:
            def __init__(self):
                self.handlers = []

            def on(self, event_builder):
                def decorator(f):
                    self.handlers.append((event_builder, f))
                    return f
                return decorator

        client = DummyMockClient()
        broadcaster = BroadcasterService(client, account_id=777)
        register_events(client, broadcaster, my_id=777)

        dispatcher = None
        for builder, handler in client.handlers:
            if handler.__name__ == "command_dispatcher":
                dispatcher = handler
                break

        self.assertIsNotNone(dispatcher)

        class MockEvent:
            def __init__(self, out, sender_id, text):
                self.out = out
                self.sender_id = sender_id
                self.raw_text = text
                self.chat_id = 99999
                self.message = self
                self.media = None
                self.id = 12345
            async def respond(self, *args, **kwargs):
                executed_actions.append("respond")
            async def edit(self, *args, **kwargs):
                executed_actions.append("edit")

        # 1. Another user writes .инструкция
        event_other = MockEvent(out=False, sender_id=88888, text=".инструкция")
        asyncio.run(dispatcher(event_other))
        self.assertEqual(len(executed_actions), 0)

        # 2. Another user writes .рассыл
        event_other_broadcast = MockEvent(out=False, sender_id=88888, text=".рассыл каждое 15с promo")
        asyncio.run(dispatcher(event_other_broadcast))
        self.assertEqual(len(executed_actions), 0)

        # 3. Another user writes .стоп
        event_other_stop = MockEvent(out=False, sender_id=88888, text=".стоп")
        asyncio.run(dispatcher(event_other_stop))
        self.assertEqual(len(executed_actions), 0)

        # 4. Our own user writes .инструкция -> must be processed!
        event_me = MockEvent(out=True, sender_id=777, text=".инструкция")
        asyncio.run(dispatcher(event_me))
        self.assertGreater(len(executed_actions), 0)

if __name__ == "__main__":
    unittest.main()


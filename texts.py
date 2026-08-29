# TODO: replace with the real main-menu caption once the full screenshot is provided
MAIN_MENU_CAPTION = (
    "ვეძებთ🦶ნებისმიერ ქალაქში ! Minimum Deposit 500 Gel\n\n"
    "1ცალის შეფუთვა,მიტანა 35Gel\n"
    "2ცალის შეფუთვა,მიტანა 60Gel\n\n"
    "დაინტერესებულმა პირებმა მომწერეთ სიტყვა (ვაკანსია)\n"
    "Owner: @Mamahaduna\n"
    "Chief Operator: @PAIN7525\n\n"
    "ჯგუფში \"Session\" აპლიკაციაზე ID:\n"
    "05a08265a2974187a3ded92e13030a187f46cb4e34e4666be922688684a124ce28"
)

CITIES_BUTTON = "🏙️ქალაქები🌆 (1)"
CHOOSE_CITY_TEXT = "აირჩიეთ ქალაქი:"
BACK_BUTTON = "⬅️ უკან"
TBILISI_BUTTON = "Tbilisi"

CITY_SELECTED_CAPTION = "თქვენ აირჩიეთ ქალაქი: {city}\n\nპროდუქტის არჩევა:"

PRODUCT_APOCALYPSE_1000 = "Apocalypse 🔥 (1000წალი) 20000$"
PRODUCT_APOCALYPSE_500 = "Apocalypse 🔥 (500წალი) 11500$"
PRODUCT_PRADA_2 = "Prada 😈 (2წალი) 150$"

# button text -> callback_data, products listed for Tbilisi
TBILISI_PRODUCTS = {
    PRODUCT_APOCALYPSE_1000: "product:apocalypse_1000",
    PRODUCT_APOCALYPSE_500: "product:apocalypse_500",
    PRODUCT_PRADA_2: "product:prada_2",
}
TBILISI_PRODUCT_CALLBACKS = {v: k for k, v in TBILISI_PRODUCTS.items()}

BALANCE_BUTTON = "💵ბალანსი💶 ({balance} $)"
PURCHASES_BUTTON = "🎁შესყიდვები🎁 (0)"
REFERRAL_BUTTON = "🔥რეფერალი🔥"
WORK_BUTTON = "👊სამუშაო (15$) 🏃"
GROUPS_BUTTON = "🧑‍🤝‍🧑 Group's & Channel's..."
LANGUAGE_BUTTON = "ენა 🇬🇪"
RESERVE_BOTS_BUTTON = "🤖რეზერვი ბოტები🤖"

PLACEHOLDER_TEXT = "🚧 მალე დაემატება"

# No more placeholder buttons — all are now implemented
PLACEHOLDER_BUTTONS = {}

# callback_data -> button text, reverse lookup for the placeholder handler
PLACEHOLDER_CALLBACKS = {v: k for k, v in PLACEHOLDER_BUTTONS.items()}

# ── Animated sticker shown before crypto deposit ───────────────────
DEPOSIT_STICKER_ID = "CAACAgQAAxkBAAERy9xqkTWxf1WQZAv_ERfXuQK_snWsLQACnxEAAqbxcR57wYUDyflSIT0E"

# ── Balance screen ─────────────────────────────────────────────────
BALANCE_CAPTION = (
    "თქვენი ბალანსი: {balance} $\n\n"
    "ბალანსის შევსება:"
)

BALANCE_DEPOSIT_CAPTION = (
    "💳 Coin: {coin}\n\n"
    "🏧 TOP UP YOUR BALANCE: 🏧\n"
    "Pay any amount to the wallet:\n\n"
    "`{wallet_address}`\n\n"
    "🤖 Payment will be processing automatically within a "
    "few minutes"
)

DEPOSIT_TOPUP_BUTTON = "🪄 Пополнить баланс еще раз"

# ── Purchases screen ──────────────────────────────────────────────
PURCHASES_CAPTION = "🎁თქვენი შესყიდვები🎁"

# ── Referral screen ───────────────────────────────────────────────
REFERRAL_CAPTION = (
    "Referals: {referrals}\n"
    "Earned: {earned} $\n\n"
    "როდესაც ახალ მომხმარებლებს ჩვენს ბოტზე "
    "გადაამისამართებთ (უნიკალური ბმულის გამოყენებით)\n"
    "თანხა ავტომატურად დაემატება თქვენს ბალანსს: 2.0%\n"
    "მითითებული მომხმარებების ყოველი "
    "შესყიდვიდან\n\n\n"
    " თქვენი ბმული, რომელიც უნდა გადასცეთ თქვენს "
    "მეგობრებს რეფერენტთან დასაკავშირებლად.\n"
    "სისტემები: https://t.me/{bot_username}?"
    "start={user_id}"
)

# ── Work screen ───────────────────────────────────────────────────
WORK_CAPTION = (
    "ვეძებთ კურიერებს 🦶  ყველა ქალაქში. მხოლოდ "
    "დეპოზიტით !!!\n"
    "მინიმალური დეპოზიტი 500 Gel.\n\n"
    "ანაზღაურება:\n"
    "1 ცალი სუნამოს შეფუთვა, მიტანა 35 Gel.\n"
    "2 ცალი სუნამოს შეფუთვა, მიტანა 60 Gel.\n"
    "3 ცალი სუნამოს შეფუთვა, მიტანა 70 Gel.\n"
    "4 ცალი სუნამოს შეფუთვა, მიტანა 80 Gel.\n\n"
    "დამატებით ინფორმაციისთვის მომწერეთ:\n"
    "Admin: @Mamahaduna\n"
    "სესიონის ჯგუფში მოსახვედრად რომელიც ღია 24/7\n"
    "ზე მომწერეთ აქ.\n\n"
    "Session ID:\n"
    "05a08265a2974187a3ded92e13030a187f46cb4e34e4666"
    "be922688684a124ce28"
)

# ── Groups & Channels screen ─────────────────────────────────────
GROUPS_CAPTION = (
    "გაიცანით ჩვენი მეგობარი ჯგუფები და ჩენელები ! 🔥\n"
    "❤️\n\n"
    "24/7 ჯგუფი სესიონ აპლიკაციაზე რომელიც არ "
    "უქმდება და სულ ღიაა Session ID: "
    "05a08265a2974187a3ded92e13030a187f46cb4e34e4666"
    "be922688684a124ce28\n\n"
    "1) https://t.me/FriendListBack\n"
    "2) https://t.me/DARKWORLDTBILISI\n"
    "3) https://t.me/+3-wBYu6gD1w0Yjdi\n"
    "4) https://t.me/Barambino\n"
    "5) https://t.me/+XcN3Imz2f_g5YTAy\n"
    "6) https://t.me/+HoaLlPir7k00MzQx"
)

# ── Language screen ──────────────────────────────────────────────
LANGUAGE_CAPTION = (
    "შეარჩიე ენა\n\n"
    "Choose Language\n\n"
    "Выберете Язык"
)

LANGUAGE_KA_BUTTON = "ქართული 🇬🇪"
LANGUAGE_RU_BUTTON = "Russian 🇷🇺"
LANGUAGE_EN_BUTTON = "English 🇬🇧"

# ── Reserve Bots screen ──────────────────────────────────────────
RESERVE_BOTS_CAPTION = (
    "MaHades_bot\n"
    "Madhades_bot\n"
    "Botbutahado_bot\n"
    "MamaHadundula_bot"
)

# ── Admin notifications ──────────────────────────────────────────
NEW_USER_NOTIFICATION = (
    "🆕 New user started the bot\n\n"
    "Name: {full_name}\n"
    "Username: {username}\n"
    "Telegram ID: {user_id}"
)

DEPOSIT_NOTIFICATION = (
    "💰 Crypto Deposit {status}\n\n"
    "🪙 Coin: {coin}\n"
    "💵 Amount: {amount}\n"
    "📬 Address: {address}\n"
    "🔗 TX: {tx_link}\n"
    "✅ Confirmations: {confirmations}/{required}\n"
    "📊 Status: {status}"
)

# ── Product detail + district selection (HTML parse_mode) ──────────
PRODUCT_DETAIL_TEXT = (
    "თქვენ შეარჩიეთ პროდუქტი\n\n"
    "🏠 ქალაქი: {city}\n"
    "🛒 პროდუქტი: {product}\n\n"
    "<b>😈 აღწერა: {description}\n"
    "პრობლემის შემთხვევაში მიწერეთ ბოტზე არასბულ "
    "მოდერატორებს თქვენი მისამართის ემოჯის "
    "მიხედვით. ჩვენს ჯგუფში მოსახვედრნრად მომწერეთ "
    "Session აპლიკაციაზე.\n"
    "Id:\n{session_id}</b>\n\n"
    "აირჩიეთ უბანი"
)

CHOOSE_DISTRICT_TEXT = "აირჩიეთ უბანი:"

DISTRICT_MOSKOVIS = "Moskovis Gamziri"

# Districts available for Tbilisi: button text -> callback_data
TBILISI_DISTRICTS = {
    DISTRICT_MOSKOVIS: "district:moskovis",
}
TBILISI_DISTRICT_CALLBACKS = {v: k for k, v in TBILISI_DISTRICTS.items()}

# ── Order confirmation ──────────────────────────────────────────────
# ── Order confirmation (HTML parse_mode) ───────────────────────────
ORDER_CONFIRMATION_CAPTION = (
    "თქვენ აირჩიეთ კატეგორია: {district}\n\n"
    "🏠 ქალაქი: {city}\n"
    "🎯 უბანი: {district}\n\n"
    "🛒 პროდუქტი: {product}\n\n"
    "<b>😈 აღწერა: {description}\n"
    "პრობლემის შემთხვევაში მიწერეთ ბოტზე არასბულ "
    "მოდერატორებს თქვენი მისამართის ემოჯის "
    "მიხედვით. ჩვენს ჯგუფში მოსახვედრნრად მომწერეთ "
    "Session აპლიკაციაზე.\n"
    "Id:\n{session_id}</b>\n\n\n"
    "💰 თქვენი ბალანსი: {balance} $\n\n"
    "👑 გადახდის მეთოდის არჩევა: 👑"
)

# ── Crypto payment buttons ──────────────────────────────────────────
# Hardcoded exchange rates for now — admin panel will make these dynamic
CRYPTO_RATES = {
    "BTC": {"rate": 0.00001341, "label": "BTC გადახდისთვის"},
    "LTC": {"rate": 0.020592,   "label": "LTC გადახდისთვის"},
    "USDT": {"rate": 1.0331,    "label": "USDT TRC20 გადახდისთვის"},
}

# ── Crypto payment detail screen ────────────────────────────────────
CRYPTO_PAYMENT_CAPTION = (
    "💳 Coin: {coin}\n\n"
    "🏧 TOP UP YOUR BALANCE: 🏧\n"
    "Pay any amount to the wallet:\n\n"
    "`{wallet_address}`\n\n"
    "🤖 Payment will be processing automatically within a "
    "few minutes\n\n"
    "➖ 🔵 ➖ 🔵 ➖ 🔵 ➖\n\n"
    "*💎 TO PAY: {crypto_amount} {coin}*\n\n"
    "*🎁 Product: {product}*\n\n"
    "*✏ Address: {city}-{district}*"
)


# ══════════════════════════════════════════════════════════════════════
#  i18n — Translations
# ══════════════════════════════════════════════════════════════════════

TRANSLATIONS = {
    "ka": {
        "MAIN_MENU_CAPTION": MAIN_MENU_CAPTION,
        "CITIES_BUTTON": "🏙️ქალაქები🌆 (1)",
        "CHOOSE_CITY_TEXT": "აირჩიეთ ქალაქი:",
        "BACK_BUTTON": "⬅️ უკან",
        "TBILISI_BUTTON": "Tbilisi",
        "CITY_SELECTED_CAPTION": "თქვენ აირჩიეთ ქალაქი: {city}\n\nპროდუქტის არჩევა:",
        "BALANCE_BUTTON": "💵ბალანსი💶 ({balance} $)",
        "PURCHASES_BUTTON": "🎁შესყიდვები🎁 (0)",
        "REFERRAL_BUTTON": "🔥რეფერალი🔥",
        "WORK_BUTTON": "👊სამუშაო (15$) 🏃",
        "GROUPS_BUTTON": "🧑‍🤝‍🧑 Group's & Channel's...",
        "LANGUAGE_BUTTON": "ენა 🇬🇪",
        "RESERVE_BOTS_BUTTON": "🤖რეზერვი ბოტები🤖",
        "BALANCE_CAPTION": "თქვენი ბალანსი: {balance} $\n\nბალანსის შევსება:",
        "PURCHASES_CAPTION": "🎁თქვენი შესყიდვები🎁",
        "REFERRAL_CAPTION": (
            "Referals: {referrals}\n"
            "Earned: {earned} $\n\n"
            "როდესაც ახალ მომხმარებლებს ჩვენს ბოტზე "
            "გადაამისამართებთ (უნიკალური ბმულის გამოყენებით)\n"
            "თანხა ავტომატურად დაემატება თქვენს ბალანსს: 2.0%\n"
            "მითითებული მომხმარებების ყოველი "
            "შესყიდვიდან\n\n\n"
            " თქვენი ბმული, რომელიც უნდა გადასცეთ თქვენს "
            "მეგობრებს რეფერენტთან დასაკავშირებლად.\n"
            "სისტემები: https://t.me/{bot_username}?"
            "start={user_id}"
        ),
        "WORK_CAPTION": WORK_CAPTION,
        "GROUPS_CAPTION": GROUPS_CAPTION,
        "LANGUAGE_CAPTION": LANGUAGE_CAPTION,
        "RESERVE_BOTS_CAPTION": RESERVE_BOTS_CAPTION,
        "PRODUCT_DETAIL_TEXT": PRODUCT_DETAIL_TEXT,
        "CHOOSE_DISTRICT_TEXT": "აირჩიეთ უბანი:",
        "ORDER_CONFIRMATION_CAPTION": ORDER_CONFIRMATION_CAPTION,
        "CRYPTO_RATES": {
            "BTC": {"rate": 0.00001341, "label": "BTC გადახდისთვის"},
            "LTC": {"rate": 0.020592,   "label": "LTC გადახდისთვის"},
            "USDT": {"rate": 1.0331,    "label": "USDT TRC20 გადახდისთვის"},
        },
    },
    "ru": {
        "MAIN_MENU_CAPTION": (
            "Ищем🦶в любом городе ! Минимальный депозит 500 Gel\n\n"
            "Упаковка и доставка 1шт 35Gel\n"
            "Упаковка и доставка 2шт 60Gel\n\n"
            "Заинтересованные лица пишите слово (вакансия)\n"
            "Owner: @Mamahaduna\n"
            "Chief Operator: @PAIN7525\n\n"
            "Группа в приложении \"Session\" ID:\n"
            "05a08265a2974187a3ded92e13030a187f46cb4e34e4666be922688684a124ce28"
        ),
        "CITIES_BUTTON": "🏙️Города🌆 (1)",
        "CHOOSE_CITY_TEXT": "Выберите город:",
        "BACK_BUTTON": "⬅️ Назад",
        "TBILISI_BUTTON": "Tbilisi",
        "CITY_SELECTED_CAPTION": "Вы выбрали город: {city}\n\nВыбор продукта:",
        "BALANCE_BUTTON": "💵Баланс💶 ({balance} $)",
        "PURCHASES_BUTTON": "🎁Покупки🎁 (0)",
        "REFERRAL_BUTTON": "🔥Реферал🔥",
        "WORK_BUTTON": "👊Работа (15$) 🏃",
        "GROUPS_BUTTON": "🧑‍🤝‍🧑 Группы и каналы...",
        "LANGUAGE_BUTTON": "Язык 🇷🇺",
        "RESERVE_BOTS_BUTTON": "🤖Резервные боты🤖",
        "BALANCE_CAPTION": "Ваш баланс: {balance} $\n\nПополнение баланса:",
        "PURCHASES_CAPTION": "🎁Ваши покупки🎁",
        "REFERRAL_CAPTION": (
            "Рефералы: {referrals}\n"
            "Заработано: {earned} $\n\n"
            "Когда вы перенаправляете новых пользователей в наш бот "
            "(используя уникальную ссылку)\n"
            "сумма автоматически добавится на ваш баланс: 2.0%\n"
            "с каждой покупки указанных пользователей\n\n\n"
            "Ваша ссылка, которую нужно передать друзьям "
            "для подключения к рефералу.\n"
            "Системы: https://t.me/{bot_username}?"
            "start={user_id}"
        ),
        "WORK_CAPTION": (
            "Ищем курьеров 🦶 во всех городах. Только "
            "с депозитом !!!\n"
            "Минимальный депозит 500 Gel.\n\n"
            "Оплата:\n"
            "1 шт упаковка и доставка 35 Gel.\n"
            "2 шт упаковка и доставка 60 Gel.\n"
            "3 шт упаковка и доставка 70 Gel.\n"
            "4 шт упаковка и доставка 80 Gel.\n\n"
            "Для дополнительной информации пишите:\n"
            "Admin: @Mamahaduna\n"
            "Для входа в сессионную группу 24/7\n"
            "пишите сюда.\n\n"
            "Session ID:\n"
            "05a08265a2974187a3ded92e13030a187f46cb4e34e4666"
            "be922688684a124ce28"
        ),
        "GROUPS_CAPTION": (
            "Познакомьтесь с нашими дружественными группами и каналами ! 🔥\n"
            "❤️\n\n"
            "24/7 группа в приложении Session которая не "
            "закрывается и всегда открыта Session ID: "
            "05a08265a2974187a3ded92e13030a187f46cb4e34e4666"
            "be922688684a124ce28\n\n"
            "1) https://t.me/FriendListBack\n"
            "2) https://t.me/DARKWORLDTBILISI\n"
            "3) https://t.me/+3-wBYu6gD1w0Yjdi\n"
            "4) https://t.me/Barambino\n"
            "5) https://t.me/+XcN3Imz2f_g5YTAy\n"
            "6) https://t.me/+HoaLlPir7k00MzQx"
        ),
        "LANGUAGE_CAPTION": LANGUAGE_CAPTION,
        "RESERVE_BOTS_CAPTION": RESERVE_BOTS_CAPTION,
        "PRODUCT_DETAIL_TEXT": (
            "Вы выбрали продукт\n\n"
            "🏠 Город: {city}\n"
            "🛒 Продукт: {product}\n\n"
            "<b>😈 Описание: {description}\n"
            "В случае проблемы напишите боту неактивным "
            "модераторам по эмодзи вашего адреса. "
            "Для входа в нашу группу напишите "
            "в приложении Session.\n"
            "Id:\n{session_id}</b>\n\n"
            "Выберите район"
        ),
        "CHOOSE_DISTRICT_TEXT": "Выберите район:",
        "ORDER_CONFIRMATION_CAPTION": (
            "Вы выбрали категорию: {district}\n\n"
            "🏠 Город: {city}\n"
            "🎯 Район: {district}\n\n"
            "🛒 Продукт: {product}\n\n"
            "<b>😈 Описание: {description}\n"
            "В случае проблемы напишите боту неактивным "
            "модераторам по эмодзи вашего адреса. "
            "Для входа в нашу группу напишите "
            "в приложении Session.\n"
            "Id:\n{session_id}</b>\n\n\n"
            "💰 Ваш баланс: {balance} $\n\n"
            "👑 Выберите способ оплаты: 👑"
        ),
        "CRYPTO_RATES": {
            "BTC": {"rate": 0.00001341, "label": "BTC для оплаты"},
            "LTC": {"rate": 0.020592,   "label": "LTC для оплаты"},
            "USDT": {"rate": 1.0331,    "label": "USDT TRC20 для оплаты"},
        },
    },
    "en": {
        "MAIN_MENU_CAPTION": (
            "Looking for🦶in any city! Minimum Deposit 500 Gel\n\n"
            "1pc packaging & delivery 35Gel\n"
            "2pc packaging & delivery 60Gel\n\n"
            "Interested persons write the word (vacancy)\n"
            "Owner: @Mamahaduna\n"
            "Chief Operator: @PAIN7525\n\n"
            "Group on \"Session\" app ID:\n"
            "05a08265a2974187a3ded92e13030a187f46cb4e34e4666be922688684a124ce28"
        ),
        "CITIES_BUTTON": "🏙️Cities🌆 (1)",
        "CHOOSE_CITY_TEXT": "Choose a city:",
        "BACK_BUTTON": "⬅️ Back",
        "TBILISI_BUTTON": "Tbilisi",
        "CITY_SELECTED_CAPTION": "You selected city: {city}\n\nChoose a product:",
        "BALANCE_BUTTON": "💵Balance💶 ({balance} $)",
        "PURCHASES_BUTTON": "🎁Purchases🎁 (0)",
        "REFERRAL_BUTTON": "🔥Referral🔥",
        "WORK_BUTTON": "👊Work (15$) 🏃",
        "GROUPS_BUTTON": "🧑‍🤝‍🧑 Groups & Channels...",
        "LANGUAGE_BUTTON": "Language 🇬🇧",
        "RESERVE_BOTS_BUTTON": "🤖Reserve Bots🤖",
        "BALANCE_CAPTION": "Your balance: {balance} $\n\nTop up balance:",
        "PURCHASES_CAPTION": "🎁Your purchases🎁",
        "REFERRAL_CAPTION": (
            "Referrals: {referrals}\n"
            "Earned: {earned} $\n\n"
            "When you redirect new users to our bot "
            "(using a unique link)\n"
            "the amount will be automatically added to your balance: 2.0%\n"
            "from each purchase of referred users\n\n\n"
            "Your link to share with friends "
            "to connect to the referral.\n"
            "Systems: https://t.me/{bot_username}?"
            "start={user_id}"
        ),
        "WORK_CAPTION": (
            "Looking for couriers 🦶 in all cities. Only "
            "with deposit!!!\n"
            "Minimum deposit 500 Gel.\n\n"
            "Payment:\n"
            "1pc packaging & delivery 35 Gel.\n"
            "2pc packaging & delivery 60 Gel.\n"
            "3pc packaging & delivery 70 Gel.\n"
            "4pc packaging & delivery 80 Gel.\n\n"
            "For more information write to:\n"
            "Admin: @Mamahaduna\n"
            "To join the session group which is open 24/7\n"
            "write here.\n\n"
            "Session ID:\n"
            "05a08265a2974187a3ded92e13030a187f46cb4e34e4666"
            "be922688684a124ce28"
        ),
        "GROUPS_CAPTION": (
            "Meet our friendly groups and channels! 🔥\n"
            "❤️\n\n"
            "24/7 group on Session app which never "
            "closes and is always open Session ID: "
            "05a08265a2974187a3ded92e13030a187f46cb4e34e4666"
            "be922688684a124ce28\n\n"
            "1) https://t.me/FriendListBack\n"
            "2) https://t.me/DARKWORLDTBILISI\n"
            "3) https://t.me/+3-wBYu6gD1w0Yjdi\n"
            "4) https://t.me/Barambino\n"
            "5) https://t.me/+XcN3Imz2f_g5YTAy\n"
            "6) https://t.me/+HoaLlPir7k00MzQx"
        ),
        "LANGUAGE_CAPTION": LANGUAGE_CAPTION,
        "RESERVE_BOTS_CAPTION": RESERVE_BOTS_CAPTION,
        "PRODUCT_DETAIL_TEXT": (
            "You selected a product\n\n"
            "🏠 City: {city}\n"
            "🛒 Product: {product}\n\n"
            "<b>😈 Description: {description}\n"
            "In case of a problem write to the bot's inactive "
            "moderators by your address emoji. "
            "To join our group write on "
            "Session app.\n"
            "Id:\n{session_id}</b>\n\n"
            "Choose a district"
        ),
        "CHOOSE_DISTRICT_TEXT": "Choose a district:",
        "ORDER_CONFIRMATION_CAPTION": (
            "You selected category: {district}\n\n"
            "🏠 City: {city}\n"
            "🎯 District: {district}\n\n"
            "🛒 Product: {product}\n\n"
            "<b>😈 Description: {description}\n"
            "In case of a problem write to the bot's inactive "
            "moderators by your address emoji. "
            "To join our group write on "
            "Session app.\n"
            "Id:\n{session_id}</b>\n\n\n"
            "💰 Your balance: {balance} $\n\n"
            "👑 Choose payment method: 👑"
        ),
        "CRYPTO_RATES": {
            "BTC": {"rate": 0.00001341, "label": "BTC for payment"},
            "LTC": {"rate": 0.020592,   "label": "LTC for payment"},
            "USDT": {"rate": 1.0331,    "label": "USDT TRC20 for payment"},
        },
    },
}


def t(key: str, lang: str = "ka") -> str:
    """Get a translated string. Falls back to Georgian."""
    val = TRANSLATIONS.get(lang, TRANSLATIONS["ka"]).get(key)
    if val is None:
        val = TRANSLATIONS["ka"].get(key, key)
    return val


def t_rates(lang: str = "ka") -> dict:
    """Get translated crypto rates dict."""
    return TRANSLATIONS.get(lang, TRANSLATIONS["ka"]).get("CRYPTO_RATES", CRYPTO_RATES)

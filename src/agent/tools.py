"""LLM agent tool definitions for Claude API tool_use.

Canonical tool names from doc/development/00-overview.md.
MVP tools: search_tires, check_availability, transfer_to_operator.
Phase 2 tools: get_order_status, create_order_draft, update_order_delivery, confirm_order.
               Under sales the three-step chain is replaced by submit_order_request.
Phase 3 tools: get_fitting_stations, get_fitting_slots, book_fitting, cancel_fitting,
               get_fitting_price, search_knowledge_base.
"""

from __future__ import annotations

from typing import Any

from src.knowledge.categories import CATEGORY_VALUES

# MVP tools — Claude API tool_use format
MVP_TOOLS: list[dict] = [  # type: ignore[type-arg]
    {
        "name": "get_vehicle_tire_sizes",
        "description": (
            "Отримати заводські розміри шин для автомобіля. "
            "Використовуй ПЕРЕД search_tires, коли клієнт називає авто. "
            "Повертає стокові та допустимі розміри шин."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "brand": {"type": "string", "description": "Марка (Kia, Toyota, BMW)"},
                "model": {"type": "string", "description": "Модель (Sportage, Camry, X5)"},
                "year": {"type": "integer", "description": "Рік випуску"},
            },
            "required": ["brand", "model"],
        },
    },
    {
        "name": "search_tires",
        "description": (
            "Пошук шин у каталозі магазину. "
            "УВАГА: НЕ викликай цей інструмент, поки не з'ясуєш у клієнта "
            "розмір шин (ширина/профіль/діаметр) та сезон. "
            "Спершу з'ясуй потреби, потім шукай."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "width": {
                    "type": "integer",
                    "description": "Ширина шини в мм (наприклад, 205, 225)",
                },
                "profile": {
                    "type": "integer",
                    "description": "Профіль шини в % (наприклад, 55, 60)",
                },
                "diameter": {
                    "type": "integer",
                    "description": "Діаметр диска в дюймах (наприклад, 16, 17)",
                },
                "season": {
                    "type": "string",
                    "enum": ["summer", "winter", "all_season"],
                    "description": "Сезон: літні, зимові або всесезонні",
                },
                "brand": {
                    "type": "string",
                    "description": "Бренд шин (наприклад, Michelin, Continental)",
                },
                "studded": {
                    "type": "boolean",
                    "description": (
                        "true — клієнт хоче шиповані, false — клієнт хоче без шипів (липучка). "
                        "Не передавай, якщо клієнт не казав про шипи."
                    ),
                },
                "runflat": {
                    "type": "boolean",
                    "description": (
                        "true — клієнту потрібні RunFlat, false — клієнт не хоче RunFlat. "
                        "Не передавай, якщо клієнт не казав про RunFlat."
                    ),
                },
                "rear_width": {
                    "type": "integer",
                    "description": (
                        "Тільки для різноширокої осі (staggered_pairs з get_vehicle_tire_sizes): "
                        "ширина задньої шини. Тоді width/profile/diameter — передня вісь."
                    ),
                },
                "rear_profile": {
                    "type": "integer",
                    "description": "Тільки для різноширокої осі: профіль задньої шини.",
                },
                "rear_diameter": {
                    "type": "integer",
                    "description": "Тільки для різноширокої осі: діаметр задньої шини.",
                },
            },
            "required": ["width", "profile", "diameter", "season"],
        },
    },
    {
        "name": "check_availability",
        "description": (
            "Перевірка наявності конкретного товару на складі. "
            "Використовуй після пошуку, коли клієнт обрав конкретну шину, "
            "або коли клієнт називає артикул (SKU) з сайту."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "product_id": {
                    "type": "string",
                    "description": "Артикул (SKU) товару — числовий код із сайту або ID з результатів пошуку",
                },
                "query": {
                    "type": "string",
                    "description": "Текстовий запит для пошуку по назві (НЕ для артикулу — використовуй product_id)",
                },
            },
        },
    },
    {
        "name": "transfer_to_operator",
        "description": (
            "Переключити клієнта на живого оператора. "
            "Використовуй, коли не можеш допомогти або клієнт просить оператора."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "enum": [
                        "customer_request",
                        "cannot_help",
                        "complex_question",
                        "negative_emotion",
                    ],
                    "description": "Причина переключення",
                },
                "summary": {
                    "type": "string",
                    "description": "Короткий опис розмови для оператора",
                },
            },
            "required": ["reason", "summary"],
        },
    },
]

# Phase 2: Order management tools
ORDER_TOOLS: list[dict] = [  # type: ignore[type-arg]
    {
        "name": "get_order_status",
        "description": (
            "Отримати статус замовлення за номером телефону або номером замовлення. "
            "Використовуй, коли клієнт запитує про статус замовлення."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "phone": {
                    "type": "string",
                    "description": "Номер телефону клієнта (+380XXXXXXXXX)",
                },
                "order_id": {
                    "type": "string",
                    "description": "Номер або ID замовлення",
                },
            },
        },
    },
    {
        "name": "create_order_draft",
        "description": (
            "Створити чорновик замовлення. "
            "Використовуй після того, як клієнт обрав шини і хоче замовити."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "description": "Список товарів для замовлення",
                    "items": {
                        "type": "object",
                        "properties": {
                            "product_id": {
                                "type": "string",
                                "description": "ID товару",
                            },
                            "quantity": {
                                "type": "integer",
                                "description": "Кількість (від 1 до 99)",
                            },
                        },
                        "required": ["product_id", "quantity"],
                    },
                },
                "customer_phone": {
                    "type": "string",
                    "description": "Номер телефону клієнта (+380XXXXXXXXX)",
                },
            },
            "required": ["items", "customer_phone"],
        },
    },
    {
        "name": "update_order_delivery",
        "description": (
            "Вказати спосіб та адресу доставки для замовлення. "
            "Використовуй після створення чорновика замовлення."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {
                    "type": "string",
                    "description": "ID замовлення",
                },
                "delivery_type": {
                    "type": "string",
                    "enum": ["delivery", "pickup"],
                    "description": "Тип: доставка або самовивіз",
                },
                "city": {
                    "type": "string",
                    "description": "Місто доставки",
                },
                "address": {
                    "type": "string",
                    "description": "Адреса доставки",
                },
                "pickup_point_id": {
                    "type": "string",
                    "description": "ID пункту самовивозу",
                },
            },
            "required": ["order_id", "delivery_type"],
        },
    },
    {
        "name": "confirm_order",
        "description": (
            "Передати заявку на замовлення менеджеру: заявка потрапляє в 1С, менеджер "
            "зателефонує клієнту для підтвердження. Замовлення цим НЕ підтверджується. "
            "ОБОВ'ЯЗКОВО: перед викликом оголоси клієнту склад, суму та отримай згоду 'так'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {
                    "type": "string",
                    "description": "ID замовлення",
                },
                "payment_method": {
                    "type": "string",
                    "enum": ["cod", "online", "card_on_delivery"],
                    "description": "Спосіб оплати: накладений платіж, онлайн, картка при отриманні",
                },
                "customer_name": {
                    "type": "string",
                    "description": "Ім'я клієнта для замовлення",
                },
            },
            "required": ["order_id", "payment_method"],
        },
    },
    {
        "name": "submit_order_request",
        "description": (
            "Передати заявку на замовлення менеджеру ОДНИМ викликом: заявка потрапляє в 1С, "
            "менеджер зателефонує клієнту для підтвердження. Замовлення цим НЕ підтверджується. "
            "Збирай поля по ходу розмови, а коли клієнт погодив підсумок — виклич з усіма "
            "полями. Якщо в результаті status=missing_field — запитай у клієнта саме те, що "
            "названо в message, і виклич ще раз з усіма полями."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "description": "Товари заявки: id товару з результату пошуку і кількість",
                    "items": {
                        "type": "object",
                        "properties": {
                            "product_id": {
                                "type": "string",
                                "description": "id товару з результату search_tires, "
                                "search_disks або check_availability",
                            },
                            "quantity": {
                                "type": "integer",
                                "description": "Кількість, яку назвав клієнт",
                            },
                        },
                        "required": ["product_id", "quantity"],
                    },
                },
                "delivery_type": {
                    "type": "string",
                    "enum": ["pickup", "delivery"],
                    "description": "pickup — самовивіз, delivery — доставка",
                },
                "pickup_point_id": {
                    "type": "string",
                    "description": "Для самовивозу: id пункту, який клієнт обрав "
                    "зі списку get_pickup_points",
                },
                "city": {
                    "type": "string",
                    "description": "Для доставки: місто",
                },
                "address": {
                    "type": "string",
                    "description": "Для доставки: адреса або відділення перевізника",
                },
                "recipient_name": {
                    "type": "string",
                    "description": "Прізвище та ім'я отримувача, як назвав клієнт",
                },
                "phone": {
                    "type": "string",
                    "description": "Телефон отримувача; не назвав інший — не передавай, "
                    "система візьме номер, з якого дзвонять",
                },
                "payment_method": {
                    "type": "string",
                    "enum": ["cod", "online", "card_on_delivery"],
                    "description": (
                        "cod — накладений платіж (оплата при отриманні), card_on_delivery — "
                        "карткою при отриманні, online — оплата карткою онлайн. Оплату "
                        "частинами чи передоплату оформить менеджер, коли зателефонує: "
                        "для них передай online."
                    ),
                },
            },
            "required": [
                "items",
                "delivery_type",
                "recipient_name",
                "payment_method",
            ],
        },
    },
    {
        "name": "get_pickup_points",
        "description": (
            "Отримати список пунктів видачі (самовивозу) для поточної мережі. "
            "Використовуй, коли клієнт обирає самовивіз як спосіб доставки. "
            "Після вибору пункту клієнтом — передай його id в update_order_delivery(pickup_point_id=...). "
            "УВАГА: якщо клієнт називає вулицю або район (наприклад 'Запорізьке шосе'), "
            "передай назву вулиці в параметр query, НЕ в city — це може бути вулиця в іншому місті."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "city": {
                    "type": "string",
                    "description": "Тільки назва міста (наприклад, 'Дніпро', 'Київ'). НЕ вулиця.",
                },
                "query": {
                    "type": "string",
                    "description": (
                        "Пошук за адресою, вулицею або районом "
                        "(наприклад, 'Запорізьке шосе', 'Перемоги')"
                    ),
                },
            },
            "required": [],
        },
    },
]

# Phase 3: Fitting and knowledge base tools
FITTING_TOOLS: list[dict] = [  # type: ignore[type-arg]
    {
        "name": "get_fitting_stations",
        "description": (
            "Отримати список точок шиномонтажу. "
            "Без параметрів — повертає всі точки (для відповіді 'в яких містах є шиномонтаж'). "
            "З city — фільтрує по місту. З query — шукає по адресі, району та орієнтирах "
            "(наприклад 'Епіцентр', 'Запорізьке шосе', 'лівий берег'). "
            "Кожна точка має підказки: district (район), landmarks (орієнтири). "
            "УВАГА: якщо клієнт називає орієнтир або район — передавай query, а не city."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "city": {
                    "type": "string",
                    "description": "Тільки назва міста (наприклад 'Дніпро', 'Київ')",
                },
                "query": {
                    "type": "string",
                    "description": (
                        "Пошук за адресою, районом або орієнтиром "
                        "(наприклад 'Епіцентр', 'шосе', 'лівий берег')"
                    ),
                },
                "for_price": {
                    "type": "boolean",
                    "description": (
                        "Передавай true, якщо це price consultation "
                        "(сценарій 4, get_fitting_price). Сервер пропускає "
                        "district-ask — ціни у мережі однакові між "
                        "точками того самого міста."
                    ),
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_fitting_slots",
        "description": (
            "Отримати доступні слоти для запису на шиномонтаж. "
            "Використовуй після вибору точки шиномонтажу. "
            "station_id ОБОВ'ЯЗКОВО бери з поля 'id' результату get_fitting_stations."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "station_id": {
                    "type": "string",
                    "description": "ID точки з результату get_fitting_stations (наприклад '000000003'). НЕ вигадуй — копіюй точне значення поля 'id'.",
                },
                "date_from": {
                    "type": "string",
                    "description": "Початкова дата у форматі YYYY-MM-DD",
                },
                "date_to": {
                    "type": "string",
                    "description": "Кінцева дата у форматі YYYY-MM-DD (за замовчуванням = date_from)",
                },
                "service_type": {
                    "type": "string",
                    "enum": ["tire_change", "balancing", "full_service"],
                    "description": "Тип послуги: заміна шин, балансування, повний сервіс",
                },
            },
            "required": ["station_id"],
        },
    },
    {
        "name": "book_fitting",
        "description": (
            "Записати клієнта на шиномонтаж. Використовуй після вибору точки, дати та часу. "
            "station_id бери з поля 'id' результату get_fitting_stations."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "station_id": {
                    "type": "string",
                    "description": "ID точки з результату get_fitting_stations (наприклад '000000003'). Копіюй точне значення поля 'id'.",
                },
                "date": {
                    "type": "string",
                    "description": "Дата запису (YYYY-MM-DD)",
                },
                "time": {
                    "type": "string",
                    "description": "Час запису (HH:MM)",
                },
                "customer_name": {
                    "type": "string",
                    "description": "Ім'я клієнта (як назвався)",
                },
                "customer_phone": {
                    "type": "string",
                    "description": "Телефон клієнта (0XXXXXXXXX — 10 цифр без +38)",
                },
                "auto_number": {
                    "type": "string",
                    "description": (
                        "Колір автомобіля (укр./рос.) — напр. «синій», «чорний», "
                        "«мокрий асфальт», «графітовий», «сріблястий». Приймай "
                        "як почув, не переспитуй формат. Якщо клієнт САМ "
                        "добровільно назвав держномер разом із кольором — "
                        "можеш об'єднати: «синій, AA1234BB». Не питай про "
                        "номер сама. У 1С зберігається як ідентифікатор авто "
                        "для оператора СТО."
                    ),
                },
                "vehicle_info": {
                    "type": "string",
                    "description": "Марка/модель автомобіля (якщо клієнт назвав)",
                },
                "service_type": {
                    "type": "string",
                    "enum": ["tire_change", "balancing", "full_service"],
                    "description": "Тип послуги",
                },
                "tire_diameter": {
                    "type": "integer",
                    "description": "Діаметр шин у дюймах — НЕ питай клієнта, передавай тільки якщо вже відомо",
                },
                "storage_contract": {
                    "type": "string",
                    "description": (
                        "Номер договору зберігання шин (11 цифр zero-padded, напр. '00000110596') — "
                        "поле Number з результату find_storage. ОБОВ'ЯЗКОВО передавай коли клієнт "
                        "монтуватиме шини зі зберігання. У 1С передається як NumberContract."
                    ),
                },
                "linked_order_id": {
                    "type": "string",
                    "description": "ID пов'язаного замовлення (якщо клієнт замовив шини)",
                },
            },
            "required": [
                "station_id",
                "date",
                "time",
                "customer_name",
                "customer_phone",
                "auto_number",
            ],
        },
    },
    {
        "name": "cancel_fitting",
        "description": (
            "Скасувати або перенести запис на шиномонтаж. "
            "Використовуй, коли клієнт хоче скасувати або змінити час запису. "
            "action='reschedule' переносить запис ОДНИМ викликом — окремий "
            "book_fitting після нього НЕ потрібен і заборонений. Для перенесення "
            "new_date і new_time обов'язкові; станція, авто та ім'я беруться з "
            "самого запису."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "booking_id": {
                    "type": "string",
                    "description": "ID запису на шиномонтаж",
                },
                "action": {
                    "type": "string",
                    "enum": ["cancel", "reschedule"],
                    "description": "Дія: скасувати або перенести",
                },
                "new_date": {
                    "type": "string",
                    "description": "Нова дата (YYYY-MM-DD, тільки для перенесення)",
                },
                "new_time": {
                    "type": "string",
                    "description": "Новий час (HH:MM, тільки для перенесення)",
                },
            },
            "required": ["booking_id", "action"],
        },
    },
    {
        "name": "get_fitting_price",
        "description": (
            "Дізнатися вартість шиномонтажу. Використовуй, коли клієнт запитує про ціну монтажу."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "tire_diameter": {
                    "type": "integer",
                    "description": "Діаметр шин у дюймах (наприклад, 16, 17)",
                },
                "station_id": {
                    "type": "string",
                    "description": "ID точки з get_fitting_stations (для конкретних цін)",
                },
                "service_type": {
                    "type": "string",
                    "enum": ["tire_change", "balancing", "full_service"],
                    "description": "Тип послуги",
                },
            },
            "required": ["tire_diameter"],
        },
    },
    {
        "name": "get_customer_bookings",
        "description": (
            "Перевірити існуючі записи клієнта на шиномонтаж за номером телефону. "
            "Використовуй, коли клієнт запитує про свої записи або хоче перевірити бронювання."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "phone": {
                    "type": "string",
                    "description": "Номер телефону (0XXXXXXXXX)",
                },
                "station_id": {
                    "type": "string",
                    "description": "ID станції з get_fitting_stations (опціонально)",
                },
            },
            "required": ["phone"],
        },
    },
    {
        "name": "reserve_fitting_slot",
        "description": (
            "Тимчасово забронювати слот на шиномонтаж (без повних даних клієнта). "
            "Використовуй, коли клієнт обрав час, але ще не назвав ім'я або номер авто — "
            "щоб слот не зайняли, поки збираєш дані. "
            "Після збору даних виклич book_fitting для повного запису."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "station_id": {
                    "type": "string",
                    "description": "ID точки з результату get_fitting_stations",
                },
                "date": {
                    "type": "string",
                    "description": "Дата (YYYY-MM-DD)",
                },
                "time": {
                    "type": "string",
                    "description": "Час (HH:MM)",
                },
                "comment": {
                    "type": "string",
                    "description": "Коментар (опціонально)",
                },
            },
            "required": ["station_id", "date", "time"],
        },
    },
    {
        "name": "find_storage",
        "description": (
            "Знайти договори зберігання шин клієнта за номером телефону або номером договору. "
            "Використовуй, коли клієнт запитує про зберігання шин або хоче забрати шини зі зберігання. "
            "Якщо є CallerID — одразу шукай за телефоном без додаткових питань."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "phone": {
                    "type": "string",
                    "description": "Номер телефону клієнта (0XXXXXXXXX або +380XXXXXXXXX)",
                },
                "storage_number": {
                    "type": "string",
                    "description": "Номер договору зберігання (або кілька через кому)",
                },
            },
            "required": [],
        },
    },
    {
        "name": "search_knowledge_base",
        "description": (
            "Пошук по базі знань магазину: акції, доставка, оплата, повернення, "
            "гарантія, бренди, порівняння, FAQ. МОЖНА викликати КІЛЬКА РАЗІВ."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Пошуковий запит (питання клієнта)",
                },
                "category": {
                    "type": "string",
                    "enum": CATEGORY_VALUES,
                    "description": "Категорія пошуку для точнішого результату",
                },
            },
            "required": ["query"],
        },
    },
]

# Callback tools — used when transfer_to_operator returned status="after_hours"
CALLBACK_TOOLS: list[dict] = [  # type: ignore[type-arg]
    {
        "name": "create_callback_request",
        "description": (
            "Зафіксувати заявку на зворотний дзвінок від оператора. "
            "Викликай ТІЛЬКИ якщо transfer_to_operator повернув status='after_hours' "
            "або 'error'/'unavailable' і клієнт погодився залишити номер. "
            "Оператор передзвонить у робочі години."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "phone": {
                    "type": "string",
                    "description": (
                        "Телефон клієнта у форматі +380XXXXXXXXX. "
                        "Якщо номер визначено автоматично (CallerID) — передавай його; "
                        "інакше запитай у клієнта і повтори за ним для підтвердження."
                    ),
                },
                "preferred_time": {
                    "type": "string",
                    "description": (
                        "Бажаний час дзвінка у вільній формі, як сказав клієнт "
                        "(наприклад, 'зранку', 'після 14:00', 'у понеділок'). "
                        "Якщо клієнт не вказав — пропусти поле."
                    ),
                },
                "note": {
                    "type": "string",
                    "description": (
                        "Короткий підсумок питання клієнта для оператора "
                        "(1-2 речення, українською)."
                    ),
                },
            },
            "required": ["phone"],
        },
    },
]


# Profile tools
PROFILE_TOOLS: list[dict] = [  # type: ignore[type-arg]
    {
        "name": "update_customer_profile",
        "description": (
            "Оновити профіль клієнта (ім'я, місто, авто, адреса доставки). "
            "Викликай при отриманні нових даних від клієнта. "
            "Передавай ТІЛЬКИ нові/змінені поля — решта збережеться."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Ім'я клієнта (як він назвався)",
                },
                "city": {
                    "type": "string",
                    "description": "Місто клієнта",
                },
                "vehicles": {
                    "type": "array",
                    "description": "Автомобілі клієнта",
                    "items": {
                        "type": "object",
                        "properties": {
                            "plate": {
                                "type": "string",
                                "description": "Державний номер (наприклад AA1234BB)",
                            },
                            "brand": {
                                "type": "string",
                                "description": "Марка та модель (наприклад Toyota Camry)",
                            },
                            "tire_size": {
                                "type": "string",
                                "description": "Розмір шин (наприклад 205/55R16)",
                            },
                        },
                        "required": ["plate"],
                    },
                },
                "delivery_address": {
                    "type": "string",
                    "description": "Адреса доставки (місто, вулиця, будинок)",
                },
            },
            "required": [],
        },
    },
]

# Wheels (disks): search + fitment verdict computed in code (src/agent/disk_fitment.py).
# No literal sizes in the descriptions — an example value becomes an argument.
DISK_TOOLS: list[dict] = [  # type: ignore[type-arg]
    {
        "name": "search_disks",
        "description": (
            "Пошук дисків у наявності в каталозі мережі. "
            "Спершу з'ясуй у клієнта діаметр диска, а якщо підбір під авто — марку, "
            "модель і рік авто. Якщо авто відоме — передай його у vehicle: сумісність "
            "(PCD, центральний отвір, виліт ET, розмір) порахує система, у кожного "
            "варіанта буде поле fit.status і fit.text — озвуч fit.text, сам сумісність "
            "не оцінюй. vehicle.status=ambiguous_car — перепитай рік або модифікацію авто. "
            "Проставки й розсвердлювання не пропонуй."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "diameter": {
                    "type": "integer",
                    "description": "Посадковий діаметр диска в дюймах",
                },
                "pcd": {
                    "type": "string",
                    "description": (
                        "Розболтовка: кількість болтів × діаметр кола болтів у мм, "
                        "як назвав клієнт. Не передавай, якщо клієнт не назвав."
                    ),
                },
                "et": {
                    "type": "number",
                    "description": "Виліт ET у мм. Тільки якщо клієнт назвав.",
                },
                "dia": {
                    "type": "number",
                    "description": "Центральний отвір (DIA) у мм. Тільки якщо клієнт назвав.",
                },
                "width": {
                    "type": "number",
                    "description": "Ширина обода в дюймах (J). Тільки якщо клієнт назвав.",
                },
                "vehicle": {
                    "type": "object",
                    "description": "Авто клієнта, якщо диски підбираються під нього.",
                    "properties": {
                        "brand": {"type": "string", "description": "Марка авто"},
                        "model": {"type": "string", "description": "Модель авто"},
                        "year": {"type": "integer", "description": "Рік випуску"},
                    },
                },
            },
            "required": ["diameter"],
        },
    },
]

# All tools for the agent (MVP + Orders + Fitting/Knowledge + Profile + Callback + Disks)
ALL_TOOLS = MVP_TOOLS + ORDER_TOOLS + FITTING_TOOLS + PROFILE_TOOLS + CALLBACK_TOOLS + DISK_TOOLS


def filter_tools_by_state(
    tools: list[dict],  # type: ignore[type-arg]
    *,
    order_stage: str | None = None,
    fitting_booked: bool = False,
) -> list[dict]:  # type: ignore[type-arg]
    """Filter tool definitions based on current conversation state.

    Removes tools that are irrelevant to the current order/fitting stage,
    reducing the number of tool definitions sent to the LLM each turn.

    Args:
        tools: Full list of tool definitions (not mutated).
        order_stage: Current order stage (None, "draft", "delivery_set", "confirmed").
        fitting_booked: Whether a fitting has already been booked this call.

    Returns:
        Filtered copy of the tools list.
    """
    exclude: set[str] = set()

    if order_stage is None:
        exclude.update(("update_order_delivery", "confirm_order"))
    elif order_stage == "draft":
        exclude.add("confirm_order")
    elif order_stage == "confirmed":
        exclude.update(
            ("create_order_draft", "update_order_delivery", "confirm_order", SUBMIT_ORDER_TOOL)
        )

    if fitting_booked:
        exclude.update(("book_fitting", "get_fitting_slots", "reserve_fitting_slot"))

    if not exclude:
        return tools

    filtered = [t for t in tools if t["name"] not in exclude]
    # Return original list if nothing was actually filtered
    return filtered if len(filtered) != len(tools) else tools


# ---------------------------------------------------------------------------
# Sales order: one `submit_order_request` instead of the three-step chain
# ---------------------------------------------------------------------------

#: The chain `submit_order_request` replaces under ``sales_enabled`` (wave 2-D,
#: goldset №2/№3: the model stopped after the draft, prompt rules did not hold).
ORDER_CHAIN_TOOLS: frozenset[str] = frozenset(
    {"create_order_draft", "update_order_delivery", "confirm_order"}
)
SUBMIT_ORDER_TOOL = "submit_order_request"

#: 1C payment codes (`OneCClient.create_order_1c`). Anything else is not a
#: payment method — default-deny, the caller is asked.
ORDER_PAYMENT_CODES: frozenset[str] = frozenset({"cod", "online", "card_on_delivery"})
ORDER_DELIVERY_TYPES: frozenset[str] = frozenset({"pickup", "delivery"})

#: Field → how the bot names it to itself when asking the caller.
ORDER_FIELD_LABELS: dict[str, str] = {
    "items": "товар і кількість",
    "delivery_type": "спосіб отримання — доставка чи самовивіз",
    "pickup_point_id": "пункт самовивозу зі списку get_pickup_points",
    "city": "місто доставки",
    "address": "адреса або відділення доставки",
    "recipient_name": "прізвище та ім'я отримувача",
    "phone": "телефон отримувача",
    "payment_method": "спосіб оплати",
}


def swap_order_chain_for_submit(names: set[str] | frozenset[str]) -> set[str]:
    """Under sales: the chain never survives; a set that allowed ordering
    (any chain tool, or the submit tool itself) gets ``submit_order_request``."""
    out = set(names) - ORDER_CHAIN_TOOLS
    if set(names) & ORDER_CHAIN_TOOLS or SUBMIT_ORDER_TOOL in names:
        out.add(SUBMIT_ORDER_TOOL)
    return out


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _order_items(value: Any) -> list[dict[str, Any]] | None:
    """Items with a product id and a whole quantity ≥ 1, or None."""
    if not isinstance(value, list) or not value:
        return None
    items: list[dict[str, Any]] = []
    for raw in value:
        if not isinstance(raw, dict):
            return None
        product_id = _text(raw.get("product_id"))
        quantity = raw.get("quantity")
        if isinstance(quantity, str) and quantity.strip().isdigit():
            quantity = int(quantity.strip())
        if not product_id or isinstance(quantity, bool) or not isinstance(quantity, int):
            return None
        if quantity < 1:
            return None
        items.append({"product_id": product_id, "quantity": quantity})
    return items


def _missing(field: str) -> dict[str, Any]:
    return {
        "status": "missing_field",
        "field": field,
        "message": (
            f"Заявку ще НЕ створено: бракує — {ORDER_FIELD_LABELS[field]}. "
            "Запитай у клієнта тільки це, одним питанням. Після відповіді виклич "
            "submit_order_request ще раз з усіма полями."
        ),
    }


def check_order_request(
    args: dict[str, Any],
    *,
    offered_pickup_point_ids: set[str] | frozenset[str],
    caller_phone: str = "",
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Completeness of a ``submit_order_request`` call — default-deny.

    Returns ``(draft, None)`` when every field is there, else
    ``(None, question)`` for the FIRST missing field only. A pickup point is
    accepted only when ``get_pickup_points`` offered it in this call; the phone
    falls back to the caller id.
    """
    items = _order_items(args.get("items"))
    if items is None:
        return None, _missing("items")
    delivery_type = _text(args.get("delivery_type"))
    if delivery_type not in ORDER_DELIVERY_TYPES:
        return None, _missing("delivery_type")
    pickup_point_id = city = address = ""
    if delivery_type == "pickup":
        pickup_point_id = _text(args.get("pickup_point_id"))
        if not pickup_point_id or pickup_point_id not in offered_pickup_point_ids:
            return None, _missing("pickup_point_id")
    else:
        city = _text(args.get("city"))
        if not city:
            return None, _missing("city")
        address = _text(args.get("address"))
        if not address:
            return None, _missing("address")
    recipient_name = _text(args.get("recipient_name"))
    if not recipient_name:
        return None, _missing("recipient_name")
    phone = _text(args.get("phone")) or _text(caller_phone)
    if not phone:
        return None, _missing("phone")
    payment_method = _text(args.get("payment_method"))
    if payment_method not in ORDER_PAYMENT_CODES:
        return None, _missing("payment_method")
    draft = {
        "items": items,
        "customer_phone": phone,
        "customer_name": recipient_name,
        "payment_method": payment_method,
        "delivery_type": delivery_type,
        "city": city,
        "address": address,
        "pickup_point_id": pickup_point_id,
    }
    return draft, None


def pickup_point_ids(result: Any) -> set[str]:
    """Ids a ``get_pickup_points`` result offered (empty on any other shape)."""
    if not isinstance(result, dict) or not isinstance(result.get("points"), list):
        return set()
    return {
        str(p["id"]).strip()
        for p in result["points"]
        if isinstance(p, dict) and p.get("id") is not None and str(p["id"]).strip()
    }

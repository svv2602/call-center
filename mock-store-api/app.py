"""Mock Store API for local development and testing."""

import uuid
from datetime import date, timedelta

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse

app = FastAPI(title="Mock Store API", version="0.1.0")

API_KEY = "test-store-api-key"

TIRES = [
    {
        "id": "tire-001",
        "brand": "Michelin",
        "model": "Primacy 4+",
        "width": 205,
        "profile": 55,
        "diameter": 16,
        "season": "summer",
        "price": 3200.0,
        "currency": "UAH",
        "in_stock": True,
        "quantity": 24,
        "speed_index": "V",
        "load_index": 91,
    },
    {
        "id": "tire-002",
        "brand": "Continental",
        "model": "WinterContact TS 870",
        "width": 205,
        "profile": 55,
        "diameter": 16,
        "season": "winter",
        "price": 3800.0,
        "currency": "UAH",
        "in_stock": True,
        "quantity": 12,
        "speed_index": "H",
        "load_index": 91,
    },
    {
        "id": "tire-003",
        "brand": "Premiorri",
        "model": "Vimero",
        "width": 195,
        "profile": 65,
        "diameter": 15,
        "season": "summer",
        "price": 1450.0,
        "currency": "UAH",
        "in_stock": True,
        "quantity": 40,
        "speed_index": "V",
        "load_index": 91,
    },
    {
        "id": "tire-004",
        "brand": "Nokian",
        "model": "Hakkapeliitta R5",
        "width": 225,
        "profile": 45,
        "diameter": 17,
        "season": "winter",
        "price": 5200.0,
        "currency": "UAH",
        "in_stock": True,
        "quantity": 8,
        "speed_index": "R",
        "load_index": 94,
    },
    {
        "id": "tire-005",
        "brand": "Bridgestone",
        "model": "Turanza T005",
        "width": 205,
        "profile": 55,
        "diameter": 16,
        "season": "summer",
        "price": 2900.0,
        "currency": "UAH",
        "in_stock": False,
        "quantity": 0,
        "speed_index": "W",
        "load_index": 91,
    },
]

STATIONS = [
    {
        "id": "station-001",
        "name": "АвтоСервіс Центр",
        "city": "Київ",
        "address": "вул. Хрещатик, 22",
        "phone": "+380441234567",
        "working_hours": "08:00-20:00",
        "rating": 4.8,
    },
    {
        "id": "station-002",
        "name": "ШиноМайстер",
        "city": "Київ",
        "address": "пр. Перемоги, 50",
        "phone": "+380441234568",
        "working_hours": "09:00-19:00",
        "rating": 4.5,
    },
    {
        "id": "station-003",
        "name": "ТаєрПро Одеса",
        "city": "Одеса",
        "address": "вул. Дерибасівська, 10",
        "phone": "+380481234567",
        "working_hours": "08:00-18:00",
        "rating": 4.6,
    },
]

ORDERS: dict[str, dict] = {}
BOOKINGS: dict[str, dict] = {}
IDEMPOTENCY_STORE: dict[str, dict] = {}


def verify_api_key(authorization: str = Header(default="")) -> None:
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")
    token = authorization.removeprefix("Bearer ")
    if token != API_KEY:
        raise HTTPException(status_code=403, detail="Invalid API key")


def make_response(data: dict | list, request: Request) -> JSONResponse:
    request_id = request.headers.get("X-Request-Id", str(uuid.uuid4()))
    return JSONResponse(
        content={"data": data},
        headers={"X-Request-Id": request_id},
    )


# --- Health ---


@app.get("/api/v1/health")
async def health() -> dict:
    return {"status": "ok", "service": "mock-store-api"}


# --- Tires ---


@app.get("/api/v1/tires/search")
async def search_tires(
    request: Request,
    width: int | None = None,
    profile: int | None = None,
    diameter: int | None = None,
    season: str | None = None,
    brand: str | None = None,
    in_stock: bool | None = None,
    _auth: None = Depends(verify_api_key),
) -> JSONResponse:
    results = TIRES
    if width:
        results = [t for t in results if t["width"] == width]
    if profile:
        results = [t for t in results if t["profile"] == profile]
    if diameter:
        results = [t for t in results if t["diameter"] == diameter]
    if season:
        results = [t for t in results if t["season"] == season]
    if brand:
        results = [t for t in results if t["brand"].lower() == brand.lower()]
    if in_stock is not None:
        results = [t for t in results if t["in_stock"] == in_stock]
    return make_response(
        {"items": results, "total": len(results), "page": 1, "per_page": 20},
        request,
    )


@app.get("/api/v1/vehicles/tires")
async def vehicle_tires(
    request: Request,
    make: str | None = None,
    model: str | None = None,
    year: int | None = None,
    season: str | None = None,
    _auth: None = Depends(verify_api_key),
) -> JSONResponse:
    results = TIRES
    if season:
        results = [t for t in results if t["season"] == season]
    return make_response(
        {"items": results[:3], "total": min(3, len(results)), "page": 1, "per_page": 20},
        request,
    )


@app.get("/api/v1/tires/{tire_id}")
async def get_tire(
    tire_id: str,
    request: Request,
    _auth: None = Depends(verify_api_key),
) -> JSONResponse:
    tire = next((t for t in TIRES if t["id"] == tire_id), None)
    if not tire:
        raise HTTPException(status_code=404, detail="Tire not found")
    return make_response(tire, request)


@app.get("/api/v1/tires/{tire_id}/availability")
async def tire_availability(
    tire_id: str,
    request: Request,
    _auth: None = Depends(verify_api_key),
) -> JSONResponse:
    tire = next((t for t in TIRES if t["id"] == tire_id), None)
    if not tire:
        raise HTTPException(status_code=404, detail="Tire not found")
    return make_response(
        {
            "product_id": tire_id,
            "available": tire["in_stock"],
            "quantity": tire["quantity"],
            "warehouses": [
                {"city": "Київ", "quantity": tire["quantity"], "delivery_days": 1},
            ],
        },
        request,
    )


# --- Orders ---


@app.get("/api/v1/orders/search")
async def search_orders(
    request: Request,
    phone: str | None = None,
    order_number: str | None = None,
    _auth: None = Depends(verify_api_key),
) -> JSONResponse:
    results = list(ORDERS.values())
    if phone:
        results = [o for o in results if o.get("customer_phone") == phone]
    if order_number:
        results = [o for o in results if o.get("order_number") == order_number]
    return make_response(
        {"items": results, "total": len(results)},
        request,
    )


@app.get("/api/v1/orders/{order_id}")
async def get_order(
    order_id: str,
    request: Request,
    _auth: None = Depends(verify_api_key),
) -> JSONResponse:
    order = ORDERS.get(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return make_response(order, request)


@app.post("/api/v1/orders")
async def create_order(
    request: Request,
    _auth: None = Depends(verify_api_key),
) -> JSONResponse:
    idempotency_key = request.headers.get("Idempotency-Key")
    if idempotency_key and idempotency_key in IDEMPOTENCY_STORE:
        return make_response(IDEMPOTENCY_STORE[idempotency_key], request)

    body = await request.json()
    order_id = f"ord-{uuid.uuid4().hex[:8]}"
    order_number = f"TC-{uuid.uuid4().hex[:6].upper()}"

    items = body.get("items", [])
    subtotal = 0.0
    order_items = []
    for item in items:
        tire = next((t for t in TIRES if t["id"] == item.get("product_id")), None)
        qty = item.get("quantity", 1)
        price = tire["price"] if tire else 0
        subtotal += price * qty
        order_items.append(
            {
                "product_id": item.get("product_id"),
                "name": f"{tire['brand']} {tire['model']}" if tire else "Unknown",
                "quantity": qty,
                "price": price,
            }
        )

    order = {
        "id": order_id,
        "order_number": order_number,
        "status": "draft",
        "items": order_items,
        "customer_phone": body.get("customer_phone"),
        "subtotal": subtotal,
        "delivery_cost": 0,
        "total": subtotal,
    }
    ORDERS[order_id] = order
    if idempotency_key:
        IDEMPOTENCY_STORE[idempotency_key] = order
    return make_response(order, request)


@app.patch("/api/v1/orders/{order_id}/delivery")
async def update_delivery(
    order_id: str,
    request: Request,
    _auth: None = Depends(verify_api_key),
) -> JSONResponse:
    order = ORDERS.get(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    body = await request.json()
    delivery_type = body.get("delivery_type", "delivery")
    delivery_cost = 0 if delivery_type == "pickup" else 150.0
    order["delivery_type"] = delivery_type
    order["delivery_cost"] = delivery_cost
    order["total"] = order["subtotal"] + delivery_cost
    if body.get("city"):
        order["city"] = body["city"]
    if body.get("address"):
        order["address"] = body["address"]
    return make_response(
        {
            "order_id": order_id,
            "delivery_type": delivery_type,
            "delivery_cost": delivery_cost,
            "estimated_days": 1 if delivery_type == "pickup" else 3,
            "total": order["total"],
        },
        request,
    )


@app.post("/api/v1/orders/{order_id}/confirm")
async def confirm_order(
    order_id: str,
    request: Request,
    _auth: None = Depends(verify_api_key),
) -> JSONResponse:
    idempotency_key = request.headers.get("Idempotency-Key")
    if idempotency_key and idempotency_key in IDEMPOTENCY_STORE:
        return make_response(IDEMPOTENCY_STORE[idempotency_key], request)

    order = ORDERS.get(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    body = await request.json()
    order["status"] = "confirmed"
    order["payment_method"] = body.get("payment_method", "cod")
    if body.get("customer_name"):
        order["customer_name"] = body["customer_name"]

    result = {
        "order_id": order_id,
        "order_number": order["order_number"],
        "status": "confirmed",
        "estimated_delivery": str(date.today() + timedelta(days=3)),
        "sms_sent": True,
        "total": order["total"],
    }
    if idempotency_key:
        IDEMPOTENCY_STORE[idempotency_key] = result
    return make_response(result, request)


# --- Fitting ---


@app.get("/api/v1/fitting/stations")
async def fitting_stations(
    request: Request,
    city: str | None = None,
    _auth: None = Depends(verify_api_key),
) -> JSONResponse:
    results = STATIONS
    if city:
        results = [s for s in results if s["city"].lower() == city.lower()]
    return make_response(
        {"items": results, "total": len(results)},
        request,
    )


@app.get("/api/v1/fitting/stations/{station_id}/slots")
async def fitting_slots(
    station_id: str,
    request: Request,
    date_from: str | None = None,
    date_to: str | None = None,
    service_type: str | None = None,
    _auth: None = Depends(verify_api_key),
) -> JSONResponse:
    station = next((s for s in STATIONS if s["id"] == station_id), None)
    if not station:
        raise HTTPException(status_code=404, detail="Station not found")

    base_date = date.today() + timedelta(days=1)
    slots = []
    for day_offset in range(3):
        d = base_date + timedelta(days=day_offset)
        for hour in [9, 11, 14, 16]:
            slots.append(
                {
                    "date": str(d),
                    "time": f"{hour:02d}:00",
                    "available": True,
                    "service_type": service_type or "tire_change",
                }
            )
    return make_response(
        {"station_id": station_id, "slots": slots},
        request,
    )


@app.post("/api/v1/fitting/bookings")
async def book_fitting(
    request: Request,
    _auth: None = Depends(verify_api_key),
) -> JSONResponse:
    idempotency_key = request.headers.get("Idempotency-Key")
    if idempotency_key and idempotency_key in IDEMPOTENCY_STORE:
        return make_response(IDEMPOTENCY_STORE[idempotency_key], request)

    body = await request.json()
    booking_id = f"book-{uuid.uuid4().hex[:8]}"
    station = next((s for s in STATIONS if s["id"] == body.get("station_id")), None)

    result = {
        "booking_id": booking_id,
        "station_id": body.get("station_id"),
        "station_name": station["name"] if station else "Unknown",
        "date": body.get("date"),
        "time": body.get("time"),
        "service_type": body.get("service_type", "tire_change"),
        "price": 800.0,
        "sms_sent": True,
    }
    BOOKINGS[booking_id] = result
    if idempotency_key:
        IDEMPOTENCY_STORE[idempotency_key] = result
    return make_response(result, request)


@app.delete("/api/v1/fitting/bookings/{booking_id}")
async def cancel_booking(
    booking_id: str,
    request: Request,
    _auth: None = Depends(verify_api_key),
) -> JSONResponse:
    if booking_id not in BOOKINGS:
        raise HTTPException(status_code=404, detail="Booking not found")
    del BOOKINGS[booking_id]
    return make_response({"booking_id": booking_id, "status": "cancelled"}, request)


@app.patch("/api/v1/fitting/bookings/{booking_id}")
async def reschedule_booking(
    booking_id: str,
    request: Request,
    _auth: None = Depends(verify_api_key),
) -> JSONResponse:
    booking = BOOKINGS.get(booking_id)
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    body = await request.json()
    if body.get("date"):
        booking["date"] = body["date"]
    if body.get("time"):
        booking["time"] = body["time"]
    return make_response(booking, request)


@app.get("/api/v1/fitting/prices")
async def fitting_prices(
    request: Request,
    tire_diameter: int = Query(default=16),
    station_id: str | None = None,
    service_type: str | None = None,
    _auth: None = Depends(verify_api_key),
) -> JSONResponse:
    base_price = 600 + (tire_diameter - 14) * 100
    return make_response(
        {
            "tire_diameter": tire_diameter,
            "prices": {
                "tire_change": base_price,
                "balancing": base_price * 0.4,
                "full_service": base_price * 1.3,
            },
            "currency": "UAH",
        },
        request,
    )


# --- Knowledge Base ---


@app.get("/api/v1/knowledge/search")
async def knowledge_search(
    request: Request,
    query: str = Query(default=""),
    category: str | None = None,
    _auth: None = Depends(verify_api_key),
) -> JSONResponse:
    articles = [
        {
            "id": "kb-001",
            "title": "Як обрати зимові шини",
            "category": "guides",
            "content": "Зимові шини забезпечують кращу тягу при температурі нижче +7C...",
            "relevance": 0.92,
        },
        {
            "id": "kb-002",
            "title": "Порівняння Michelin vs Continental",
            "category": "comparisons",
            "content": "Michelin Primacy 4 та Continental PremiumContact 6 — обидва преміальні...",
            "relevance": 0.85,
        },
    ]
    if category:
        articles = [a for a in articles if a["category"] == category]
    return make_response(
        {"items": articles, "total": len(articles)},
        request,
    )


# --- Pickup Points ---


@app.get("/api/v1/pickup-points")
async def pickup_points(
    request: Request,
    city: str | None = None,
    _auth: None = Depends(verify_api_key),
) -> JSONResponse:
    points = [
        {
            "id": "pp-001",
            "name": "Нова Пошта #25",
            "city": "Київ",
            "address": "вул. Саксаганського, 100",
        },
        {
            "id": "pp-002",
            "name": "Нова Пошта #12",
            "city": "Одеса",
            "address": "вул. Канатна, 15",
        },
    ]
    if city:
        points = [p for p in points if p["city"].lower() == city.lower()]
    return make_response(
        {"items": points, "total": len(points)},
        request,
    )

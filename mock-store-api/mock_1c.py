"""Mock 1C REST API for local development and integration testing.

Mimics the 1C ERP REST endpoints: get_wares, get_stock.
Uses Basic Auth (web_service / 44332211).

Run: uvicorn mock-store-api.mock_1c:app --port 8081
"""

import secrets
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

app = FastAPI(title="Mock 1C API", version="0.1.0")
security = HTTPBasic()

VALID_USERNAME = "web_service"
VALID_PASSWORD = "44332211"
TRADING_NETWORKS = ("ProKoleso", "Tshina")

# In-memory catalog (mimics get_wares response)
WARES = [
    {
        "type": "000000001",
        "model_id": "000167134",
        "model": "Winter1",
        "manufacturer_id": "000022523",
        "manufacturer": "Tigar",
        "seasonality": "Зимняя",
        "tread_pattern_type": "Направленный",
        "product": [
            {
                "sku": "00000019835",
                "art": "",
                "text": "155/70 R13 Tigar Winter1 [75T]",
                "diametr": "13",
                "size_id": "26",
                "size": "155/70R13",
                "profile_height": "70",
                "profile_width": "155",
                "speed_rating": "T",
                "load_rating": "75",
                "reinforcement_type_id": "",
                "reinforcement_type": "",
                "reinforcement_type2_id": "",
                "reinforcement_type2": "",
                "reinforcement_type3_id": "",
                "reinforcement_type3": "",
                "ommology_id": "",
                "ommology": "",
                "studded": "",
                "tire_insulation": "",
                "tire_rim_peeling": "",
                "tire_ply": "",
            },
        ],
    },
    {
        "type": "000000001",
        "model_id": "000123456",
        "model": "Primacy 4+",
        "manufacturer_id": "000011111",
        "manufacturer": "Michelin",
        "seasonality": "Летняя",
        "tread_pattern_type": "Симметричный",
        "product": [
            {
                "sku": "00000035988",
                "art": "MIC-PRI4-205",
                "text": "205/55 R16 Michelin Primacy 4+ [91V]",
                "diametr": "16",
                "size_id": "42",
                "size": "205/55R16",
                "profile_height": "55",
                "profile_width": "205",
                "speed_rating": "V",
                "load_rating": "91",
                "reinforcement_type_id": "",
                "reinforcement_type": "",
                "reinforcement_type2_id": "",
                "reinforcement_type2": "",
                "reinforcement_type3_id": "",
                "reinforcement_type3": "",
                "ommology_id": "",
                "ommology": "",
                "studded": "",
                "tire_insulation": "",
                "tire_rim_peeling": "",
                "tire_ply": "",
            },
            {
                "sku": "00000035989",
                "art": "MIC-PRI4-225",
                "text": "225/45 R17 Michelin Primacy 4+ [94W]",
                "diametr": "17",
                "size_id": "55",
                "size": "225/45R17",
                "profile_height": "45",
                "profile_width": "225",
                "speed_rating": "W",
                "load_rating": "94",
                "reinforcement_type_id": "",
                "reinforcement_type": "",
                "reinforcement_type2_id": "",
                "reinforcement_type2": "",
                "reinforcement_type3_id": "",
                "reinforcement_type3": "",
                "ommology_id": "",
                "ommology": "",
                "studded": "",
                "tire_insulation": "",
                "tire_rim_peeling": "",
                "tire_ply": "",
            },
        ],
    },
    {
        "type": "000000001",
        "model_id": "000789012",
        "model": "Hakkapeliitta R5",
        "manufacturer_id": "000033333",
        "manufacturer": "Nokian",
        "seasonality": "Зимняя",
        "tread_pattern_type": "Направленный",
        "product": [
            {
                "sku": "00000042001",
                "art": "NOK-HR5-205",
                "text": "205/55 R16 Nokian Hakkapeliitta R5 [94R]",
                "diametr": "16",
                "size_id": "42",
                "size": "205/55R16",
                "profile_height": "55",
                "profile_width": "205",
                "speed_rating": "R",
                "load_rating": "94",
                "reinforcement_type_id": "",
                "reinforcement_type": "",
                "reinforcement_type2_id": "",
                "reinforcement_type2": "",
                "reinforcement_type3_id": "",
                "reinforcement_type3": "",
                "ommology_id": "",
                "ommology": "",
                "studded": "",
                "tire_insulation": "",
                "tire_rim_peeling": "",
                "tire_ply": "",
            },
        ],
    },
]

# In-memory stock data (mimics get_stock response)
STOCK = {
    "ProKoleso": [
        {
            "sku": "00000019835",
            "price": "1850",
            "stock": "24",
            "foreign_product": "1",
            "price_tshina": "1850",
            "year_issue": "25-24",
            "country": "Сербія",
        },
        {
            "sku": "00000035988",
            "price": "3200",
            "stock": "12",
            "foreign_product": "1",
            "price_tshina": "3200",
            "year_issue": "25-10",
            "country": "Франція",
        },
        {
            "sku": "00000035989",
            "price": "4100",
            "stock": "8",
            "foreign_product": "1",
            "price_tshina": "4100",
            "year_issue": "25-15",
            "country": "Франція",
        },
        {
            "sku": "00000042001",
            "price": "3800",
            "stock": "16",
            "foreign_product": "1",
            "price_tshina": "3800",
            "year_issue": "25-20",
            "country": "Фінляндія",
        },
    ],
    "Tshina": [
        {
            "sku": "00000019835",
            "price": "1900",
            "stock": "20",
            "foreign_product": "1",
            "price_tshina": "1850",
            "year_issue": "25-24",
            "country": "Сербія",
        },
        {
            "sku": "00000035988",
            "price": "3300",
            "stock": "6",
            "foreign_product": "1",
            "price_tshina": "3200",
            "year_issue": "25-10",
            "country": "Франція",
        },
        {
            "sku": "00000035989",
            "price": "4200",
            "stock": "4",
            "foreign_product": "1",
            "price_tshina": "4100",
            "year_issue": "25-15",
            "country": "Франція",
        },
        {
            "sku": "00000042001",
            "price": "3900",
            "stock": "10",
            "foreign_product": "1",
            "price_tshina": "3800",
            "year_issue": "25-20",
            "country": "Фінляндія",
        },
    ],
}

# In-memory Nova Poshta reference data
NOVAPOST_CITIES = [
    {
        "Ref": "8d5a980d-391c-11dd-90d9-001a92567626",
        "Description": "Київ",
        "DescriptionRu": "Киев",
        "CityID": "1",
        "Area": "dcaad4db-4b33-11e4-ab6d-005056801329",
        "SettlementTypeDescription": "місто",
        "IsBranch": "0",
    },
    {
        "Ref": "db5c88e0-391c-11dd-90d9-001a92567626",
        "Description": "Одеса",
        "DescriptionRu": "Одесса",
        "CityID": "2",
        "Area": "7150812c-9b87-11de-822f-000c2965ae0e",
        "SettlementTypeDescription": "місто",
        "IsBranch": "0",
    },
    {
        "Ref": "db5c88f0-391c-11dd-90d9-001a92567626",
        "Description": "Харків",
        "DescriptionRu": "Харьков",
        "CityID": "3",
        "Area": "7150813e-9b87-11de-822f-000c2965ae0e",
        "SettlementTypeDescription": "місто",
        "IsBranch": "0",
    },
]

NOVAPOST_BRANCHES = [
    {
        "Ref": "1ec09d88-e1c2-11e3-8c4a-0050568002cf",
        "Description": "Відділення №1 (до 30 кг): вул. Пирогівський шлях, 135",
        "DescriptionRu": "Отделение №1 (до 30 кг): ул. Пироговский путь, 135",
        "ShortAddress": "Київ, вул. Пирогівський шлях, 135",
        "CityRef": "8d5a980d-391c-11dd-90d9-001a92567626",
        "CityDescription": "Київ",
        "Number": "1",
        "Phone": "(044) 364-12-81",
        "CategoryOfWarehouse": "Branch",
        "WarehouseStatus": "Working",
        "Latitude": "50.4016",
        "Longitude": "30.4525",
        "PostalCodeUA": "03045",
        "PlaceMaxWeightAllowed": "30",
    },
    {
        "Ref": "2a8ed341-e1c2-11e3-8c4a-0050568002cf",
        "Description": "Відділення №2 (до 30 кг): вул. Хрещатик, 22",
        "DescriptionRu": "Отделение №2 (до 30 кг): ул. Крещатик, 22",
        "ShortAddress": "Київ, вул. Хрещатик, 22",
        "CityRef": "8d5a980d-391c-11dd-90d9-001a92567626",
        "CityDescription": "Київ",
        "Number": "2",
        "Phone": "(044) 364-12-82",
        "CategoryOfWarehouse": "Branch",
        "WarehouseStatus": "Working",
        "Latitude": "50.4474",
        "Longitude": "30.5225",
        "PostalCodeUA": "01001",
        "PlaceMaxWeightAllowed": "30",
    },
    {
        "Ref": "3b9fe452-e1c2-11e3-8c4a-0050568002cf",
        "Description": "Відділення №1 (до 30 кг): вул. Дерибасівська, 1",
        "DescriptionRu": "Отделение №1 (до 30 кг): ул. Дерибасовская, 1",
        "ShortAddress": "Одеса, вул. Дерибасівська, 1",
        "CityRef": "db5c88e0-391c-11dd-90d9-001a92567626",
        "CityDescription": "Одеса",
        "Number": "1",
        "Phone": "(048) 700-11-22",
        "CategoryOfWarehouse": "Branch",
        "WarehouseStatus": "Working",
        "Latitude": "46.4856",
        "Longitude": "30.7406",
        "PostalCodeUA": "65000",
        "PlaceMaxWeightAllowed": "30",
    },
]

# Track receipt confirmations per network
_confirmed: dict[str, bool] = {}


def verify_basic_auth(credentials: Annotated[HTTPBasicCredentials, Depends(security)]) -> None:
    correct_username = secrets.compare_digest(credentials.username, VALID_USERNAME)
    correct_password = secrets.compare_digest(credentials.password, VALID_PASSWORD)
    if not (correct_username and correct_password):
        raise HTTPException(status_code=401, detail="Invalid credentials")


# --- Health ---


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "mock-1c-api"}


# --- get_wares ---


@app.get("/Trade/hs/site/get_wares/")
async def get_wares(
    request: Request,
    TradingNetwork: str | None = None,  # noqa: N803
    UploadingAll: str | None = None,  # noqa: N803
    ConfirmationOfReceipt: str | None = None,  # noqa: N803
    sku: str | None = None,
    limit: int | None = None,
    _auth: None = Depends(verify_basic_auth),
) -> JSONResponse:
    # Single SKU lookup
    if sku is not None:
        results = []
        for ware in WARES:
            matching = [p for p in ware["product"] if p["sku"] == sku]
            if matching:
                results.append({**ware, "product": matching})
        return JSONResponse({"success": True, "data": results, "errors": []})

    # Full upload
    if UploadingAll is not None:
        data = WARES
        if limit and limit > 0:
            data = data[:limit]
        return JSONResponse({"success": True, "data": data, "errors": []})

    # Incremental by network
    if TradingNetwork is not None:
        if TradingNetwork not in TRADING_NETWORKS:
            return JSONResponse(
                {"success": False, "data": [], "errors": [f"Unknown network: {TradingNetwork}"]}
            )

        # Confirmation of receipt
        if ConfirmationOfReceipt is not None:
            _confirmed[TradingNetwork] = True
            return JSONResponse({"success": True, "data": [], "errors": []})

        # Return all wares if not yet confirmed, empty if confirmed
        if _confirmed.get(TradingNetwork, False):
            _confirmed[TradingNetwork] = False  # Reset for next cycle
            return JSONResponse({"success": True, "data": [], "errors": []})

        return JSONResponse({"success": True, "data": WARES, "errors": []})

    return JSONResponse({"success": False, "data": [], "errors": ["No valid parameters"]})


# --- get_stock ---


@app.get("/Trade/hs/site/get_stock/{network}")
async def get_stock(
    network: str,
    _auth: None = Depends(verify_basic_auth),
) -> JSONResponse:
    if network not in TRADING_NETWORKS:
        return JSONResponse(
            {
                "success": False,
                "TradingNetwork": network,
                "data": [],
                "errors": [f"Unknown network: {network}"],
            },
            status_code=404,
        )
    return JSONResponse(
        {
            "success": True,
            "TradingNetwork": network,
            "data": STOCK.get(network, []),
        }
    )


# --- Nova Poshta reference data ---


@app.get("/Trade/hs/site/novapost/city")
async def novapost_cities(
    _auth: None = Depends(verify_basic_auth),
) -> JSONResponse:
    return JSONResponse({"success": True, "data": NOVAPOST_CITIES})


@app.get("/Trade/hs/site/novapost/branch")
async def novapost_branches(
    _auth: None = Depends(verify_basic_auth),
) -> JSONResponse:
    return JSONResponse({"success": True, "data": NOVAPOST_BRANCHES})

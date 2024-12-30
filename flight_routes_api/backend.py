import json
import asyncio
import re
from redis import asyncio as aioredis
from fastapi import FastAPI, HTTPException, Depends, Security
from fastapi.responses import Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security.api_key import APIKeyHeader, APIKey
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    redis_host: str = "127.0.0.1"
    allowed_origins: str = ""
    plane_limit: int = 100
    api_key: str


settings = Settings()

CALLSIGN_REGEX = re.compile(r"^[A-Z]{3}[0-9][A-Z0-9]{0,4}$")


class PlaneInstance(BaseModel):
    callsign: str
    lat: float | None = None
    lng: float | None = None


class PlaneList(BaseModel):
    planes: list[PlaneInstance] = Field(
        ...,
        example=[
            {"callsign": "AFR136", "lat": 49.5429, "lng": -8.4444},
            {"callsign": "DLH430"},
            {"callsign": "KLM000"},
            {"callsign": "DEOZK"},
        ],
    )


class RouteResponse(BaseModel):
    airport_codes_iata: str = Field(..., alias="_airport_codes_iata")
    airport_codes: str
    callsign: str
    plausible: int

    class Config:
        populate_by_name = True


class RouteRequest(RouteResponse):
    class Config:
        json_schema_extra = {
            "example": {
                "_airport_codes_iata": "CDG-ORD",
                "airport_codes": "LFPG-KORD",
                "callsign": "AFR136",
                "plausible": 1,
            }
        }


example_response = [
    {
        "_airport_codes_iata": "CDG-ORD",
        "airport_codes": "LFPG-KORD",
        "callsign": "AFR136",
        "plausible": 1,
    },
    {
        "_airport_codes_iata": "FRA-ORD",
        "airport_codes": "EDDF-KORD",
        "callsign": "DLH430",
        "plausible": 1,
    },
    {
        "_airport_codes_iata": "unknown",
        "airport_codes": "unknown",
        "callsign": "KLM000",
        "plausible": 0,
    },
]

app = FastAPI(
    title="tar1090 flight routes API",
    description=(
        "API for tar1090 flight routes. "
        "[GitHub Repository](https://github.com/jaluebbe/tar1090-flightroutes)"
    ),
    version="1.0.0",
)

origins = settings.allowed_origins.split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["POST", "OPTIONS"],
    allow_headers=["access-control-allow-origin,content-type"],
)

redis_pool: aioredis.Redis | None = None

API_KEY_NAME = "api_key"
api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=True)


async def get_api_key(api_key_header: str = Security(api_key_header)):
    if api_key_header == settings.api_key:
        return api_key_header
    else:
        raise HTTPException(
            status_code=403, detail="Could not validate credentials"
        )


@app.on_event("startup")
async def startup_event() -> None:
    global redis_pool
    redis_pool = await aioredis.from_url(
        f"redis://{settings.redis_host}", decode_responses=True
    )


@app.on_event("shutdown")
async def shutdown_event() -> None:
    if redis_pool:
        await redis_pool.close()


async def get_route_for_callsign(callsign: str) -> RouteResponse:
    if redis_pool is None:
        raise RuntimeError("Redis pool is not initialized")
    route = await redis_pool.get(f"route:{callsign}")
    if route is None:
        return RouteResponse(
            _airport_codes_iata="unknown",
            airport_codes="unknown",
            callsign=callsign,
            plausible=0,
        )
    return RouteResponse(**json.loads(route))


@app.post(
    "/api/routeset",
    response_model=list[RouteResponse],
    responses={
        200: {
            "description": "Successful Response",
            "content": {"application/json": {"example": example_response}},
        }
    },
    tags=["tar1090"],
)
async def api_routeset(planeList: PlaneList) -> list[RouteResponse]:
    """
    Returns route information on a list of callsigns / positions.
    Positions are optional and will be ignored.
    """
    if len(planeList.planes) > settings.plane_limit:
        raise HTTPException(
            status_code=413,
            detail=(
                "The number of planes exceeds the limit of "
                f"{settings.plane_limit}."
            ),
        )
    valid_planes = [
        plane
        for plane in planeList.planes
        if CALLSIGN_REGEX.match(plane.callsign)
    ]
    tasks = [get_route_for_callsign(plane.callsign) for plane in valid_planes]
    response = await asyncio.gather(*tasks)
    return response


@app.options("/api/routeset", include_in_schema=False, tags=["tar1090"])
async def api_routeset_options() -> Response:
    return Response(status_code=200)


@app.get("/api/all_callsigns", tags=["database"])
async def get_all_callsigns(
    api_key: APIKey = Depends(get_api_key)
) -> list[str]:
    """
    Returns all callsigns that are available in the database.
    """
    if redis_pool is None:
        raise RuntimeError("Redis pool is not initialized")
    return [key.split(":")[1] async for key in redis_pool.scan_iter("route:*")]


async def get_callsigns_by_plausibility(plausible: int) -> list[str]:
    """
    Returns all callsigns filtered by plausibility.
    """
    if redis_pool is None:
        raise RuntimeError("Redis pool is not initialized")
    keys = [key async for key in redis_pool.scan_iter("route:*")]
    if keys:
        values = await redis_pool.mget(*keys)
        callsigns = [
            key.split(":")[1]
            for key, value in zip(keys, values)
            if value is not None
            and json.loads(value).get("plausible") == plausible
        ]

    return callsigns


@app.get("/api/unplausible_callsigns", tags=["database"])
async def get_unplausible_callsigns(
    api_key: APIKey = Depends(get_api_key)
) -> list[str]:
    """
    Returns all unplausible callsigns that are available in the database.
    """
    return await get_callsigns_by_plausibility(plausible=0)


@app.get("/api/plausible_callsigns", tags=["database"])
async def get_plausible_callsigns(
    api_key: APIKey = Depends(get_api_key)
) -> list[str]:
    """
    Returns all plausible callsigns that are available in the database.
    """
    return await get_callsigns_by_plausibility(plausible=1)


@app.get(
    "/api/route/{callsign}", tags=["database"], response_model=RouteResponse
)
async def get_route(
    callsign: str, api_key: APIKey = Depends(get_api_key)
) -> RouteResponse:
    """
    Returns the route information for a given callsign.
    """
    return await get_route_for_callsign(callsign)


@app.post("/api/set_route", tags=["database"])
async def set_route(
    route: RouteRequest, api_key: APIKey = Depends(get_api_key)
) -> dict:
    """
    Sets the route information for a given callsign.
    """
    if redis_pool is None:
        raise RuntimeError("Redis pool is not initialized")
    route_data = route.dict(by_alias=True)
    await redis_pool.set(f"route:{route.callsign}", json.dumps(route_data))
    return {"status": "success", "message": f"Route set for {route.callsign}."}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)

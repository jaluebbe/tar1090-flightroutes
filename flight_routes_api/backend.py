import json
import asyncio
import re
from redis import asyncio as aioredis
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    redis_host: str = "127.0.0.1"
    allowed_origins: str = ""
    plane_limit: int = 100


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
        ],
    )


class RouteResponse(BaseModel):
    airport_codes_iata: str = Field(..., alias="_airport_codes_iata")
    airport_codes: str
    callsign: str
    plausible: int

    class Config:
        allow_population_by_field_name = True


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
)
async def api_routeset(planeList: PlaneList) -> list[RouteResponse]:
    """
    Return route information on a list of callsigns / positions.
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


@app.options("/api/routeset", include_in_schema=False)
async def api_routeset_options() -> Response:
    return Response(status_code=200)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)

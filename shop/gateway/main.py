import os, re, time, uuid, httpx, logging, jwt
SECRET = os.getenv("JWT_SECRET", "dev-secret")
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
logging.basicConfig(level=logging.INFO, format='{"time":"%(asctime)s","msg":"%(message)s"}')
H = "http://127.0.0.1:"
ROUTES = {"auth": os.getenv("AUTH_URL", H + "8001"), "products": os.getenv("PRODUCTS_URL", H + "8002"),
          "inventory": os.getenv("INVENTORY_URL", H + "8003"), "orders": os.getenv("ORDERS_URL", H + "8004"),
          "coupons": os.getenv("COUPONS_URL", H + "8005"), "returns": os.getenv("RETURNS_URL", H + "8006"), "analytics": os.getenv("ANALYTICS_URL", H + "8007"),
          "payments": os.getenv("PAYMENTS_URL", H + "8008"), "notifications": os.getenv("NOTIFICATIONS_URL", H + "8009"), "reviews": os.getenv("REVIEWS_URL", H + "8010")}
app = FastAPI(title="API Gateway"); hits = {}
@app.get("/health")
def health(): return {"status": "ok"}
@app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy(path: str, req: Request):
    cid = req.headers.get("x-correlation-id", uuid.uuid4().hex[:12])
    base = ROUTES.get(path.split("/")[0])
    if re.fullmatch(r"products/\d+/reviews", path): base = ROUTES["reviews"]
    if not base: return JSONResponse({"detail": "Unknown route"}, 404, headers={"X-Correlation-ID": cid})
    if path in ("auth/login", "auth/register"):
        ip = req.client.host; now = time.time()
        hits[ip] = [t for t in hits.get(ip, []) if now - t < 60] + [now]
        if len(hits[ip]) > 10: return JSONResponse({"detail": "Too many attempts, wait a minute"}, 429)
    hdrs = {k: v for k, v in req.headers.items() if k in ("content-type", "idempotency-key")}
    hdrs["X-Correlation-ID"] = cid
    first = path.split("/")[0]
    if path.startswith("inventory/reservations") or path.endswith("/paid") or path == "coupons/redeem":
        return JSONResponse({"detail": "Internal only"}, 403, headers={"X-Correlation-ID": cid})
    public = (first == "auth" and path != "auth/me") or (first == "products" and req.method == "GET")
    if not public:  # login token is checked ONCE, here
        try: d = jwt.decode((req.headers.get("authorization") or "").replace("Bearer ", ""), SECRET, ["HS256"])
        except jwt.PyJWTError: return JSONResponse({"detail": "Login required or session expired"}, 401, headers={"X-Correlation-ID": cid})
        if d.get("type") != "access": return JSONResponse({"detail": "Invalid token"}, 401, headers={"X-Correlation-ID": cid})
        hdrs["X-User-Id"] = d["sub"]; hdrs["X-User-Role"] = d["role"]
    try:
        async with httpx.AsyncClient(timeout=8) as c:
            r = await c.request(req.method, f"{base}/{path}", params=req.query_params, content=await req.body(), headers=hdrs)
    except httpx.HTTPError:
        return JSONResponse({"detail": "Service unavailable"}, 503, headers={"X-Correlation-ID": cid})
    logging.info(f"{cid} {req.method} /{path} -> {r.status_code}")
    return Response(r.content, r.status_code, media_type=r.headers.get("content-type"), headers={"X-Correlation-ID": cid})
app.mount("/", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static"), html=True), name="ui")

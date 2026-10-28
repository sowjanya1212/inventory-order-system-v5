import os, sqlite3, json, uuid, jwt, httpx
from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel, Field
from events import publish
SECRET = os.getenv("JWT_SECRET", "dev-secret"); KEY = os.getenv("INTERNAL_KEY", "internal")
COUPONS = os.getenv("COUPONS_URL", "http://127.0.0.1:8005")
PRODUCTS = os.getenv("PRODUCTS_URL", "http://127.0.0.1:8002"); INVENTORY = os.getenv("INVENTORY_URL", "http://127.0.0.1:8003")
app = FastAPI(title="Order Service")

from fastapi import Depends
def U(x_user_id: str = Header(None), x_user_role: str = Header(None)):
    """Identity is set by the gateway after it checks the login token once."""
    if not x_user_id: raise HTTPException(401, "Login required")
    return {"sub": x_user_id, "role": x_user_role}
def H(u): return {"X-User-Id": u["sub"], "X-User-Role": u["role"]}
def db():
    c = sqlite3.connect("orders.db"); c.row_factory = sqlite3.Row; return c
with db() as c:
    c.execute("create table if not exists orders(id integer primary key, number text, user_id text, idem text, items text, subtotal real, discount real default 0, tax real, total real, status text, reservation_id int, created text default current_timestamp)")
def out(r):
    d = dict(r); d["items"] = json.loads(d["items"]); d.pop("reservation_id"); return d
class Item(BaseModel):
    product_id: int; qty: int = Field(gt=0, le=100)
class OrderIn(BaseModel):
    items: list[Item] = Field(min_length=1)
    coupon: str = ""
class St(BaseModel):
    status: str
STATUSES = ["Pending", "Confirmed", "Processing", "Shipped", "Delivered", "Cancelled"]
@app.get("/health")
def health(): return {"status": "ok"}
@app.post("/orders", status_code=201)
def create(o: OrderIn, u: dict = Depends(U), idempotency_key: str = Header(None)):
    u = u
    if idempotency_key:
        r = db().execute("select * from orders where idem=? and user_id=?", (idempotency_key, u["sub"])).fetchone()
        if r: return out(r)
    lines, sub = [], 0
    try:
        for i in o.items:
            r = httpx.get(f"{PRODUCTS}/products/{i.product_id}", timeout=5)
            if r.status_code != 200: raise HTTPException(404, f"Product {i.product_id} not found")
            p = r.json(); sub += p["price"] * i.qty
            lines.append({"product_id": p["id"], "name": p["name"], "price": p["price"], "qty": i.qty})
        res = httpx.post(f"{INVENTORY}/inventory/reservations", json={"items": [i.model_dump() for i in o.items]}, headers={"X-Internal-Key": KEY}, timeout=5)
    except httpx.HTTPError: raise HTTPException(503, "A dependent service is unavailable")
    if res.status_code != 200: raise HTTPException(409, res.json().get("detail", "Out of stock"))
    rid = res.json()["reservation_id"]; disc = 0
    if o.coupon:
        try: cr = httpx.post(f"{COUPONS}/coupons/redeem", json={"code": o.coupon, "subtotal": sub}, headers={"X-Internal-Key": KEY}, timeout=5)
        except httpx.HTTPError: cr = None
        if cr is None or cr.status_code != 200:
            try: httpx.post(f"{INVENTORY}/inventory/reservations/{rid}/release", headers={"X-Internal-Key": KEY}, timeout=5)
            except httpx.HTTPError: pass
            raise HTTPException(400, cr.json().get("detail", "Invalid coupon") if cr is not None else "Coupon service unavailable")
        disc = cr.json()["discount"]
    tax = round((sub - disc) * 0.05, 2)
    with db() as c:
        cur = c.execute("insert into orders(number,user_id,idem,items,subtotal,discount,tax,total,status,reservation_id) values(?,?,?,?,?,?,?,?,?,?)",
            ("ORD-" + uuid.uuid4().hex[:8].upper(), u["sub"], idempotency_key, json.dumps(lines), sub, disc, tax, round(sub - disc + tax, 2), "Pending", rid))
        o2 = out(c.execute("select * from orders where id=?", (cur.lastrowid,)).fetchone())
        publish("order.created", user_id=u["sub"], order_number=o2["number"])
        return o2
@app.get("/orders/my-orders")
def mine(u: dict = Depends(U)):
    u = u
    return [out(r) for r in db().execute("select * from orders where user_id=? order by id desc", (u["sub"],))]
@app.get("/orders")
def all_(u: dict = Depends(U)):
    if u["role"] != "admin": raise HTTPException(403, "Admin only")
    return [out(r) for r in db().execute("select * from orders order by id desc")]
def own(oid, u):
    r = db().execute("select * from orders where id=?", (oid,)).fetchone()
    if not r or (r["user_id"] != u["sub"] and u["role"] != "admin"): raise HTTPException(404, "Order not found")
    return r
@app.get("/orders/{oid}")
def get(oid: int, u: dict = Depends(U)): return out(own(oid, u))
@app.patch("/orders/{oid}/status")
def status(oid: int, b: St, u: dict = Depends(U)):
    u = u
    if u["role"] != "admin": raise HTTPException(403, "Admin only")
    if b.status not in STATUSES or b.status == "Cancelled": raise HTTPException(400, "Invalid status")
    r = own(oid, u)
    with db() as c: c.execute("update orders set status=? where id=?", (b.status, oid))
    if b.status in ("Shipped", "Delivered"): publish(f"order.{b.status.lower()}", user_id=r["user_id"], order_number=r["number"])
    return {"id": oid, "status": b.status}
@app.post("/orders/{oid}/cancel")
def cancel(oid: int, u: dict = Depends(U)):
    r = own(oid, u)
    if r["status"] not in ("Pending", "Confirmed", "Processing"): raise HTTPException(400, "Order can no longer be cancelled")
    try: httpx.post(f"{INVENTORY}/inventory/reservations/{r['reservation_id']}/release", headers={"X-Internal-Key": KEY}, timeout=5).raise_for_status()
    except httpx.HTTPError: raise HTTPException(503, "Inventory service unavailable, try again")
    with db() as c: c.execute("update orders set status='Cancelled' where id=?", (oid,))
    publish("order.cancelled", user_id=r["user_id"], order_number=r["number"])
    return {"id": oid, "status": "Cancelled"}

@app.post("/orders/{oid}/paid")
def paid(oid: int, x_internal_key: str = Header(None)):
    """Called by the payment service after a successful payment."""
    if x_internal_key != KEY: raise HTTPException(403, "Internal only")
    r = db().execute("select * from orders where id=?", (oid,)).fetchone()
    if not r: raise HTTPException(404, "Order not found")
    if r["status"] == "Pending":
        with db() as c: c.execute("update orders set status='Confirmed' where id=?", (oid,))
        publish("order.paid", user_id=r["user_id"], order_number=r["number"])
        return {"id": oid, "status": "Confirmed"}
    return {"id": oid, "status": r["status"]}

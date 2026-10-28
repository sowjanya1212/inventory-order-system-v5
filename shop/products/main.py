import os, sqlite3, jwt
from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel, Field
SECRET = os.getenv("JWT_SECRET", "dev-secret")
app = FastAPI(title="Product Catalog Service")

from fastapi import Depends
def U(x_user_id: str = Header(None), x_user_role: str = Header(None)):
    """Identity is set by the gateway after it checks the login token once."""
    if not x_user_id: raise HTTPException(401, "Login required")
    return {"sub": x_user_id, "role": x_user_role}
def H(u): return {"X-User-Id": u["sub"], "X-User-Role": u["role"]}
def db():
    c = sqlite3.connect("products.db"); c.row_factory = sqlite3.Row; return c
def admin(u):
    if u["role"] not in ("admin", "inventory_manager"): raise HTTPException(403, "Admin only")
class P(BaseModel):
    name: str = Field(min_length=1); description: str = ""; category: str = Field(min_length=1)
    brand: str = ""; price: float = Field(gt=0); image: str = ""
with db() as c:
    c.execute("create table if not exists products(id integer primary key, name text, description text, category text, brand text, price real, image text)")
    if not c.execute("select 1 from products").fetchone():
        c.executemany("insert into products(name,description,category,brand,price,image) values(?,?,?,?,?,?)", [
            ("Wireless Mouse", "2.4GHz ergonomic mouse", "Electronics", "Logi", 799, ""),
            ("Notebook A5", "200 pages ruled", "Stationery", "Classmate", 120, ""),
            ("Water Bottle", "1L steel bottle", "Home", "Milton", 450, "")])
@app.get("/health")
def health(): return {"status": "ok"}
@app.get("/products")
def lst(q: str = "", category: str = "", min_price: float = 0, max_price: float = 1e12, sort: str = "id", page: int = 1, size: int = 10):
    sort = sort if sort in ("id", "price", "name") else "id"
    w = "name like ? and category like ? and price between ? and ?"
    a = (f"%{q}%", f"%{category}%", min_price, max_price)
    c = db(); total = c.execute(f"select count(*) from products where {w}", a).fetchone()[0]
    rows = c.execute(f"select * from products where {w} order by {sort} limit ? offset ?", a + (size, (page - 1) * size)).fetchall()
    return {"total": total, "page": page, "items": [dict(r) for r in rows]}
@app.get("/products/{pid}")
def get(pid: int):
    r = db().execute("select * from products where id=?", (pid,)).fetchone()
    if not r: raise HTTPException(404, "Product not found")
    return dict(r)
@app.post("/products", status_code=201)
def create(p: P, u: dict = Depends(U)):
    admin(u)
    with db() as c:
        cur = c.execute("insert into products(name,description,category,brand,price,image) values(?,?,?,?,?,?)", (p.name, p.description, p.category, p.brand, p.price, p.image))
    return {"id": cur.lastrowid, **p.model_dump()}
@app.put("/products/{pid}")
def update(pid: int, p: P, u: dict = Depends(U)):
    admin(u)
    with db() as c:
        n = c.execute("update products set name=?,description=?,category=?,brand=?,price=?,image=? where id=?", (p.name, p.description, p.category, p.brand, p.price, p.image, pid)).rowcount
    if not n: raise HTTPException(404, "Product not found")
    return {"id": pid, **p.model_dump()}
@app.delete("/products/{pid}", status_code=204)
def delete(pid: int, u: dict = Depends(U)):
    admin(u)
    with db() as c: c.execute("delete from products where id=?", (pid,))

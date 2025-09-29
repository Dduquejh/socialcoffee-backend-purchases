from fastapi import FastAPI, HTTPException, Path, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List
import pymysql
import os
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()

app = FastAPI()

# ──────── CORS ─────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ──────── Middleware de seguridad ─────────────
@app.middleware("http")
async def verify_gateway_header(request: Request, call_next):
    trusted = request.headers.get("X-Trusted-Gateway")
    if trusted != "true":
        raise HTTPException(status_code=403, detail="Unauthorized source")
    response = await call_next(request)
    return response

# ──────── DB Connection ─────────────
def get_connection():
    return pymysql.connect(
        host=os.getenv("DB_HOST"),
        port=int(os.getenv("DB_PORT")),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        database=os.getenv("DB_NAME"),
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True
    )

# ──────── Schemas ────────────
class CartSlugItem(BaseModel):
    slug: str
    quantity: int

class PurchaseRequest(BaseModel):
    email: str
    items: List[CartSlugItem]

class DeliveryUpdate(BaseModel):
    delivered: bool

# ──────── Endpoints ─────────────
@app.get("/products")
def get_products():
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM products")
        return cur.fetchall()

@app.post("/cart/validate")
def validate_cart_item(data: CartSlugItem):
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT stock FROM products WHERE slug = %s", (data.slug,))
        product = cur.fetchone()
        if not product:
            raise HTTPException(status_code=404, detail="Product not found")
        if product["stock"] < data.quantity:
            raise HTTPException(status_code=400, detail="Insufficient stock")
        return {"status": "ok", "available_stock": product["stock"]}

@app.post("/purchase")
def make_purchase(data: PurchaseRequest):
    conn = get_connection()
    with conn.cursor() as cur:
        total = 0
        purchase_items = []

        for item in data.items:
            cur.execute("SELECT id, stock, price FROM products WHERE slug = %s", (item.slug,))
            product = cur.fetchone()
            if not product or product["stock"] < item.quantity:
                raise HTTPException(status_code=400, detail=f"Insufficient stock for product {item.slug}")
            total += product["price"] * item.quantity
            purchase_items.append({
                "product_id": product["id"],
                "slug": item.slug,
                "quantity": item.quantity,
                "unit_price": product["price"]
            })

        cur.execute(
            "INSERT INTO purchases (email, date, total, delivered) VALUES (%s, %s, %s, %s)",
            (data.email, datetime.utcnow(), total, False)
        )
        purchase_id = cur.lastrowid

        for item in purchase_items:
            cur.execute(
                "INSERT INTO purchase_details (purchase_id, product_id, quantity, unit_price) VALUES (%s, %s, %s, %s)",
                (purchase_id, item["product_id"], item["quantity"], item["unit_price"])
            )
            cur.execute(
                "UPDATE products SET stock = stock - %s WHERE id = %s",
                (item["quantity"], item["product_id"])
            )

        return {"purchase_id": purchase_id, "total": total}

@app.get("/admin/purchases")
def get_all_purchases():
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM purchases")
        return cur.fetchall()

@app.post("/admin/purchases/{purchase_id}")
def mark_as_delivered(purchase_id: int = Path(...), data: DeliveryUpdate = None):
    if not data or not data.delivered:
        raise HTTPException(status_code=400, detail="Missing or invalid delivery status")
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("UPDATE purchases SET delivered = 1 WHERE id = %s", (purchase_id,))
        return {"status": "ok", "purchase_id": purchase_id}

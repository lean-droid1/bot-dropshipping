import os
import time
import json
import requests
from bs4 import BeautifulSoup

# Configuración desde las variables de Railway
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

URL_A = "https://rxzweb.com/tienda/?et_per_page=-1"
URL_B = "https://leandroid.tiendanegocio.com/productos"

DB_FILE = "estado_productos.json"

def enviar_telegram(mensaje):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": mensaje, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload)
    except Exception as e:
        print(f"Error al enviar Telegram: {e}")

def cargar_estado_anterior():
    if os.path.exists(DB_FILE):
        with open(DB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"productos_a": {}, "productos_b": {}}

def guardar_estado_actual(estado):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(estado, f, ensure_ascii=False, indent=4)

def scrapear_web_a():
    """ Scrapea el proveedor (rxzweb) usando WooCommerce """
    productos = {}
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        response = requests.get(URL_A, headers=headers, timeout=20)
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Selectores reales para la estructura de tu proveedor
        items = soup.select('li.product') 
        for item in items:
            title_el = item.select_one('.woocommerce-loop-product__title')
            price_el = item.select_one('.price')
            
            if title_el and price_el:
                nombre = title_el.text.strip().lower()
                
                # Extraemos solo los números del precio
                precio_texto = ''.join(filter(str.isdigit, price_el.text))
                precio = int(precio_texto) if precio_texto else 0
                
                # WooCommerce detecta falta de stock con clases en el <li>
                clases = item.get('class', [])
                tiene_stock = "out-of-stock" not in clases
                
                productos[nombre] = {
                    "nombre_real": title_el.text.strip(),
                    "precio": precio,
                    "stock": tiene_stock
                }
    except Exception as e:
        print(f"Error scrapeando Proveedor (Web A): {e}")
    return productos

def scrapear_web_b():
    """ Scrapea tu tienda (TiendaNegocio) """
    productos = {}
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        response = requests.get(URL_B, headers=headers, timeout=20)
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Selectores estándar para la plataforma TiendaNegocio
        items = soup.select('.product-item, .item, .js-product-container') 
        for item in items:
            title_el = item.select_one('.item-name, .product-title, h2')
            price_el = item.select_one('.item-price, .product-price, .price')
            
            if title_el and price_el:
                nombre = title_el.text.strip().lower()
                precio_texto = ''.join(filter(str.isdigit, price_el.text))
                precio = int(precio_texto) if precio_texto else 0
                
                # Si el texto del producto contiene 'sin stock' o 'falso', asumimos que no hay stock
                texto_producto = item.text.lower()
                tiene_stock = "sin stock" not in texto_producto and "agotado" not in texto_producto
                
                productos[nombre] = {
                    "nombre_real": title_el.text.strip(),
                    "precio": precio,
                    "stock": tiene_stock
                }
    except Exception as e:
        print(f"Error scrapeando Tu Tienda (Web B): {e}")
    return productos

def procesar_logica():
    print("Chequeando productos...")
    estado_anterior = cargar_estado_anterior()
    
    prod_a = scrapear_web_a()
    prod_b = scrapear_web_b()
    
    if not prod_a:
        print("No se pudo obtener información del proveedor. Reintentando en el próximo ciclo.")
        return

    # REGLA 4: Alerta de productos nuevos en el proveedor
    for nombre, datos in prod_a.items():
        if nombre not in estado_anterior["productos_a"]:
            enviar_telegram(f"🆕 *¡Nuevo Producto en Proveedor!*\n*Nombre:* {datos['nombre_real']}\n*Precio:* ${datos['precio']}")

    # REGLAS 1, 2 y 3: Comparación cruzada
    for nombre, datos_a in prod_a.items():
        if nombre in prod_b:
            datos_b = prod_b[nombre]
            
            # REGLA 1: Web A no tiene stock pero tu Web B sí tiene (¡Peligro de vender sin stock!)
            if not datos_a["stock"] and datos_b["stock"]:
                enviar_telegram(f"⚠️ *Alerta Stock:* El proveedor NO tiene stock de `{datos_a['nombre_real']}`, pero en tu tienda figura como DISPONIBLE.")
            
            # REGLA 3: Tu tienda (B) tiene el precio más bajo que el proveedor (A) (Estás perdiendo plata)
            if datos_b["precio"] < datos_a["precio"]:
                enviar_telegram(f"📉 *Alerta Precio:* Tu precio (${datos_b['precio']}) es MENOR que el del proveedor (${datos_a['precio']}) en `{datos_a['nombre_real']}`.")

    # Guardar estado para la próxima vuelta
    guardar_estado_actual({"productos_a": prod_a, "productos_b": prod_b})
    print("Ciclo completado con éxito.")

if __name__ == "__main__":
    print("Bot iniciado con éxito. Corriendo control cada 15 minutos...")
    while True:
        procesar_logica()
        time.sleep(900)  # Chequea cada 15 minutos para evitar bloqueos


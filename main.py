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
        # Cabeceras completas para simular un navegador Google Chrome real en Windows 10
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'es-ES,es;q=0.8,en-US;q=0.5,en;q=0.3',
            'Upgrade-Insecure-Requests': '1'
        }
        response = requests.get(URL_A, headers=headers, timeout=30)
        
        if response.status_code != 200:
            print(f"Error de conexión Web A: Código de estado {response.status_code}")
            return productos
            
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Buscamos de forma más abierta tanto li.product como div.product
        items = soup.select('li.product, div.product, .product-grid-item') 
        print(f"Productos encontrados en HTML de Web A: {len(items)}") # Log para ver si encuentra algo
        
        for item in items:
            # Selectores flexibles para el título de WooCommerce
            title_el = item.select_one('.woocommerce-loop-product__title, .product-title, h2, h3')
            price_el = item.select_one('.price, .woocommerce-Price-amount')
            
            if title_el and price_el:
                nombre = title_el.text.strip().lower()
                
                # Extraemos solo los números del precio
                precio_texto = ''.join(filter(str.isdigit, price_el.text))
                precio = int(precio_texto) if precio_texto else 0
                
                # WooCommerce detecta falta de stock con clases en el contenedor principal
                clases = item.get('class', [])
                tiene_stock = "out-of-stock" not in clases and "instock" in clases
                
                # Si no tiene la clase 'instock' explícita, asumimos True a menos que diga 'sin stock' textualmente
                texto_item = item.text.lower()
                if "sin stock" in texto_item or "agotado" in texto_item:
                    tiene_stock = False
                elif "out-of-stock" not in clases:
                    tiene_stock = True
                
                productos[nombre] = {
                    "nombre_real": title_el.text.strip(),
                    "precio": precio,
                    "stock": tiene_stock
                }
    except Exception as e:
        print(f"Error crítico scrapeando Proveedor (Web A): {e}")
    return productos

def scrapear_web_b():
    """ Scrapea tu tienda (TiendaNegocio) """
    productos = {}
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
        response = requests.get(URL_B, headers=headers, timeout=30)
        
        if response.status_code != 200:
            print(f"Error de conexión Web B: Código de estado {response.status_code}")
            return productos
            
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Selectores adaptados para la plataforma TiendaNegocio
        items = soup.select('.product-item, .item, .js-product-container, div[data-product-id]') 
        print(f"Productos encontrados en HTML de Web B: {len(items)}")
        
        for item in items:
            title_el = item.select_one('.item-name, .product-title, h2, .title')
            price_el = item.select_one('.item-price, .product-price, .price, .money')
            
            if title_el and price_el:
                nombre = title_el.text.strip().lower()
                precio_texto = ''.join(filter(str.isdigit, price_el.text))
                precio = int(precio_texto) if precio_texto else 0
                
                texto_producto = item.text.lower()
                tiene_stock = "sin stock" not in texto_producto and "agotado" not in texto_producto
                
                productos[nombre] = {
                    "nombre_real": title_el.text.strip(),
                    "precio": precio,
                    "stock": tiene_stock
                }
    except Exception as e:
        print(f"Error crítico scrapeando Tu Tienda (Web B): {e}")
    return productos

def procesar_logica():
    print("--- Iniciando Chequeo de productos ---")
    estado_anterior = cargar_estado_anterior()
    
    prod_a = scrapear_web_a()
    prod_b = scrapear_web_b()
    
    if not prod_a:
        print("❌ Alerta: No se pudo leer ningún producto del proveedor (Web A). Reintentando en el próximo ciclo.")
        return
    if not prod_b:
        print("❌ Alerta: No se pudo leer ningún producto de tu tienda (Web B). Reintentando en el próximo ciclo.")
        return

    print(f"✅ Web A procesada con {len(prod_a)} productos.")
    print(f"✅ Web B procesada con {len(prod_b)} productos.")

    # REGLA 4: Alerta de productos nuevos en el proveedor
    for nombre, datos in prod_a.items():
        if nombre not in estado_anterior.get("productos_a", {}):
            enviar_telegram(f"🆕 *¡Nuevo Producto en Proveedor!*\n*Nombre:* {datos['nombre_real']}\n*Precio:* ${datos['precio']}")

    # REGLAS 1, 2 y 3: Comparación cruzada
    for nombre, datos_a in prod_a.items():
        if nombre in prod_b:
            datos_b = prod_b[nombre]
            
            # REGLA 1: Web A no tiene stock pero tu Web B sí tiene
            if not datos_a["stock"] and datos_b["stock"]:
                enviar_telegram(f"⚠️ *Alerta Stock:* El proveedor NO tiene stock de `{datos_a['nombre_real']}`, pero en tu tienda figura como DISPONIBLE.")
            
            # REGLA 3: Tu tienda (B) tiene el precio más bajo que el proveedor (A)
            if datos_b["precio"] < datos_a["precio"]:
                enviar_telegram(f"📉 *Alerta Precio:* Tu precio (${datos_b['precio']}) es MENOR que el del proveedor (${datos_a['precio']}) en `{datos_a['nombre_real']}`.")

    # Guardar estado para la próxima vuelta
    guardar_estado_actual({"productos_a": prod_a, "productos_b": prod_b})
    print("--- Ciclo completado con éxito ---")

if __name__ == "__main__":
    print("Bot iniciado con éxito. Corriendo control de fondo...")
    while True:
        procesar_logica()
        time.sleep(900)  # Chequea cada 15 minutos

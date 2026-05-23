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
    """ Scrapea el proveedor (rxzweb) imitando un iPhone para saltar el error 403 """
    productos = {}
    try:
        # Cabeceras de iPhone (suelen saltarse bloqueos Cloudflare comunes)
        headers = {
            'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.5 Mobile/15E148 Safari/604.1',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'es-ES,es;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br'
        }
        
        session = requests.Session()
        response = session.get(URL_A, headers=headers, timeout=30)
        
        if response.status_code != 200:
            print(f"Error de conexión Web A: Código de estado {response.status_code}")
            # Intento alternativo sin HTTPS estricto si falla
            response = session.get(URL_A, headers=headers, timeout=30, verify=False)
            if response.status_code != 200:
                return productos
            
        soup = BeautifulSoup(response.text, 'lxml')
        
        # Buscamos de forma masiva cualquier estructura de producto de WooCommerce
        items = soup.find_all(['li', 'div'], class_=lambda x: x and 'product' in x)
        print(f"Elementos sospechosos de ser productos en Web A: {len(items)}")
        
        for item in items:
            title_el = item.find(['h2', 'h3', 'h4', 'a'], class_=lambda x: x and 'title' in x or 'woocommerce-loop' in x)
            price_el = item.find(class_=lambda x: x and 'price' in x)
            
            if title_el and price_el and title_el.text.strip():
                nombre = title_el.text.strip().lower()
                
                precio_texto = ''.join(filter(str.isdigit, price_el.text))
                precio = int(precio_texto) if precio_texto else 0
                
                texto_item = item.text.lower()
                clases = item.get('class', [])
                
                tiene_stock = True
                if "sin stock" in texto_item or "agotado" in texto_item or "out-of-stock" in clases:
                    tiene_stock = False
                
                productos[nombre] = {
                    "nombre_real": title_el.text.strip(),
                    "precio": precio,
                    "stock": tiene_stock
                }
    except Exception as e:
        print(f"Error crítico scrapeando Proveedor (Web A): {e}")
    return productos

def scrapear_web_b():
    """ Scrapea tu tienda (TiendaNegocio) buscando estructuras genéricas de e-commerce """
    productos = {}
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        response = requests.get(URL_B, headers=headers, timeout=30)
        soup = BeautifulSoup(response.text, 'lxml')
        
        # TiendaNegocio suele estructurar en divs con clases de productos o directamente enlaces
        # Buscamos de forma muy abierta para capturar todo
        items = soup.find_all(['div', 'li', 'article'])
        
        for item in items:
            # Buscamos títulos por etiquetas comunes dentro de contenedores pequeños
            title_el = item.find(['h2', 'h3', 'h1'], class_=lambda x: x and ('title' in x or 'name' in x or 'producto' in x))
            price_el = item.find(class_=lambda x: x and ('price' in x or 'precio' in x or 'money' in x))
            
            if title_el and price_el:
                nombre = title_el.text.strip().lower()
                if nombre not in productos: # Evitar duplicar sub-elementos
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
    
    print(f"📊 Resumen de escaneo:")
    print(f" -> Web A (Proveedor): {len(prod_a)} productos encontrados.")
    print(f" -> Web B (Tu tienda): {len(prod_b)} productos encontrados.")
    
    if not prod_a:
        print("❌ Freno preventivo: No se detectaron productos en el Proveedor (Posible bloqueo 403 activo).")
        return

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

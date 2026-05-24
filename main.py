import os
import time
import json
import requests
from bs4 import BeautifulSoup
import urllib3

# Desactivar advertencias de certificados SSL en el log
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Buscamos las variables limpiando cualquier espacio accidental que tengan en Railway
TELEGRAM_TOKEN = None
CHAT_ID = None

for k, v in os.environ.items():
    if "TELEGRAM_TOKEN" in k:
        TELEGRAM_TOKEN = v.strip()
    if "CHAT_ID" in k:
        CHAT_ID = v.strip()

URL_A = "https://rxzweb.com/tienda/?et_per_page=-1"
URL_B = "https://leandroid.tiendanegocio.com/productos"
DB_FILE = "estado_productos.json"

def enviar_telegram(mensaje):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print(f"❌ ERROR CRÍTICO: Variables no detectadas. Claves en sistema: {[k for k in os.environ.keys() if 'TOKEN' in k or 'CHAT' in k]}")
        return
        
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": mensaje, "parse_mode": "Markdown"}
    
    try:
        response = requests.post(url, json=payload, timeout=15)
        if response.status_code != 200:
            print(f"❌ ERROR DE TELEGRAM (Código {response.status_code}): {response.text}")
        else:
            print("🚀 Mensaje enviado a Telegram con éxito.")
    except Exception as e:
        print(f"❌ Error de conexión al intentar hablar con Telegram: {e}")

def cargar_estado_anterior():
    if os.path.exists(DB_FILE):
        with open(DB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"productos_a": {}, "productos_b": {}}

def guardar_estado_actual(estado):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(estado, f, ensure_ascii=False, indent=4)

def scrapear_web_a():
    productos = {}
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    try:
        session = requests.Session()
        response = session.get(URL_A, headers=headers, timeout=30, verify=False)
        
        soup = BeautifulSoup(response.text, 'lxml')
        items = soup.find_all(['li', 'div'], class_=lambda x: x and 'product' in x)
        
        for item in items:
            title_el = item.find(['h2', 'h3', 'h4', 'a'], class_=lambda x: x and ('title' in x or 'woocommerce-loop' in x))
            price_el = item.find(class_=lambda x: x and 'price' in x)
            
            if title_el and price_el and title_el.text.strip():
                nombre = title_el.text.strip().lower()
                precio_texto = ''.join(filter(str.isdigit, price_el.text))
                precio = int(precio_texto) if precio_texto else 0
                
                texto_item = item.text.lower()
                clases = item.get('class', [])
                tiene_stock = not ("sin stock" in texto_item or "agotado" in texto_item or "out-of-stock" in clases)
                
                productos[nombre] = {
                    "nombre_real": title_el.text.strip(),
                    "precio": precio,
                    "stock": tiene_stock
                }
    except Exception as e:
        print(f"Error Proveedor (Web A): {e}")
    return productos

def scrapear_web_b():
    productos = {}
    pagina_actual = 1
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
    }
    
    while True:
        url = URL_B if pagina_actual == 1 else f"{URL_B}?page={pagina_actual}"
        print(f"Scrapeando Tu Tienda (Web B) - Página {pagina_actual}...")
        
        try:
            response = requests.get(url, headers=headers, timeout=30)
            if response.status_code != 200:
                break
                
            soup = BeautifulSoup(response.text, 'lxml')
            items = soup.find_all(['div', 'li', 'article', 'form'])
            productos_en_pagina = 0
            
            for item in items:
                title_el = item.find(['h2', 'h3', 'h1', 'a'], class_=lambda x: x and ('title' in x or 'name' in x or 'producto' in x))
                price_el = item.find(class_=lambda x: x and ('price' in x or 'precio' in x or 'money' in x))
                
                if title_el and price_el and title_el.text.strip():
                    nombre = title_el.text.strip().lower()
                    if nombre not in productos:
                        precio_texto = ''.join(filter(str.isdigit, price_el.text))
                        precio = int(precio_texto) if precio_texto else 0
                        
                        texto_producto = item.text.lower()
                        tiene_stock = "sin stock" not in texto_producto and "agotado" not in texto_producto
                        
                        productos[nombre] = {
                            "nombre_real": title_el.text.strip(),
                            "precio": precio,
                            "stock": tiene_stock
                        }
                        productos_en_pagina += 1
            
            print(f"Página {pagina_actual}: {productos_en_pagina} productos.")
            if productos_en_pagina == 0:
                break
            pagina_actual += 1
            time.sleep(1)
        except Exception as e:
            print(f"Error Web B: {e}")
            break
    return productos

def procesar_logica():
    print("--- Iniciando Chequeo de productos ---")
    
    print("Enviando señal de inicio a Telegram...")
    enviar_telegram("🤖 *Bot de Monitoreo Activo*\nIniciando ciclo de control de stock y precios...")

    estado_anterior = cargar_estado_anterior()
    
    prod_a = scrapear_web_a()
    prod_b = scrapear_web_b()
    
    print(f"📊 Resumen: Web A: {len(prod_a)} | Web B: {len(prod_b)}")
    
    if not prod_a:
        print("⚠️ Nota: Proveedor vacío en este ciclo, se omiten comparaciones de stock.")
        return

    # REGLA 4: Alertas de productos nuevos
    for nombre, datos in prod_a.items():
        if nombre not in estado_anterior.get("productos_a", {}):
            enviar_telegram(f"🆕 *¡Nuevo Producto!*\n*Nombre:* {datos['nombre_real']}\n*Precio:* ${datos['precio']}")

    # REGLAS 1 y 3: Comparación cruzada flexible
    for nombre_a, datos_a in prod_a.items():
        for nombre_b, datos_b in prod_b.items():
            if nombre_a in nombre_b or nombre_b in nombre_a:
                if not datos_a["stock"] and datos_b["stock"]:
                    enviar_telegram(f"⚠️ *Alerta Stock:* El proveedor NO tiene stock de `{datos_a['nombre_real']}`, pero en tu tienda figura como DISPONIBLE.")
                if datos_b["precio"] < datos_a["precio"] and datos_b["precio"] > 0:
                    enviar_telegram(f"📉 *Alerta Precio:* Tu precio (${datos_b['precio']}) es MENOR que el del proveedor (${datos_a['precio']}) en `{datos_a['nombre_real']}`.")

    guardar_estado_actual({"productos_a": prod_a, "productos_b": prod_b})
    print("--- Ciclo completado con éxito ---")

if __name__ == "__main__":
    print("Bot iniciado con éxito. Corriendo...")
    while True:
        procesar_logica()
        time.sleep(900)

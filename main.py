import os
import time
import json
import requests
from bs4 import BeautifulSoup
import urllib3

# Desactivar advertencias de certificados SSL
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Configuración de variables de entorno de Railway
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
        print("❌ ERROR CRÍTICO: Variables de Telegram ausentes en Railway.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": mensaje, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=15)
        print("🚀 Mensaje enviado a Telegram con éxito.")
    except Exception as e:
        print(f"❌ Error al enviar a Telegram: {e}")

def cargar_estado_anterior():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"productos_a": {}, "productos_b": {}}
    return {"productos_a": {}, "productos_b": {}}

def guardar_estado_actual(estado):
    try:
        with open(DB_FILE, "w", encoding="utf-8") as f:
            json.dump(estado, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"❌ Error al guardar la base de datos local: {e}")

def limpiar_precio(texto_precio):
    """Limpia el texto del precio eliminando centavos tras la coma y dejando solo enteros"""
    if not texto_precio:
        return 0
    try:
        # Si tiene coma (separador de centavos), nos quedamos solo con la parte entera de la izquierda
        if "," in texto_precio:
            texto_precio = texto_precio.split(",")[0]
        
        # Filtramos y nos quedamos solo con los números puros
        precio_numerico = ''.join(filter(str.isdigit, texto_precio))
        return int(precio_numerico) if precio_numerico else 0
    except Exception:
        return 0

def scrapear_web_a():
    """Scraping del Proveedor (Web A)"""
    productos = {}
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'es-419,es;q=0.9,en;q=0.8',
        'Connection': 'keep-alive'
    }
    try:
        print("Consultando Web A (Proveedor)...")
        session = requests.Session()
        response = session.get(URL_A, headers=headers, timeout=45, verify=False)
        
        if response.status_code != 200:
            return productos

        soup = BeautifulSoup(response.text, 'lxml')
        items = soup.find_all(['li', 'div'], class_=lambda x: x and 'product' in x)
        
        if len(items) == 0:
            items = soup.select('ul.products li') or soup.find_all('div', class_='product-grid-item')

        for item in items:
            title_el = item.find(['h2', 'h3', 'h4', 'a', 'p'], class_=lambda x: x and ('title' in x or 'woocommerce-loop' in x or 'name' in x))
            price_el = item.find(class_=lambda x: x and ('price' in x or 'precio' in x))
            
            if not title_el:
                title_el = item.find(['h2', 'h3', 'h4'])

            if title_el and price_el and title_el.text.strip():
                nombre_original = title_el.text.strip()
                nombre_clave = nombre_original.lower().replace("  ", " ")
                
                # Usamos la nueva función de limpieza inteligente para evitar los centavos extra
                precio = limpiar_precio(price_el.text)
                
                texto_item = item.text.lower()
                clases = item.get('class', [])
                tiene_stock = not ("sin stock" in texto_item or "agotado" in texto_item or "out-of-stock" in clases)
                
                productos[nombre_clave] = {
                    "nombre_real": nombre_original,
                    "precio": precio,
                    "stock": tiene_stock
                }
    except Exception as e:
        print(f"❌ Error en scraping de Web A: {e}")
    return productos

def scrapear_web_b():
    """Scraping de Tu Tienda (Web B)"""
    productos = {}
    pagina_actual = 1
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
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
                    nombre_original = title_el.text.strip()
                    nombre_clave = nombre_original.lower().replace("  ", " ")
                    
                    if nombre_clave not in productos:
                        precio = limpiar_precio(price_el.text)
                        
                        texto_producto = item.text.lower()
                        tiene_stock = "sin stock" not in texto_producto and "agotado" not in texto_producto
                        
                        productos[nombre_clave] = {
                            "nombre_real": nombre_original,
                            "precio": precio,
                            "stock": tiene_stock
                        }
                        productos_en_pagina += 1
            
            if productos_en_pagina == 0:
                break
            pagina_actual += 1
            time.sleep(1.0)
        except Exception as e:
            print(f"❌ Error en scraping de Web B: {e}")
            break
    return productos

def procesar_logica():
    print("\n--- 🔄 Iniciando Nuevo Ciclo de Monitoreo ---")
    
    estado_anterior = cargar_estado_anterior()
    db_vacia = len(estado_anterior.get("productos_a", {})) == 0

    prod_a = scrapear_web_a()
    prod_b = scrapear_web_b()
    
    print(f"📊 Resumen: Web A (Proveedor): {len(prod_a)} | Web B (Tu tienda): {len(prod_b)}")
    
    if len(prod_a) == 0:
        print("⚠️ Freno preventivo: El proveedor devolvió 0 productos.")
        return

    # REGLA 1: Alertas de productos totalmente NUEVOS en el proveedor
    if not db_vacia:
        for nombre_clave, datos in prod_a.items():
            if nombre_clave not in estado_anterior.get("productos_a", {}):
                msg_nuevo = (
                    f"🆕 *¡Nuevo Producto!*\n"
                    f"{datos['nombre_real']}\n\n"
                    f"💰 *Precio proveedor:* ${datos['precio']:,}"
                )
                enviar_telegram(msg_nuevo)

    # REGLAS DE COMPARACIÓN CRUZADA (PROVEEDOR vs TU WEB)
    for clave_a, datos_a in prod_a.items():
        for clave_b, datos_b in prod_b.items():
            if clave_a in clave_b or clave_b in clave_a:
                
                # REGLA 2: Alerta de Stock Desincronizado (Proveedor sin stock, vos sí)
                if not datos_a["stock"] and datos_b["stock"]:
                    msg_stock = (
                        f"⚠️ *Alerta de Stock:*\n"
                        f"{datos_a['nombre_real']}\n\n"
                        f"❌ *Proveedor:* SIN STOCK\n"
                        f"✅ *Tu Web:* Disponible"
                    )
                    enviar_telegram(msg_stock)
                
                # REGLA 3: Alerta de Precio Bajo (Tu precio quedó por debajo de su costo)
                if datos_b["precio"] < datos_a["precio"] and datos_b["precio"] > 0:
                    msg_precio = (
                        f"📉 *Alerta de Precio Bajo:*\n"
                        f"{datos_a['nombre_real']}\n\n"
                        f"📱 *Tu web:* ${datos_b['precio']:,}\n"
                        f"📦 *Web proveedor:* ${datos_a['precio']:,}"
                    )
                    enviar_telegram(msg_precio)

    guardar_estado_actual({"productos_a": prod_a, "productos_b": prod_b})
    print("--- ✅ Ciclo completado y base de datos actualizada ---")

if __name__ == "__main__":
    print("🚀 Bot de Control Iniciado Exitosamente...")
    
    while True:
        procesar_logica()
        print("💤 Esperando 30 minutos hasta el próximo control...")
        time.sleep(1800)

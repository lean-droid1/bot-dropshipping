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

def scrapear_web_a():
    """Scraping del Proveedor (Web A)"""
    productos = {}
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'es-419,es;q=0.9,en;q=0.8',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1'
    }
    try:
        print("Consultando Web A (Proveedor)...")
        session = requests.Session()
        response = session.get(URL_A, headers=headers, timeout=45, verify=False)
        
        print(f"Respuesta Web A - Código de estado: {response.status_code}")
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
                
                precio_texto = ''.join(filter(str.isdigit, price_el.text))
                precio = int(precio_texto) if precio_texto else 0
                
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
                        precio_texto = ''.join(filter(str.isdigit, price_el.text))
                        precio = int(precio_texto) if precio_texto else 0
                        
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
    
    print(f"📊 Resumen de escaneo: Web A (Proveedor): {len(prod_a)} | Web B (Tu tienda): {len(prod_b)}")
    
    # FRENO DE SEGURIDAD: Si la Web A da cero por bloqueo o error, no rompemos nada.
    if len(prod_a) == 0:
        print("⚠️ Freno preventivo: El proveedor devolvió 0 productos. Se cancela el ciclo.")
        return

    # REGLA 1: Alertas de productos verdaderamente NUEVOS en el proveedor
    # Solo se ejecuta si ya teníamos guardados datos previos (evita el spam del primer inicio)
    if not db_vacia:
        for nombre_clave, datos in prod_a.items():
            if nombre_clave not in estado_anterior.get("productos_a", {}):
                enviar_telegram(f"🆕 *¡Nuevo Producto en Proveedor!*\n*Nombre:* {datos['nombre_real']}\n*Precio:* ${datos['precio']:,}")

    # REGLAS DE COMPARACIÓN CRUZADA (PROVEEDOR vs TU WEB)
    # Buscamos coincidencias inteligentes de nombres entre ambas tiendas
    for clave_a, datos_a in prod_a.items():
        for clave_b, datos_b in prod_b.items():
            # Si los nombres coinciden o uno está contenido dentro del otro
            if clave_a in clave_b or clave_b in clave_a:
                
                # REGLA 2: Alerta si el proveedor se quedó SIN STOCK pero vos lo tenés ACTIVO
                if not datos_a["stock"] and datos_b["stock"]:
                    enviar_telegram(f"⚠️ *Alerta Stock Desincronizado:*\nEl proveedor NO tiene stock de `{datos_a['nombre_real']}`, pero en tu tienda todavía figura como *Disponible*.")
                
                # REGLA 3: Alerta si quedaste más BARATO que el costo del proveedor
                if datos_b["precio"] < datos_a["precio"] and datos_b["precio"] > 0:
                    enviar_telegram(f"📉 *Alerta de Precio Bajo:*\nTu precio (${datos_b['precio']:,}) es MENOR que el del proveedor (${datos_a['precio']:,}) en el producto: `{datos_a['nombre_real']}`.")

    # Guardamos el catálogo actual para el próximo ciclo
    guardar_estado_actual({"productos_a": prod_a, "productos_b": prod_b})
    print("--- ✅ Ciclo completado y base de datos actualizada ---")

if __name__ == "__main__":
    print("🚀 Bot de Control Iniciado Exitosamente. Corriendo en segundo plano...")
    # Envía un aviso limpio a Telegram una única vez al arrancar el contenedor
    enviar_telegram("🤖 *¡Bot Inicializado!*\nA partir de este momento monitoreando cambios de precios y stock cada 30 minutos.")
    
    while True:
        procesar_logica()
        print("💤 Durmiendo por 30 minutos hasta el próximo control...")
        time.sleep(1800)

import os
import time
import json
import requests
from bs4 import BeautifulSoup
import urllib3
import re

# Desactivar advertencias de certificados SSL
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Configuración de variables de entorno de Railway
TELEGRAM_TOKEN = None
CHAT_ID = None
SCRAPERAPI_KEY = None

for k, v in os.environ.items():
    if "TELEGRAM_TOKEN" in k:
        TELEGRAM_TOKEN = v.strip()
    if "CHAT_ID" in k:
        CHAT_ID = v.strip()
    if "SCRAPERAPI_KEY" in k:
        SCRAPERAPI_KEY = v.strip()

URL_A = "https://rxzweb.com/tienda/?et_per_page=-1"
URL_B = "https://leandroid.tiendanegocio.com/productos"
DB_FILE = "estado_productos.json"

# LISTA DE MARCAS Y PALABRAS DE INTERÉS
PALABRAS_INTERES = [
    'ma ant', 'amaoe', '2uul', 'goot wick', 'mijing', 'louwei', 
    'rf4', 'jakemy', 'kailiwei', 'kslid', 'aifen', 'sugon',
    'organizador', 'cinta', 'silla', 'mesa', 'puas', 'hilo', 'cepillo'
]

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

def limpiar_precio(html_precio):
    """Limpia el precio manejando ofertas (<ins>) y formatos con coma decimal"""
    if not html_precio:
        return 0
    try:
        # Si es un objeto de BeautifulSoup, buscamos si hay una oferta (tag <ins>)
        if hasattr(html_precio, 'find'):
            ins_tag = html_precio.find('ins')
            if ins_tag:
                texto = ins_tag.text.strip()
            else:
                texto = html_precio.text.strip()
        else:
            texto = str(html_precio).strip()

        # Si el texto contiene una coma de decimales (ej: ,00), tiramos lo que esté después
        if "," in texto:
            texto = texto.split(",")[0]
            
        # Nos quedamos solo con los números pura y exclusivamente
        precio_numerico = ''.join(filter(str.isdigit, texto))
        return int(precio_numerico) if precio_numerico else 0
    except Exception:
        return 0

def son_coincidentes_inteligentes(nombre1, nombre2):
    n1 = re.sub(r'[^a-z0-9 ]', ' ', nombre1.lower())
    n2 = re.sub(r'[^a-z0-9 ]', ' ', nombre2.lower())
    palabras_n1 = set(n1.split())
    palabras_n2 = set(n2.split())
    descartables = {'de', 'para', 'con', 'el', 'la', 'los', 'las', 'un', 'una', 'y', 'en', 'del', 'al'}
    palabras_n1 = palabras_n1 - descartables
    palabras_n2 = palabras_n2 - descartables
    if not palabras_n1 or not palabras_n2:
        return False
    comunes = palabras_n1.intersection(palabras_n2)
    numeros_n1 = {w for w in palabras_n1 if any(char.isdigit() for char in w)}
    numeros_n2 = {w for w in palabras_n2 if any(char.isdigit() for char in w)}
    if numeros_n1 or numeros_n2:
        if numeros_n1 != numeros_n2:
            return False
    menor_cantidad = min(len(palabras_n1), len(palabras_n2))
    porcentaje_coincidencia = len(comunes) / menor_cantidad
    return porcentaje_coincidencia >= 0.75

def scrapear_web_a():
    productos = {}
    if SCRAPERAPI_KEY:
        print("🔗 Utilizando túnel anti-bloqueos (ScraperAPI)...")
        target_url = f"http://api.scraperapi.com?api_key={SCRAPERAPI_KEY}&url={URL_A}"
        headers = None
    else:
        print("⚠️ Modo clásico activo (Sin proxy anti-bloqueos)...")
        target_url = URL_A
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept-Language': 'es-419,es;q=0.9,en;q=0.8'
        }
    try:
        print("Consultando Web A (Proveedor)...")
        response = requests.get(target_url, headers=headers, timeout=60, verify=False)
        print(f"Respuesta Web A - Código de estado: {response.status_code}")
        if response.status_code != 200:
            return productos

        soup = BeautifulSoup(response.text, 'lxml')
        # Selector genérico ultra-amplio para capturar estructuras Woocommerce fijas y dinámicas
        items = soup.select('.product') or soup.find_all(['li', 'div'], class_=lambda x: x and 'product' in x)

        for item in items:
            title_el = item.find(['h2', 'h3', 'h4', 'a', 'p'], class_=lambda x: x and ('title' in x or 'woocommerce-loop' in x or 'name' in x))
            price_el = item.find(class_=lambda x: x and ('price' in x or 'precio' in x))
            if not title_el:
                title_el = item.find(['h2', 'h3', 'h4'])

            if title_el and price_el and title_el.text.strip():
                nombre_original = title_el.text.strip()
                # Parche: Evitar capturar bloques vacíos o contenedores de categorías
                if len(nombre_original) < 3:
                    continue
                nombre_clave = " ".join(nombre_original.lower().split())
                precio = limpiar_precio(price_el)
                if precio > 0:
                    productos[nombre_clave] = {
                        "nombre_real": nombre_original,
                        "precio": precio,
                        "stock": True
                    }
    except Exception as e:
        print(f"❌ Error en scraping de Web A: {e}")
    return productos

def scrapear_web_b():
    productos = {}
    pagina_actual = 1
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
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
                    nombre_clave = " ".join(nombre_original.lower().split())
                    if nombre_clave not in productos:
                        precio = limpiar_precio(price_el)
                        texto_producto = item.text.lower()
                        tiene_stock = "sin stock" not in texto_producto and "agotado" not in texto_producto
                        if precio > 0:
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
        print("⚠️ Freno preventivo: El proveedor devolvió 0 productos. Esperando próxima vuelta.")
        return

    # REGLA DE OPORTUNIDADES: Si el proveedor suma algo de tu interés que NO tenés en tu web
    if not db_vacia:
        for nombre_clave, datos in prod_a.items():
            interesa_producto = any(p in nombre_clave for p in PALABRAS_INTERES)
            if interesa_producto:
                lo_tengo_en_web = any(son_coincidentes_inteligentes(nombre_clave, cb) for cb in prod_b.keys())
                if not lo_tengo_en_web:
                    if nombre_clave not in estado_anterior.get("productos_a", {}):
                        msg_oportunidad = (
                            f"🔥 *¡Oportunidad de Stock / Nuevo Producto!*\n"
                            f"{datos['nombre_real']}\n\n"
                            f"📦 *El proveedor lo tiene a:* ${datos['precio']:,}\n"
                            f"⚠️ *Nota:* No lo tenés publicado en tu tienda."
                        )
                        enviar_telegram(msg_oportunidad)

    # REGLAS DE COMPARACIÓN CRUZADA (Para productos mapeados en tu web)
    for clave_b, datos_b in prod_b.items():
        encontrado_en_proveedor = False
        datos_a_coincidente = None
        
        for clave_a, datos_a in prod_a.items():
            if son_coincidentes_inteligentes(clave_b, clave_a):
                encontrado_en_proveedor = True
                datos_a_coincidente = datos_a
                break

        # CASO 1: El producto está ACTIVO en tu web
        if datos_b["stock"]:
            if not encontrado_en_proveedor:
                # Evitamos alertar si el producto se llama literalmente "Productos" por un error de tag
                if len(datos_b['nombre_real']) > 3 and datos_b['nombre_real'].lower() != "productos":
                    msg_stock = (
                        f"⚠️ *Alerta de Stock:*\n"
                        f"{datos_b['nombre_real']}\n\n"
                        f"❌ *Proveedor:* SIN STOCK (Eliminado de su web)\n"
                        f"✅ *Tu Web:* Disponible"
                    )
                    enviar_telegram(msg_stock)
                
            elif datos_a_coincidente and datos_b["precio"] < datos_a_coincidente["precio"]:
                msg_precio = (
                    f"📉 *Alerta de precio:*\n"
                    f"{datos_b['nombre_real']}\n\n"
                    f"📱 *Tu web:* ${datos_b['precio']:,}\n"
                    f"📦 *Web proveedor:* ${datos_a_coincidente['precio']:,}"
                )
                enviar_telegram(msg_precio)

        # CASO 2: El producto está SIN STOCK (Pausado) en tu web, pero volvió al proveedor (Opción 3)
        else:
            if encontrado_en_proveedor and datos_a_coincidente:
                estaba_en_proveedor_antes = clave_b in estado_anterior.get("productos_a", {}) or any(son_coincidentes_inteligentes(clave_b, ca) for ca in estado_anterior.get("productos_a", {}).keys())
                
                if not estaba_en_proveedor_antes or db_vacia:
                    msg_recuperado = (
                        f"🔄 *¡Stock Recuperado en Proveedor!*\n"
                        f"{datos_b['nombre_real']}\n\n"
                        f"📦 *Proveedor:* Vuelve a tener stock a ${datos_a_coincidente['precio']:,}\n"
                        f"📱 *Tu Web:* Actualmente figura 'Sin Stock'. ¡Ya podés reactivarlo!"
                    )
                    enviar_telegram(msg_recuperado)

    guardar_estado_actual({"productos_a": prod_a, "productos_b": prod_b})
    print("--- ✅ Ciclo completado y base de datos actualizada ---")

if __name__ == "__main__":
    print("🚀 Bot de Control Iniciado Exitosamente...")
    while True:
        procesar_logica()
        print("💤 Esperando 30 minutos hasta el próximo control...")
        time.sleep(1800)

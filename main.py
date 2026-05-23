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
        headers = {
            'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone
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
            
            print(f"Encontrados {productos_en_pagina} nuevos productos en la página {pagina_actual}.")
            
            # Si en esta página no encontramos ningún producto nuevo, significa que ya pasamos el límite y terminamos
            if productos_en_pagina == 0:
                break
                
            # Avanzamos a la siguiente página
            pagina_actual += 1
            time.sleep(1) # Pausa de 1 segundo entre páginas para evitar bloqueos
            
        except Exception as e:
            print(f"Error en paginación de Web B: {e}")
            break
            
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
        print("❌ Freno preventivo: No se detectaron productos en el Proveedor.")
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

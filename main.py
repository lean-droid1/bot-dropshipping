import os
import time
import json
import requests
from bs4 import BeautifulSoup
import urllib3
import re
import imaplib
import email
from email.header import decode_header
from datetime import datetime, timedelta

# Desactivar advertencias de certificados SSL para evitar ruidos en consola
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Configuración de URLs y Base de Datos local
URL_A = "https://rxzweb.com/tienda/?et_per_page=-1"
URL_B = "https://leandroid.tiendanegocio.com/productos"
DB_FILE = "estado_productos.json"

# LISTA DE MARCAS Y PALABRAS DE INTERÉS PARA ALERTAS DE NUEVOS/OFERTAS
PALABRAS_INTERES = [
    'ma ant', 'amaoe', '2uul', 'goot wick', 'mijing', 'louwei', 
    'rf4', 'jakemy', 'kailiwei', 'kslid', 'aifen', 'sugon', 'jcid', 'jc',
    'v1', 'v1s', 'v1se', 'v1 pro', 'programadora',
    'organizador', 'cinta', 'silla', 'mesa', 'puas', 'hilo', 'cepillo'
]

# Captura limpia de variables de entorno de Railway
TELEGRAM_TOKEN = None
CHAT_ID = None
SCRAPERAPI_KEY = None
GMAIL_USER = None
GMAIL_PASS = None

for k, v in os.environ.items():
    if "TELEGRAM_TOKEN" in k: TELEGRAM_TOKEN = v.strip()
    if "CHAT_ID" in k: CHAT_ID = v.strip()
    if "SCRAPERAPI_KEY" in k: SCRAPERAPI_KEY = v.strip()
    if "GMAIL_USER" in k: GMAIL_USER = v.strip()
    if "GMAIL_PASS" in k: GMAIL_PASS = v.strip()


def enviar_telegram(mensaje):
    if not mensaje or not TELEGRAM_TOKEN or not CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": mensaje, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=15)
        print("🚀 Mensaje agrupado enviado a Telegram con éxito.")
    except Exception as e:
        print(f"❌ Error al enviar a Telegram: {e}")


def cargar_estado_anterior():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if "pedidos_procesados" not in data:
                    data["pedidos_procesados"] = []
                if "productos_a" not in data:
                    data["productos_a"] = {}
                if "productos_b" not in data:
                    data["productos_b"] = {}
                return data
        except Exception:
            return {"productos_a": {}, "productos_b": {}, "pedidos_procesados": []}
    return {"productos_a": {}, "productos_b": {}, "pedidos_procesados": []}


def guardar_estado_actual(estado):
    try:
        with open(DB_FILE, "w", encoding="utf-8") as f:
            json.dump(estado, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"❌ Error al guardar la base de datos local: {e}")


def limpiar_precio_simple(texto):
    if not texto: return 0
    if "," in texto: texto = texto.split(",")[0]
    precio_numerico = ''.join(filter(str.isdigit, texto))
    return int(precio_numerico) if precio_numerico else 0


def procesar_html_precio(html_precio):
    if not html_precio: return 0, 0, False
    try:
        del_tag = html_precio.find('del')
        ins_tag = html_precio.find('ins')
        if del_tag and ins_tag:
            precio_viejo = limpiar_precio_simple(del_tag.text)
            precio_nuevo = limpiar_precio_simple(ins_tag.text)
            if precio_nuevo > 0: return precio_nuevo, precio_viejo, True
        precio_normal = limpiar_precio_simple(html_precio.text)
        return precio_normal, 0, False
    except Exception:
        return 0, 0, False


def son_coincidentes_inteligentes(nombre1, nombre2):
    n1 = nombre1.lower()
    n2 = nombre2.lower()
    
    # Si son exactamente iguales tras limpiar espacios, matchean directo
    if " ".join(n1.split()) == " ".join(n2.split()):
        return True
    
    # --- FILTRO SEPARADOR CRÍTICO ---
    palabras_raiz = ['bateria', 'battery', 'bat', 'face', 'id', 'maneral', 'mango', 'zocalo', 'board']
    for pr in palabras_raiz:
        if (pr in n1 and pr not in n2) or (pr in n2 and pr not in n1):
            return False

    base_n1 = n1.split('(')[0].strip() if '(' in n1 else n1
    base_n2 = n2.split('(')[0].strip() if '(' in n2 else n2
    
    palabras_criticas = ['mini', 'pro', 'plus', 'max', 'kit', 'ultra', 'xl', 'lw-a1']
    for pc in palabras_criticas:
        if (pc in n1 and pc not in n2) or (pc in n2 and pc not in n1):
            return False

    n1_clean = re.sub(r'[^a-z0-9 ]', ' ', n1)
    n2_clean = re.sub(r'[^a-z0-9 ]', ' ', n2)
    palabras_n1 = set(n1_clean.split())
    palabras_n2 = set(n2_clean.split())
    
    descartables = {'de', 'para', 'con', 'el', 'la', 'los', 'las', 'un', 'una', 'y', 'en', 'del', 'al'}
    palabras_n1 = palabras_n1 - descartables
    palabras_n2 = palabras_n2 - descartables
    
    if not palabras_n1 or not palabras_n2: return False
    comunes = palabras_n1.intersection(palabras_n2)
    
    numeros_n1 = {w for w in palabras_n1 if any(char.isdigit() for char in w)}
    numeros_n2 = {w for w in palabras_n2 if any(char.isdigit() for char in w)}
    if numeros_n1 or numeros_n2:
        if numeros_n1 != numeros_n2: return False
            
    menor_cantidad = min(len(palabras_n1), len(palabras_n2))
    if menor_cantidad == 0: return False
    
    return (len(comunes) / menor_cantidad) >= 0.85


def extraer_productos_del_mail(cuerpo_texto):
    productos_encontrados = []
    lineas = cuerpo_texto.split('\n')
    en_bloque_productos = False
    
    for linea in lineas:
        linea_limpia = linea.strip()
        if "productos:" in linea_limpia.lower():
            en_bloque_productos = True
            continue
        if en_bloque_productos:
            if not linea_limpia or "subtotal:" in linea_limpia.lower() or "descuento" in linea_limpia.lower():
                en_bloque_productos = False
                continue
            if linea_limpia.startswith('-'):
                match = re.match(r'-\s*(.+?)\s+x(\d+)\s*-', linea_limpia)
                if match:
                    nombre_prod = match.group(1).strip()
                    cantidad = int(match.group(2).strip())
                    productos_encontrados.append({"nombre": nombre_prod, "cantidad": cantidad})
    return productos_encontrados


def chequear_nuevos_pedidos_gmail():
    pedidos = []
    if not GMAIL_USER or not GMAIL_PASS:
        return pedidos

    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(GMAIL_USER, GMAIL_PASS)
        mail.select("inbox")
        
        hace_24h = (datetime.now() - timedelta(days=1)).strftime("%d-%b-%Y")
        criterio_busqueda = f'(FROM "tiendanegocio.com" SINCE {hace_24h})'
        status, mensajes = mail.search(None, criterio_busqueda)
        
        if status != "OK" or not mensajes[0]:
            mail.close()
            mail.logout()
            return pedidos
            
        id_lista = mensajes[0].split()
        
        for msg_id in id_lista:
            str_id = msg_id.decode()
            res, data = mail.fetch(msg_id, "(RFC822)")
            if res != "OK": continue
            
            raw_email = data[0][1]
            msg = email.message_from_bytes(raw_email)
            
            subject, encoding = decode_header(msg["Subject"])[0]
            if isinstance(subject, bytes):
                subject = subject.decode(encoding or "utf-8")
            
            asunto_minuscula = subject.lower()
            
            if any(palabra in asunto_minuscula for palabra in ["compra", "realizó", "pedido", "venta"]):
                match_orden = re.search(r'#(\d+)', subject)
                num_orden_tienda = match_orden.group(1) if match_orden else str_id
                
                cuerpo = ""
                if msg.is_multipart():
                    for part in msg.walk():
                        if part.get_content_type() == "text/plain":
                            cuerpo = part.get_payload(decode=True).decode("utf-8", errors="ignore")
                            break
                else:
                    cuerpo = msg.get_payload(decode=True).decode("utf-8", errors="ignore")
                
                lista_items = extraer_productos_del_mail(cuerpo)
                if lista_items:
                    pedidos.append({
                        "id_mail": str_id,
                        "num_orden": num_orden_tienda,
                        "productos": lista_items
                    })
        mail.close()
        mail.logout()
    except Exception as e:
        print(f"❌ Error leyendo Gmail: {e}")
    return pedidos


def verificar_pedido_contra_proveedor(pedido, prod_proveedor):
    reporte = f"🛒 *¡Nuevo Pedido Recibido en tu Web! (Orden: #{pedido['num_orden']})*\n\n"
    todo_ok = True
    
    for item in pedido["productos"]:
        nombre_item = item["nombre"].lower()
        encontrado = False
        datos_prov = None
        
        for clave_a, datos_a in prod_proveedor.items():
            if son_coincidentes_inteligentes(nombre_item, clave_a):
                encontrado = True
                datos_prov = datos_a
                break
                
        if encontrado and datos_prov:
            reporte += (
                f"✅ *{item['nombre']}* (Cant: {item['cantidad']})\n"
                f"• Proveedor: *CON STOCK* disponible a ${datos_prov['precio']:,}\n\n"
            )
        else:
            todo_ok = False
            reporte += (
                f"❌ *{item['nombre']}* (Cant: {item['cantidad']})\n"
                f"• Proveedor: 🔥 *SIN STOCK / NO DETECTADO*\n\n"
            )
            
    if todo_ok:
        reporte += "🚀 *Verificación:* El proveedor tiene stock de todo. ¡Podés armar el pedido!"
    else:
        reporte += "⚠️ *Verificación:* ¡Atención! Hay faltantes en la web del proveedor."
    return reporte


def extraer_variaciones_woocommerce(url_producto):
    variaciones = {}
    target_url = f"http://api.scraperapi.com?api_key={SCRAPERAPI_KEY}&url={url_producto}"
    try:
        response = requests.get(target_url, timeout=30, verify=False)
        if response.status_code != 200: return variaciones
        
        soup = BeautifulSoup(response.text, 'lxml')
        form_variaciones = soup.find('form', class_='variations_form')
        
        if form_variaciones and form_variaciones.get('data-product_variations'):
            raw_json = form_variaciones['data-product_variations']
            data_variations = json.loads(raw_json)
            
            for var in data_variations:
                atributos = [str(v).replace('-', ' ').replace('_', ' ').strip() for k, v in var.get('attributes', {}).items() if v]
                if not atributos: 
                    continue
                texto_atributos = " - ".join(atributos).title()
                
                precio_display = var.get('display_price', 0)
                is_in_stock = var.get('is_in_stock', True)
                html_variacion = var.get('variation_html', '').lower()
                
                if "agotado" in html_variacion or "out-of-stock" in html_variacion:
                    is_in_stock = False
                
                if precio_display > 0 and is_in_stock:
                    variaciones[texto_atributos] = {
                        "precio": int(precio_display),
                        "stock": True
                    }
    except Exception as e:
        print(f"⚠️ Error desmenuzando variantes del link {url_producto}: {e}")
    return variaciones


def scrapear_web_a():
    productos = {}
    if not SCRAPERAPI_KEY:
        print("❌ ERROR CRÍTICO: SCRAPERAPI_KEY no configurada.")
        return productos

    target_url = f"http://api.scraperapi.com?api_key={SCRAPERAPI_KEY}&url={URL_A}"
    try:
        print("Consultando Web A (Proveedor) mediante túnel ScraperAPI...")
        response = requests.get(target_url, timeout=60, verify=False)
        if response.status_code != 200: return productos
        
        soup = BeautifulSoup(response.text, 'lxml')
        items = soup.select('.product') or soup.find_all(['li', 'div'], class_=lambda x: x and 'product' in x)

        for item in items:
            title_el = item.find(['h2', 'h3', 'h4', 'a', 'p'], class_=lambda x: x and ('title' in x or 'woocommerce-loop' in x or 'name' in x))
            price_el = item.find(class_=lambda x: x and ('price' in x or 'precio' in x))
            link_el = item.find('a', href=True)
            
            if not title_el: title_el = item.find(['h2', 'h3', 'h4'])

            if title_el and title_el.text.strip():
                nombre_original = title_el.text.strip()
                if len(nombre_original) < 4 or nombre_original.lower() == "productos": continue
                nombre_clave = " ".join(nombre_original.lower().split())
                
                interesa = any(p in nombre_clave for p in PALABRAS_INTERES)
                
                es_variable = False
                add_to_cart_el = item.find('a', class_='product_type_variable')
                if add_to_cart_el or (price_el and "–" in price_el.text):
                    es_variable = True

                if interesa and es_variable and link_el:
                    print(f"🔎 Producto variable detectado: '{nombre_original}'. Escaneando propiedades...")
                    id_variaciones = extraer_variaciones_woocommerce(link_el['href'])
                    
                    for nombre_var, datos_var in id_variaciones.items():
                        nombre_combinado = f"{nombre_original} ({nombre_var})"
                        clave_combinada = nombre_combinado.lower()
                        productos[clave_combinada] = {
                            "nombre_real": nombre_combinado,
                            "nombre_base_proveedor": nombre_clave,
                            "precio": datos_var["precio"],
                            "precio_anterior": 0,
                            "en_oferta": False,
                            "stock": datos_var["stock"]
                        }
                    time.sleep(1.0)
                else:
                    if price_el:
                        precio, precio_anterior, en_oferta = procesar_html_precio(price_el)
                        if precio > 0:
                            productos[nombre_clave] = {
                                "nombre_real": nombre_original,
                                "nombre_base_proveedor": nombre_clave,
                                "precio": precio,
                                "precio_anterior": precio_anterior,
                                "en_oferta": en_oferta,
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
        html_obtenido = None
        
        # Corrección crítica: Inicializamos la variable siempre para evitar UnboundLocalError
        productos_en_pagina = 0
        
        for intento in range(1, 4):
            try:
                print(f"Scrapeando Tu Tienda (Web B) - Página {pagina_actual} (Intento {intento})...")
                response = requests.get(url, headers=headers, timeout=20)
                if response.status_code == 200:
                    html_obtenido = response.text
                    break
                elif response.status_code == 404:
                    break
            except Exception as e:
                print(f"⚠️ Alerta temporal en Página {pagina_actual}: {e}")
            time.sleep(2)
        
        if not html_obtenido:
            break
            
        soup = BeautifulSoup(html_obtenido, 'lxml')
        items = soup.find_all(['div', 'li', 'article', 'form'])
        
        for item in items:
            title_el = item.find(['h2', 'h3', 'h1', 'a'], class_=lambda x: x and ('title' in x or 'name' in x or 'producto' in x))
            price_el = item.find(class_=lambda x: x and ('price' in x or 'precio' in x or 'money' in x))
            if title_el and price_el and title_el.text.strip():
                nombre_original = title_el.text.strip()
                if len(nombre_original) < 4 or nombre_original.lower() == "productos": continue
                nombre_clave = " ".join(nombre_original.lower().split())
                if nombre_clave not in productos:
                    precio, _, _ = procesar_html_precio(price_el)
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
        time.sleep(0.5)
        
    return productos


def procesar_logica():
    print("\n--- 🔄 Iniciando Nuevo Ciclo de Monitoreo ---")
    estado_anterior = cargar_estado_anterior()
    pedidos_procesados = estado_anterior.get("pedidos_procesados", [])
    historial_a_viejo = estado_anterior.get("productos_a", {})

    # 1. CORREOS GMAIL
    pedidos_nuevos = chequear_nuevos_pedidos_gmail()

    # 2. ESCANEO DE AMBAS TIENDAS
    prod_a = scrapear_web_a()
    prod_b = scrapear_web_b()
    
    print(f"📊 Resumen: Web A (Con Variantes): {len(prod_a)} | Web B (Tu tienda): {len(prod_b)}")
    
    # --- PROTECCIÓN ULTRA CONTRA CAÍDAS DEL PROVEEDOR ---
    if len(prod_a) == 0:
        print("⚠️ Freno preventivo: El proveedor devolvió 0 productos. No pisamos el historial viejo.")
        return
        
    historial_proveedor_consolidado = historial_a_viejo.copy()
    historial_proveedor_consolidado.update(prod_a)

    # 3. VERIFICAR COMPRAS RECIBIDAS
    for ped in pedidos_nuevos:
        if ped["id_mail"] not in pedidos_procesados:
            msg_reporte = verificar_pedido_contra_proveedor(ped, prod_a)
            enviar_telegram(msg_reporte)
            pedidos_procesados.append(ped["id_mail"])

    # BOLSAS CONSOLIDADAS
    bloque_ofertas = ""
    bloque_nuevos = ""
    bloque_recuperados = ""
    bloque_precios_bajos = ""
    bloque_faltantes = ""

    # 4. CRUCE DE ALERTAS DESDE EL PROVEEDOR
    for nombre_clave, datos in prod_a.items():
        interesa_producto = any(p in nombre_clave for p in PALABRAS_INTERES)
        if not interesa_producto:
            continue

        estaba_antes_en_proveedor = nombre_clave in historial_a_viejo

        # Ofertas Nuevas
        if datos["en_oferta"]:
            era_oferta_antes = historial_a_viejo.get(nombre_clave, {}).get("en_oferta", False) if estaba_antes_en_proveedor else False
            if not era_oferta_antes:
                bloque_ofertas += f"• *{datos['nombre_real']}*\n  💰 Reg: ${datos['precio_anterior']:,} ➔ *🔥 REBAJADO: ${datos['precio']:,}*\n\n"

        # Variaciones u Oportunidades contra tu Inventario
        base_prov = datos.get("nombre_base_proveedor", nombre_clave)
        lo_tengo_en_web = any(
            son_coincidentes_inteligentes(nombre_clave, cb) or son_coincidentes_inteligentes(base_prov, cb) 
            for cb in prod_b.keys()
        )
        
        if not lo_tengo_en_web:
            precio_anterior_prov = historial_a_viejo.get(nombre_clave, {}).get("precio", 0) if estaba_antes_en_proveedor else 0
            if not estaba_antes_en_proveedor or precio_anterior_prov == 0:
                bloque_nuevos += f"• *{datos['nombre_real']}*\n  📦 Costo proveedor: ${datos['precio']:,}\n  ⚠️ _Nota: No tenés esta propiedad cargada_\n\n"
        else:
            era_invalido_o_sin_stock = historial_a_viejo.get(nombre_clave, {}).get("stock", False) == False if estaba_antes_en_proveedor else False
            if estaba_antes_en_proveedor and era_invalido_o_sin_stock:
                bloque_recuperados += f"• *{datos['nombre_real']}*\n  📦 Vuelve a tener stock a: ${datos['precio']:,}\n\n"

    # 5. CONTROL DE PRECIOS Y FALTANTES EN TU TIENDA
    for clave_b, datos_b in prod_b.items():
        encontrado_en_proveedor = False
        datos_a_coincidente = None
        
        for clave_a, datos_a in prod_a.items():
            if son_coincidentes_inteligentes(clave_b, clave_a):
                encontrado_en_proveedor = True
                datos_a_coincidente = datos_a
                break
        
        if not encontrado_en_proveedor:
            variantes_proveedor = []
            for clave_a, datos_a in prod_a.items():
                base_prov = datos_a.get("nombre_base_proveedor", "")
                if son_coincidentes_inteligentes(clave_b, base_prov):
                    variantes_proveedor.append(datos_a)
            
            if variantes_proveedor:
                encontrado_en_proveedor = True
                datos_a_coincidente = min(variantes_proveedor, key=lambda x: x["precio"])

        if datos_b["stock"]:
            if not encontrado_en_proveedor:
                bloque_faltantes += f"• *{datos_b['nombre_real']}*\n  ❌ _Proveedor se quedó SIN STOCK_\n\n"
            elif datos_a_coincidente and datos_b["precio"] < datos_a_coincidente["precio"]:
                bloque_precios_bajos += f"• *{datos_b['nombre_real']}*\n  📱 Tu web: ${datos_b['precio']:,} ➔ 📦 Proveedor (Min): ${datos_a_coincidente['precio']:,}\n\n"

    # 6. ENVIAR REPORTES AGRUPADOS
    if bloque_ofertas:
        enviar_telegram(f"🏷️ *¡Nuevos Descuentos en el Proveedor!*\n\n{bloque_ofertas}")
    if bloque_nuevos:
        enviar_telegram(f"🔥 *¡Oportunidades de Stock / Nuevos Productos!*\n_Propiedades o productos del proveedor que NO tenés cargados en tu web:_\n\n{bloque_nuevos}")
    if bloque_recuperados:
        enviar_telegram(f"🔄 *¡Stock Recuperado en Proveedor!*\n\n{bloque_recuperados}")
    if bloque_precios_bajos:
        enviar_telegram(f"📉 *¡Alerta de precios desactualizados en tu Web!*\n_Estás vendiendo por debajo del costo:_\n\n{bloque_precios_bajos}")
    if bloque_faltantes:
        enviar_telegram(f"⚠️ *¡Alerta de Stock Crítica!*\n_Tenés disponible algo que el proveedor ya agotó:_\n\n{bloque_faltantes}")

    # Guardado seguro consolidado
    guardar_estado_actual({
        "productos_a": historial_proveedor_consolidado, 
        "productos_b": prod_b, 
        "pedidos_procesados": pedidos_procesados
    })
    print("--- ✅ Ciclo completado sin bucles de falsos positivos ---")


if __name__ == "__main__":
    print("🚀 Bot de Control Iniciado Espectacularmente...")
    while True:
        procesar_logica()
        print("💤 Esperando 15 minutos hasta el próximo control...")
        time.sleep(900)

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
                
        if encontrado and

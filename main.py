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
import threading
 
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
 
# ─── URLs ────────────────────────────────────────────────────────────────────
URL_A          = "https://rxzweb.com/tienda/?et_per_page=-1"
URL_B          = "https://leandroid.tiendanegocio.com/productos"
DB_FILE        = "estado_productos.json"
USER_AGENT_API = "dropshipping (lean.6roid@gmail.com)"
 
# Estos se completan automáticamente cuando se obtiene el token
URL_API_PRODUCTS = None   # se arma con el user_id: https://api.tiendanube.com/2025-03/{user_id}/products
 
# ─── Variables de entorno (Railway) ──────────────────────────────────────────
TELEGRAM_TOKEN = None
CHAT_ID        = None
SCRAPERAPI_KEY = None
GMAIL_USER     = None
GMAIL_PASS     = None
CLIENT_ID      = None
CLIENT_SECRET  = None
 
for k, v in os.environ.items():
    val = v.strip()
    if "TELEGRAM_TOKEN" in k: TELEGRAM_TOKEN = val
    if "CHAT_ID"        in k: CHAT_ID        = val
    if "SCRAPERAPI_KEY" in k: SCRAPERAPI_KEY = val
    if "GMAIL_USER"     in k: GMAIL_USER     = val
    if "GMAIL_PASS"     in k: GMAIL_PASS     = val
    if "CLIENT_ID"      in k: CLIENT_ID      = val
    if "CLIENT_SECRET"  in k: CLIENT_SECRET  = val
 
# ─── Estado global del token API ─────────────────────────────────────────────
_api_token   = None   # access_token activo
_api_user_id = None   # store ID (viene junto con el token)
 
def _cargar_token_desde_db():
    """Al arrancar, intenta recuperar el token guardado en el JSON."""
    global _api_token, _api_user_id, URL_API_PRODUCTS
    estado = cargar_estado_anterior()
    t = estado.get("api_token")
    u = estado.get("api_user_id")
    if t and u:
        _api_token   = t
        _api_user_id = u
        URL_API_PRODUCTS = f"https://api.tiendanube.com/2025-03/{u}/products"
        print(f"✅ Token API cargado desde DB (user_id={u})")
 
def _guardar_token_en_db(token, user_id):
    """Persiste el token para sobrevivir reinicios de Railway."""
    global _api_token, _api_user_id, URL_API_PRODUCTS
    _api_token   = token
    _api_user_id = user_id
    URL_API_PRODUCTS = f"https://api.tiendanube.com/2025-03/{user_id}/products"
    estado = cargar_estado_anterior()
    estado["api_token"]   = token
    estado["api_user_id"] = user_id
    guardar_estado_actual(estado)
    print(f"💾 Token guardado en DB (user_id={user_id})")
 
# ─── Palabras de interés ─────────────────────────────────────────────────────
PALABRAS_INTERES = [
    'ma ant', 'amaoe', '2uul', 'goot wick', 'mijing', 'louwei',
    'rf4', 'jakemy', 'kailiwei', 'kslid', 'aifen', 'sugon', 'jcid', 'jc',
    'v1', 'v1s', 'v1se', 'v1 pro', 'programadora',
    'organizador', 'cinta', 'silla', 'mesa', 'puas', 'hilo', 'cepillo'
]
 
# ═══════════════════════════════════════════════════════════════════════════════
# TELEGRAM
# ═══════════════════════════════════════════════════════════════════════════════
 
def enviar_telegram(mensaje):
    if not mensaje or not TELEGRAM_TOKEN or not CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": mensaje, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=15)
        print("🚀 Mensaje enviado a Telegram.")
    except Exception as e:
        print(f"❌ Error Telegram: {e}")
 
# ═══════════════════════════════════════════════════════════════════════════════
# OAUTH — CANJE DE TOKEN
# ═══════════════════════════════════════════════════════════════════════════════
 
# Endpoints a probar en orden (se prueba cada uno hasta que alguno responda bien)
OAUTH_ENDPOINTS = [
    "https://www.tiendanube.com/apps/authorize/token",
    "https://www.tiendanegocio.com/apps/authorize/token",
]
 
def intercambiar_codigo_por_token(auth_code):
    """
    Intenta canjear el code por el access_token.
    Prueba todos los endpoints conocidos con form-data y JSON.
    Guarda el token automáticamente si tiene éxito.
    """
    payload = {
        "client_id":     CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "code":          auth_code,
        "grant_type":    "authorization_code"
    }
 
    for endpoint in OAUTH_ENDPOINTS:
        # Intento A: form-data (estándar OAuth2)
        for content_type, use_json in [("application/x-www-form-urlencoded", False),
                                        ("application/json; charset=utf-8",    True)]:
            try:
                print(f"🔄 Probando {endpoint} ({'JSON' if use_json else 'Form'})...")
                headers = {"Content-Type": content_type, "User-Agent": USER_AGENT_API}
                if use_json:
                    resp = requests.post(endpoint, json=payload, headers=headers, timeout=15)
                else:
                    resp = requests.post(endpoint, data=payload, headers=headers, timeout=15)
 
                print(f"   → HTTP {resp.status_code}: {resp.text[:120]}")
 
                if resp.status_code == 200:
                    data = resp.json()
                    token   = data.get("access_token")
                    user_id = str(data.get("user_id", ""))
                    if token and user_id:
                        _guardar_token_en_db(token, user_id)
                        return token
            except Exception as e:
                print(f"   ⚠️ Excepción: {e}")
 
    return None
 
# ═══════════════════════════════════════════════════════════════════════════════
# API — TIENDANUBE / TIENDANEGOCIO
# ═══════════════════════════════════════════════════════════════════════════════
 
def _api_headers():
    return {
        "Authentication": f"bearer {_api_token}",
        "User-Agent":     USER_AGENT_API,
        "Content-Type":   "application/json"
    }
 
def obtener_todos_los_productos_api():
    """Trae TODOS los productos de la tienda paginando de a 200."""
    if not _api_token or not URL_API_PRODUCTS:
        return []
    todos = []
    pagina = 1
    while True:
        try:
            url  = f"{URL_API_PRODUCTS}?per_page=200&page={pagina}"
            resp = requests.get(url, headers=_api_headers(), timeout=20)
            if resp.status_code != 200:
                print(f"❌ API productos HTTP {resp.status_code}: {resp.text[:100]}")
                break
            data = resp.json()
            # La API de Tiendanube devuelve una lista directa o un dict con "results"
            lote = data if isinstance(data, list) else data.get("results", [])
            if not lote:
                break
            todos.extend(lote)
            if len(lote) < 200:
                break
            pagina += 1
        except Exception as e:
            print(f"❌ Error paginando productos: {e}")
            break
    print(f"   📦 Total productos traídos de la API: {len(todos)}")
    return todos
 
def buscar_producto_api(nombre_buscado):
    """Devuelve (product_id, variant_id) o (None, None) si no encuentra."""
    productos = obtener_todos_los_productos_api()
    for p in productos:
        nombre_api = p.get("name", {})
        # El nombre puede ser string o dict {"es": "..."}
        if isinstance(nombre_api, dict):
            nombre_api = next(iter(nombre_api.values()), "")
        if son_coincidentes_inteligentes(str(nombre_api), nombre_buscado):
            product_id = p.get("id")
            # Primera variante disponible
            variantes  = p.get("variants", [])
            variant_id = variantes[0].get("id") if variantes else None
            return product_id, variant_id
    return None, None
 
def modificar_stock_api(product_id, variant_id, nuevo_stock):
    """PUT al variant para actualizar stock."""
    if not _api_token or not URL_API_PRODUCTS:
        return False
    url = f"{URL_API_PRODUCTS}/{product_id}/variants/{variant_id}"
    try:
        resp = requests.put(url, json={"stock": int(nuevo_stock)}, headers=_api_headers(), timeout=15)
        ok = resp.status_code in (200, 201)
        print(f"{'✅' if ok else '❌'} Stock → {nuevo_stock} (HTTP {resp.status_code})")
        return ok
    except Exception as e:
        print(f"❌ Error modificando stock: {e}")
        return False
 
def modificar_precio_api(product_id, variant_id, nuevo_precio):
    """PUT al variant para actualizar precio."""
    if not _api_token or not URL_API_PRODUCTS:
        return False
    url = f"{URL_API_PRODUCTS}/{product_id}/variants/{variant_id}"
    try:
        resp = requests.put(url, json={"price": str(nuevo_precio)}, headers=_api_headers(), timeout=15)
        ok = resp.status_code in (200, 201)
        print(f"{'✅' if ok else '❌'} Precio → ${nuevo_precio} (HTTP {resp.status_code})")
        return ok
    except Exception as e:
        print(f"❌ Error modificando precio: {e}")
        return False
 
def ocultar_producto_api(product_id):
    """Pone published=false para ocultar un producto sin borrarlo."""
    if not _api_token or not URL_API_PRODUCTS:
        return False
    url = f"{URL_API_PRODUCTS}/{product_id}"
    try:
        resp = requests.put(url, json={"published": False}, headers=_api_headers(), timeout=15)
        ok = resp.status_code in (200, 201)
        print(f"{'✅' if ok else '❌'} Producto {product_id} ocultado (HTTP {resp.status_code})")
        return ok
    except Exception as e:
        print(f"❌ Error ocultando producto: {e}")
        return False
 
def publicar_producto_api(product_id):
    """Reactiva un producto que estaba oculto."""
    if not _api_token or not URL_API_PRODUCTS:
        return False
    url = f"{URL_API_PRODUCTS}/{product_id}"
    try:
        resp = requests.put(url, json={"published": True}, headers=_api_headers(), timeout=15)
        ok = resp.status_code in (200, 201)
        print(f"{'✅' if ok else '❌'} Producto {product_id} publicado (HTTP {resp.status_code})")
        return ok
    except Exception as e:
        print(f"❌ Error publicando producto: {e}")
        return False
 
# ═══════════════════════════════════════════════════════════════════════════════
# SINCRONIZACIÓN AUTOMÁTICA (usa la API si hay token, si no solo notifica)
# ═══════════════════════════════════════════════════════════════════════════════
 
def sincronizar_producto(nombre_web, datos_proveedor, accion, valor=None):
    """
    Intenta ejecutar una acción via API.
    Si no hay token, solo avisa por Telegram.
    accion: 'ocultar' | 'precio' | 'stock_cero' | 'publicar'
    """
    if not _api_token:
        return  # Solo notifica desde la lógica principal, no hace nada más
 
    product_id, variant_id = buscar_producto_api(nombre_web)
    if not product_id:
        print(f"⚠️ No encontré '{nombre_web}' en la API para sincronizar.")
        return
 
    if accion == "ocultar":
        if ocultar_producto_api(product_id):
            enviar_telegram(f"🤖 *Auto-sync:* Oculté *{nombre_web}* (sin stock en proveedor)")
 
    elif accion == "stock_cero":
        if variant_id and modificar_stock_api(product_id, variant_id, 0):
            enviar_telegram(f"🤖 *Auto-sync:* Stock → 0 en *{nombre_web}*")
 
    elif accion == "precio" and valor is not None:
        if variant_id and modificar_precio_api(product_id, variant_id, valor):
            enviar_telegram(f"🤖 *Auto-sync:* Precio actualizado a *${valor:,}* en *{nombre_web}*")
 
    elif accion == "publicar":
        if publicar_producto_api(product_id):
            enviar_telegram(f"🤖 *Auto-sync:* Publiqué *{nombre_web}* (stock recuperado)")
 
# ═══════════════════════════════════════════════════════════════════════════════
# COMANDOS TELEGRAM — escucha en hilo separado
# ═══════════════════════════════════════════════════════════════════════════════
 
AYUDA_MSG = """
🤖 *Comandos disponibles:*
 
🔑 *Token / API*
`?code=XXXX` — Canjear código OAuth por token
`/estado_api` — Ver si el token está activo
`/borrar_token` — Eliminar token guardado
 
📦 *Productos (requiere token)*
`/listar` — Ver todos los productos de tu tienda
`/ocultar NOMBRE` — Ocultar un producto
`/publicar NOMBRE` — Publicar un producto oculto
`/stock NOMBRE CANTIDAD` — Cambiar stock de un producto
`/precio NOMBRE VALOR` — Cambiar precio de un producto
 
🔄 *Control del bot*
`/ciclo` — Forzar un ciclo de monitoreo ahora
`/ayuda` — Ver este mensaje
""".strip()
 
def procesar_comando(texto):
    """Ejecuta el comando recibido por Telegram y devuelve respuesta."""
    global _api_token, _api_user_id
 
    texto = texto.strip()
 
    # ── Canje de código OAuth ──────────────────────────────────────────────
    if "?code=" in texto:
        auth_code = texto.split("?code=")[1].split("&")[0].strip()
        enviar_telegram("🔄 Canjeando código OAuth...")
        token = intercambiar_codigo_por_token(auth_code)
        if token:
            enviar_telegram(f"✅ *¡Token obtenido!* Ya puedo modificar tu tienda automáticamente.\nUser ID: `{_api_user_id}`")
        else:
            enviar_telegram("❌ No pude obtener el token. Revisá que el código no haya vencido (dura 5 min).")
        return
 
    cmd = texto.lower().split()
    if not cmd:
        return
 
    # ── /ayuda ─────────────────────────────────────────────────────────────
    if cmd[0] == "/ayuda":
        enviar_telegram(AYUDA_MSG)
 
    # ── /estado_api ────────────────────────────────────────────────────────
    elif cmd[0] == "/estado_api":
        if _api_token:
            enviar_telegram(f"✅ *Token activo*\nUser ID: `{_api_user_id}`\nToken: `{_api_token[:12]}...`")
        else:
            enviar_telegram("❌ No hay token guardado. Instalá la app en tu tienda y mandá el `?code=` aquí.")
 
    # ── /borrar_token ──────────────────────────────────────────────────────
    elif cmd[0] == "/borrar_token":
        _api_token = _api_user_id = None
        estado = cargar_estado_anterior()
        estado.pop("api_token", None)
        estado.pop("api_user_id", None)
        guardar_estado_actual(estado)
        enviar_telegram("🗑️ Token eliminado.")
 
    # ── /listar ────────────────────────────────────────────────────────────
    elif cmd[0] == "/listar":
        if not _api_token:
            enviar_telegram("❌ Necesito el token primero. Usá `/estado_api` para verificar.")
            return
        enviar_telegram("⏳ Buscando productos...")
        productos = obtener_todos_los_productos_api()
        if not productos:
            enviar_telegram("No encontré productos o hubo un error.")
            return
        lineas = []
        for p in productos[:50]:  # Máximo 50 para no spam
            nombre = p.get("name", {})
            if isinstance(nombre, dict): nombre = next(iter(nombre.values()), "")
            variantes  = p.get("variants", [])
            stock_total = sum(v.get("stock", 0) or 0 for v in variantes)
            precio      = variantes[0].get("price", "?") if variantes else "?"
            published   = "✅" if p.get("published") else "🚫"
            lineas.append(f"{published} *{nombre}* — ${precio} — Stock: {stock_total}")
        msg = f"📦 *Tus productos ({len(productos)} total):*\n\n" + "\n".join(lineas)
        if len(productos) > 50:
            msg += f"\n\n_...y {len(productos)-50} más_"
        enviar_telegram(msg)
 
    # ── /ocultar NOMBRE ────────────────────────────────────────────────────
    elif cmd[0] == "/ocultar":
        if not _api_token:
            enviar_telegram("❌ Necesito el token primero.")
            return
        nombre = " ".join(texto.split()[1:])
        if not nombre:
            enviar_telegram("Uso: `/ocultar Nombre del producto`")
            return
        enviar_telegram(f"🔍 Buscando *{nombre}*...")
        product_id, _ = buscar_producto_api(nombre)
        if product_id:
            if ocultar_producto_api(product_id):
                enviar_telegram(f"🚫 *{nombre}* ocultado correctamente.")
            else:
                enviar_telegram(f"❌ No pude ocultar *{nombre}*. Revisá los permisos de la app.")
        else:
            enviar_telegram(f"❌ No encontré *{nombre}* en tu tienda.")
 
    # ── /publicar NOMBRE ───────────────────────────────────────────────────
    elif cmd[0] == "/publicar":
        if not _api_token:
            enviar_telegram("❌ Necesito el token primero.")
            return
        nombre = " ".join(texto.split()[1:])
        if not nombre:
            enviar_telegram("Uso: `/publicar Nombre del producto`")
            return
        enviar_telegram(f"🔍 Buscando *{nombre}*...")
        product_id, _ = buscar_producto_api(nombre)
        if product_id:
            if publicar_producto_api(product_id):
                enviar_telegram(f"✅ *{nombre}* publicado correctamente.")
            else:
                enviar_telegram(f"❌ No pude publicar *{nombre}*.")
        else:
            enviar_telegram(f"❌ No encontré *{nombre}* en tu tienda.")
 
    # ── /stock NOMBRE CANTIDAD ─────────────────────────────────────────────
    elif cmd[0] == "/stock":
        if not _api_token:
            enviar_telegram("❌ Necesito el token primero.")
            return
        partes = texto.split()
        if len(partes) < 3:
            enviar_telegram("Uso: `/stock Nombre del producto 10`")
            return
        try:
            nuevo_stock = int(partes[-1])
            nombre      = " ".join(partes[1:-1])
        except ValueError:
            enviar_telegram("El último parámetro tiene que ser un número. Ej: `/stock Cepillo 5`")
            return
        enviar_telegram(f"🔍 Buscando *{nombre}*...")
        product_id, variant_id = buscar_producto_api(nombre)
        if product_id and variant_id:
            if modificar_stock_api(product_id, variant_id, nuevo_stock):
                enviar_telegram(f"📦 Stock de *{nombre}* actualizado a *{nuevo_stock}*.")
            else:
                enviar_telegram(f"❌ No pude actualizar el stock de *{nombre}*.")
        else:
            enviar_telegram(f"❌ No encontré *{nombre}* en tu tienda.")
 
    # ── /precio NOMBRE VALOR ───────────────────────────────────────────────
    elif cmd[0] == "/precio":
        if not _api_token:
            enviar_telegram("❌ Necesito el token primero.")
            return
        partes = texto.split()
        if len(partes) < 3:
            enviar_telegram("Uso: `/precio Nombre del producto 9999`")
            return
        try:
            nuevo_precio = int(partes[-1])
            nombre       = " ".join(partes[1:-1])
        except ValueError:
            enviar_telegram("El último parámetro tiene que ser un número. Ej: `/precio Cepillo 1500`")
            return
        enviar_telegram(f"🔍 Buscando *{nombre}*...")
        product_id, variant_id = buscar_producto_api(nombre)
        if product_id and variant_id:
            if modificar_precio_api(product_id, variant_id, nuevo_precio):
                enviar_telegram(f"💲 Precio de *{nombre}* actualizado a *${nuevo_precio:,}*.")
            else:
                enviar_telegram(f"❌ No pude actualizar el precio de *{nombre}*.")
        else:
            enviar_telegram(f"❌ No encontré *{nombre}* en tu tienda.")
 
    # ── /ciclo ─────────────────────────────────────────────────────────────
    elif cmd[0] == "/ciclo":
        enviar_telegram("🔄 Iniciando ciclo de monitoreo manual...")
        threading.Thread(target=procesar_logica, daemon=True).start()
 
    else:
        enviar_telegram(f"❓ No reconozco ese comando. Mandá `/ayuda` para ver los disponibles.")
 
 
def bucle_escucha_telegram():
    if not TELEGRAM_TOKEN:
        return
    offset = 0
    url_updates = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
    print("📡 Hilo Telegram activo...")
    while True:
        try:
            resp = requests.get(f"{url_updates}?offset={offset}&timeout=10", timeout=15)
            if resp.status_code == 200:
                for update in resp.json().get("result", []):
                    offset = update.get("update_id") + 1
                    message = update.get("message", {})
                    texto   = message.get("text", "")
                    chat_remitente = str(message.get("chat", {}).get("id", ""))
                    if chat_remitente != CHAT_ID or not texto:
                        continue
                    print(f"📨 Comando recibido: {texto[:60]}")
                    try:
                        procesar_comando(texto)
                    except Exception as e:
                        print(f"❌ Error procesando comando: {e}")
        except Exception as e:
            print(f"⚠️ Hilo Telegram: {e}")
        time.sleep(1)
 
# ═══════════════════════════════════════════════════════════════════════════════
# BASE DE DATOS LOCAL
# ═══════════════════════════════════════════════════════════════════════════════
 
def cargar_estado_anterior():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                for k in ["pedidos_procesados", "productos_a", "productos_b"]:
                    if k not in data: data[k] = [] if k == "pedidos_procesados" else {}
                return data
        except Exception:
            pass
    return {"productos_a": {}, "productos_b": {}, "pedidos_procesados": []}
 
def guardar_estado_actual(estado):
    try:
        with open(DB_FILE, "w", encoding="utf-8") as f:
            json.dump(estado, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"❌ Error guardando DB: {e}")
 
# ═══════════════════════════════════════════════════════════════════════════════
# UTILIDADES DE PRECIOS Y NOMBRES
# ═══════════════════════════════════════════════════════════════════════════════
 
def limpiar_precio_simple(texto):
    if not texto: return 0
    if "," in texto: texto = texto.split(",")[0]
    numeros = ''.join(filter(str.isdigit, texto))
    return int(numeros) if numeros else 0
 
def procesar_html_precio(html_precio):
    if not html_precio: return 0, 0, False
    try:
        del_tag = html_precio.find('del')
        ins_tag = html_precio.find('ins')
        if del_tag and ins_tag:
            precio_viejo = limpiar_precio_simple(del_tag.text)
            precio_nuevo = limpiar_precio_simple(ins_tag.text)
            if precio_nuevo > 0: return precio_nuevo, precio_viejo, True
        return limpiar_precio_simple(html_precio.text), 0, False
    except Exception:
        return 0, 0, False
 
def son_coincidentes_inteligentes(nombre1, nombre2):
    n1 = nombre1.lower()
    n2 = nombre2.lower()
    if " ".join(n1.split()) == " ".join(n2.split()): return True
    palabras_raiz = ['bateria', 'battery', 'bat', 'face', 'id', 'maneral', 'mango', 'zocalo', 'board']
    for pr in palabras_raiz:
        if (pr in n1 and pr not in n2) or (pr in n2 and pr not in n1): return False
    palabras_criticas = ['mini', 'pro', 'plus', 'max', 'kit', 'ultra', 'xl', 'lw-a1']
    for pc in palabras_criticas:
        if (pc in n1 and pc not in n2) or (pc in n2 and pc not in n1): return False
    n1c = set(re.sub(r'[^a-z0-9 ]', ' ', n1).split()) - {'de','para','con','el','la','los','las','un','una','y','en','del','al'}
    n2c = set(re.sub(r'[^a-z0-9 ]', ' ', n2).split()) - {'de','para','con','el','la','los','las','un','una','y','en','del','al'}
    if not n1c or not n2c: return False
    nums1 = {w for w in n1c if any(c.isdigit() for c in w)}
    nums2 = {w for w in n2c if any(c.isdigit() for c in w)}
    if (nums1 or nums2) and nums1 != nums2: return False
    comunes = n1c.intersection(n2c)
    return (len(comunes) / min(len(n1c), len(n2c))) >= 0.85
 
# ═══════════════════════════════════════════════════════════════════════════════
# GMAIL — CHEQUEO DE PEDIDOS
# ═══════════════════════════════════════════════════════════════════════════════
 
def extraer_productos_del_mail(cuerpo):
    productos = []
    en_bloque = False
    for linea in cuerpo.split('\n'):
        l = linea.strip()
        if "productos:" in l.lower(): en_bloque = True; continue
        if en_bloque:
            if not l or "subtotal:" in l.lower() or "descuento" in l.lower():
                en_bloque = False; continue
            if l.startswith('-'):
                m = re.match(r'-\s*(.+?)\s+x(\d+)\s*-', l)
                if m: productos.append({"nombre": m.group(1).strip(), "cantidad": int(m.group(2))})
    return productos
 
def chequear_nuevos_pedidos_gmail():
    pedidos = []
    if not GMAIL_USER or not GMAIL_PASS: return pedidos
    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(GMAIL_USER, GMAIL_PASS)
        mail.select("inbox")
        hace_24h = (datetime.now() - timedelta(days=1)).strftime("%d-%b-%Y")
        status, msgs = mail.search(None, f'(FROM "tiendanegocio.com" SINCE {hace_24h})')
        if status != "OK" or not msgs[0]:
            mail.close(); mail.logout(); return pedidos
        for msg_id in msgs[0].split():
            str_id = msg_id.decode()
            res, data = mail.fetch(msg_id, "(RFC822)")
            if res != "OK": continue
            msg = email.message_from_bytes(data[0][1])
            subject, enc = decode_header(msg["Subject"])[0]
            if isinstance(subject, bytes): subject = subject.decode(enc or "utf-8")
            if not any(p in subject.lower() for p in ["compra", "realizó", "pedido", "venta"]): continue
            match = re.search(r'#(\d+)', subject)
            num_orden = match.group(1) if match else str_id
            cuerpo = ""
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/plain":
                        cuerpo = part.get_payload(decode=True).decode("utf-8", errors="ignore"); break
            else:
                cuerpo = msg.get_payload(decode=True).decode("utf-8", errors="ignore")
            items = extraer_productos_del_mail(cuerpo)
            if items: pedidos.append({"id_mail": str_id, "num_orden": num_orden, "productos": items})
        mail.close(); mail.logout()
    except Exception as e:
        print(f"❌ Gmail: {e}")
    return pedidos
 
def verificar_pedido_contra_proveedor(pedido, prod_proveedor):
    reporte = f"🛒 *¡Nuevo Pedido! (Orden #{pedido['num_orden']})*\n\n"
    todo_ok = True
    for item in pedido["productos"]:
        nombre = item["nombre"].lower()
        datos_prov = next((d for c, d in prod_proveedor.items() if son_coincidentes_inteligentes(nombre, c)), None)
        if datos_prov:
            reporte += f"✅ *{item['nombre']}* (x{item['cantidad']})\n  Proveedor: CON STOCK a ${datos_prov['precio']:,}\n\n"
        else:
            todo_ok = False
            reporte += f"❌ *{item['nombre']}* (x{item['cantidad']})\n  Proveedor: 🔥 SIN STOCK / NO DETECTADO\n\n"
    reporte += "🚀 ¡Podés armar el pedido!" if todo_ok else "⚠️ Hay faltantes en el proveedor."
    return reporte
 
# ═══════════════════════════════════════════════════════════════════════════════
# SCRAPING — PROVEEDOR (Web A) y TIENDA PROPIA (Web B)
# ═══════════════════════════════════════════════════════════════════════════════
 
def extraer_variaciones_woocommerce(url_producto):
    variaciones = {}
    target = f"http://api.scraperapi.com?api_key={SCRAPERAPI_KEY}&url={url_producto}"
    try:
        resp = requests.get(target, timeout=30, verify=False)
        if resp.status_code != 200: return variaciones
        soup = BeautifulSoup(resp.text, 'lxml')
        form = soup.find('form', class_='variations_form')
        if form and form.get('data-product_variations'):
            for var in json.loads(form['data-product_variations']):
                atrs = [str(v).replace('-', ' ').replace('_', ' ').strip()
                        for k, v in var.get('attributes', {}).items() if v]
                if not atrs: continue
                texto = " - ".join(atrs).title()
                precio = var.get('display_price', 0)
                in_stock = var.get('is_in_stock', True)
                if "agotado" in var.get('variation_html', '').lower(): in_stock = False
                if precio > 0 and in_stock:
                    variaciones[texto] = {"precio": int(precio), "stock": True}
    except Exception as e:
        print(f"⚠️ Variantes {url_producto}: {e}")
    return variaciones
 
def scrapear_web_a():
    productos = {}
    if not SCRAPERAPI_KEY: return productos
    target = f"http://api.scraperapi.com?api_key={SCRAPERAPI_KEY}&url={URL_A}"
    try:
        print("📥 Scrapeando proveedor...")
        resp = requests.get(target, timeout=60, verify=False)
        if resp.status_code != 200: return productos
        soup = BeautifulSoup(resp.text, 'lxml')
        items = soup.select('.product') or soup.find_all(['li', 'div'],
                    class_=lambda x: x and 'product' in x)
        for item in items:
            title_el = (item.find(['h2','h3','h4','a','p'],
                         class_=lambda x: x and ('title' in x or 'woocommerce-loop' in x or 'name' in x))
                        or item.find(['h2','h3','h4']))
            price_el = item.find(class_=lambda x: x and ('price' in x or 'precio' in x))
            link_el  = item.find('a', href=True)
            if not title_el or not title_el.text.strip(): continue
            nombre_original = title_el.text.strip()
            if len(nombre_original) < 4 or nombre_original.lower() == "productos": continue
            nombre_clave = " ".join(nombre_original.lower().split())
            interesa   = any(p in nombre_clave for p in PALABRAS_INTERES)
            es_variable = (item.find('a', class_='product_type_variable')
                           or (price_el and "–" in price_el.text))
            if interesa and es_variable and link_el:
                for nombre_var, datos_var in extraer_variaciones_woocommerce(link_el['href']).items():
                    nc = f"{nombre_original} ({nombre_var})"
                    productos[nc.lower()] = {
                        "nombre_real": nc, "nombre_base_proveedor": nombre_clave,
                        "precio": datos_var["precio"], "precio_anterior": 0,
                        "en_oferta": False, "stock": datos_var["stock"]
                    }
                time.sleep(1.0)
            elif price_el:
                precio, precio_anterior, en_oferta = procesar_html_precio(price_el)
                if precio > 0:
                    productos[nombre_clave] = {
                        "nombre_real": nombre_original, "nombre_base_proveedor": nombre_clave,
                        "precio": precio, "precio_anterior": precio_anterior,
                        "en_oferta": en_oferta, "stock": True
                    }
    except Exception as e:
        print(f"❌ Scraping proveedor: {e}")
    return productos
 
def scrapear_web_b():
    productos = {}
    pagina    = 1
    headers   = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    while True:
        url = URL_B if pagina == 1 else f"{URL_B}?page={pagina}"
        html = None
        en_pagina = 0
        for _ in range(3):
            try:
                resp = requests.get(url, headers=headers, timeout=20)
                if resp.status_code == 200: html = resp.text; break
                if resp.status_code == 404: break
            except Exception: pass
            time.sleep(2)
        if not html: break
        soup  = BeautifulSoup(html, 'lxml')
        items = soup.find_all(['div', 'li', 'article', 'form'])
        for item in items:
            title_el = item.find(['h2','h3','h1','a'],
                         class_=lambda x: x and ('title' in x or 'name' in x or 'producto' in x))
            price_el = item.find(class_=lambda x: x and ('price' in x or 'precio' in x or 'money' in x))
            if not title_el or not price_el or not title_el.text.strip(): continue
            nombre_original = title_el.text.strip()
            if len(nombre_original) < 4 or nombre_original.lower() == "productos": continue
            nombre_clave = " ".join(nombre_original.lower().split())
            if nombre_clave not in productos:
                precio, _, _ = procesar_html_precio(price_el)
                texto = item.text.lower()
                tiene_stock = "sin stock" not in texto and "agotado" not in texto
                if precio > 0:
                    productos[nombre_clave] = {"nombre_real": nombre_original,
                                               "precio": precio, "stock": tiene_stock}
                    en_pagina += 1
        if en_pagina == 0: break
        pagina += 1
        time.sleep(0.5)
    return productos
 
# ═══════════════════════════════════════════════════════════════════════════════
# LÓGICA PRINCIPAL DE MONITOREO
# ═══════════════════════════════════════════════════════════════════════════════
 
def procesar_logica():
    print("\n─── 🔄 Nuevo ciclo de monitoreo ───")
    estado_anterior  = cargar_estado_anterior()
    pedidos_proc     = estado_anterior.get("pedidos_procesados", [])
    historial_a_viejo = estado_anterior.get("productos_a", {})
 
    # Chequeo de pedidos por Gmail
    for ped in chequear_nuevos_pedidos_gmail():
        if ped["id_mail"] not in pedidos_proc:
            enviar_telegram(verificar_pedido_contra_proveedor(ped, historial_a_viejo))
            pedidos_proc.append(ped["id_mail"])
 
    prod_a = scrapear_web_a()
    prod_b = scrapear_web_b()
 
    if not prod_a:
        print("⚠️ Proveedor devolvió 0 productos. Ciclo abortado.")
        return
 
    historial_consolidado = {**historial_a_viejo, **prod_a}
 
    bloque_ofertas = bloque_nuevos = bloque_recuperados = ""
    bloque_precios_bajos = bloque_faltantes = ""
 
    # ── Analizar proveedor ──────────────────────────────────────────────────
    for clave, datos in prod_a.items():
        if not any(p in clave for p in PALABRAS_INTERES): continue
        estaba = clave in historial_a_viejo
        viejo  = historial_a_viejo.get(clave, {})
 
        # Oferta nueva
        if datos["en_oferta"] and not viejo.get("en_oferta", False):
            bloque_ofertas += f"• *{datos['nombre_real']}*\n  Reg: ${datos['precio_anterior']:,} → *🔥 ${datos['precio']:,}*\n\n"
 
        base_prov    = datos.get("nombre_base_proveedor", clave)
        lo_tengo_web = any(
            son_coincidentes_inteligentes(clave, cb) or son_coincidentes_inteligentes(base_prov, cb)
            for cb in prod_b
        )
 
        # Producto nuevo en proveedor que no tengo en mi web
        if not lo_tengo_web and (not estaba or viejo.get("precio", 0) == 0):
            bloque_nuevos += f"• *{datos['nombre_real']}*\n  Costo proveedor: ${datos['precio']:,}\n\n"
 
        # Stock recuperado
        elif lo_tengo_web and estaba and not viejo.get("stock", False) and datos["stock"]:
            bloque_recuperados += f"• *{datos['nombre_real']}*\n  Vuelve a tener stock a ${datos['precio']:,}\n\n"
            sincronizar_producto(datos['nombre_real'], datos, "publicar")
 
    # ── Analizar mi tienda contra proveedor ────────────────────────────────
    for clave_b, datos_b in prod_b.items():
        datos_a = None
        for clave_a, da in prod_a.items():
            if son_coincidentes_inteligentes(clave_b, clave_a):
                datos_a = da; break
        if datos_a is None:
            variantes = [d for c, d in prod_a.items()
                         if son_coincidentes_inteligentes(clave_b, d.get("nombre_base_proveedor", ""))]
            if variantes:
                datos_a = min(variantes, key=lambda x: x["precio"])
 
        if not datos_b["stock"]:
            continue  # Ya sin stock en mi web, no reportar
 
        if datos_a is None:
            # Proveedor se quedó sin stock
            bloque_faltantes += f"• *{datos_b['nombre_real']}*\n  ❌ Proveedor SIN STOCK\n\n"
            sincronizar_producto(datos_b['nombre_real'], {}, "ocultar")
 
        elif datos_b["precio"] < datos_a["precio"]:
            # Mi precio está por debajo del costo
            diff = datos_a["precio"] - datos_b["precio"]
            bloque_precios_bajos += (
                f"• *{datos_b['nombre_real']}*\n"
                f"  Tu web: ${datos_b['precio']:,}  →  Proveedor: ${datos_a['precio']:,}  "
                f"_(perdés ${diff:,})_\n\n"
            )
            # Auto-corregir precio: sube un 10% sobre el costo del proveedor
            precio_sugerido = int(datos_a["precio"] * 1.10)
            sincronizar_producto(datos_b['nombre_real'], datos_a, "precio", precio_sugerido)
 
    # ── Enviar bloques de Telegram ─────────────────────────────────────────
    if bloque_ofertas:
        enviar_telegram(f"🏷️ *¡Nuevos Descuentos en el Proveedor!*\n\n{bloque_ofertas}")
    if bloque_nuevos:
        enviar_telegram(f"🔥 *¡Nuevos Productos en el Proveedor!*\n\n{bloque_nuevos}")
    if bloque_recuperados:
        enviar_telegram(f"🔄 *¡Stock Recuperado!*\n\n{bloque_recuperados}")
    if bloque_precios_bajos:
        enviar_telegram(f"📉 *¡Precios Desactualizados en tu Web!*\n\n{bloque_precios_bajos}")
    if bloque_faltantes:
        enviar_telegram(f"⚠️ *¡Alerta de Stock Crítica!*\n\n{bloque_faltantes}")
 
    if not any([bloque_ofertas, bloque_nuevos, bloque_recuperados, bloque_precios_bajos, bloque_faltantes]):
        print("✅ Todo en orden, sin cambios detectados.")
 
    guardar_estado_actual({
        "productos_a":       historial_consolidado,
        "productos_b":       prod_b,
        "pedidos_procesados": pedidos_proc,
        "api_token":         _api_token,
        "api_user_id":       _api_user_id
    })
    print("─── ✅ Ciclo completado ───")
 
# ═══════════════════════════════════════════════════════════════════════════════
# ARRANQUE
# ═══════════════════════════════════════════════════════════════════════════════
 
if __name__ == "__main__":
    print("🚀 Bot de Dropshipping iniciado...")
    _cargar_token_desde_db()  # Recupera token si ya estaba guardado
 
    if _api_token:
        enviar_telegram(f"🟢 *Bot iniciado* — Token API activo (user_id: `{_api_user_id}`)\nMandá `/ayuda` para ver los comandos.")
    else:
        enviar_telegram("🟡 *Bot iniciado* — Sin token API todavía.\nInstalá la app en tu tienda demo y mandá el `?code=` aquí.\nMandá `/ayuda` para ver los comandos.")
 
    hilo = threading.Thread(target=bucle_escucha_telegram, daemon=True)
    hilo.start()
 
    while True:
        procesar_logica()
        print("💤 Esperando 15 minutos...")
        time.sleep(900)

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
import io

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ─── URLs ────────────────────────────────────────────────────────────────────
URL_PROVEEDOR    = "https://rxzweb.com/tienda/?et_per_page=-1"
DB_FILE          = "estado_productos.json"
USER_AGENT_API   = "dropshipping (lean.6roid@gmail.com)"

URL_API_BASE     = "https://developers.tiendanegocio.com/v1"
URL_API_PRODUCTS = f"{URL_API_BASE}/products"
URL_API_VARIANTS = f"{URL_API_BASE}/variants"
URL_API_WEBHOOKS = f"{URL_API_BASE}/webhooks"

# ─── Variables de entorno ─────────────────────────────────────────────────────
TELEGRAM_TOKEN  = None
CHAT_ID         = None
SCRAPERAPI_KEY  = None
GMAIL_USER      = None
GMAIL_PASS      = None
CLIENT_ID       = None
CLIENT_SECRET   = None
WEBHOOK_SECRET  = None   # Para validar webhooks de order/paid

for k, v in os.environ.items():
    val = v.strip()
    if "TELEGRAM_TOKEN" in k:  TELEGRAM_TOKEN = val
    if "CHAT_ID"        in k:  CHAT_ID        = val
    if "SCRAPERAPI_KEY" in k:  SCRAPERAPI_KEY = val
    if "GMAIL_USER"     in k:  GMAIL_USER     = val
    if "GMAIL_PASS"     in k:  GMAIL_PASS     = val
    if "CLIENT_ID"      in k:  CLIENT_ID      = val
    if "CLIENT_SECRET"  in k:  CLIENT_SECRET  = val
    if "WEBHOOK_SECRET" in k:  WEBHOOK_SECRET = val

# ─── Token API ───────────────────────────────────────────────────────────────
_api_token   = None
_api_user_id = None

def _cargar_token():
    global _api_token, _api_user_id
    env_token   = os.environ.get("API_TOKEN")
    env_user_id = os.environ.get("API_USER_ID")
    if env_token and env_user_id:
        _api_token   = env_token.strip()
        _api_user_id = env_user_id.strip()
        print(f"✅ Token cargado desde Railway (store_id={_api_user_id})")
        return
    estado = cargar_estado()
    if estado.get("api_token") and estado.get("api_user_id"):
        _api_token   = estado["api_token"]
        _api_user_id = estado["api_user_id"]
        print(f"✅ Token cargado desde DB (store_id={_api_user_id})")

def _guardar_token(token, user_id):
    global _api_token, _api_user_id
    _api_token   = token
    _api_user_id = user_id
    estado = cargar_estado()
    estado["api_token"]   = token
    estado["api_user_id"] = user_id
    guardar_estado(estado)
    print(f"💾 Token guardado (store_id={user_id})")

# ─── Palabras de interés del proveedor ───────────────────────────────────────
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
    if not mensaje or not TELEGRAM_TOKEN or not CHAT_ID: return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": CHAT_ID, "text": mensaje, "parse_mode": "Markdown"}, timeout=15)
    except Exception as e:
        print(f"❌ Telegram: {e}")

def enviar_archivo_telegram(buffer_bytes, nombre_archivo, caption=""):
    if not TELEGRAM_TOKEN or not CHAT_ID: return False
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendDocument"
    try:
        resp = requests.post(url,
            data={"chat_id": CHAT_ID, "caption": caption},
            files={"document": (nombre_archivo, buffer_bytes,
                   "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            timeout=30)
        return resp.status_code == 200
    except Exception as e:
        print(f"❌ Archivo Telegram: {e}"); return False

# ═══════════════════════════════════════════════════════════════════════════════
# OAUTH
# ═══════════════════════════════════════════════════════════════════════════════

def intercambiar_codigo_por_token(auth_code):
    payload = {"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
               "grant_type": "authorization_code", "code": auth_code}
    try:
        resp = requests.post(f"{URL_API_BASE}/oauth/app/token", json=payload,
                             headers={"Content-Type": "application/json", "User-Agent": USER_AGENT_API},
                             timeout=30)
        print(f"OAuth HTTP {resp.status_code}: {resp.text[:200]}")
        if resp.status_code in (200, 201):
            api_data = resp.json().get("data", resp.json())
            token    = api_data.get("access_token")
            user_id  = str(api_data.get("store_id") or api_data.get("user_id") or "")
            if token and user_id:
                _guardar_token(token, user_id)
                return token
        return None
    except Exception as e:
        print(f"❌ OAuth: {e}"); return None

# ═══════════════════════════════════════════════════════════════════════════════
# API — TIENDA NEGOCIO
# ═══════════════════════════════════════════════════════════════════════════════

_cache_api   = None   # Lista de productos con variantes
_tiempo_cache = 0

def _h():
    """Headers correctos para la API."""
    return {"Authorization": f"Bearer {_api_token}",
            "User-Agent": USER_AGENT_API,
            "Content-Type": "application/json"}

def _api_get(url, params=None):
    """GET con manejo de rate limit."""
    for _ in range(3):
        try:
            resp = requests.get(url, headers=_h(), params=params, timeout=20)
            if resp.status_code == 429:
                print("   ⚠️ Rate limit. Esperando 2s..."); time.sleep(2); continue
            return resp
        except Exception as e:
            print(f"❌ GET {url}: {e}"); time.sleep(1)
    return None

def _api_put(url, data):
    """PUT con manejo de rate limit."""
    for _ in range(3):
        try:
            resp = requests.put(url, headers=_h(), json=data, timeout=15)
            if resp.status_code == 429:
                print("   ⚠️ Rate limit. Esperando 2s..."); time.sleep(2); continue
            return resp
        except Exception as e:
            print(f"❌ PUT {url}: {e}"); time.sleep(1)
    return None

def _paginar(url, params_base):
    """
    Pagina un endpoint hasta que no haya más next_page.
    Respeta el rate limit con pausa entre páginas.
    ✅ Condición de corte: solo cuando next_page es null/vacío.
    """
    todos  = []
    pagina = 1
    while True:
        params = {**params_base, "page": pagina}
        resp   = _api_get(url, params=params)
        if not resp or resp.status_code != 200:
            print(f"❌ Paginación HTTP {resp.status_code if resp else 'None'}"); break
        data = resp.json()
        lote = data.get("results", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
        if not lote: break
        todos.extend(lote)
        print(f"   Página {pagina}: {len(lote)} items (total acumulado: {len(todos)})")
        next_page = data.get("pagination", {}).get("next_page") if isinstance(data, dict) else None
        if not next_page:   # ✅ Solo cortamos cuando no hay más páginas
            break
        pagina += 1
        time.sleep(0.6)     # Respetar rate limit: 2 req/seg
    return todos

def obtener_catalogo_api(forzar=False):
    """
    ✅ Descarga el catálogo completo en dos pasos:
    1. GET /products (sin variantes) → trae los 297 productos paginados
    2. GET /products/{id}/variants → trae variantes de cada producto
    Devuelve dict: {nombre_norm: {product_id, variant_ids, precio_base, published}}
    """
    global _cache_api, _tiempo_cache
    if not forzar and _cache_api and (time.time() - _tiempo_cache) < 300:
        return _cache_api
    if not _api_token:
        return {}

    # ── Paso 1: traer todos los productos (sin variantes para evitar límite de 50) ──
    print("⏳ Paso 1: descargando lista de productos...")
    todos = _paginar(URL_API_PRODUCTS, {"per_page": 200})
    print(f"   📦 {len(todos)} productos encontrados.")

    if not todos:
        return {}

    # ── Paso 2: traer variantes de cada producto ────────────────────────────
    print("⏳ Paso 2: descargando variantes...")
    for i, p in enumerate(todos):
        product_id = p.get("id")
        if not product_id:
            p["variants"] = []; continue
        resp = _api_get(f"{URL_API_PRODUCTS}/{product_id}/variants", params={"per_page": 200})
        if resp and resp.status_code == 200:
            data = resp.json()
            variantes = data.get("results", data) if isinstance(data, dict) else data
            p["variants"] = variantes if isinstance(variantes, list) else []
        else:
            p["variants"] = []
        if (i + 1) % 50 == 0:
            print(f"   Variantes: [{i+1}/{len(todos)}]")
        time.sleep(0.5)   # Respetar rate limit

    # ── Construir índice ────────────────────────────────────────────────────
    catalogo = {}
    for p in todos:
        nombre = p.get("name", {})
        if isinstance(nombre, dict): nombre = next(iter(nombre.values()), "")
        nombre_norm = " ".join(str(nombre).lower().split())
        variantes   = p.get("variants", [])
        catalogo[nombre_norm] = {
            "nombre_real":       str(nombre),
            "product_id":        p.get("id"),
            "variant_ids":       [v.get("id") for v in variantes if v.get("id")],
            "variantes_completas": variantes,   # ← guardamos completas para matching de precios
            "precio_base":       float(variantes[0].get("price", 0) or 0) if variantes else float(p.get("price", 0) or 0),
            "published":         p.get("published", True)
        }

    print(f"   ✅ Catálogo listo: {len(catalogo)} productos.")
    _cache_api    = catalogo
    _tiempo_cache = time.time()
    return catalogo

def actualizar_todas_las_variantes(product_id, variant_ids, nuevo_precio=None,
                                    nuevo_stock=None, precios_por_variante=None):
    """
    Actualiza TODAS las variantes de un producto.
    - nuevo_precio: mismo precio para todas
    - precios_por_variante: dict {variant_id: precio} para precios individuales
    - nuevo_stock: mismo stock para todas
    """
    if not _api_token or not variant_ids: return False
    exitos = 0

    for variant_id in variant_ids:
        payload = {}
        # Precio individual si está disponible, sino precio único
        if precios_por_variante and variant_id in precios_por_variante:
            payload["price"] = str(precios_por_variante[variant_id])
        elif nuevo_precio is not None:
            payload["price"] = str(nuevo_precio)
        if nuevo_stock is not None:
            payload["stock"]            = int(nuevo_stock)
            payload["stock_management"] = True
        if not payload: continue

        resp = _api_put(f"{URL_API_VARIANTS}/{variant_id}", payload)
        if resp and resp.status_code in (200, 201):
            exitos += 1
        else:
            print(f"   ⚠️ Variante {variant_id}: HTTP {resp.status_code if resp else 'None'}")
        time.sleep(0.5)

    print(f"   ✅ {exitos}/{len(variant_ids)} variantes actualizadas.")
    return exitos > 0

def restaurar_stock_variantes(variant_ids):
    """Desactiva stock_management para volver a 'disponible' (sin límite)."""
    if not _api_token or not variant_ids: return False
    exitos = 0
    for variant_id in variant_ids:
        resp = _api_put(f"{URL_API_VARIANTS}/{variant_id}", {"stock_management": False})
        if resp and resp.status_code in (200, 201):
            exitos += 1
        time.sleep(0.5)
    return exitos > 0

def cambiar_visibilidad_producto(product_id, published):
    """PUT /products/{id} para ocultar o publicar."""
    if not _api_token: return False
    resp = _api_put(f"{URL_API_PRODUCTS}/{product_id}", {"published": published})
    return resp and resp.status_code in (200, 201)

# ═══════════════════════════════════════════════════════════════════════════════
# SINCRONIZACIÓN COMPLETA
# ═══════════════════════════════════════════════════════════════════════════════

def redondear_precio(precio):
    if precio >= 100000: return round(precio / 1000) * 1000
    elif precio >= 10000: return round(precio / 500) * 500
    elif precio >= 1000:  return round(precio / 100) * 100
    else:                 return round(precio / 50) * 50

def buscar_precios_por_variante(nombre_norm_api, variantes_api, prod_a):
    """
    Para productos con variantes, intenta asignar el precio correcto a cada variante
    buscando la entrada específica del proveedor que incluye el nombre de la variante.
    
    Ejemplo:
      API producto: "Maneral Mango MiJing C210-C115-C245"
        variante 1: values=[{es:"C245"}] → busca en prod_a la entrada con "(c245)" → precio X
        variante 2: values=[{es:"C210"}] → busca en prod_a la entrada con "(c210)" → precio Y
    
    Devuelve: {variant_id: precio_objetivo} o {} si no se pueden distinguir precios
    """
    precios = {}
    if not variantes_api: return precios

    # Recolectar todas las entradas del proveedor que coinciden con este producto
    entradas_prov = {}  # {valor_variante_norm: precio}
    for clave_a, da in prod_a.items():
        base = da.get("nombre_base_proveedor", clave_a)
        if not (son_coincidentes(nombre_norm_api, base) or son_coincidentes(nombre_norm_api, clave_a)):
            continue
        # Extraer el nombre de la variante del proveedor (entre paréntesis)
        if '(' in clave_a:
            variant_part = clave_a.split('(')[-1].rstrip(')')
            variant_norm = " ".join(variant_part.lower().split())
            entradas_prov[variant_norm] = da["precio"]

    if not entradas_prov: return precios  # Producto simple, sin variantes en proveedor

    # Matchear cada variante de la API con su entrada del proveedor
    for var in variantes_api:
        variant_id = var.get("id")
        if not variant_id: continue
        values = var.get("values", [])
        # Construir el nombre de la variante de la API
        value_name = " ".join(
            str(v.get("es", v.get("en", ""))).lower()
            for v in values
            if v.get("es") or v.get("en")
        ).strip()
        if not value_name: continue

        # Buscar en las entradas del proveedor
        for prov_variant_norm, precio_prov in entradas_prov.items():
            if son_coincidentes(value_name, prov_variant_norm):
                precios[variant_id] = redondear_precio(precio_prov / 0.78)
                break

    return precios


def sincronizacion_completa(prod_a):
    """
    ✅ Usa directamente la API (no scraping) para comparar y actualizar.
    Elimina el problema de matching de nombres.
    """
    catalogo = obtener_catalogo_api(forzar=True)
    if not catalogo:
        enviar_telegram("❌ No pude obtener el catálogo de la API. Verificá el token.")
        return {}

    sincronizados    = {}
    lineas_precios   = []
    lineas_sin_stock = []
    lineas_sin_match = []
    errores          = []
    total            = len(catalogo)
    procesados       = 0

    print(f"🔄 Sync completa: {total} productos del catálogo API...")

    for nombre_norm, datos_web in catalogo.items():
        procesados += 1
        nombre_real = datos_web["nombre_real"]
        variant_ids = datos_web["variant_ids"]
        precio_web  = datos_web["precio_base"]
        product_id  = datos_web["product_id"]

        if procesados % 50 == 0:
            print(f"   [{procesados}/{total}]...")

        # Buscar en el proveedor (scraping)
        datos_prov = None
        for clave_a, da in prod_a.items():
            if son_coincidentes(nombre_norm, clave_a) or son_coincidentes(nombre_norm, da.get("nombre_base_proveedor", "")):
                datos_prov = da; break

        if datos_prov is None:
            # Sin match en proveedor
            if variant_ids:
                if actualizar_todas_las_variantes(product_id, variant_ids, nuevo_stock=0):
                    sincronizados[nombre_real] = {"sin_stock": True}
                    lineas_sin_stock.append(f"• *{nombre_real}*")
                else:
                    errores.append(nombre_real)
            else:
                lineas_sin_match.append(nombre_real)
            continue

        # Tiene match → calcular precios
        if not variant_ids:
            lineas_sin_match.append(f"{nombre_real} (sin variantes)")
            continue

        # Intentar precios individuales por variante (si el proveedor los tiene)
        # Para esto necesitamos las variantes completas del catálogo
        variantes_api  = catalogo.get(nombre_norm, {}).get("variantes_completas", [])
        precios_var    = buscar_precios_por_variante(nombre_norm, variantes_api, prod_a)
        precio_base    = redondear_precio(datos_prov["precio"] / 0.78)

        if precios_var and len(precios_var) == len(variant_ids):
            # Tenemos precio individual para cada variante
            ok = actualizar_todas_las_variantes(product_id, variant_ids,
                                                precios_por_variante=precios_var)
            precio_resumen = f"variado (${min(precios_var.values()):,}–${max(precios_var.values()):,})"
        else:
            # Precio único para todas las variantes
            ok = actualizar_todas_las_variantes(product_id, variant_ids, nuevo_precio=precio_base)
            precio_resumen = f"${precio_base:,}"

        if ok:
            sincronizados[nombre_real] = {"precio": precio_base}
            if int(precio_web) != precio_base:
                lineas_precios.append(
                    f"• *{nombre_real}* ({len(variant_ids)} var.)\n"
                    f"  Proveedor: ${datos_prov['precio']:,} | "
                    f"Anterior: ${int(precio_web):,} | "
                    f"*Nuevo: {precio_resumen}*"
                )
        else:
            errores.append(nombre_real)

    # ── Resumen ──────────────────────────────────────────────────────────────
    resumen = (
        f"✅ *Sincronización completa terminada*\n\n"
        f"📊 Total: *{total}*\n"
        f"💲 Precios actualizados: *{len(lineas_precios)}*\n"
        f"📦 Sin stock (proveedor no tiene): *{len(lineas_sin_stock)}*\n"
    )
    if errores:        resumen += f"⚠️ Errores: *{len(errores)}*\n"
    if lineas_sin_match: resumen += f"❓ Sin match con proveedor: *{len(lineas_sin_match)}*\n"
    enviar_telegram(resumen)

    # Detalles precios (bloques de 20)
    if lineas_precios:
        for i in range(0, len(lineas_precios), 20):
            enviar_telegram("💲 *Precios actualizados:*\n\n" + "\n\n".join(lineas_precios[i:i+20]))

    # Detalles sin stock (bloques de 30)
    if lineas_sin_stock:
        for i in range(0, len(lineas_sin_stock), 30):
            enviar_telegram("📦 *Marcados sin stock:*\n\n" + "\n".join(lineas_sin_stock[i:i+30]))

    return sincronizados

# ═══════════════════════════════════════════════════════════════════════════════
# WEBHOOK ORDER/PAID (preparado para compras automáticas)
# ═══════════════════════════════════════════════════════════════════════════════

def registrar_webhook_order_paid(url_servidor):
    """
    Registra el webhook de order/paid en Tienda Negocio.
    Llamá esto una sola vez desde /registrar_webhook URL_DE_TU_SERVIDOR.
    """
    if not _api_token:
        return False, "❌ Sin token API."
    payload = {
        "event": "order/paid",
        "url":   url_servidor
    }
    try:
        resp = requests.post(URL_API_WEBHOOKS, json=payload, headers=_h(), timeout=15)
        if resp.status_code in (200, 201):
            return True, f"✅ Webhook registrado en {url_servidor}"
        return False, f"❌ HTTP {resp.status_code}: {resp.text[:100]}"
    except Exception as e:
        return False, f"❌ {e}"

def procesar_webhook_orden_pagada(datos_orden):
    """
    Se llama cuando llega un webhook de order/paid.
    Por ahora notifica por Telegram. En el futuro: compra automática al proveedor.
    """
    num_orden = datos_orden.get("id") or datos_orden.get("number", "?")
    productos = []

    # Extraer productos del JSON del webhook
    line_items = datos_orden.get("products", datos_orden.get("line_items", []))
    for item in line_items:
        nombre   = item.get("name", item.get("product_name", "?"))
        cantidad = item.get("quantity", 1)
        precio   = item.get("price", "?")
        productos.append(f"• *{nombre}* x{cantidad} — ${precio}")

    msg = (
        f"💳 *¡Orden PAGADA detectada!* (#{num_orden})\n\n"
        + "\n".join(productos) +
        f"\n\n🤖 _Próximamente: compra automática al proveedor_\n"
        f"Por ahora hacé el pedido manual usando estos datos."
    )
    enviar_telegram(msg)

# ═══════════════════════════════════════════════════════════════════════════════
# EXCEL
# ═══════════════════════════════════════════════════════════════════════════════

def generar_excel_precios(prod_a):
    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment
    except ImportError:
        return None, "❌ Falta openpyxl en requirements.txt."

    catalogo = obtener_catalogo_api()
    if not catalogo: return None, "❌ Sin datos de la API."

    columnas = [
        "Hash", "Nombre del producto", "Precio", "Oferta", "Stock",
        "Visibilidad (Visible o Oculto)", "Descripción", "SKU",
        "Peso en KG", "Alto en CM", "Ancho en CM", "Profundidad en CM",
        "Nombre de variante #1", "Opción de variante #1",
        "Nombre de variante #2", "Opción de variante #2",
        "Nombre de variante #3", "Opción de variante #3",
        "Categorías > Subcategorías > … > Subcategorías"
    ]
    filas = []; actualizados = sin_match = igual = 0

    for nombre_norm, datos_web in catalogo.items():
        datos_prov = None
        for clave_a, da in prod_a.items():
            if son_coincidentes(nombre_norm, clave_a) or son_coincidentes(nombre_norm, da.get("nombre_base_proveedor","")):
                datos_prov = da; break

        if datos_prov is None:
            sin_match += 1; continue

        precio_nuevo = redondear_precio(datos_prov["precio"] / 0.78)
        precio_actual = int(datos_web["precio_base"])
        if precio_nuevo == precio_actual:
            igual += 1; continue

        filas.append({"Hash": nombre_norm, "Nombre del producto": datos_web["nombre_real"],
                      "Precio": precio_nuevo,
                      **{c: "" for c in columnas if c not in ["Hash","Nombre del producto","Precio"]}})
        actualizados += 1

    if not filas:
        return None, f"ℹ️ Sin cambios.\n• {igual} ya correctos\n• {sin_match} sin match con proveedor"

    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Productos"
    ws.append(columnas)
    hf = PatternFill("solid", fgColor="1F6B3B")
    for cell in ws[1]:
        cell.fill = hf; cell.font = Font(bold=True, color="FFFFFF", name="Arial", size=10)
        cell.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 25
    fp = [PatternFill("solid", fgColor="F0F7F2"), PatternFill("solid", fgColor="FFFFFF")]
    fn = Font(name="Arial", size=9); fv = Font(name="Arial", size=9, bold=True, color="1F6B3B")
    for i, fila in enumerate(filas, start=2):
        ws.append([fila.get(c,"") for c in columnas])
        for cell in ws[i]: cell.fill = fp[i%2]; cell.font = fn
        ws.cell(row=i, column=3).font = fv; ws.cell(row=i, column=3).number_format = '#,##0'
    for col, w in {'A':38,'B':48,'C':12,'D':8,'E':8,'F':12,'G':8,'H':8,'I':8,
                   'J':8,'K':8,'L':8,'M':20,'N':22,'O':20,'P':22,'Q':20,'R':22,'S':30}.items():
        ws.column_dimensions[col].width = w
    buf = io.BytesIO(); wb.save(buf); buf.seek(0)
    return buf.getvalue(), (
        f"✅ Excel:\n• *{actualizados}* actualizados\n• *{igual}* ya correctos\n• *{sin_match}* sin match\n\n"
        f"Importalo: *Productos → Importar y exportar → Importar*"
    )

# ═══════════════════════════════════════════════════════════════════════════════
# COMANDOS TELEGRAM
# ═══════════════════════════════════════════════════════════════════════════════

AYUDA_MSG = """
🤖 *Comandos disponibles:*

🔑 *Token / API*
`?code=XXXX` — Canjear código OAuth
`/estado_api` — Ver token activo
`/borrar_token` — Eliminar token

📦 *Productos (requiere token)*
`/listar` — Ver productos con variantes
`/ocultar NOMBRE` — Ocultar producto
`/publicar NOMBRE` — Publicar producto
`/stock NOMBRE CANTIDAD` — Cambiar stock (todas las variantes)
`/precio NOMBRE VALOR` — Cambiar precio (todas las variantes)

🔄 *Sincronización*
`/sync_total` — Sincronizar TODOS precios y stock ahora
`/ciclo` — Forzar ciclo de monitoreo
`/exportar_precios` — Excel precios +22%

🔔 *Webhook*
`/registrar_webhook URL` — Registrar webhook order/paid

❓ `/ayuda` — Este mensaje
""".strip()

def procesar_comando(texto):
    global _api_token, _api_user_id
    texto = texto.strip()

    if "?code=" in texto:
        auth_code = texto.split("?code=")[1].split("&")[0].strip()
        enviar_telegram("🔄 Canjeando código OAuth...")
        token = intercambiar_codigo_por_token(auth_code)
        if token:
            enviar_telegram(
                f"✅ *¡Token obtenido!*\n\n"
                f"Guardá en Railway → Variables:\n"
                f"• `API_USER_ID` = `{_api_user_id}`\n"
                f"• `API_TOKEN` = `{token}`"
            )
        else:
            enviar_telegram("❌ Token fallido. Código vencido (dura 1 min).")
        return

    cmd = texto.lower().split()
    if not cmd: return

    if cmd[0] == "/ayuda":
        enviar_telegram(AYUDA_MSG)

    elif cmd[0] == "/estado_api":
        if _api_token:
            enviar_telegram(f"✅ *Token activo*\nStore ID: `{_api_user_id}`\nToken: `{_api_token[:12]}...`")
        else:
            enviar_telegram("❌ Sin token. Mandá el `?code=` para obtenerlo.")

    elif cmd[0] == "/borrar_token":
        _api_token = _api_user_id = None
        estado = cargar_estado()
        estado.pop("api_token", None); estado.pop("api_user_id", None)
        guardar_estado(estado)
        enviar_telegram("🗑️ Token eliminado.")

    elif cmd[0] == "/listar":
        if not _api_token:
            enviar_telegram("❌ Necesito el token primero."); return
        enviar_telegram("⏳ Cargando catálogo...")
        catalogo = obtener_catalogo_api(forzar=True)
        if not catalogo:
            enviar_telegram("No encontré productos o hubo un error."); return
        lineas = []
        for nombre_norm, datos in list(catalogo.items())[:50]:
            cant_variantes = len(datos["variant_ids"])
            precio         = int(datos["precio_base"])
            published      = "✅" if datos["published"] else "🚫"
            variantes_str  = f"({cant_variantes} var.)" if cant_variantes > 1 else ""
            lineas.append(f"{published} *{datos['nombre_real']}* {variantes_str} — ${precio:,}")
        msg = f"📦 *{len(catalogo)} productos:*\n\n" + "\n".join(lineas)
        if len(catalogo) > 50: msg += f"\n\n_...y {len(catalogo)-50} más_"
        enviar_telegram(msg)

    elif cmd[0] == "/sync_total":
        enviar_telegram("🔄 *Iniciando sincronización completa...*\nEsto puede tardar varios minutos.")
        def _run():
            estado  = cargar_estado()
            prod_a  = estado.get("productos_a", {})
            if not prod_a:
                enviar_telegram("⚠️ Sin datos del proveedor. Esperá que complete un ciclo primero.")
                return
            sinc = sincronizacion_completa(prod_a)
            estado["sincronizados"] = sinc
            guardar_estado(estado)
        threading.Thread(target=_run, daemon=True).start()

    elif cmd[0] == "/ocultar":
        if not _api_token:
            enviar_telegram("❌ Necesito el token primero."); return
        nombre = " ".join(texto.split()[1:])
        if not nombre:
            enviar_telegram("Uso: `/ocultar Nombre del producto`"); return
        catalogo = obtener_catalogo_api()
        encontrado = next(((k,v) for k,v in catalogo.items() if son_coincidentes(k, nombre.lower())), None)
        if encontrado:
            _, datos = encontrado
            if cambiar_visibilidad_producto(datos["product_id"], False):
                enviar_telegram(f"🚫 *{datos['nombre_real']}* ocultado.")
            else:
                enviar_telegram(f"❌ No pude ocultar el producto.")
        else:
            enviar_telegram(f"❌ No encontré *{nombre}*.")

    elif cmd[0] == "/publicar":
        if not _api_token:
            enviar_telegram("❌ Necesito el token primero."); return
        nombre = " ".join(texto.split()[1:])
        if not nombre:
            enviar_telegram("Uso: `/publicar Nombre del producto`"); return
        catalogo = obtener_catalogo_api()
        encontrado = next(((k,v) for k,v in catalogo.items() if son_coincidentes(k, nombre.lower())), None)
        if encontrado:
            _, datos = encontrado
            if cambiar_visibilidad_producto(datos["product_id"], True):
                enviar_telegram(f"✅ *{datos['nombre_real']}* publicado.")
            else:
                enviar_telegram(f"❌ No pude publicar el producto.")
        else:
            enviar_telegram(f"❌ No encontré *{nombre}*.")

    elif cmd[0] == "/stock":
        if not _api_token:
            enviar_telegram("❌ Necesito el token primero."); return
        partes = texto.split()
        if len(partes) < 3:
            enviar_telegram("Uso: `/stock Nombre 10`"); return
        try:
            nuevo_stock = int(partes[-1]); nombre = " ".join(partes[1:-1])
        except ValueError:
            enviar_telegram("El último parámetro tiene que ser un número."); return
        catalogo = obtener_catalogo_api()
        encontrado = next(((k,v) for k,v in catalogo.items() if son_coincidentes(k, nombre.lower())), None)
        if encontrado:
            _, datos = encontrado
            if actualizar_todas_las_variantes(datos["product_id"], datos["variant_ids"], nuevo_stock=nuevo_stock):
                enviar_telegram(f"📦 *{datos['nombre_real']}* — Stock → *{nuevo_stock}* en {len(datos['variant_ids'])} variante(s).")
            else:
                enviar_telegram(f"❌ No pude actualizar el stock.")
        else:
            enviar_telegram(f"❌ No encontré *{nombre}*.")

    elif cmd[0] == "/precio":
        if not _api_token:
            enviar_telegram("❌ Necesito el token primero."); return
        partes = texto.split()
        if len(partes) < 3:
            enviar_telegram("Uso: `/precio Nombre 9999`"); return
        try:
            nuevo_precio = int(partes[-1]); nombre = " ".join(partes[1:-1])
        except ValueError:
            enviar_telegram("El último parámetro tiene que ser un número."); return
        catalogo = obtener_catalogo_api()
        encontrado = next(((k,v) for k,v in catalogo.items() if son_coincidentes(k, nombre.lower())), None)
        if encontrado:
            _, datos = encontrado
            if actualizar_todas_las_variantes(datos["product_id"], datos["variant_ids"], nuevo_precio=nuevo_precio):
                enviar_telegram(f"💲 *{datos['nombre_real']}* — Precio → *${nuevo_precio:,}* en {len(datos['variant_ids'])} variante(s).")
            else:
                enviar_telegram(f"❌ No pude actualizar el precio.")
        else:
            enviar_telegram(f"❌ No encontré *{nombre}*.")

    elif cmd[0] == "/exportar_precios":
        estado = cargar_estado()
        prod_a = estado.get("productos_a", {})
        if not prod_a:
            enviar_telegram("⚠️ Sin datos del proveedor. Esperá un ciclo."); return
        enviar_telegram("⏳ Generando Excel...")
        excel_bytes, resumen = generar_excel_precios(prod_a)
        if excel_bytes:
            fecha = datetime.now().strftime("%d-%m-%Y")
            if not enviar_archivo_telegram(excel_bytes, f"precios_{fecha}.xlsx", caption=resumen):
                enviar_telegram("❌ Excel generado pero falló el envío.")
        else:
            enviar_telegram(resumen)

    elif cmd[0] == "/registrar_webhook":
        if not _api_token:
            enviar_telegram("❌ Necesito el token primero."); return
        partes = texto.split()
        if len(partes) < 2:
            enviar_telegram("Uso: `/registrar_webhook https://tu-servidor.com/webhook`"); return
        url_wh = partes[1]
        ok, msg = registrar_webhook_order_paid(url_wh)
        enviar_telegram(msg)
        if ok:
            enviar_telegram(
                "ℹ️ Cuando un cliente pague, Tienda Negocio enviará un POST a esa URL.\n"
                "Próximamente el bot procesará esos pedidos automáticamente."
            )

    elif cmd[0] == "/ciclo":
        enviar_telegram("🔄 Iniciando ciclo manual...")
        threading.Thread(target=procesar_logica, daemon=True).start()

    else:
        enviar_telegram("❓ Comando no reconocido. Mandá `/ayuda`.")

def bucle_escucha_telegram():
    if not TELEGRAM_TOKEN: return
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
                    if str(message.get("chat", {}).get("id","")) != CHAT_ID or not texto: continue
                    print(f"📨 Cmd: {texto[:60]}")
                    try: procesar_comando(texto)
                    except Exception as e: print(f"❌ Cmd: {e}")
        except Exception as e:
            print(f"⚠️ Hilo: {e}")
        time.sleep(1)

# ═══════════════════════════════════════════════════════════════════════════════
# BASE DE DATOS
# ═══════════════════════════════════════════════════════════════════════════════

def cargar_estado():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                for k in ["pedidos_procesados","productos_a"]:
                    if k not in data: data[k] = [] if k == "pedidos_procesados" else {}
                if "sincronizados" not in data: data["sincronizados"] = {}
                return data
        except Exception: pass
    return {"productos_a":{},"pedidos_procesados":[],"sincronizados":{}}

def guardar_estado(estado):
    try:
        with open(DB_FILE, "w", encoding="utf-8") as f:
            json.dump(estado, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"❌ DB: {e}")

# ═══════════════════════════════════════════════════════════════════════════════
# MATCHING DE NOMBRES
# ═══════════════════════════════════════════════════════════════════════════════

def son_coincidentes(nombre1, nombre2):
    """
    Matching flexible. Ejemplos que ahora funcionan:
      "Microscopio RF4 RF-6558x" vs "Microscopio RF4 RF-6558x + Barlow 0.7X"
      "JC V1S Pro"               vs "Programadora JC V1S Pro (V1 - V1S Pro)"
    """
    n1 = str(nombre1).lower(); n2 = str(nombre2).lower()
    if " ".join(n1.split()) == " ".join(n2.split()): return True
    stop = {'de','para','con','el','la','los','las','un','una','y','en','del','al','a','o','e'}
    n1c = set(re.sub(r'[^a-z0-9 ]', ' ', n1).split()) - stop
    n2c = set(re.sub(r'[^a-z0-9 ]', ' ', n2).split()) - stop
    if not n1c or not n2c: return False
    # Palabras críticas: si están en uno y no en el otro son productos distintos
    criticas = ['bateria','battery','bat','face','maneral','mango','zocalo','board',
                'mini','plus','max','kit','ultra','xl','lw-a1']
    for pc in criticas:
        if (pc in n1c) != (pc in n2c): return False
    # Números: todos los del nombre MÁS CORTO deben estar en el más largo
    nums1 = {w for w in n1c if any(c.isdigit() for c in w)}
    nums2 = {w for w in n2c if any(c.isdigit() for c in w)}
    if nums1 and nums2:
        corto = nums1 if len(n1c) <= len(n2c) else nums2
        largo = nums2 if len(n1c) <= len(n2c) else nums1
        if not corto.issubset(largo): return False
    # Overlap: al menos 75% del nombre más corto debe estar en el otro
    return len(n1c & n2c) / min(len(n1c), len(n2c)) >= 0.75

# ═══════════════════════════════════════════════════════════════════════════════
# SCRAPING DEL PROVEEDOR
# ═══════════════════════════════════════════════════════════════════════════════

def limpiar_precio(texto):
    if not texto: return 0
    if "," in texto: texto = texto.split(",")[0]
    return int(''.join(filter(str.isdigit, texto)) or 0)

def procesar_precio_html(html):
    if not html: return 0, 0, False
    try:
        del_tag = html.find('del'); ins_tag = html.find('ins')
        if del_tag and ins_tag:
            pv = limpiar_precio(del_tag.text); pn = limpiar_precio(ins_tag.text)
            if pn > 0: return pn, pv, True
        return limpiar_precio(html.text), 0, False
    except Exception: return 0, 0, False

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
                atrs = [str(v).replace('-',' ').replace('_',' ').strip()
                        for k, v in var.get('attributes',{}).items() if v]
                if not atrs: continue
                texto = " - ".join(atrs).title()
                precio = var.get('display_price', 0)
                in_stock = var.get('is_in_stock', True)
                if "agotado" in var.get('variation_html','').lower(): in_stock = False
                if precio > 0 and in_stock:
                    variaciones[texto] = {"precio": int(precio), "stock": True}
    except Exception as e: print(f"⚠️ Variantes: {e}")
    return variaciones

def scrapear_proveedor():
    productos = {}
    if not SCRAPERAPI_KEY: return productos
    target = f"http://api.scraperapi.com?api_key={SCRAPERAPI_KEY}&url={URL_PROVEEDOR}"
    try:
        print("📥 Scrapeando proveedor...")
        resp = requests.get(target, timeout=60, verify=False)
        if resp.status_code != 200: return productos
        soup  = BeautifulSoup(resp.text, 'lxml')
        items = soup.select('.product') or soup.find_all(['li','div'], class_=lambda x: x and 'product' in x)
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
            interesa    = any(p in nombre_clave for p in PALABRAS_INTERES)
            es_variable = (item.find('a', class_='product_type_variable') or (price_el and "–" in price_el.text))
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
                precio, precio_anterior, en_oferta = procesar_precio_html(price_el)
                if precio > 0:
                    productos[nombre_clave] = {
                        "nombre_real": nombre_original, "nombre_base_proveedor": nombre_clave,
                        "precio": precio, "precio_anterior": precio_anterior,
                        "en_oferta": en_oferta, "stock": True
                    }
    except Exception as e: print(f"❌ Scraping proveedor: {e}")
    print(f"   🏪 {len(productos)} productos del proveedor.")
    return productos

# ═══════════════════════════════════════════════════════════════════════════════
# GMAIL
# ═══════════════════════════════════════════════════════════════════════════════

def extraer_productos_mail(cuerpo):
    productos = []; en_bloque = False
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

def chequear_pedidos_gmail():
    pedidos = []
    if not GMAIL_USER or not GMAIL_PASS: return pedidos
    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(GMAIL_USER, GMAIL_PASS); mail.select("inbox")
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
            if not any(p in subject.lower() for p in ["compra","realizó","pedido","venta"]): continue
            match = re.search(r'#(\d+)', subject)
            num_orden = match.group(1) if match else str_id
            cuerpo = ""
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/plain":
                        cuerpo = part.get_payload(decode=True).decode("utf-8", errors="ignore"); break
            else:
                cuerpo = msg.get_payload(decode=True).decode("utf-8", errors="ignore")
            items = extraer_productos_mail(cuerpo)
            if items: pedidos.append({"id_mail": str_id, "num_orden": num_orden, "productos": items})
        mail.close(); mail.logout()
    except Exception as e: print(f"❌ Gmail: {e}")
    return pedidos

def notificar_pedido(pedido, prod_a):
    reporte = f"🛒 *¡Nuevo Pedido! (#{pedido['num_orden']})*\n\n"
    todo_ok = True
    for item in pedido["productos"]:
        nombre = item["nombre"].lower()
        prov = next((d for c, d in prod_a.items() if son_coincidentes(nombre, c)), None)
        if prov:
            reporte += f"✅ *{item['nombre']}* x{item['cantidad']} — Proveedor: ${prov['precio']:,}\n"
        else:
            todo_ok = False
            reporte += f"❌ *{item['nombre']}* x{item['cantidad']} — SIN STOCK en proveedor\n"
    reporte += "\n🚀 ¡Todo disponible!" if todo_ok else "\n⚠️ Hay faltantes en el proveedor."
    return reporte

# ═══════════════════════════════════════════════════════════════════════════════
# LÓGICA PRINCIPAL — MONITOREO (cada 15 min)
# ═══════════════════════════════════════════════════════════════════════════════

def procesar_logica():
    print("\n─── 🔄 Ciclo de monitoreo ───")
    estado        = cargar_estado()
    pedidos_proc  = estado.get("pedidos_procesados", [])
    historial_a   = estado.get("productos_a", {})
    sincronizados = estado.get("sincronizados", {})

    # Chequear pedidos Gmail
    for ped in chequear_pedidos_gmail():
        if ped["id_mail"] not in pedidos_proc:
            enviar_telegram(notificar_pedido(ped, historial_a))
            pedidos_proc.append(ped["id_mail"])

    # Scrapear proveedor
    prod_a = scrapear_proveedor()
    if not prod_a:
        print("⚠️ Proveedor 0 productos. Abortando."); return

    historial_consolidado = {**historial_a, **prod_a}

    # Obtener catálogo de mi tienda desde la API (no scraping)
    catalogo_mi_tienda = obtener_catalogo_api() if _api_token else {}

    bloque_ofertas = bloque_nuevos = ""
    lineas_recuperados = lineas_precios = lineas_sin_stock = []
    lineas_recuperados = []; lineas_precios = []; lineas_sin_stock = []

    # ── Analizar proveedor: novedades ──────────────────────────────────────
    for clave, datos in prod_a.items():
        if not any(p in clave for p in PALABRAS_INTERES): continue
        estaba = clave in historial_a
        viejo  = historial_a.get(clave, {})

        # Oferta nueva
        if datos["en_oferta"] and not viejo.get("en_oferta", False):
            bloque_ofertas += f"• *{datos['nombre_real']}*\n  Reg: ${datos['precio_anterior']:,} → 🔥 ${datos['precio']:,}\n\n"

        # Producto nuevo que no tengo en mi tienda
        base_prov = datos.get("nombre_base_proveedor", clave)
        lo_tengo  = any(son_coincidentes(clave, k) or son_coincidentes(base_prov, k)
                        for k in catalogo_mi_tienda)
        if not lo_tengo and (not estaba or viejo.get("precio", 0) == 0):
            precio_ideal = redondear_precio(datos['precio'] / 0.78)
            bloque_nuevos += f"• *{datos['nombre_real']}*\n  Costo: ${datos['precio']:,} → Sugerido: ${precio_ideal:,}\n\n"

        # Proveedor recuperó stock
        elif lo_tengo and estaba and not viejo.get("stock", False) and datos["stock"]:
            nombre_real = datos["nombre_real"]
            if sincronizados.get(nombre_real, {}).get("sin_stock", False):
                # Buscar en catálogo y restaurar
                match = next(((k,v) for k,v in catalogo_mi_tienda.items()
                               if son_coincidentes(k, clave) or son_coincidentes(k, base_prov)), None)
                if match and _api_token:
                    _, datos_web = match
                    if restaurar_stock_variantes(datos_web["variant_ids"]):
                        sincronizados.pop(nombre_real, None)
                        lineas_recuperados.append(
                            f"• *{nombre_real}*\n  Proveedor vuelve a tener stock a ${datos['precio']:,}"
                        )

    # ── Analizar mi tienda contra proveedor: precios y stock ───────────────
    for nombre_norm, datos_web in catalogo_mi_tienda.items():
        nombre_real = datos_web["nombre_real"]
        variant_ids = datos_web["variant_ids"]
        sync_actual = sincronizados.get(nombre_real, {})

        # Buscar en proveedor
        datos_prov = None
        for clave_a, da in prod_a.items():
            if son_coincidentes(nombre_norm, clave_a) or son_coincidentes(nombre_norm, da.get("nombre_base_proveedor","")):
                datos_prov = da; break

        if datos_prov is None:
            # Proveedor no tiene → sin stock (solo si no lo hicimos ya)
            if not sync_actual.get("sin_stock", False) and variant_ids and _api_token:
                if actualizar_todas_las_variantes(datos_web["product_id"], variant_ids, nuevo_stock=0):
                    sincronizados[nombre_real] = {"sin_stock": True}
                    lineas_sin_stock.append(f"• *{nombre_real}* ({len(variant_ids)} var.)")
        else:
            # Limpiar flag sin_stock si volvió
            if sync_actual.get("sin_stock", False):
                sincronizados.pop(nombre_real, None)

            # Actualizar precio solo si cambió
            precio_obj      = redondear_precio(datos_prov["precio"] / 0.78)
            ultimo_sync     = sync_actual.get("precio")
            if precio_obj != ultimo_sync and variant_ids and _api_token:
                if actualizar_todas_las_variantes(datos_web["product_id"], variant_ids, nuevo_precio=precio_obj):
                    sincronizados[nombre_real] = {"precio": precio_obj}
                    precio_web_actual = int(datos_web["precio_base"])
                    if precio_web_actual != precio_obj:
                        lineas_precios.append(
                            f"• *{nombre_real}* ({len(variant_ids)} var.)\n"
                            f"  Prov: ${datos_prov['precio']:,} | Antes: ${precio_web_actual:,} | *Nuevo: ${precio_obj:,}*"
                        )

    # ── Notificaciones ─────────────────────────────────────────────────────
    if bloque_ofertas:
        enviar_telegram(f"🏷️ *¡Descuentos en el Proveedor!*\n\n{bloque_ofertas}")
    if bloque_nuevos:
        enviar_telegram(f"🔥 *¡Nuevo producto en el Proveedor!*\n\n{bloque_nuevos}")
    if lineas_recuperados:
        enviar_telegram("🔄 *¡Stock recuperado!*\n\n" + "\n\n".join(lineas_recuperados))
    if lineas_precios:
        for i in range(0, len(lineas_precios), 20):
            enviar_telegram("💲 *Precios actualizados:*\n\n" + "\n\n".join(lineas_precios[i:i+20]))
    if lineas_sin_stock:
        for i in range(0, len(lineas_sin_stock), 30):
            enviar_telegram("📦 *Marcados sin stock:*\n\n" + "\n".join(lineas_sin_stock[i:i+30]))

    if not any([bloque_ofertas, bloque_nuevos, lineas_recuperados, lineas_precios, lineas_sin_stock]):
        print("✅ Sin cambios.")

    guardar_estado({
        "productos_a":        historial_consolidado,
        "pedidos_procesados": pedidos_proc,
        "sincronizados":      sincronizados,
        "api_token":          _api_token,
        "api_user_id":        _api_user_id
    })
    print("─── ✅ Ciclo completado ───")

# ═══════════════════════════════════════════════════════════════════════════════
# ARRANQUE
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("🚀 Bot de Dropshipping iniciado...")
    _cargar_token()

    if _api_token:
        enviar_telegram(
            f"🟢 *Bot iniciado* — Token activo (store_id: `{_api_user_id}`)\n\n"
            f"Mandá `/sync_total` para sincronizar todos los precios y stock.\n"
            f"Mandá `/ayuda` para ver todos los comandos."
        )
    else:
        enviar_telegram("🟡 *Bot iniciado* — Sin token API.\nMandá `/ayuda`.")

    hilo = threading.Thread(target=bucle_escucha_telegram, daemon=True)
    hilo.start()

    # Primer ciclo
    procesar_logica()

    while True:
        time.sleep(900)
        procesar_logica()
